#!/usr/bin/env python3
"""Guard explicit client-only replacements; preserve immutable PyPI distributions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
from urllib.parse import urlparse
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = "study-233/zotero-pdf2zh-pro"
PRODUCT = "zotero-pdf2zh-pro"
ASSETS = [f"{PRODUCT}.xpi", f"{PRODUCT}-windows-x64.zip", "update.json", "windows-update.json"]
# All project files that can contribute to the server distribution, including licenses.
PYPI_INPUTS = ["server", "LICENSE", "LICENSES", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
               "setup.py", "setup.cfg", "MANIFEST.in", ".gitattributes"]


def run(*args: str, root: Path = ROOT, input: str | None = None) -> str:
    # Git object payloads require LF bytes even when this helper runs on Windows.
    return subprocess.check_output(args, cwd=root, input=input.encode("utf-8") if input is not None else None).decode("utf-8").strip()


def verify_origin(root: Path) -> None:
    origin = run("git", "remote", "get-url", "origin", root=root)
    if origin not in [f"https://github.com/{REPO}", f"https://github.com/{REPO}.git",
                      f"git@github.com:{REPO}", f"git@github.com:{REPO}.git"]:
        raise ValueError("Replacement and recovery require the canonical GitHub origin")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> dict:
    with path.open("rb") as source:
        return {"size": path.stat().st_size, "sha256": hashlib.file_digest(source, "sha256").hexdigest()}


def pypi_names(version: str) -> list[str]:
    return [f"zotero_pdf2zh_pro-{version}-py3-none-any.whl", f"zotero_pdf2zh_pro-{version}.tar.gz"]


def validate_source(version: str, old: str, commit: str, root: Path = ROOT) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Replacement version must be stable semver")
    if any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in [old, commit]):
        raise ValueError("Replacement requires full lowercase old and new commit SHAs")
    if run("git", "rev-parse", "HEAD", root=root) != commit:
        raise ValueError("Replacement checkout does not match the new commit")
    run("git", "merge-base", "--is-ancestor", old, commit, root=root)
    # Check both committed inputs and staged/unstaged copies used by build tools.
    run("git", "diff", "--exit-code", old, commit, "--", *PYPI_INPUTS, root=root)
    run("git", "diff", "--exit-code", old, "--", *PYPI_INPUTS, root=root)
    project = tomllib.loads(run("git", "show", f"{old}:server/pyproject.toml", root=root))
    if project["project"]["name"] != PRODUCT or project["project"]["version"] != version:
        raise ValueError("Replacement must keep the original PyPI project and version")


def remote_tag(version: str, root: Path = ROOT) -> tuple[str, str]:
    tag = f"refs/tags/v{version}"
    refs = dict(line.split()[::-1] for line in run(
        "git", "ls-remote", "--tags", "origin", tag, tag + "^{}", root=root).splitlines())
    if tag not in refs:
        raise ValueError(f"Replacement requires an existing remote tag: v{version}")
    return refs[tag], refs.get(tag + "^{}", refs[tag])


def public_pypi(version: str) -> dict:
    with urlopen(f"https://pypi.org/pypi/{PRODUCT}/{version}/json", timeout=60) as response:
        data = json.load(response)
    if len(data["urls"]) != 2 or {entry["filename"] for entry in data["urls"]} != set(pypi_names(version)):
        raise ValueError("Replacement requires exactly the original wheel and sdist on PyPI")
    files = {}
    for name in pypi_names(version):
        matches = [entry for entry in data["urls"] if entry["filename"] == name]
        if len(matches) != 1:
            raise ValueError(f"Original PyPI distribution is missing or ambiguous: {name}")
        entry = matches[0]
        url = urlparse(entry["url"])
        sha = entry["digests"]["sha256"]
        if (url.scheme != "https" or url.netloc != "files.pythonhosted.org"
                or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or type(entry["size"]) is not int or not 0 < entry["size"] <= 32 * 1024 * 1024
                or entry.get("yanked")):
            raise ValueError(f"Invalid original PyPI distribution metadata: {name}")
        files[name] = {"size": entry["size"], "sha256": sha, "url": entry["url"]}
    return files


def verify_files(directory: Path, files: dict) -> None:
    for name, expected in files.items():
        if digest(directory / name) != {key: expected[key] for key in ["size", "sha256"]}:
            raise ValueError(f"Original PyPI content mismatch: {name}")


def download_pypi(directory: Path, files: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".replacement-", dir=directory) as temporary:
        staged = Path(temporary)
        for name, expected in files.items():
            with urlopen(expected["url"], timeout=120) as response, (staged / name).open("wb") as output:
                final_url = urlparse(response.url)
                if final_url.scheme != "https" or final_url.netloc != "files.pythonhosted.org":
                    raise ValueError("PyPI download redirected outside the official file host")
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > expected["size"]:
                        raise ValueError(f"Original PyPI download exceeds expected size: {name}")
                    output.write(chunk)
        # Validate the complete pair before replacing either local distribution.
        verify_files(staged, files)
        for name in files:
            os.replace(staged / name, directory / name)


def prepare(version: str, old: str, root: Path = ROOT) -> dict:
    verify_origin(root)
    commit = run("git", "rev-parse", "HEAD", root=root)
    validate_source(version, old, commit, root)
    tag_object, tagged_commit = remote_tag(version, root)
    if tagged_commit != old:
        raise ValueError(f"Existing tag does not point to the explicitly expected commit: {old}")
    files = public_pypi(version)
    original = root / "dist/release/checksums.json"
    if original.is_file():
        manifest = json.loads(original.read_text(encoding="utf-8"))
        if manifest.get("version") == version and manifest.get("commit") == old:
            for name, expected in files.items():
                if manifest["artifacts"].get(name) != {key: expected[key] for key in ["size", "sha256"]}:
                    raise ValueError(f"Public PyPI differs from original release provenance: {name}")
    snapshot = {"previousCommit": old, "previousTagObject": tag_object,
                "pypiSourceCommit": old, "clientCommit": commit, "pypiArtifacts": files}
    download_pypi(root / "server/dist", files)
    (root / "dist").mkdir(exist_ok=True)
    write_json(root / "dist/replacement-source.json", snapshot)
    return snapshot


def replacement_metadata(version: str, old: str, commit: str, root: Path = ROOT) -> dict:
    validate_source(version, old, commit, root)
    snapshot = json.loads((root / "dist/replacement-source.json").read_text(encoding="utf-8"))
    if (snapshot.get("previousCommit") != old or snapshot.get("pypiSourceCommit") != old
            or snapshot.get("clientCommit") != commit
            or not re.fullmatch(r"[0-9a-f]{40}", snapshot.get("previousTagObject", ""))
            or set(snapshot.get("pypiArtifacts", {})) != set(pypi_names(version))):
        raise ValueError("Replacement provenance does not match this release")
    verify_files(root / "server/dist", snapshot["pypiArtifacts"])
    return snapshot


def release_info(version: str, root: Path = ROOT) -> dict:
    return json.loads(run("gh", "api", f"repos/{REPO}/releases/tags/v{version}", root=root))


def upload(version: str, paths: list[Path], root: Path = ROOT) -> None:
    run("gh", "release", "upload", f"v{version}", *map(str, paths), "--repo", REPO, "--clobber", root=root)


def set_notes(version: str, notes: Path, root: Path = ROOT) -> None:
    run("gh", "release", "edit", f"v{version}", "--repo", REPO, "--notes-file", str(notes), root=root)


def move_tag(version: str, expected: str, replacement: str, root: Path = ROOT) -> None:
    ref = f"refs/tags/v{version}"
    run("git", "push", f"--force-with-lease={ref}:{expected}", "origin", f"{replacement}:{ref}", root=root)


def sync_local_tag(version: str, expected: str, replacement: str, root: Path) -> None:
    ref = f"refs/tags/v{version}"
    current = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=root, text=True, capture_output=True)
    if current.returncode == 0 and current.stdout.strip() == expected:
        result = subprocess.run(["git", "update-ref", ref, replacement, expected], cwd=root)
        if result.returncode:
            print("Local tag changed concurrently; the unknown local tag was left alone")


def download_assets(version: str, target: Path, info: dict, root: Path = ROOT,
                    allow_missing: set[str] | None = None) -> dict:
    entries = {entry["name"]: entry for entry in info["assets"]}
    if (len(entries) != len(info["assets"]) or set(entries) - set(ASSETS)
            or set(ASSETS) - set(entries) - (allow_missing or set())):
        raise ValueError("Release assets differ from the expected four public assets")
    target.mkdir(parents=True)
    hashes = {}
    for name in ASSETS:
        if name not in entries:
            continue
        run("gh", "release", "download", f"v{version}", "--repo", REPO,
            "--pattern", name, "--dir", str(target), root=root)
        hashes[name] = digest(target / name)
        if hashes[name]["size"] != entries[name]["size"]:
            raise ValueError(f"Release asset download size mismatch: {name}")
        expected = entries[name].get("digest")
        if expected and expected != "sha256:" + hashes[name]["sha256"]:
            raise ValueError(f"Release asset download hash mismatch: {name}")
    return hashes


def verify_rollback_ownership(backup: Path, report: dict, root: Path) -> None:
    """Do not overwrite another operator's changes, even if the tag is unchanged."""
    info = release_info(report["version"], root)
    original_info = json.loads((backup / "release.json").read_text(encoding="utf-8"))
    notes = [original_info["body"] or ""]
    if report["notesAttempted"]:
        notes.append(report["replacementNotes"])
    if (info["id"] != original_info["id"] or (info["body"] or "") not in notes
            or info["draft"] != original_info["draft"] or info["prerelease"] != original_info["prerelease"]):
        raise ValueError("Rollback refused: release identity or notes changed outside this replacement")
    attempted = set(report["attemptedAssets"])
    with tempfile.TemporaryDirectory(prefix="replacement-rollback-check-") as temporary:
        hashes = download_assets(report["version"], Path(temporary) / "assets", info, root,
                                 allow_missing=attempted)
    for name, value in hashes.items():
        allowed = [report["originalAssets"][name]]
        if name in attempted:
            allowed.append(report["replacementAssets"][name])
        if value not in allowed:
            raise ValueError(f"Rollback refused: asset changed outside this replacement: {name}")


def restore(backup: Path, root: Path = ROOT) -> None:
    verify_origin(root)
    report = json.loads((backup / "report.json").read_text(encoding="utf-8"))
    version = report["version"]
    verify_files(backup / "assets", report["originalAssets"])
    current, _ = remote_tag(version, root)
    if current != report["replacementTagObject"]:
        raise ValueError("Rollback refused: remote tag changed concurrently; keep the backup for recovery")
    try:
        verify_rollback_ownership(backup, report, root)
    except Exception as error:
        report["status"] = "restore_failed"
        report["restoreErrors"] = [str(error)]
        write_json(backup / "report.json", report)
        raise
    restored_object = subprocess.check_output(
        ["git", "hash-object", "-t", report["previousTagType"], "-w", "--stdin"],
        input=(backup / "tag-object").read_bytes(), cwd=root).decode("ascii").strip()
    if restored_object != report["previousTagObject"]:
        raise ValueError("Original tag object backup is corrupt; rollback stopped before changing assets")
    def require_owned_tag():
        if remote_tag(version, root)[0] != report["replacementTagObject"]:
            report["status"] = "restore_failed"
            report["restoreErrors"] = ["Remote tag changed during rollback; remaining writes stopped"]
            write_json(backup / "report.json", report)
            raise ValueError(f"Rollback stopped after concurrent tag change; backup: {backup}")

    require_owned_tag()
    errors = []
    for name in ASSETS:  # Payloads precede their update manifests, including during rollback.
        if name not in report["attemptedAssets"]:
            continue
        require_owned_tag()
        try:
            upload(version, [backup / "assets" / name], root)
        except Exception as error:
            errors.append(str(error))
    if report["notesAttempted"]:
        require_owned_tag()
        try:
            set_notes(version, backup / "notes.md", root)
        except Exception as error:
            errors.append(str(error))
    if not errors:
        try:
            move_tag(version, report["replacementTagObject"], report["previousTagObject"], root)
        except Exception as error:
            errors.append(str(error))
    report["status"] = "restore_failed" if errors else "restored"
    report["restoreErrors"] = errors
    write_json(backup / "report.json", report)
    if errors:
        raise RuntimeError(f"Replacement rollback incomplete; backup: {backup}; errors: {errors}")
    sync_local_tag(version, report["replacementTagObject"], report["previousTagObject"], root)


def publish(version: str, old: str, commit: str, notes: Path, root: Path = ROOT,
            backup_path_file: Path | None = None) -> Path:
    verify_origin(root)
    snapshot = replacement_metadata(version, old, commit, root)
    if commit == old:
        raise ValueError("Replacement requires a new client commit")
    if public_pypi(version) != snapshot["pypiArtifacts"]:
        raise ValueError("Public PyPI changed since replacement preparation")
    manifest = json.loads((root / "dist/release/checksums.json").read_text(encoding="utf-8"))
    if manifest.get("version") != version or manifest.get("commit") != commit or manifest.get("replacement") != snapshot:
        raise ValueError("Verified replacement artifact provenance mismatch")
    verify_files(root / "dist/release", {name: manifest["artifacts"][name] for name in ASSETS})
    expected_tag = snapshot["previousTagObject"]
    if remote_tag(version, root) != (expected_tag, old):
        raise ValueError("Existing tag changed since replacement preparation")
    info = release_info(version, root)
    if info["draft"] or info["prerelease"]:
        raise ValueError("Replacement requires an existing stable public release")
    backup = root / "dist/replacement-backups" / (f"v{version}-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    write_json(backup / "release.json", info)
    (backup / "notes.md").write_text(info["body"] or "", encoding="utf-8", newline="")
    hashes = download_assets(version, backup / "assets", info, root)
    run("git", "fetch", "--no-tags", "origin", f"refs/tags/v{version}", root=root)
    tag_type = run("git", "cat-file", "-t", expected_tag, root=root)
    raw_tag = subprocess.check_output(["git", "cat-file", tag_type, expected_tag], cwd=root)
    (backup / "tag-object").write_bytes(raw_tag)
    if run("git", "rev-parse", expected_tag + "^{commit}", root=root) != old:
        raise ValueError("Original tag object does not resolve to the expected old commit")
    ident = run("git", "var", "GIT_COMMITTER_IDENT", root=root)
    replacement_tag = run("git", "mktag", root=root, input=(
        f"object {commit}\ntype commit\ntag v{version}\ntagger {ident}\n\nv{version} client replacement\n"))
    report = {"version": version, **snapshot, "previousTagType": tag_type,
              "replacementTagObject": replacement_tag, "originalAssets": hashes,
              "replacementAssets": {name: manifest["artifacts"][name] for name in ASSETS},
              "attemptedAssets": [], "notesAttempted": False,
              "replacementNotes": notes.read_bytes().decode("utf-8"), "status": "backed_up"}
    write_json(backup / "report.json", report)
    # Recheck release identity after downloads and before the first public mutation.
    current_info = release_info(version, root)
    identity = lambda value: (value["id"], value["body"], sorted((a["id"], a["name"], a["size"]) for a in value["assets"]))
    if identity(current_info) != identity(info):
        raise ValueError(f"Release changed while backing it up; no replacement made. Backup: {backup}")
    try:
        move_tag(version, expected_tag, replacement_tag, root)
        for name in ASSETS:
            report["attemptedAssets"].append(name)
            write_json(backup / "report.json", report)
            upload(version, [root / "dist/release" / name], root)
        report["notesAttempted"] = True
        write_json(backup / "report.json", report)
        set_notes(version, notes, root)
        with tempfile.TemporaryDirectory(prefix="replacement-verify-") as temporary:
            verified = download_assets(version, Path(temporary) / "assets", release_info(version, root), root)
            if verified != report["replacementAssets"]:
                raise ValueError("Published replacement assets do not match verified files")
        if remote_tag(version, root) != (replacement_tag, commit):
            raise ValueError("Replacement tag changed during publication")
        report["status"] = "published"
        write_json(backup / "report.json", report)
        (root / "dist/replacement-backup-path.txt").write_text(str(backup), encoding="utf-8")
        if backup_path_file is not None:
            backup_path_file.write_text(str(backup), encoding="utf-8")
    except BaseException as error:
        current, _ = remote_tag(version, root)
        if current == replacement_tag:
            restore(backup, root)
        elif current != expected_tag:
            report["status"] = "restore_failed"
            write_json(backup / "report.json", report)
            raise RuntimeError(f"Tag changed concurrently; automatic rollback refused. Backup: {backup}") from error
        raise RuntimeError(f"Replacement failed; backup: {backup}") from error
    sync_local_tag(version, expected_tag, replacement_tag, root)
    print(f"Replaced v{version}: clients {commit}; unchanged PyPI {old}; backup {backup}")
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for action in ["prepare", "publish"]:
        command = commands.add_parser(action)
        command.add_argument("version")
        command.add_argument("old_commit")
        if action == "publish":
            command.add_argument("commit")
            command.add_argument("--notes", required=True, type=Path)
            command.add_argument("--backup-path-file", type=Path)
    command = commands.add_parser("restore")
    command.add_argument("backup", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.version, args.old_commit), indent=2))
    elif args.command == "publish":
        publish(args.version, args.old_commit, args.commit, args.notes, backup_path_file=args.backup_path_file)
    else:
        restore(args.backup)


if __name__ == "__main__":
    main()
