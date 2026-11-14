from __future__ import annotations

import unittest
from types import SimpleNamespace

from babeldoc.format.pdf.document_il.midend.reference_filter import (
    find_reference_paragraph_ids,
)


def paragraph(text: str, label: str = "text", style=None):
    return SimpleNamespace(unicode=text, layout_label=label, pdf_style=style)


def document(*pages):
    return SimpleNamespace(
        page=[SimpleNamespace(pdf_paragraph=list(items)) for items in pages]
    )


class ReferenceFilterTests(unittest.TestCase):
    def test_explicit_labels_and_verified_trailing_sections_are_skipped(self) -> None:
        body = paragraph("Body")
        reference = paragraph("Smith 2020", "reference_content")
        skipped = find_reference_paragraph_ids(document([body, reference]))
        self.assertNotIn(id(body), skipped)
        self.assertIn(id(reference), skipped)

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

    def test_named_appendix_ends_even_mislabelled_references(self) -> None:
        for title in (
            "Appendix A. Proofs", "Appendix A: Additional Results",
            "Appendices", "附录 A 实验细节", "Supplementary Materials",
        ):
            with self.subTest(title=title):
                heading = paragraph("Bibliography", "title")
                refs = [paragraph("[1] Smith 2020"), paragraph("[2] Doe 2021")]
                appendix = paragraph(title, "reference_content")
                body = paragraph("We compare our results with [1] (2020).", "reference_hybrid")
                doc = document([paragraph("Body")], [heading, *refs, appendix], [body])
                self.assertEqual(
                    find_reference_paragraph_ids(doc),
                    {id(p) for p in [heading, *refs]},
                )

    def test_lettered_heading_above_figure_on_last_reference_page(self) -> None:
        # In the two-column sample, the layout model calls appendix A a figure caption.
        heading_style = SimpleNamespace(font_id="F155", font_size=11.96)
        body_style = SimpleNamespace(font_id="F164", font_size=9.96)
        heading = paragraph("References", "title", heading_style)
        refs = [
            paragraph("[1] Smith 2020", style=body_style),
            paragraph("A Survey of Appendix Detection", style=body_style),
            paragraph("Appendix A describes the protocol used in this work.", style=body_style),
            paragraph("[2] Doe 2021", style=body_style),
        ]
        appendix = paragraph("A Evolution of CoT ReasoningResearch", "figure_caption", heading_style)
        caption = paragraph("Figure 2: Evolution of Topics", "figure_caption", body_style)
        body = paragraph("Results of the experiment", "reference")
        doc = document(
            [paragraph("Introduction")], [paragraph("Results")], [heading, *refs[:2]],
            [*refs[2:], appendix, caption], [body],
        )
        self.assertEqual(find_reference_paragraph_ids(doc), {id(p) for p in [heading, *refs]})

    def test_long_appendix_after_an_early_layout_confirmed_bibliography(self) -> None:
        heading = paragraph("References", "title")
        first = paragraph("[1] Smith 2020", "reference_content")
        second = paragraph("[2] Doe 2021")
        doc = document(
            [heading, first, second], [paragraph("Appendix")],
            [paragraph("Results", "reference_content")], [paragraph("Conclusion")],
        )
        self.assertEqual(find_reference_paragraph_ids(doc), {id(heading), id(first), id(second)})

    def test_layout_labels_without_heading_and_a_later_bibliography(self) -> None:
        first = paragraph("Smith 2020", "reference_content")
        appendix = paragraph("A. Experimental Results", "title")
        body = paragraph("Experimental setup", "reference_content")
        heading = paragraph("References", "title")
        refs = [paragraph("[1] Doe 2021"), paragraph("[2] Lee 2022")]
        self.assertEqual(
            find_reference_paragraph_ids(document([first, appendix, body], [heading, *refs])),
            {id(p) for p in [first, heading, *refs]},
        )

    def test_unverified_headings_are_not_used(self) -> None:
        cases = (
            document(
                [paragraph("References", "title"), paragraph("[1] Smith 2020"), paragraph("[2] Doe 2021")],
                [paragraph("Body")],
                [paragraph("End")],
            ),
            document(
                [paragraph("Body")],
                [paragraph("Bibliography", "title"), paragraph("ordinary text")],
            ),
        )
        for candidate in cases:
            with self.subTest():
                skipped = find_reference_paragraph_ids(candidate)
                headings = [
                    item
                    for page in candidate.page
                    for item in page.pdf_paragraph
                    if item.layout_label == "title"
                ]
                self.assertTrue(all(id(heading) not in skipped for heading in headings))


if __name__ == "__main__":
    unittest.main()
