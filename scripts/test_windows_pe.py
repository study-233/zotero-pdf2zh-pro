from __future__ import annotations

import struct
import unittest

from windows_pe import PEError, imports, validate_release_pe


def fixture(normal=("kernel32.dll",), delayed=(), machine=0x8664) -> bytes:
    """Minimal PE32+ with independently addressable normal and delay descriptors."""
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<Q", data, optional + 24, 0x140000000)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<IIII", data, optional + 240 + 8, 0xE00, 0x1000, 0xE00, 0x200)
    name_offset = 0x900
    for index, start, width, names in [(1, 0x200, 20, normal), (13, 0x500, 32, delayed)]:
        if not names:
            continue
        struct.pack_into("<II", data, optional + 112 + index * 8, start + 0xE00, (len(names) + 1) * width)
        for n, name in enumerate(names):
            descriptor = start + n * width
            if index == 13:
                struct.pack_into("<I", data, descriptor, 1)
            struct.pack_into("<I", data, descriptor + (12 if index == 1 else 4), name_offset + 0xE00)
            payload = name.encode("ascii") + b"\0"
            data[name_offset:name_offset + len(payload)] = payload
            name_offset += len(payload)
    return bytes(data)


class PEDependencyTests(unittest.TestCase):
    def test_accepts_os_and_api_set_dependencies(self):
        data = fixture(("KERNEL32.dll", "advapi32.dll"), ("api-ms-win-core-synch-l1-2-0.dll",))
        self.assertEqual(len(validate_release_pe(data)), 3)

    def test_rejects_loader_and_vc_runtime_in_either_directory(self):
        for name in ["WebView2Loader.dll", "VCRUNTIME140.dll", "MSVCP140.dll", "libgcc_s_seh-1.dll", "unknown.dll"]:
            for data in [fixture((name,)), fixture(delayed=(name,))]:
                with self.subTest(name=name), self.assertRaisesRegex(PEError, "Unshipped DLL"):
                    validate_release_pe(data)

    def test_rejects_wrong_architecture_and_truncated_files(self):
        with self.assertRaises(PEError):
            validate_release_pe(fixture(machine=0x14C))
        for size in [0, 64, 255, 1024]:
            with self.subTest(size=size), self.assertRaises(PEError):
                validate_release_pe(fixture()[:size])

    def test_rejects_corrupt_name_rva(self):
        data = bytearray(fixture())
        struct.pack_into("<I", data, 0x20C, 0xDEADBEEF)
        with self.assertRaises(PEError):
            imports(bytes(data))

    def test_requires_descriptor_terminator(self):
        data = bytearray(fixture())
        struct.pack_into("<I", data, 0x98 + 112 + 8 + 4, 20)
        with self.assertRaisesRegex(PEError, "Unterminated import"):
            imports(bytes(data))

    def test_rejects_missing_imports_and_path_names(self):
        for data in [fixture(normal=()), fixture(("../kernel32.dll",))]:
            with self.assertRaises(PEError):
                imports(data)


if __name__ == "__main__":
    unittest.main()
