#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REPO = "study-233/zotero-pdf2zh-pro"
ASSET_NAME = "zotero-pdf2zh-pro-windows-x64.zip"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def build_manifest(version: str, package: Path) -> dict[str, object]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Windows update version must be stable semver: {version}")
    if not package.is_file():
        raise FileNotFoundError(f"Windows update package is missing: {package}")
    payload = package.read_bytes()
    if not payload:
        raise ValueError("Windows update package must not be empty")
    return {
        "schemaVersion": 1,
        "version": version,
        "url": f"https://github.com/{REPO}/releases/download/v{version}/{ASSET_NAME}",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Windows update manifest")
    parser.add_argument("--version", required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("dist/windows-update.json")
    )
    args = parser.parse_args()

    manifest = build_manifest(args.version, args.package.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
