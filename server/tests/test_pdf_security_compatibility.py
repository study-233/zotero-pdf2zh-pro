"""PDF-standard crypto is used to read existing files, not to store app passwords."""
from io import BytesIO
import unittest

from pypdf import PdfWriter
from babeldoc.pdfminer.converter import XMLConverter
from babeldoc.pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
from babeldoc.pdfminer.pdfparser import PDFParser


class PdfSecurityCompatibilityTests(unittest.TestCase):
    def test_standard_security_revisions_accept_user_owner_and_empty_passwords(self):
        for algorithm in ["RC4-40", "RC4-128", "AES-128", "AES-256-R5", "AES-256"]:
            for user_password in ["open-pdf", ""]:
                with self.subTest(algorithm=algorithm, user_password=user_password):
                    writer = PdfWriter()
                    writer.add_blank_page(width=72, height=72)
                    writer.add_metadata({"/Title": "PDF security compatibility"})
                    writer.encrypt(user_password, owner_password="owner-pdf", algorithm=algorithm)
                    stream = BytesIO()
                    writer.write(stream)
                    raw = stream.getvalue()
                    expected = b"PDF security compatibility"
                    # This bundled parser returns raw AES plaintext, including PKCS#7.
                    if algorithm.startswith("AES"):
                        padding = 16 - len(expected) % 16
                        expected += bytes([padding]) * padding
                    for password in [user_password, "owner-pdf"]:
                        document = PDFDocument(PDFParser(BytesIO(raw)), password=password)
                        self.assertEqual(document.info[0]["Title"], expected)
                    with self.assertRaises(PDFPasswordIncorrect):
                        PDFDocument(PDFParser(BytesIO(raw)), password="wrong-password")

    def test_xml_control_filter_matches_exactly_the_intended_unicode_codepoints(self):
        actual = {code for code in range(0x110000) if XMLConverter.CONTROL.fullmatch(chr(code))}
        self.assertEqual(actual, set(range(9)) | {11, 12} | set(range(14, 32)))
        for text in ["\t\n\r", "中文", "abc<&>"]:
            self.assertEqual(XMLConverter.CONTROL.sub("", text), text)


if __name__ == "__main__":
    unittest.main()
