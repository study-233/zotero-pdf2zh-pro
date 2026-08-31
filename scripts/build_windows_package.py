#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DIR = REPO_ROOT / "scripts" / "windows"
PACKAGE_FILES = [
    "使用说明.txt",
    "安装.cmd",
    "启动服务.cmd",
    "停止服务.cmd",
    "查看日志.cmd",
    "卸载.cmd",
    "common.ps1",
    "install.ps1",
    "start-server.ps1",
    "stop-server.ps1",
    "view-log.ps1",
    "uninstall.ps1",
]


def project_version() -> str:
    pyproject = tomllib.loads(
        (REPO_ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["project"]["version"]


def script_version() -> str:
    text = (WINDOWS_DIR / "common.ps1").read_text(encoding="utf-8")
    match = re.search(
        r'^\$PackageVersion = "([^"]+)" # release-version$',
        text,
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError("common.ps1 is missing the release-version marker")
    return match.group(1)


def build_package(version: str, output_dir: Path) -> Path:
    if version != project_version() or version != script_version():
        raise RuntimeError(
            "Windows package version mismatch: "
            f"requested={version}, project={project_version()}, script={script_version()}"
        )

    missing = [name for name in PACKAGE_FILES if not (WINDOWS_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"Windows package files are missing: {missing}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "zotero-pdf2zh-pro-windows-x64.zip"
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in PACKAGE_FILES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (WINDOWS_DIR / name).read_bytes())

    with zipfile.ZipFile(output_path) as archive:
        if archive.namelist() != PACKAGE_FILES:
            raise RuntimeError("Windows package content validation failed")
        if archive.testzip() is not None:
            raise RuntimeError("Windows package CRC validation failed")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Windows helper ZIP")
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    args = parser.parse_args()
    output = build_package(args.version, args.output_dir.resolve())
    print(output)


if __name__ == "__main__":
    main()
