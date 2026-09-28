# Bundled runtime snapshots

`zotero-pdf2zh-pro` bundles fixed, source-readable runtime snapshots so its
PyPI package does not resolve a newer `pdf2zh-next` release or install the
upstream Gradio/FastAPI web UI.

| Project | Version | Upstream | Wheel SHA256 | License |
| --- | --- | --- | --- | --- |
| pdf2zh-next | 2.8.2 | https://github.com/PDFMathTranslate-next/PDFMathTranslate-next | `5416f8e65828783df9a2323893380145d30846ed7c201539f847307b8689b770` | AGPL-3.0 |
| BabelDOC | 0.5.24 | https://github.com/funstory-ai/BabelDOC | `8810b9d8faecbe9b3f3e41f7af1f5d83cbee060ac16c9f17aa2a81abb149c6f2` | AGPL-3.0 |
| rapidocr-onnxruntime | 1.4.4 | https://github.com/RapidAI/RapidOCR | `971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf` | Apache-2.0 |

The `pdf2zh-next` Gradio GUI modules and BabelDOC development tools are not
included. Translation providers, PDF processing, OCR models, table handling,
and glossary extraction remain included. Exact license texts are under
`LICENSES/` and are copied into wheel metadata.

These are **base snapshot versions**, not unmodified upstream distributions.
Local translation recovery, provider protocols, metrics and compatibility fixes
must be retained when replacing a snapshot. The extraction script removes the
existing package directories; run it only in a disposable checkout when preparing
an upgrade, then review and reapply the downstream changes:

```bash
uv run python scripts/vendor_pdf2zh_runtime.py
```

## Downstream compatibility backports (2026-09-29)

- PyMuPDF 1.25.1/1.25.2: format warning arguments for unsupported font subtypes
  and invalid annotation items before passing them to `message()`. This does not
  add support for unsupported fonts or suppress PDF processing exceptions.
- BabelDOC 0.6.4 (`17480db9df92`): backport the CMap manifest and verified loader
  to the bundled legacy `pdfminer` layout (GHSA-m8gf-v64p-gfmg). All 148 CMaps
  match the upstream manifest. Reads are bounded and external `CMAP_PATH`
  overrides are intentionally unsupported.
- Backport LZW invalid-code checks and ImageWriter filename containment from the
  same upstream comparison; image names also strip Windows separators on POSIX.
- Restore the missing `ClaudeCodeSettings` public import in `pdf2zh_next`.

The PyMuPDF compatibility hook is loaded by `babeldoc` in both the service and
fresh PDF worker processes. These backports do **not** include the BabelDOC 0.6
parser rewrite. See `docs/runtime-compatibility-audit-2026-09-29.md` in the source
repository for the remaining migration work and verification limits.
