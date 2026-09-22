#!/usr/bin/env python3
"""Build the separately published glossary-data tree; never bundle it in the app.

The data checkout contains sources.json, curation.json, raw/, and LICENSES/.
Only reviewed, explicitly selected upstream entries are emitted. The application
repository contains this tool and catalog metadata, but no terminology corpus.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import ssl
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class Headings(HTMLParser):
    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag
        self.active: list[str] | None = None
        self.entries: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == self.tag:
            self.active = [dict(attrs).get("id", ""), ""]

    def handle_data(self, value):
        if self.active is not None:
            self.active[1] += value

    def handle_endtag(self, tag):
        if tag == self.tag and self.active is not None:
            key, value = self.active
            self.entries[key] = value.strip().rstrip("¶").strip()
            self.active = None


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sha256(value: bytes):
    return hashlib.sha256(value).hexdigest()


def source_entries(source, root):
    inputs = source["inputs"]
    for item in inputs + source.get("licenseFiles", []):
        data = (root / item["file"]).read_bytes()
        if sha256(data) != item["sha256"]:
            raise ValueError(f"Upstream snapshot checksum mismatch: {item['file']}")
    if source["format"] == "naer-csv":
        content = (root / inputs[0]["file"]).read_text(encoding="utf-8-sig")
        return {r["序號"]: (r["英文名稱"].strip(), r[source["targetColumn"]].strip())
                for r in csv.DictReader(io.StringIO(content))}
    if source["format"] == "python-glossary":
        parser = Headings("dt")
        parser.feed((root / inputs[0]["file"]).read_text(encoding="utf-8"))
        return {key: tuple(value.split(" -- ", 1)) for key, value in parser.entries.items()
                if " -- " in value}
    if source["format"] == "google-glossary":
        pages = []
        for item in inputs:
            parser = Headings("h2")
            parser.feed((root / item["file"]).read_text(encoding="utf-8"))
            pages.append(parser.entries)
        return {key: (value, pages[1][key]) for key, value in pages[0].items()
                if key in pages[1]}
    if source["format"] == "gemet-translations":
        result = {}
        for item in inputs:
            labels = {label["language"]: label["string"] for label in read_json(root / item["file"])}
            if "en" in labels and "zh-CN" in labels:
                result[item["entryId"]] = (labels["en"], labels["zh-CN"])
        return result
    raise ValueError(f"Unknown source format: {source['format']}")


def fetch_missing(root, sources):
    # Windows' trust store can retrieve a missing intermediate certificate.
    # Certificate and hostname validation remain enabled; never use verify=False.
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = ssl.create_default_context()
    for source in sources:
        for item in source["inputs"] + source.get("licenseFiles", []):
            path = root / item["file"]
            if path.exists():
                continue
            if not item["url"].startswith("https://"):
                raise ValueError("Upstream downloads must use HTTPS")
            with urllib.request.urlopen(item["url"], context=context, timeout=60) as response:
                data = response.read()
            if sha256(data) != item["sha256"]:
                raise ValueError(f"Upstream changed: {item['url']}; use the archived data checkout")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def build(root, revision):
    sources = read_json(root / "sources.json")
    curation = read_json(root / "curation.json")
    lookup = {s["id"]: source_entries(s, root) for s in sources}
    source_by_id = {s["id"]: s for s in sources}
    metadata = []
    for specification in curation["packs"]:
        terms = {}
        for selected in specification["selected"]:
            source_id = selected["sourceId"]
            original = lookup[source_id][selected["entryId"]]
            if list(original) != selected["original"]:
                raise ValueError(f"Unreviewed source change: {source_id}/{selected['entryId']}")
            english, chinese = selected.get("source", original[0]), selected.get("target", original[1])
            if ("source" in selected or "target" in selected) and not selected.get("note"):
                raise ValueError("Every adaptation must have a review note")
            english, chinese = " ".join(english.split()), " ".join(chinese.split())
            if not english or not chinese or not re.search(r"[\u3400-\u9fff]", chinese):
                raise ValueError(f"Invalid bilingual term: {english!r}")
            if len(english) > 120 or len(chinese) > 100 or re.search(r"[\x00-\x1f]", english + chinese):
                raise ValueError("Term exceeds allowed shape")
            key = english.casefold()
            ref = {"id": source_id, "entry": selected["entryId"]}
            if key in terms:
                if terms[key]["target"] != chinese:
                    raise ValueError(f"Conflicting reviewed translation: {english}")
                if source_id not in terms[key]["sourceIds"]:
                    terms[key]["sourceIds"].append(source_id)
                terms[key]["sourceRefs"].append(ref)
            else:
                terms[key] = {"source": english, "target": chinese, "tgt_lng": "zh-CN",
                              "sourceIds": [source_id], "sourceRefs": [ref]}
        used = sorted({s for term in terms.values() for s in term["sourceIds"]})
        descriptions = [{key: source_by_id[s][key] for key in
                         ("id", "name", "url", "license", "licenseUrl")} for s in used]
        pack_sources = []
        for description in descriptions:
            source = source_by_id[description["id"]]
            detail = {**description, "notice": source["notice"]}
            if source.get("includeLicenseText"):
                detail["licenseText"] = (root / source["includeLicenseText"]).read_text(encoding="utf-8")
            pack_sources.append(detail)
        pack = {"schemaVersion": 1, "id": specification["id"], "version": curation["version"],
                "sourceLang": "en", "targetLang": "zh-CN", "sources": pack_sources,
                "entries": sorted(terms.values(), key=lambda t: t["source"].casefold())}
        if not pack["entries"]:
            raise ValueError(f"Empty pack: {pack['id']}")
        path = root / "packs" / f"{pack['id']}.json"
        write_json(path, pack)
        raw = path.read_bytes()
        metadata.append({"id": pack["id"], "name": specification["name"], "version": pack["version"],
                         "sha256": sha256(raw), "entryCount": len(pack["entries"]), "sizeBytes": len(raw),
                         "url": f"https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/{revision}/packs/{pack['id']}.json",
                         "sourceLang": "en", "targetLang": "zh-CN", "sources": descriptions})
    result = {"schemaVersion": 1, "packs": metadata}
    write_json(root / "catalog.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Separate glossary-data checkout")
    parser.add_argument("--revision", default="UNPUBLISHED", help="40-character commit containing packs/")
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--write-module", type=Path, help="Write application metadata, requiring a pinned commit")
    args = parser.parse_args()
    if args.write_module and not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("--write-module requires an immutable 40-character commit")
    if args.fetch_missing:
        fetch_missing(args.data_dir, read_json(args.data_dir / "sources.json"))
    catalog = build(args.data_dir, args.revision)
    if args.write_module:
        import pprint
        args.write_module.write_text(
            '"""Metadata only. Term corpora are downloaded into the user data directory."""\n\n'
            'CATALOG_URL = "https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/glossary-data/catalog.json"\n\n'
            + "GLOSSARY_CATALOG = " + pprint.pformat(catalog, sort_dicts=False, width=100) + "\n",
            encoding="utf-8", newline="\n")
    for item in catalog["packs"]:
        print(f"{item['id']}: {item['entryCount']} entries, {item['sizeBytes']} bytes, sha256={item['sha256']}")


if __name__ == "__main__":
    main()
