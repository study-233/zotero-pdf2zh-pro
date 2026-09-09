"""Fail-closed dependency validation for the Windows x64 release (no external tools)."""
from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path

# Windows 10/11 OS components only. VC redistributables and WebView2Loader are
# intentionally absent: the control center must statically link these runtimes.
SYSTEM_DLLS = frozenset("""
advapi32.dll avrt.dll bcrypt.dll bcryptprimitives.dll cabinet.dll cfgmgr32.dll
comctl32.dll comdlg32.dll credui.dll crypt32.dll cryptbase.dll cryptnet.dll
cryptsp.dll d2d1.dll d3d11.dll dcomp.dll dbghelp.dll dnsapi.dll dwmapi.dll
dwrite.dll dxgi.dll gdi32.dll gdiplus.dll hid.dll imm32.dll iphlpapi.dll
kernel32.dll kernelbase.dll ktmw32.dll mpr.dll msimg32.dll msvcrt.dll
ncrypt.dll netapi32.dll normaliz.dll ntdll.dll ole32.dll oleacc.dll oleaut32.dll
powrprof.dll profapi.dll propsys.dll psapi.dll rpcrt4.dll secur32.dll
setupapi.dll shcore.dll shell32.dll shlwapi.dll sspicli.dll ucrtbase.dll
urlmon.dll user32.dll userenv.dll usp10.dll uxtheme.dll version.dll
winhttp.dll wininet.dll winmm.dll winspool.drv wintrust.dll wldap32.dll
ws2_32.dll wtsapi32.dll
""".split())


class PEError(ValueError):
    pass


def imports(data: bytes) -> set[str]:
    def read(fmt: str, offset: int):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise PEError("Truncated PE structure")
        return struct.unpack_from(fmt, data, offset)

    if data[:2] != b"MZ":
        raise PEError("Missing DOS header")
    pe, = read("<I", 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise PEError("Missing PE signature")
    machine, sections = read("<HH", pe + 4)
    optional_size, = read("<H", pe + 20)
    optional = pe + 24
    magic, = read("<H", optional)
    if machine != 0x8664 or magic != 0x20B:
        raise PEError("Release must be x86_64 PE32+")
    if optional_size < 112 or optional + optional_size > len(data):
        raise PEError("Invalid optional header")
    image_base, = read("<Q", optional + 24)
    header_size, = read("<I", optional + 60)
    count, = read("<I", optional + 108)
    if count < 14 or 112 + count * 8 > optional_size:
        raise PEError("Incomplete data directories")
    section_table = optional + optional_size
    ranges = []
    for index in range(sections):
        size, address, raw_size, raw = read("<IIII", section_table + index * 40 + 8)
        if raw + raw_size > len(data):
            raise PEError("Truncated section")
        ranges.append((address, raw_size, raw))

    def offset(rva: int, length: int = 1) -> int:
        if 0 < rva < header_size and rva + length <= min(header_size, len(data)):
            return rva
        for address, size, raw in ranges:
            if address <= rva and rva + length <= address + size:
                return raw + rva - address
        raise PEError(f"Invalid RVA: {rva:#x}")

    def dll_name(rva: int) -> str:
        name = bytearray()
        for i in range(260):
            char = data[offset(rva + i)]
            if char == 0:
                break
            name.append(char)
        else:
            raise PEError("Unterminated DLL name")
        try:
            value = name.decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise PEError("Non-ASCII DLL name") from exc
        if not re.fullmatch(r"[a-z0-9_.-]+\.(dll|drv)", value):
            raise PEError(f"Invalid DLL name: {value!r}")
        return value

    result = set()
    for directory, width in [(1, 20), (13, 32)]:
        rva, size = read("<II", optional + 112 + directory * 8)
        if not rva and not size:
            continue
        if not rva or size < width:
            raise PEError("Invalid import directory")
        for entry in range(0, size - width + 1, width):
            values = read("<" + "I" * (width // 4), offset(rva + entry, width))
            if not any(values):
                break
            name_rva = values[3] if directory == 1 else values[1]
            if directory == 13:
                if values[0] not in (0, 1):
                    raise PEError("Unknown delay import addressing")
                if not values[0]:
                    name_rva -= image_base
            result.add(dll_name(name_rva))
        else:
            raise PEError("Unterminated import directory")
    if not result:
        raise PEError("Executable has no verifiable imports")
    return result


def validate_release_pe(data: bytes) -> set[str]:
    dependencies = imports(data)
    unexpected = sorted(
        name for name in dependencies
        if name not in SYSTEM_DLLS
        and not re.fullmatch(r"(?:api|ext)-ms-win-[a-z0-9-]+\.dll", name)
    )
    if unexpected:
        raise PEError("Unshipped DLL dependency (static linking required): " + ", ".join(unexpected))
    return dependencies


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    print("Verified Windows x64 dependencies:", ", ".join(sorted(validate_release_pe(args.executable.read_bytes()))))
