# Maintained core and bundled PDF engine

`zotero-pdf2zh-pro` maintains `pdf2zh_pro` directly. It derives from the customized
pdf2zh-next 2.8.2 source, with provider protocols, cache identities and downstream
behavior retained. It is no longer an installable `pdf2zh-next` snapshot or CLI.

| Origin | Base version | Upstream | Wheel SHA256 | License |
| --- | --- | --- | --- | --- |
| pdf2zh-next → pdf2zh_pro | 2.8.2 | https://github.com/PDFMathTranslate-next/PDFMathTranslate-next | `5416f8e65828783df9a2323893380145d30846ed7c201539f847307b8689b770` | AGPL-3.0 |
| BabelDOC | 0.6.4 | https://github.com/funstory-ai/BabelDOC | `e7dcdd5b8213f657af1df68e329f92d0534c9b94c53fcd82e9e04c52060cb7d0` | AGPL-3.0 |

Exact source moves, original hashes and downstream feature inventory are recorded
in `pdf2zh_pro/vendor-manifest.json`. License texts remain under `LICENSES/` and
are included in distribution metadata. The historical RapidOCR license is retained
for attribution; its unused vendored table OCR implementation and models are removed.
OCR detection and BabelDOC's OCR text-layer workaround remain in the PDF pipeline.

BabelDOC uses the 0.6.4 native parser and active IL creator, with local font caching,
Type3 geometry, cancellation, translation recovery, glossary precedence, placeholder
repair, quality review and metrics preserved. The native parser and legacy helpers
share one verified, bounded CMap resource loader; external CMap overrides remain
unsupported. The old PyMuPDF 1.25 compatibility patch is removed.

The external `pymupdf` 2.0 dependency includes Layout/4LLM. Its public Document APIs
provide on-demand Markdown, JSON and TXT export in a separate worker. BabelDOC still
provides translated PDF typesetting; its provider implementations, CLI and duplicate
translation cache are removed. PyMuPDF and its bundled components retain their own
upstream licenses and distribution metadata.

`scripts/vendor_pdf2zh_runtime.py` downloads hash-pinned upstream source into a new
managed staging directory **only**. It refuses maintained source directories and
existing targets. Future upgrades require an explicit diff and migration of the
manifest's downstream features; the script never overwrites `server/pdf2zh_pro`
or `server/babeldoc`.
