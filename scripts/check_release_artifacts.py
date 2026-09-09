#!/usr/bin/env python3
"""Validate a fixed release commit and collect independently verifiable artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

from build_windows_package import package_entries, validate_versions
from windows_pe import validate_release_pe

ROOT = Path(__file__).resolve().parents[1]
REPO = "study-233/zotero-pdf2zh-pro"
PRODUCT = "zotero-pdf2zh-pro"
ADDON_ID = f"{PRODUCT}@study-233"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_source(version: str, commit: str) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Version must be stable semver")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Commit must be a full lowercase SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != commit:
        raise ValueError(f"Checkout mismatch: {head} != {commit}")
    validate_versions(version)
    for lock, name in [("server/uv.lock", PRODUCT), ("windows-app/src-tauri/Cargo.lock", f"{PRODUCT}-control")]:
        packages = tomllib.loads((ROOT / lock).read_text(encoding="utf-8"))["package"]
        own = [p["version"] for p in packages if p["name"] == name]
        if own != [version]:
            raise ValueError(f"Lock version mismatch: {lock}: {own}")
    runtime = (ROOT / "server/server.py").read_text(encoding="utf-8")
    if re.search(r'^VERSION = "([^"]+)"$', runtime, re.MULTILINE).group(1) != version:
        raise ValueError("Runtime version mismatch")
    if f"<!-- release-version --> `{version}`" not in (ROOT / "README.md").read_text(encoding="utf-8"):
        raise ValueError("README version mismatch")
    if not re.search(rf"^## v{re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.MULTILINE):
        raise ValueError("Missing dated changelog section")
    for args in [["diff", "--exit-code"], ["diff", "--cached", "--exit-code"]]:
        subprocess.run(["git", *args], cwd=ROOT, check=True)


def collect(version: str, commit: str) -> None:
    xpi = ROOT / f"plugin/build/{PRODUCT}.xpi"
    update_path = ROOT / "plugin/build/update.json"
    with zipfile.ZipFile(xpi) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.testzip() is None
        assert manifest["version"] == version
        assert manifest["name"] == PRODUCT
        assert manifest["applications"]["zotero"]["id"] == ADDON_ID
        assert manifest["applications"]["zotero"]["update_url"] == f"https://github.com/{REPO}/releases/latest/download/update.json"
    updates = read_json(update_path)["addons"]
    assert list(updates) == [ADDON_ID]
    assert len(updates[ADDON_ID]["updates"]) == 1
    update = updates[ADDON_ID]["updates"][0]
    assert update["version"] == version
    assert update["update_link"] == f"https://github.com/{REPO}/releases/download/v{version}/{PRODUCT}.xpi"
    assert update["update_hash"] == "sha512:" + hashlib.sha512(xpi.read_bytes()).hexdigest()

    windows = ROOT / f"dist/{PRODUCT}-windows-x64.zip"
    windows_update = ROOT / "dist/windows-update.json"
    data = windows.read_bytes()
    assert read_json(windows_update) == {
        "schemaVersion": 1,
        "version": version,
        "url": f"https://github.com/{REPO}/releases/download/v{version}/{windows.name}",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    with zipfile.ZipFile(windows) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [e.archive_name for e in package_entries(Path("unused"))]
        validate_release_pe(archive.read(f"{PRODUCT}.exe"))
        assert f'$PackageVersion = "{version}" # release-version' in archive.read("common.ps1").decode("utf-8-sig")
    source = ROOT / f"dist/{PRODUCT}-{version}-source.zip"
    # git archive records the source commit in the ZIP comment.
    with zipfile.ZipFile(source) as archive:
        assert archive.comment.decode("ascii") == commit
        assert archive.testzip() is None
        assert json.loads(archive.read(f"{PRODUCT}-{version}/plugin/package.json"))["version"] == version
    artifacts = [xpi, update_path, windows, windows_update, source,
                 ROOT / f"server/dist/zotero_pdf2zh_pro-{version}-py3-none-any.whl",
                 ROOT / f"server/dist/zotero_pdf2zh_pro-{version}.tar.gz"]
    target = ROOT / "dist/release"
    target.mkdir(parents=True, exist_ok=True)
    checksums = {"version": version, "commit": commit, "artifacts": {}}
    for artifact in artifacts:
        payload = artifact.read_bytes()
        checksums["artifacts"][artifact.name] = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        shutil.copy2(artifact, target / artifact.name)
    (target / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checksums, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("commit")
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    verify_source(args.version, args.commit)
    if args.collect:
        collect(args.version, args.commit)
    print(f"Verified v{args.version} at {args.commit}")
