# Provenance and source licenses

Acquired 2026-09-22 over HTTPS with certificate verification enabled. `sources.json` is the machine-readable acquisition manifest, including exact download URLs and SHA-256 for all input and license snapshots. Source pages are mutable, so the archived bytes are authoritative for reproducing this release.

Google HTML snapshots omit script elements, including unrelated public website client configuration. The transform is `strip-script-elements-v1`: `upstreamSha256` records the original fetched bytes and `sha256` records the sanitized archive. The builder verifies both hashes when fetching a missing snapshot. Terminology headings, license statements and all five pack hashes are unchanged by this sanitization. No project API credentials are included in these archives.

| Source ID | Owner / material | Reuse basis | Modification |
| --- | --- | --- | --- |
| `naer-computing` | National Academy for Educational Research, two-shore computing terminology | [NAER open information declaration](https://terms.naer.edu.tw/mysite/about/2/) | Reviewed selection; mainland translation field retained |
| `naer-mechanical` | NAER, two-shore mechanical terminology | Same declaration | Selection of building equipment / HVAC terms; mainland translation field retained |
| `naer-civil` | NAER, civil engineering terminology | Same declaration | Selection, one sense chosen where necessary, editorial adaptation to Simplified Chinese construction terminology; every adaptation is noted |
| `naer-physics` | NAER, two-shore physics terminology | Same declaration | Reviewed selection; mainland translation field retained |
| `naer-environment` | NAER, two-shore environmental protection terminology | Same declaration | Reviewed selection; mainland translation field retained |
| `naer-medicine` | NAER, two-shore medicine terminology | Same declaration | Basic medical and respiratory selection; mainland translation field retained |
| `python` | Python Software Foundation and documentation/translation contributors, Python 3.13 glossary | [PSF License Version 2](https://docs.python.org/zh-cn/3.13/license.html); translation contributions supplied under [CC0](https://github.com/python/python-docs-zh-cn/blob/master/README.rst) | Paired glossary headings, reviewed selection, whitespace normalization |
| `google-foundation`, `google-generative` | Google LLC, Google Developers ML glossary | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) under [site policy](https://developers.google.com/terms/site-policies) | English/Chinese headings paired by ID, reviewed selection, whitespace normalization |
| `gemet` | European Environment Agency, GEMET 4.2.3 | [CC BY 4.0 on official download page](https://www.eionet.europa.eu/gemet/en/exports/rdf/latest) | English/zh-CN preferred labels paired by concept URI; definitions excluded |

Copyright ©2021 National Academy for Educational Research, R.O.C.; ©2001-2026 Python Software Foundation and contributors; Google LLC; European Environment Agency. Attribution is repeated in each relevant pack. No institutional endorsement or official national-standard status is claimed. All material is provided as-is; applicable license disclaimers are retained.

## Full license material

- `LICENSES/NAER.html`: complete original open-information declaration.
- `LICENSES/PSF-2.0.txt`: complete CPython 3.13.0 license and historical copyright notices. `Python-documentation-license.html` additionally preserves the current Python 3.13 documentation license page. The Python-derived pack embeds the full PSF text and an attribution/modification notice so it can be downloaded alone.
- `LICENSES/Python-zh-CN-README.rst`, `Python-TRANSLATORS.txt`, `CC0-1.0.txt`: original Chinese translation contribution agreement, translator credits and full CC0 legal code.
- `LICENSES/CC-BY-4.0.txt`: complete legal code from the official Creative Commons repository. `Google-site-policies.html` and `GEMET-license.html` preserve the publishers' explicit license statements.

No source data is relicensed as application code. Each upstream license remains applicable. The original selection/arrangement and expressly marked editorial adaptations are contributed under CC BY 4.0, without restricting the underlying rights granted by the sources.

## Selection record

`curation.json` contains an explicit list of source IDs and entry IDs, exact source pairs, and per-entry adaptation notes. It is not a list of unreviewed raw-import candidates. Multi-synonym and annotation-heavy rows are not split automatically. Known erroneous Google headings, NAER environmental noise mappings and doubtful GEMET mappings were excluded. Common cross-domain ambiguous bare terms identified during review were omitted. A second reviewer checked all computing, physics and medicine pairs plus building/environment subsets. This editorial review is not an expert certification.

Each distributed term carries `sourceIds` and `sourceRefs`. When identical pairs appear in multiple sources, their provenance is merged. Differing translations for the same English spelling are rejected by the builder; no conflict is silently resolved by insertion order.
