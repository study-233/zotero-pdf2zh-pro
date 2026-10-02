"""Regressions reproduced while auditing the bundled 0.5.24 PDF runtime."""

import gzip
import hashlib
import io
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import babeldoc
import pymupdf

from babeldoc.pdfminer import cmap_secure_loader
from babeldoc.pdfminer._cmap_manifest_data import CMAP_MANIFEST
from babeldoc.pdfminer.cmapdb import CMapDB
from babeldoc.pdfminer.image import ImageWriter
from babeldoc.pdfminer.lzw import lzwdecode
from babeldoc.pymupdf_compat import install_compatibility_fixes


def unusual_font_pdf(subtype="/UnsupportedTestFont"):
    doc = pymupdf.open()
    page = doc.new_page()
    fontfile = doc.get_new_xref()
    doc.update_object(fontfile, f"<< /Subtype {subtype} >>")
    doc.update_stream(fontfile, b"test")
    descriptor = doc.get_new_xref()
    doc.update_object(descriptor, f"<< /Type /FontDescriptor /FontName /Test /FontFile3 {fontfile} 0 R >>")
    font = doc.get_new_xref()
    doc.update_object(font, f"<< /Type /Font /Subtype /Type1 /BaseFont /Test /FontDescriptor {descriptor} 0 R >>")
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /F1 {font} 0 R >> >>")
    return doc, font


class PyMuPDFCompatibilityTests(unittest.TestCase):
    def test_unknown_font_warning_does_not_abort_font_enumeration(self):
        for subtype in ("/UnsupportedTestFont", "null"):
            with self.subTest(subtype=subtype):
                doc, font = unusual_font_pdf(subtype)
                output = io.StringIO()
                with doc, patch.object(pymupdf, "_g_out_message", output):
                    fonts = doc[0].get_fonts()
                    self.assertEqual(fonts[0][0], font)
                    self.assertEqual(fonts[0][1], "n/a")
                    self.assertIn("unhandled font type", output.getvalue())

    def test_bad_annotation_warning_in_python_fallback(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            output = io.StringIO()
            with patch.object(pymupdf, "g_use_extra", False), patch.object(pymupdf, "_g_out_message", output):
                page._addAnnot_FromString((None,))
            self.assertIn("skipping bad link / annot item 0.", output.getvalue())

    def test_normal_pdf_roundtrip_preserves_text_and_fonts(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "PDF compatibility check")
            with pymupdf.open(stream=doc.tobytes(), filetype="pdf") as result:
                self.assertIn("PDF compatibility check", result[0].get_text())
                self.assertTrue(result[0].get_fonts())

    def test_install_is_idempotent_and_version_scoped(self):
        original = pymupdf.message
        install_compatibility_fixes()
        self.assertIs(pymupdf.message, original)
        sentinel = lambda text="": None
        with patch.object(pymupdf, "VersionBind", "9.0.0"), patch.object(pymupdf, "message", sentinel):
            install_compatibility_fixes()
            self.assertIs(pymupdf.message, sentinel)

    def test_fresh_worker_import_installs_fix(self):
        result = subprocess.run(
            [sys.executable, "-c", "import babeldoc, pymupdf; pymupdf.message('worker %s', 'ready')"],
            cwd=Path(babeldoc.__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class CMapCompatibilityTests(unittest.TestCase):
    def test_every_bundled_cmap_matches_upstream_manifest(self):
        root = cmap_secure_loader.BUNDLED_CMAP_DIR
        self.assertEqual({p.name for p in root.glob("*.pickle.gz")}, set(CMAP_MANIFEST))
        for filename, (sha, size) in CMAP_MANIFEST.items():
            with self.subTest(filename=filename):
                raw = (root / filename).read_bytes()
                self.assertEqual((hashlib.sha256(raw).hexdigest(), len(raw)), (sha, size))
                data = cmap_secure_loader.load_verified_cmap_data(filename.removesuffix(".pickle.gz"))
                self.assertIsInstance(data, dict)
        self.assertTrue(CMapDB.get_cmap("UniGB-UCS2-H").code2cid)
        self.assertTrue(CMapDB.get_unicode_map("Adobe-GB1").cid2unichr)

    def test_external_names_are_rejected_before_deserialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "outside"
            external.with_suffix(".pickle.gz").write_bytes(gzip.compress(pickle.dumps({"sentinel": True})))
            with patch.object(cmap_secure_loader.pickle, "loads") as loads:
                for name in (str(external), "../outside", "..\\outside", "C:\\outside"):
                    with self.subTest(name=name), self.assertRaises(CMapDB.CMapNotFound):
                        CMapDB._load_data(name)
                loads.assert_not_called()

    def test_legacy_environment_cannot_override_packaged_cmaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "UniGB-UCS2-H.pickle.gz").write_bytes(gzip.compress(pickle.dumps({"sentinel": True})))
            with patch.dict(os.environ, {"CMAP_PATH": tmp}):
                self.assertFalse(hasattr(CMapDB._load_data("UniGB-UCS2-H"), "sentinel"))

    def test_missing_tampered_and_symlinked_maps_fail_closed(self):
        filename = "UniGB-UCS2-H.pickle.gz"
        raw = (cmap_secure_loader.BUNDLED_CMAP_DIR / filename).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / filename
            with patch.object(cmap_secure_loader, "BUNDLED_CMAP_DIR", root), patch.object(cmap_secure_loader.pickle, "loads") as loads:
                for content in (None, b"short", bytes([raw[0] ^ 1]) + raw[1:]):
                    if content is not None:
                        target.write_bytes(content)
                    with self.assertRaises(CMapDB.CMapNotFound):
                        CMapDB._load_data("UniGB-UCS2-H")
                target.unlink()
                try:
                    target.symlink_to(Path(__file__).resolve())
                except OSError as error:
                    if getattr(error, "winerror", None) != 1314:
                        raise
                    # Ordinary Windows users need not enable Developer Mode to
                    # verify refusal before deserialization. Real junctions are
                    # separately exercised by the development-manager tests.
                    with patch.object(Path, "resolve", return_value=Path(__file__).resolve()):
                        with self.assertRaises(CMapDB.CMapNotFound):
                            CMapDB._load_data("UniGB-UCS2-H")
                else:
                    with self.assertRaises(CMapDB.CMapNotFound):
                        CMapDB._load_data("UniGB-UCS2-H")
                loads.assert_not_called()


class LegacyParserTests(unittest.TestCase):
    @staticmethod
    def lzw_stream(codes):
        bits = "".join(f"{code:09b}" for code in codes)
        bits += "0" * (-len(bits) % 8)
        return int(bits, 2).to_bytes(len(bits) // 8, "big")

    def test_malformed_lzw_stops_without_index_error(self):
        for codes in ([65], [256, 300]):
            self.assertEqual(lzwdecode(self.lzw_stream(codes)), b"")

    def test_valid_lzw_is_preserved(self):
        self.assertEqual(lzwdecode(self.lzw_stream([256, 65, 66, 258, 257])), b"ABAB")

    def test_image_names_stay_inside_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = ImageWriter(tmp)
            for name in ("../escape", "/tmp/escape", "..\\escape", "C:\\temp\\escape", "", ".."):
                with self.subTest(name=name):
                    _, output = writer._create_unique_image_name(SimpleNamespace(name=name), ".jpg")
                    self.assertEqual(Path(output).resolve().parent, Path(tmp).resolve())
            (Path(tmp) / "image.jpg").write_bytes(b"existing")
            name, _ = writer._create_unique_image_name(SimpleNamespace(name="image"), ".jpg")
            self.assertEqual(name, "image.0.jpg")

    def test_public_engine_exports_are_importable(self):
        namespace = {}
        exec("from pdf2zh_next import *", namespace)
        self.assertIn("ClaudeCodeSettings", namespace)


if __name__ == "__main__":
    unittest.main()
