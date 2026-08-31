#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "zotero-pdf2zh-pro"
ADDON_ID = "zotero-pdf2zh-pro@study-233"
PACKAGE_FILES = [
    f"{PRODUCT}.xpi",
    f"{PRODUCT}-windows-x64.zip",
    "安装说明.txt",
]


def project_version() -> str:
    pyproject = tomllib.loads(
        (REPO_ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["project"]["version"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_xpi(path: Path, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    zotero = manifest["applications"]["zotero"]
    if manifest["name"] != PRODUCT or manifest["version"] != version:
        raise RuntimeError("XPI name or version does not match the release")
    if zotero["id"] != ADDON_ID:
        raise RuntimeError("XPI add-on ID does not match the release")
    if "update_url" in zotero:
        raise RuntimeError("Private-repository XPI must not contain update_url")


def zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def build_package(version: str, output_dir: Path, source_ref: str) -> Path:
    if version != project_version():
        raise RuntimeError(
            f"Version mismatch: requested={version}, project={project_version()}"
        )

    xpi = REPO_ROOT / "plugin" / "build" / f"{PRODUCT}.xpi"
    windows = REPO_ROOT / "dist" / f"{PRODUCT}-windows-x64.zip"
    instructions = REPO_ROOT / "scripts" / "friends" / "安装说明.txt"
    missing = [path for path in (xpi, windows, instructions) if not path.is_file()]
    if missing:
        raise RuntimeError(f"Friend package inputs are missing: {missing}")
    validate_xpi(xpi, version)

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{PRODUCT}-{version}-friends.zip"
    source_name = f"{PRODUCT}-{version}-source.zip"

    with tempfile.TemporaryDirectory(prefix=f"{PRODUCT}-source-") as temp_dir:
        source = Path(temp_dir) / source_name
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--prefix={PRODUCT}-{version}/",
                f"--output={source}",
                source_ref,
            ],
            cwd=REPO_ROOT,
            check=True,
        )
        checksums = "".join(
            [
                f"{sha256(xpi)}  {xpi.name}\n",
                f"{sha256(windows)}  {windows.name}\n",
                f"{sha256(source)}  {source.name}\n",
            ]
        ).encode("utf-8")

        entries = [
            (xpi.name, xpi.read_bytes()),
            (windows.name, windows.read_bytes()),
            (instructions.name, instructions.read_bytes()),
            (source.name, source.read_bytes()),
            ("SHA256SUMS.txt", checksums),
        ]
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, content in entries:
                archive.writestr(zip_info(name), content)

    with zipfile.ZipFile(output) as archive:
        expected = PACKAGE_FILES + [source_name, "SHA256SUMS.txt"]
        if archive.namelist() != expected:
            raise RuntimeError("Friend package content validation failed")
        if archive.testzip() is not None:
            raise RuntimeError("Friend package CRC validation failed")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the direct-share friend ZIP")
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--source-ref", default="HEAD")
    args = parser.parse_args()
    output = build_package(args.version, args.output_dir.resolve(), args.source_ref)
    print(output)


if __name__ == "__main__":
    main()
