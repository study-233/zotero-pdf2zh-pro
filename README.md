# Downloadable glossary data

This independent `glossary-data` branch contains optional English → Simplified Chinese translation dictionaries for zotero-pdf2zh-pro. Application releases contain catalog metadata only. Users explicitly download the small files in `packs/` to their own data directory.

These are curated selections and editorial adaptations of identified sources, not complete dictionaries or an official mainland standards database. They are not endorsed by the source institutions. The medicine selection covers basic medical vocabulary and some respiratory terms; it is not a clinical reference or a specialist-validated comprehensive respiratory terminology standard.

## Sources and attribution

`sources.json` identifies every source, exact acquisition URL, retrieval date, snapshot checksum, applicable license, and modification notice. Every pack embeds its source attribution and license links. The Python-derived pack additionally embeds the complete PSF license. `LICENSES/` retains full license texts, original source license declarations, Python translation contribution terms and translator credits.

- National Academy for Educational Research (NAER), Taiwan: the two-shore computing, mechanical engineering, physics, environmental protection and medicine tables provide a dedicated **中國大陸譯名** column. Those translations are used directly. The civil-engineering table has only traditional Chinese; its selected entries are individually adapted to Simplified Chinese construction usage and marked as editorial adaptations in the ledger. Permission is the [NAER Government Website Open Information Declaration](https://terms.naer.edu.tw/mysite/about/2/). Copyright ©2021 National Academy for Educational Research, R.O.C.; source attribution retained.
- Python Software Foundation and documentation/Chinese translation contributors: [Python 3.13 glossary](https://docs.python.org/zh-cn/3.13/glossary.html), PSF License Version 2. Translation contribution agreement: CC0-1.0. Copyright © 2001-2026 Python Software Foundation; translator credits and complete notices retained in `LICENSES/`.
- Google LLC: [Machine Learning Glossary](https://developers.google.com/machine-learning/glossary/fundamentals?hl=zh-cn) and [generative AI glossary](https://developers.google.com/machine-learning/glossary/generative?hl=zh-cn), CC BY 4.0 under the [Google Developers site policy](https://developers.google.com/terms/site-policies). Only selected paired term headings enter packs.
- European Environment Agency: [GEMET 4.2.3](https://www.eionet.europa.eu/gemet/en/exports/rdf/latest), CC BY 4.0. Only preferred labels are used. API snapshots contain translations of labels, not third-party definitions.

The sources retain their respective licenses. This project claims no exclusive rights in the underlying terminology. To the extent original selection, arrangement or editorial adaptations create new rights, those contributions are available under CC BY 4.0. The upstream licenses and attribution requirements still apply. Source trademarks and logos are not licensed or reproduced. Data is supplied as-is, without warranty; see the complete applicable license disclaimers.

## Curation and reproduction

`curation.json` is the explicit selection ledger. Each record identifies the source entry and exact original pair. Any changed translation has a review note. Synonym strings and disambiguation annotations are not split automatically. Obvious source errors, misspellings and uncertain equivalences were excluded. This is an editorial review, not certification by a subject-matter institution. Repeated identical pairs are merged with all provenance; conflicting translations fail the build.

`raw/` archives the licensed source inputs so reproduction does not depend on mutable websites. `sources.json` checksums cover every archived input and license file. Keep these snapshots and the ledger together. `tools/build_glossary_data.py` is copied from the application repository's `scripts/` and uses Python's standard library. The optional `truststore` package can be used solely when fetching missing sources on platforms that need system certificate-chain support; certificate checks remain enabled.

From this data checkout:

```sh
python tools/build_glossary_data.py --data-dir . --revision <40-character-data-commit>
```

The command validates snapshot hashes, checks selected originals, rejects missing review notes and conflicting translations, then deterministically writes `packs/*.json` and `catalog.json`. `--fetch-missing` fetches only absent snapshots from the recorded HTTPS URLs and rejects changed upstream content. For an old version whose source pages changed, use the archived inputs at its commit.

## Publication

Commit `packs/`, `raw/`, `LICENSES/`, `curation.json`, `sources.json`, this README and the reproducible builder first. Then generate a catalog whose pack URLs pin that actual data commit, and commit `catalog.json` separately. The mutable branch catalog points only to immutable pack URLs. Pack SHA-256, byte size and entry count must match the published files. Application metadata is generated with the same data revision using the builder's `--write-module` option; only metadata is copied back to the application repository.
