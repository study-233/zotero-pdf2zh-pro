# Dictionary response fixtures

Captured with public test expressions on 2026-10-09. These are reduced parser fixtures,
not a redistributable dictionary database. Youdao samples keep the word entry and up to
three bilingual examples. Bing keeps the definition card and two examples, stripping
scripts, tracking attributes and unrelated page content.

- Youdao: `https://dict.youdao.com/jsonapi_s?doctype=json&jsonversion=4`
- Bing: `https://www.bing.com/dict/search?q=learning&FORM=BDVSP6&cc=cn`

The adapter's endpoint discovery was informed by
[KISS Translator](https://github.com/fishjar/kiss-translator/blob/dev/src/apis/index.js).
The plugin uses its own typed parsers and DOM renderer. Tests do not load upstream scripts
or contact live services. `python scripts/selection_dictionary_smoke.py --record` refreshes
the reduced Youdao fixtures and writes the Bing live response to ignored local diagnostics.
