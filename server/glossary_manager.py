"""Download verified glossary packs and capture their terms in translation tasks."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from babeldoc.glossary_options import normalize_glossary_entries
from glossary_catalog import CATALOG_URL, GLOSSARY_CATALOG

MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_CATALOG_BYTES = 1024 * 1024
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class GlossaryError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _language(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _source(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def _validate_catalog(catalog: dict) -> dict[str, dict]:
    if not isinstance(catalog, dict) or catalog.get("schemaVersion") != 1 or not isinstance(catalog.get("packs"), list):
        raise GlossaryError("Invalid glossary catalog")
    packs = {}
    for pack in catalog["packs"]:
        if not isinstance(pack, dict):
            raise GlossaryError("Invalid glossary catalog entry")
        for field in ("id", "version"):
            if not isinstance(pack.get(field), str) or not SAFE_COMPONENT.fullmatch(pack[field]):
                raise GlossaryError(f"Invalid glossary {field}")
        if pack["id"] in packs or not isinstance(pack.get("sha256"), str) or not SHA256.fullmatch(pack["sha256"]):
            raise GlossaryError("Invalid or duplicate glossary identity")
        if type(pack.get("sizeBytes")) is not int or not 0 < pack["sizeBytes"] <= MAX_PACK_BYTES:
            raise GlossaryError("Invalid glossary download size")
        if type(pack.get("entryCount")) is not int or pack["entryCount"] <= 0:
            raise GlossaryError("Invalid glossary entry count")
        if pack.get("sourceLang") != "en" or pack.get("targetLang") != "zh-CN":
            raise GlossaryError("Unsupported glossary language pair")
        url = urlsplit(str(pack.get("url", "")))
        parts = url.path.split("/")
        if (url.scheme != "https" or url.netloc != "raw.githubusercontent.com" or len(parts) < 5
                or parts[1:3] != ["study-233", "zotero-pdf2zh-pro"]
                or not re.fullmatch(r"[0-9a-f]{40}", parts[3]) or url.query or url.fragment):
            raise GlossaryError("Glossary download must use the project's pinned data commit")
        if not isinstance(pack.get("name"), (str, dict)) or not isinstance(pack.get("sources"), list) or not pack["sources"]:
            raise GlossaryError("Glossary source attribution is missing")
        for source in pack["sources"]:
            if not isinstance(source, dict) or any(not isinstance(source.get(key), str) or not source[key] for key in ("name", "url", "license", "licenseUrl")):
                raise GlossaryError("Invalid glossary source attribution")
        packs[pack["id"]] = copy.deepcopy(pack)
    return packs


def _decode_pack(content: bytes, metadata: dict) -> list[dict]:
    if len(content) != metadata["sizeBytes"] or hashlib.sha256(content).hexdigest() != metadata["sha256"]:
        raise GlossaryError("Glossary checksum or size does not match; download it again")
    try:
        pack = json.loads(content)
        if (not isinstance(pack, dict) or pack.get("schemaVersion") != 1
                or any(pack.get(key) != metadata[key] for key in ("id", "version", "sourceLang", "targetLang"))):
            raise ValueError("identity mismatch")
        entries = normalize_glossary_entries(pack.get("entries"))
        if len(entries) != metadata["entryCount"] or any(row["tgt_lng"] != "zh-cn" for row in entries):
            raise ValueError("entry count or language mismatch")
    except (ValueError, TypeError, UnicodeError) as error:
        raise GlossaryError(f"Invalid glossary pack: {error}") from None
    return entries


class GlossaryManager:
    def __init__(self, root: Path, *, catalog: dict | None = None, catalog_url: str | None = None):
        self.root = Path(root)
        self._catalog = _validate_catalog(GLOSSARY_CATALOG if catalog is None else catalog)
        self._catalog_url = CATALOG_URL if catalog_url is None else catalog_url
        self._lock = threading.RLock()
        self._installed: dict[tuple[str, str, str], dict] = {}
        self._integrity_errors: dict[str, str] = {}
        self._downloads: dict[str, dict] = {}
        self._initialized = False
        self._closed = False

    def _initialize(self):
        if self._initialized:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        # Only this manager's temporary files are removed; installed versions survive.
        for temporary in self.root.glob("*/*/*.part"):
            temporary.unlink(missing_ok=True)
        (self.root / "catalog.json.part").unlink(missing_ok=True)
        cached = self.root / "catalog.json"
        if cached.exists():
            try:
                if cached.stat().st_size <= MAX_CATALOG_BYTES:
                    parsed = _validate_catalog(json.loads(cached.read_bytes()))
                    if self._catalog.keys() <= parsed.keys():
                        self._catalog = parsed
            except (OSError, ValueError):
                pass
        for path in self.root.glob("*/*/*.meta.json"):
            try:
                if path.stat().st_size > MAX_CATALOG_BYTES:
                    continue
                metadata = json.loads(path.read_bytes())
                _validate_catalog({"schemaVersion": 1, "packs": [metadata]})
                expected = self._metadata_path(metadata)
                if expected != path or not isinstance(metadata.get("installedAt"), str):
                    continue
                content_path = self._content_path(metadata)
                if content_path.stat().st_size != metadata["sizeBytes"]:
                    continue
                _decode_pack(content_path.read_bytes(), metadata)
                self._installed[self._key(metadata)] = metadata
            except (OSError, ValueError, TypeError):
                continue
        self._initialized = True

    @staticmethod
    def _key(metadata):
        return metadata["id"], metadata["version"], metadata["sha256"]

    def _content_path(self, metadata):
        return self.root / metadata["id"] / metadata["version"] / f"{metadata['sha256']}.json"

    def _metadata_path(self, metadata):
        return self._content_path(metadata).with_suffix(".meta.json")

    def _read_installed(self, metadata):
        try:
            path = self._content_path(metadata)
            if path.stat().st_size != metadata["sizeBytes"]:
                raise GlossaryError("Glossary checksum or size does not match; download it again")
            return _decode_pack(path.read_bytes(), metadata)
        except (OSError, GlossaryError) as error:
            self._installed.pop(self._key(metadata), None)
            message = f"Glossary {metadata['id']} is damaged or missing; download it again: {error}"
            self._integrity_errors[metadata["id"]] = message
            raise GlossaryError(message) from None

    def _require_pack(self, pack_id):
        pack = self._catalog.get(pack_id)
        if pack is None:
            raise GlossaryError("Unknown glossary pack", 404)
        return pack

    def _snapshot(self, pack_id):
        pack = copy.deepcopy(self._require_pack(pack_id))
        installed = sorted(
            (metadata for metadata in self._installed.values() if metadata["id"] == pack_id),
            key=lambda item: item["installedAt"], reverse=True,
        )
        pack["installedVersions"] = [
            {key: row[key] for key in ("version", "sha256", "entryCount", "sizeBytes", "installedAt")}
            for row in installed
        ]
        current = self._key(pack) in self._installed
        pack["status"] = "installed" if current else "update_available" if installed else "not_downloaded"
        job = self._downloads.get(pack_id)
        if job:
            pack["download"] = {key: job[key] for key in ("state", "receivedBytes", "totalBytes", "error")}
            if job["state"] in {"downloading", "cancelling"}:
                pack["status"] = "downloading"
            elif job["state"] == "failed":
                pack["status"] = "failed"
        if pack_id in self._integrity_errors and not current and pack["status"] != "downloading":
            pack["status"] = "failed"
            pack["download"] = {"state": "failed", "receivedBytes": 0,
                                "totalBytes": pack["sizeBytes"], "error": self._integrity_errors[pack_id]}
        return pack

    def list_packs(self):
        with self._lock:
            self._initialize()
            for metadata in list(self._installed.values()):
                try:
                    self._read_installed(metadata)
                except GlossaryError:
                    pass
            return [self._snapshot(pack_id) for pack_id in self._catalog]

    def refresh_catalog(self):
        try:
            with requests.get(self._catalog_url, stream=True, timeout=(5, 10), allow_redirects=False) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > MAX_CATALOG_BYTES:
                        raise GlossaryError("Glossary catalog is too large")
            catalog = json.loads(content)
            parsed = _validate_catalog(catalog)
            with self._lock:
                self._initialize()
                if not self._catalog.keys() <= parsed.keys():
                    raise GlossaryError("Glossary catalog cannot remove existing categories")
                temporary = self.root / "catalog.json.part"
                temporary.write_bytes(content)
                temporary.replace(self.root / "catalog.json")
                self._catalog = parsed
                return self.list_packs()
        except (requests.RequestException, OSError, ValueError) as error:
            raise GlossaryError(f"Could not update glossary catalog: {error}", 502) from None

    def download(self, pack_id: str, version: str | None = None):
        with self._lock:
            self._initialize()
            if self._closed:
                raise GlossaryError("Glossary manager is shutting down", 409)
            metadata = copy.deepcopy(self._require_pack(pack_id))
            if version is not None and version != metadata["version"]:
                raise GlossaryError("This glossary version is not in the catalog")
            previous = self._downloads.get(pack_id)
            if previous and previous["state"] in {"downloading", "cancelling"}:
                return self._snapshot(pack_id)
            if self._key(metadata) in self._installed:
                try:
                    self._read_installed(metadata)
                except GlossaryError:
                    pass
                else:
                    self._downloads.pop(pack_id, None)
                    self._integrity_errors.pop(pack_id, None)
                    return self._snapshot(pack_id)
            self._integrity_errors.pop(pack_id, None)
            job = {"state": "downloading", "receivedBytes": 0, "totalBytes": metadata["sizeBytes"],
                   "error": None, "cancel": threading.Event()}
            thread = threading.Thread(target=self._download, args=(metadata, job), daemon=True,
                                      name=f"glossary-{pack_id}")
            job["thread"] = thread
            self._downloads[pack_id] = job
            thread.start()
            return self._snapshot(pack_id)

    def _download(self, metadata, job):
        path = self._content_path(metadata)
        temporary = path.with_suffix(".json.part")
        metadata_temporary = self._metadata_path(metadata).with_suffix(".json.part")
        error_message = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(metadata["url"], stream=True, timeout=(5, 5), allow_redirects=False) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(65536):
                        if job["cancel"].is_set():
                            return
                        with self._lock:
                            job["receivedBytes"] += len(chunk)
                            if job["receivedBytes"] > metadata["sizeBytes"]:
                                raise GlossaryError("Glossary download exceeds its declared size")
                        output.write(chunk)
            _decode_pack(temporary.read_bytes(), metadata)
            with self._lock:
                if job["cancel"].is_set():
                    return
                installed = {**metadata, "installedAt": datetime.now(UTC).isoformat()}
                metadata_temporary.write_text(json.dumps(installed, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
                metadata_temporary.replace(self._metadata_path(metadata))
                self._installed[self._key(metadata)] = installed
        except Exception as error:
            # A background worker has no caller to receive unexpected errors.
            error_message = str(error) or type(error).__name__
        finally:
            with self._lock:
                for leftover in (temporary, metadata_temporary):
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass
                if job["cancel"].is_set():
                    job["state"] = "cancelled"
                    job["error"] = None
                elif error_message:
                    job["state"] = "failed"
                    job["error"] = error_message
                else:
                    job["state"] = "completed"

    def cancel(self, pack_id):
        with self._lock:
            self._initialize()
            self._require_pack(pack_id)
            job = self._downloads.get(pack_id)
            if job and job["state"] in {"downloading", "cancelling"}:
                job["cancel"].set()
                job["state"] = "cancelling"
            return self._snapshot(pack_id)

    def uninstall(self, pack_id):
        with self._lock:
            self._initialize()
            self._require_pack(pack_id)
            job = self._downloads.get(pack_id)
            if job and job["state"] in {"downloading", "cancelling"}:
                raise GlossaryError("Cancel the download before removing this glossary", 409)
            directory = self.root / pack_id
            if directory.exists():
                shutil.rmtree(directory)
            self._installed = {key: value for key, value in self._installed.items() if key[0] != pack_id}
            self._downloads.pop(pack_id, None)
            self._integrity_errors.pop(pack_id, None)
            return self._snapshot(pack_id)

    def task_snapshot(self, references, custom, source_lang, target_lang):
        if not isinstance(references, list):
            raise GlossaryError("glossaryPacks must be an array")
        with self._lock:
            self._initialize()
            installed = []
            seen = set()
            for reference in references:
                if not isinstance(reference, dict) or any(not isinstance(reference.get(key), str) for key in ("id", "version", "sha256")):
                    raise GlossaryError("Each glossaryPacks entry needs id, version and sha256")
                identity = self._key(reference)
                if identity in seen:
                    continue
                if any(previous[0] == identity[0] for previous in seen):
                    raise GlossaryError("Select only one version of each glossary pack")
                seen.add(identity)
                metadata = self._installed.get(identity)
                if metadata is None:
                    raise GlossaryError(f"Glossary {reference['id']} version {reference['version']} is not installed")
                entries = self._read_installed(metadata)
                installed.append((metadata, entries))
            installed.sort(key=lambda item: self._key(item[0]))
            source, target = _language(source_lang), _language(target_lang)
            applicable = (source == "en" or source.startswith("en-")) and target in {"zh", "zh-cn", "zh-hans"}
            merged = copy.deepcopy(custom)
            if applicable:
                overrides = {_source(row["source"]) for row in custom if _language(row["tgt_lng"]) in {"", target}}
                candidates = {}
                for _, entries in installed:
                    for row in entries:
                        key = _source(row["source"])
                        if key not in overrides:
                            candidates.setdefault(key, {})[row["target"]] = row
                for key in sorted(candidates):
                    targets = candidates[key]
                    if len(targets) == 1:
                        row = next(iter(targets.values()))
                        merged.append({**row, "tgt_lng": target})
            metadata = [{key: copy.deepcopy(pack[key]) for key in
                         ("id", "version", "sha256", "entryCount", "sourceLang", "targetLang", "sources")}
                        for pack, _ in installed]
            return merged, metadata

    def close(self):
        with self._lock:
            self._closed = True
            jobs = list(self._downloads.values())
            for job in jobs:
                job["cancel"].set()
        for job in jobs:
            job["thread"].join(timeout=6)
