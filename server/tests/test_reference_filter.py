from __future__ import annotations

import unittest
from types import SimpleNamespace

from babeldoc.format.pdf.document_il.midend.reference_filter import (
    find_reference_paragraph_ids,
)


def paragraph(text: str, label: str = "text"):
    return SimpleNamespace(unicode=text, layout_label=label)


def document(*pages):
    return SimpleNamespace(
        page=[SimpleNamespace(pdf_paragraph=list(items)) for items in pages]
    )


class ReferenceFilterTests(unittest.TestCase):
    def test_layout_labels_are_skipped_anywhere(self) -> None:
        body = paragraph("Body")
        reference = paragraph("Smith 2020", "reference_content")
        skipped = find_reference_paragraph_ids(document([body, reference]))
        self.assertNotIn(id(body), skipped)
        self.assertIn(id(reference), skipped)

    def test_verified_heading_in_second_half_skips_until_appendix(self) -> None:
        body = paragraph("Body")
        heading = paragraph("References", "title")
        ref1 = paragraph("[1] Smith, J. 2020. A paper.")
        ref2 = paragraph("[2] Doe, J. https://doi.org/10.1/example")
        appendix = paragraph("Appendix A", "title")
        appendix_body = paragraph("Additional results")
        skipped = find_reference_paragraph_ids(
            document([body], [heading, ref1, ref2, appendix, appendix_body])
        )
        self.assertIn(id(heading), skipped)
        self.assertIn(id(ref1), skipped)
        self.assertIn(id(ref2), skipped)
        self.assertNotIn(id(appendix), skipped)
        self.assertNotIn(id(appendix_body), skipped)

    def test_heading_in_first_half_is_not_used_as_fallback(self) -> None:
        heading = paragraph("References", "title")
        ref1 = paragraph("[1] Smith 2020")
        ref2 = paragraph("[2] Doe 2021")
        skipped = find_reference_paragraph_ids(
            document([heading, ref1, ref2], [paragraph("Body")], [paragraph("End")])
        )
        self.assertNotIn(id(heading), skipped)

    def test_heading_without_two_reference_shapes_is_not_used(self) -> None:
        heading = paragraph("Bibliography", "title")
        skipped = find_reference_paragraph_ids(
            document([paragraph("Body")], [heading, paragraph("ordinary text")])
        )
        self.assertNotIn(id(heading), skipped)


if __name__ == "__main__":
    unittest.main()
