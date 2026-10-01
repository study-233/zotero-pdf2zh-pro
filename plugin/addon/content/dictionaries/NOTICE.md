# ECDICT offline dictionary

This plugin contains a 50,000-entry subset of [ECDICT](https://github.com/skywind3000/ECDICT), under the MIT license reproduced in `LICENSE-ECDICT.txt`.

The upstream `ecdict.csv` was retrieved on 2026-09-30. `source.json` records its SHA-256 and the selection rule. Rebuild from that CSV with `python3 scripts/build_selection_dictionary.py /path/to/ecdict.csv` from the repository root. Only word, phonetic and Chinese translation fields are included. Translation fields retain upstream part-of-speech labels where present. This is a general dictionary; it does not claim context-specific word alignment with a paper's translation.

The selection popup interaction was inspired by [Zotero Context Translate](https://github.com/maverickzyc/zotero-context-translate). Its source files were not copied; the popup is implemented for this plugin's translation-memory workflow.

## ODH local import

The importer reads a user-selected Online Dictionary Helper `bg/data/collins.json` and converts its fields locally. ODH's extension code is MIT-licensed (copyright 2018 Zhenyu Huang); this independently written importer does not embed its scripts or dictionary corpus. No rights to redistribute Collins dictionary content are asserted. Imported data remains in the user's Zotero data directory and is not included in the plugin source branch or XPI. Separately versioned dictionary download assets are hosted in the project’s `glossary-data/dictionaries` directory; downloads are explicitly initiated by the user and verified before installation.

Optional entries explicitly marked `_pdf2zhSupplement.kind = "ai"` are displayed as AI-generated supplements, separately from original Collins definitions. They do not represent publisher-verified dictionary entries.
