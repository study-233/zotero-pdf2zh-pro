#!/usr/bin/env python3
"""Combine the native bootstrapper with its checked, self-contained installation payload."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path

from build_windows_package import DEFAULT_GUI_BINARY, package_entries, project_version, validate_gui_binary, validate_versions
from windows_pe import validate_release_pe

MAGIC = b"PDF2ZH_SETUP_V1\0"
FOOTER = struct.Struct("<Q32s16s")
PRODUCT = "zotero-pdf2zh-pro"
METADATA_NAMES = ["windows-dependencies.json", "windows-requirements.txt"]
MAX_INSTALLER_BYTES = 40_000_000


def verify_installer(path: Path, version: str) -> dict:
    data = path.read_bytes()
    if len(data) > MAX_INSTALLER_BYTES:
        raise ValueError(f"Installer exceeds the 40 MB budget: {len(data)} bytes")
    validate_release_pe(data)
    if len(data) < FOOTER.size:
        raise ValueError("Installer footer is missing")
    size, digest, magic = FOOTER.unpack(data[-FOOTER.size:])
    if magic != MAGIC or size > 256 * 1024 * 1024 or size > len(data) - FOOTER.size:
        raise ValueError("Installer payload length or signature is invalid")
    payload = data[-FOOTER.size - size:-FOOTER.size]
    if hashlib.sha256(payload).digest() != digest:
        raise ValueError("Installer payload checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Installer ZIP checksum mismatch")
        dependency = json.loads(archive.read("windows-dependencies.json"))
        if dependency["version"] != version:
            raise ValueError("Installer dependency version mismatch")
        wheel = dependency["serverWheel"]
        expected = [entry.archive_name for entry in package_entries(Path("unused"))] + METADATA_NAMES + [wheel["name"]]
        if "setup-profile.json" in archive.namelist():
            if json.loads(archive.read("setup-profile.json")) != {"candidate": True}:
                raise ValueError("Invalid candidate profile")
            expected += ["setup-profile.json"]
        if archive.namelist() != expected:
            raise ValueError("Installer payload has unexpected or missing files")
        for entry in archive.infolist():
            if "/" in entry.filename or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError("Installer payload contains a path")
        if hashlib.sha256(archive.read(wheel["name"])).hexdigest() != wheel["sha256"]:
            raise ValueError("Embedded service wheel checksum mismatch")
        if len(archive.read(wheel["name"])) != wheel["size"]:
            raise ValueError("Embedded service wheel size mismatch")
        if hashlib.sha256(archive.read("windows-requirements.txt")).hexdigest() != dependency["requirementsSha256"]:
            raise ValueError("Embedded dependency lock checksum mismatch")
        validate_release_pe(archive.read(f"{PRODUCT}.exe"))
        if f'$PackageVersion = "{version}" # release-version' not in archive.read("common.ps1").decode("utf-8-sig"):
            raise ValueError("Embedded install script version mismatch")
    return {"version": version, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "payloadSize": size, "bootstrapSize": len(data) - size - FOOTER.size}


def build_installer(version: str, package: Path, wheel_path: Path, bootstrap: Path, output: Path, candidate: bool = False) -> Path:
    validate_versions(version)
    validate_gui_binary(bootstrap)
    with zipfile.ZipFile(package) as archive:
        expected = [e.archive_name for e in package_entries(Path("unused"))] + METADATA_NAMES
        if archive.namelist() != expected or archive.testzip() is not None:
            raise ValueError("The control ZIP is incomplete or unexpected")
        dependency = json.loads(archive.read("windows-dependencies.json"))
        wheel = dependency["serverWheel"]
        wheel_bytes = wheel_path.read_bytes()
        if wheel_path.name != wheel["name"] or hashlib.sha256(wheel_bytes).hexdigest() != wheel["sha256"] or len(wheel_bytes) != wheel["size"]:
            raise ValueError("The supplied wheel does not match the locked release")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as payload:
            files = [(name, archive.read(name)) for name in expected] + [(wheel["name"], wheel_bytes)]
            if candidate:
                files += [("setup-profile.json", b'{"candidate":true}\n')]
            for name, contents in files:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                payload.writestr(info, contents)
    payload_bytes = buffer.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bootstrap.read_bytes() + payload_bytes + FOOTER.pack(len(payload_bytes), hashlib.sha256(payload_bytes).digest(), MAGIC))
    report = verify_installer(output, version)
    output.with_suffix(".exe.sha256").write_text(f"{report['sha256']}  {output.name}\n", encoding="ascii")
    print(json.dumps(report, indent=2))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--package", type=Path, default=Path(f"dist/{PRODUCT}-windows-x64.zip"))
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--bootstrap-binary", type=Path, default=DEFAULT_GUI_BINARY.with_name(f"{PRODUCT}-setup.exe"))
    parser.add_argument("--output", type=Path, default=Path(f"dist/{PRODUCT}-windows-x64-setup.exe"))
    parser.add_argument("--candidate", action="store_true", help="Embed isolated candidate install defaults")
    args = parser.parse_args()
    wheel = args.wheel or Path(f"server/dist/zotero_pdf2zh_pro-{args.version}-py3-none-any.whl")
    build_installer(args.version, args.package, wheel, args.bootstrap_binary, args.output, args.candidate)


if __name__ == "__main__":
    main()
