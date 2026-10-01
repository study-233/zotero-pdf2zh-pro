# Optional offline dictionary resources

These assets are downloaded explicitly from the Zotero PDF2zh Pro preferences. They are separate from the plugin XPI, server, and full-document glossary packs.

`catalog.json` provides a versioned gzip JSON URL, compressed byte size and SHA-256. The plugin verifies the compressed payload, validates the dictionary structure and entry counts, and atomically installs it in the user's Zotero data directory. No user documents, settings or credentials are included in these assets or sent by dictionary downloads.

## Collins English–Chinese pack — 2026.10.01

- Source: local Online Dictionary Helper 1.0.1 dictionary JSON, with its original nonempty entries preserved.
- 37,379 source keys; 37,375 entries contain usable Chinese definitions.
- 945 originally empty entries have separately marked GPT-6-luna-generated supplements. They are not original Collins definitions or publisher-verified entries. Variant spellings, abbreviations and phrase fragments include explanatory notes.
- Four original entries without usable Chinese definitions are skipped during installation; the plugin's built-in ECDICT provides fallback lookup.
- Original Collins content remains attributed to its respective rights holders. ODH's source-code license is not asserted to grant ownership of this dictionary content. The AI additions do not change the provenance of the original entries.
- Package data uses `_pdf2zhSupplement.kind = "ai"` for generated entries. The plugin must preserve this distinction in displayed source labels.

This directory contains data only; no scripts from dictionary packages are executed. Updating these resources does not publish a plugin Release or change its version.
