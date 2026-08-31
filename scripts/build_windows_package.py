#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DIR = REPO_ROOT / "scripts" / "windows"
TAURI_DIR = REPO_ROOT / "windows-app" / "src-tauri"
DEFAULT_GUI_BINARY = TAURI_DIR / "target" / "release" / "zotero-pdf2zh-pro.exe"


@dataclass(frozen=True)
class PackageEntry:
    archive_name: str
    source: Path
    executable: bool = False


SCRIPT_NAMES = [
    "README.txt",
    "install.cmd",
    "start-server.cmd",
    "stop-server.cmd",
    "view-log.cmd",
    "uninstall.cmd",
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


def gui_versions() -> dict[str, str]:
    cargo = tomllib.loads((TAURI_DIR / "Cargo.toml").read_text(encoding="utf-8"))
    tauri = json.loads((TAURI_DIR / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads(
        (REPO_ROOT / "windows-app" / "package.json").read_text(encoding="utf-8")
    )
    plugin = json.loads(
        (REPO_ROOT / "plugin" / "package.json").read_text(encoding="utf-8")
    )
    return {
        "tauri Cargo.toml": cargo["package"]["version"],
        "tauri.conf.json": tauri["version"],
        "windows-app package.json": package["version"],
        "plugin package.json": plugin["version"],
    }


def package_entries(gui_binary: Path) -> list[PackageEntry]:
    return [
        PackageEntry("zotero-pdf2zh-pro.exe", gui_binary, executable=True),
        *(PackageEntry(name, WINDOWS_DIR / name) for name in SCRIPT_NAMES),
        PackageEntry("LICENSE.txt", REPO_ROOT / "LICENSE"),
        PackageEntry(
            "THIRD_PARTY_NOTICES.md", REPO_ROOT / "server" / "THIRD_PARTY_NOTICES.md"
        ),
    ]


def validate_versions(version: str) -> None:
    versions = {
        "requested": version,
        "server pyproject.toml": project_version(),
        "common.ps1": script_version(),
        **gui_versions(),
    }
    mismatched = {name: value for name, value in versions.items() if value != version}
    if mismatched:
        details = ", ".join(f"{name}={value}" for name, value in versions.items())
        raise RuntimeError(f"Windows package version mismatch: {details}")


def validate_gui_binary(gui_binary: Path) -> None:
    if not gui_binary.is_file():
        raise RuntimeError(
            f"Tauri release executable is missing: {gui_binary}. "
            "Run `pnpm --dir windows-app tauri build --no-bundle` on Windows first."
        )
    if gui_binary.stat().st_size < 1024 or gui_binary.read_bytes()[:2] != b"MZ":
        raise RuntimeError(f"Tauri output is not a valid Windows PE executable: {gui_binary}")


def build_package(version: str, output_dir: Path, gui_binary: Path) -> Path:
    validate_versions(version)
    validate_gui_binary(gui_binary)
    entries = package_entries(gui_binary)

    non_ascii = [entry.archive_name for entry in entries if not entry.archive_name.isascii()]
    if non_ascii:
        raise RuntimeError(f"Windows package file names must be ASCII: {non_ascii}")
    missing = [entry.source for entry in entries if not entry.source.is_file()]
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
        for entry in entries:
            info = zipfile.ZipInfo(entry.archive_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if entry.executable else 0o644) << 16
            archive.writestr(info, entry.source.read_bytes())

    expected = [entry.archive_name for entry in entries]
    with zipfile.ZipFile(output_path) as archive:
        if archive.namelist() != expected:
            raise RuntimeError("Windows package content validation failed")
        if archive.testzip() is not None:
            raise RuntimeError("Windows package CRC validation failed")
        if archive.read("zotero-pdf2zh-pro.exe")[:2] != b"MZ":
            raise RuntimeError("Packaged GUI executable validation failed")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Windows GUI ZIP")
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--gui-binary", type=Path, default=DEFAULT_GUI_BINARY)
    args = parser.parse_args()
    output = build_package(
        args.version,
        args.output_dir.resolve(),
        args.gui_binary.resolve(),
    )
    print(output)


if __name__ == "__main__":
    main()
