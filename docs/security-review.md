# Security review

## Dependency fixes — 2026-10-12

The default branch audit found 24 open Dependabot alerts across six packages.
The following fixes update source manifests and lockfiles; they do not replace
the already published 1.9.0 artifacts.

| Dependency | Minimum fixed version | Alert count |
| --- | --- | --- |
| Axios | `1.20.0` | 9 |
| brace-expansion | `1.1.21` / `5.0.12` | 2 |
| pypdf | `6.19.0` (retain `<7`) | 8 |
| urllib3 | `2.8.0` | 3 |
| fsspec | `2026.6.0` | 1 |
| Werkzeug | `3.1.9` | 1 |

All four Python minimums are declared in the wheel dependency metadata as well
as the uv lockfile, covering PyPI installation without a checkout. Axios is
imported and bundled into the plugin despite its `devDependencies` classification;
it must not be excluded from security review on that basis.

GitHub marked all 24 alerts `fixed` on 2026-10-12 between 14:56:54 and
14:56:59 UTC (22:56:54–22:56:59 Asia/Hong_Kong), after
[`701acfe`](https://github.com/study-233/zotero-pdf2zh-pro/commit/701acfe9102e8548ce51a52a874aa978dce037f2)
reached main. None was dismissed or auto-dismissed. The checked alert IDs are
77, 78, 80, 81, 84–102, and 104. The SBOM reports Axios 1.20.0,
both fixed brace-expansion versions, pypdf 6.19.0, urllib3 2.8.0,
fsspec 2026.6.0 and Werkzeug 3.1.9. Installed-runtime checks now enforce the
four Python minimums on both Python 3.12 Docker and Python 3.13 CI.

The Security workflow analyzes Python, JavaScript/TypeScript and Actions with
CodeQL and rejects new high/critical dependency findings in pull requests.
Dependabot is configured for grouped security updates only, including uv,
website requirements, Cargo and Actions. Rust source analysis remains outside
this CodeQL configuration; native Windows validation is unchanged.

## CodeQL remediation — 2026-10-13

The first complete main analysis at `df21a39` reported 20 existing findings:
six Actions, five JavaScript/TypeScript and nine Python. All three language
categories remain enabled; no rule, test directory or bundled-source directory
is excluded. These source changes do not update published 1.9.0 packages.

Actions changes are in
[`87a2def`](https://github.com/study-233/zotero-pdf2zh-pro/commit/87a2def),
and input/output boundary changes in
[`f5a912c`](https://github.com/study-233/zotero-pdf2zh-pro/commit/f5a912c).
The follow-up [path/parser correction](https://github.com/study-233/zotero-pdf2zh-pro/commit/14db2ec)
uses normalized path prefix checks recognized by CodeQL and explicitly rejects
HTML recovery ambiguities across Python maintenance versions.

[PR #16](https://github.com/study-233/zotero-pdf2zh-pro/pull/16) merged as
[`42c32e5`](https://github.com/study-233/zotero-pdf2zh-pro/commit/42c32e507a33cca16cc67ad7512659b5d54f1c19).
All three main language analyses completed successfully for that exact commit
(analysis IDs: Actions 1937673471, JavaScript/TypeScript 1937675875,
Python 1937678232), and main CI passed. Twelve original alerts automatically
became `fixed`; eight individually reviewed findings were dismissed as
`false positive`, each with an immutable evidence link. The main alert API
then reported **zero open CodeQL alerts and zero open Dependabot alerts**.
The table records the verified GitHub states; analysis result counts still
include the eight dismissed findings because their rules remain enabled.

The intermediate PR analysis produced three additional path findings, 21–23.
The final PR analysis after the path correction reported zero new findings
for each language; these PR-only findings were never promoted to main.

| Alert | Rule | Resolution | Verified GitHub state |
| --- | --- | --- | --- |
| 1 | actions/missing-workflow-permissions | CI explicitly grants contents: read  fixed |
| 2 | actions/missing-workflow-permissions | Same workflow permission correction  fixed |
| 3 | actions/missing-workflow-permissions | Same workflow permission correction  fixed |
| 4 | actions/cache-poisoning/direct-cache | Trusted-main source/CI gates precede caches  fixed |
| 5 | actions/cache-poisoning/direct-cache | Cache only pinned, verified renderer archives  fixed |
| 6 | actions/cache-poisoning/poisonable-step | Target scripts run only after source/CI gates  fixed |
| 7 | js/xss-through-dom | Same-origin HTTP(S) or a module-created, hash-verified PDF blob  fixed |
| 8 | js/xss-through-dom | Same URL gate protects both preview and download links  fixed |
| 9 | js/bad-tag-filter | Reviewed false positive: fixed local test-script extraction  dismissed: false positive |
| 10 | js/bad-tag-filter | Reviewed false positive: fixed local test-script extraction  dismissed: false positive |
| 11 | js/incomplete-multi-character-sanitization | Inert DOM text extraction; remove script/style nodes  fixed |
| 12 | py/path-injection | Cross-platform basename validation, resolved-parent check, exclusive creation  fixed |
| 13 | py/bad-tag-filter | HTML-parser script ranges preserve original bytes; ambiguous tags rejected  fixed |
| 14 | py/stack-trace-exposure | Explicit business messages or fixed diagnoses; correlated, redacted logs  fixed |
| 15 | py/weak-sensitive-data-hashing | Reviewed false positive: PDF Algorithm 3.5 padding hash  dismissed: false positive |
| 16 | py/weak-sensitive-data-hashing | Reviewed false positive: PDF Algorithm 3.2 key derivation  dismissed: false positive |
| 17 | py/weak-sensitive-data-hashing | Reviewed false positive: PDF Algorithm 3.7 owner authentication  dismissed: false positive |
| 18 | py/weak-sensitive-data-hashing | Reviewed false positive: PDF revision 5 authentication hash  dismissed: false positive |
| 19 | py/weak-sensitive-data-hashing | Reviewed false positive: PDF revision 6 initial hash  dismissed: false positive |
| 20 | py/overly-large-range | Reviewed false positive: precisely the intended XML control characters  dismissed: false positive |

### Evidence and regression coverage

Alerts 9–10 read a fixed repository XHTML file and extract its script for a
Node VM test fixture. The regex is not an HTML filter for user input, and its
output never enters a product HTML rendering sink. The tests and their coverage
are retained; the two individual findings are eligible for false-positive closure.

Alerts 15–19 are in the bundled PDF Standard Security Handler, not an application
password database. MD5/SHA-256 are required to reproduce existing PDF encryption
and authentication algorithms. Replacing them with Argon2 or another password
hash would change the derived keys and break decryption. See the
[Adobe PDF reference](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.4.pdf)
and the [upstream security handler](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdfdocument.py).
[PDF security regressions](../server/tests/test_pdf_security_compatibility.py)
generate RC4-40, RC4-128, AES-128, AES-256-R5 and AES-256 PDFs, check user/owner
and empty passwords, reject incorrect passwords and verify decrypted metadata.
The bundled parser's raw AES result includes PKCS#7 padding, which the test
accounts for explicitly without changing the cryptography.

Alert 20's XML filter was evaluated against all 1,114,112 Unicode code points.
It matches exactly 29 characters: U+0000–U+0008, U+000B–U+000C and
U+000E–U+001F. Tab, LF, CR and printable Unicode remain intact. The same
PDF regression file preserves this exact-set assertion.

[Input/error regressions](../server/tests/test_security_boundaries.py) cover
Windows and POSIX paths, Unicode names, ADS/device names, existing files and
symbolic links, and errors containing credentials, URLs and absolute paths.
Unknown failures return an errorId; local diagnostics keep only exception type
and bounded frame metadata, without messages, source lines or local variables.
Explicit business errors retain status and public messages.

[Release guards](../scripts/test_release_workflows.py) reject non-main dispatch,
malformed SHAs, non-ancestor sources and missing successful push CI. Guards run
before target checkout, dependency installation, cache use or target scripts.
The trusted workflow revision supplies the renderer verifier for historical builds.
[Archive checks](../scripts/test_windows_build_tools.ps1) verify warm reuse,
replacement of poisoned cache bytes and rejection of poisoned downloads.
Expanded tools are reconstructed into a fresh directory from pinned archives.

[PDF preview checks](../benchmarks/translation/test_pdf_viewer.cjs) exercise both
actual link assignments and local file selection. Arbitrary blob strings,
cross-origin URLs, credentials in URLs, javascript: and data: are rejected.
Local object URLs are created only after SHA-256 matching, with application/pdf
MIME regardless of the selected file's declared type.

The snapshot parser was rerun on six original archived upstream inputs from
`18d67c0^`. All prepared SHA-256 values match the existing data manifest.
No glossary-data history, prepared snapshots, packs or term counts were changed.
The complete plugin audit still reports only the two pre-existing PDF.js
declarations matches assessed below; the new DOM test dependency adds no finding.

## Dependency fixes

These changes were reviewed against 1.6.9 and ship in the 1.7.0 release.

| Dependency | Fixed version | Findings |
| --- | --- | --- |
| AnyIO | `>=4.14.2`, locked to `4.14.2` | [TLS hostname handling](https://github.com/agronholm/anyio/security/advisories/GHSA-82r6-8w77-94w6), [worker stderr deadlock](https://github.com/agronholm/anyio/security/advisories/GHSA-5p39-cfhj-2xmp) |
| adm-zip | `0.6.1` | [Allocation DoS and extraction through symlinks](https://github.com/cthackers/adm-zip/releases/tag/v0.6.1) |
| js-yaml | `4.3.2` | [Merge-key CPU exhaustion](https://github.com/advisories/GHSA-2883-xcg3-v3hh) |
| Vitest / @vitest/mocker | `4.1.11` | [Redirect-mock path traversal](https://github.com/advisories/GHSA-82fw-gwwq-j7x9) |
| brace-expansion | `1.1.18` / `5.0.9` | [GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg), [GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) |

AnyIO's minimum version is also in wheel dependency metadata, so installation
from PyPI receives the fix without needing this repository's lockfile.
The plugin now uses Zotero's native menu API, allowing stable toolkit 5.2.0
and zotero-types 4.1.3 to compile. Zotero owns menu cleanup through `pluginID`.
The attachment title path handles the documented `Item | false` lookup result.

Audits use `https://registry.npmjs.org`. The Windows frontend and production
plugin dependencies report no vulnerabilities. The plugin's full development
audit retains two name-based PDF.js findings assessed below; they have not
been globally ignored or hidden.

## Public source-snapshot client configuration

Secret-scanning alerts 1–9 were created when complete Google Developers HTML
pages were archived in the separate `glossary-data` branch. All nine detected
values occurred only in the client scripts of five upstream glossary/license
pages. On 2026-09-22 they were matched byte-for-byte against unauthenticated
responses from the original official URLs, including
[the English fundamentals glossary](https://developers.google.com/machine-learning/glossary/fundamentals?hl=en)
and [the Google site policy](https://developers.google.com/terms/site-policies).
They are third-party public website configuration, not this project's API
credentials. The verification did not use any value to authenticate a request.

The archived script elements were removed in data commit
[`18d67c0`](https://github.com/study-233/zotero-pdf2zh-pro/commit/18d67c02548e55e6e64bb60c18ae169bef6e8d9f).
`sources.json` records the original and sanitized SHA-256 values and the exact
`strip-script-elements-v1` transform. The catalog now pins downloads to the
sanitized revision. All five pack hashes and all 1,842 terms are unchanged.
The builder checks both newly fetched and previously archived inputs to prevent
client keys from being included again. No historical commit was rewritten;
the alerts were resolved as false positives with this evidence, not as revoked
credentials.

## Available GitHub analysis

On 2026-09-22 GitHub's Code Quality setup API returned
`Code quality is not available for this repository` (HTTP 404). Code scanning
default setup reported `not-configured`, and no analysis existed. These are
coverage limits, not successful code-quality or CodeQL scans. The checks used
for this review are dependency advisories/audits, type checking, lint, builds
and the project's regression tests.

## GHSA-wrw7-89jp-8q8g / RUSTSEC-2024-0429

The following is the historical Tauri assessment from 2026-09-22. The native
Windows candidate removes Tauri, GTK and glib; its current dependency list is
[`windows-app/Cargo.lock`](../windows-app/Cargo.lock). Commands and workflow
descriptions below refer to the earlier implementation.

Reviewed 2026-09-22 for the Windows control center, using `glib 0.18.5`,
`gtk 0.18.2` and `tauri 2.11.5` in
the former `windows-app/src-tauri/Cargo.lock`.

**The vulnerable Rust code is not included in the supported Windows artifact.**
This is a target-specific applicability assessment, not a claim that the locked
crate has been patched. A Linux build of the Tauri control center would need a new
review before being supported or distributed.

Reproduce from the repository root:

```sh
cargo tree --locked --manifest-path windows-app/src-tauri/Cargo.toml -i glib --target x86_64-pc-windows-msvc
```

Result: `warning: nothing to print.` The same command with
`--target x86_64-unknown-linux-gnu` includes `glib 0.18.5` through GTK, WebKitGTK
and Linux tray dependencies. Cargo's shared lockfile records dependencies for
multiple targets even when a particular build does not use them.

The supported artifact boundary is explicit:

- [`scripts/release.sh`](../scripts/release.sh) builds and tests the control
  center using `stable-x86_64-pc-windows-msvc` and
  `--target x86_64-pc-windows-msvc`.
- [The Windows release workflow](../.github/workflows/build-windows-release.yml)
  runs on `windows-latest` and selects that Windows Rust toolchain and target.
- [The Windows packager](../scripts/build_windows_package.py) selects
  `target/x86_64-pc-windows-msvc/release/zotero-pdf2zh-pro.exe` and validates its
  Windows PE format. [Development notes](development-notes.md#windows) document
  this Windows-only control center; its native entry point imports
  `std::os::windows::process::CommandExt`.
- [Ubuntu core CI](../.github/workflows/ci.yml) runs only JavaScript frontend
  state tests for `windows-app`; it does not build a Linux Rust executable.
  [PyPI publication](../.github/workflows/publish-pypi.yml) builds Python
  artifacts or republishes an already verified Windows package. The
  [Dockerfile](../server/Dockerfile) copies and installs only the Python service,
  not the Rust control center. Its system `libglib2.0-0` package is distinct
  from the affected Rust `glib` crate. No current workflow produces a Linux
  control-center artifact.

The [official advisory](https://github.com/advisories/GHSA-wrw7-89jp-8q8g)
affects Rust `glib >=0.15.0,<0.20.0`. The
[upstream fix](https://github.com/gtk-rs/gtk-rs-core/commit/b5a4071e439bef2b5eea76c3aa25e5ae84839e34)
makes the output pointer mutable before passing it to the C function. A direct
stock upgrade was checked without changing the lockfile:

```sh
cargo update --manifest-path windows-app/src-tauri/Cargo.toml --offline --dry-run -p glib --precise 0.20.0
```

Cargo rejects this because `gtk 0.18.2` requires `glib = "^0.18"`.
The official `0.18` registry series ends at `0.18.5`; the
[official 0.18 branch examined](https://github.com/gtk-rs/gtk-rs-core/blob/42b9caf98e03ded086362d9653ca58fe94dc8658/glib/src/variant_iter.rs)
still contains the affected implementation.
[Tauri's upstream issue](https://github.com/tauri-apps/tauri/issues/12048)
and the [open Wry GTK4 migration](https://github.com/tauri-apps/wry/issues/1474)
track the compatibility constraint.

No dependency version was falsified, lockfile entry removed, audit rule disabled,
or vendored copy introduced. If Linux control-center support is added, revisit
this assessment and require a compatible fixed dependency or a verified source
backport before publishing that artifact.

## PDF.js advisory matches against development type declarations

On 2026-09-22, the plugin dependency audit reported
[GHSA-7jg2-jgv3-fmr4](https://github.com/advisories/GHSA-7jg2-jgv3-fmr4)
and [GHSA-wgrm-67xf-hhpq](https://github.com/advisories/GHSA-wgrm-67xf-hhpq)
through `zotero-types > pdfjs-dist`, with a reported version of `0.0.0`.
Both advisories concern executable PDF.js code processing a malicious PDF.
The dependency at this path is a different, declarations-only distribution,
not the affected npm PDF.js runtime.

Evidence for the exact locked dependency:

- [`plugin/pnpm-lock.yaml`](../plugin/pnpm-lock.yaml) resolves this name to
  `zotero-plugin-dev/zotero-pdfjs-types` at commit
  `cb4ba25178e5fd5a6cfbde859d87ef763a2a20f1`, through `zotero-types 4.1.3`.
- That revision's
  [package manifest](https://github.com/zotero-plugin-dev/zotero-pdfjs-types/blob/cb4ba25178e5fd5a6cfbde859d87ef763a2a20f1/package.json)
  declares an empty `main`, `types: "types/src/pdf.d.ts"`, an empty `scripts`
  object, and a `files` list containing only `types`. It has no version field;
  the audit's `0.0.0` is not a PDF.js runtime version. The upstream
  [README](https://github.com/zotero-plugin-dev/zotero-pdfjs-types/blob/cb4ba25178e5fd5a6cfbde859d87ef763a2a20f1/README.md)
  describes extracting declarations for Zotero plugin development. Both files
  were retrieved over verified HTTPS and matched the installed files exactly.
- The installed package contains 107 files: 105 `.d.ts` declarations,
  `README.md`, and `package.json`. It contains no executable JavaScript.
  `zotero-types/types/reader/pdf/pdfjs.d.ts` references this dependency only
  through TypeScript types, including `typeof import("pdfjs-dist")`.
- The [plugin build configuration](../plugin/zotero-plugin.config.ts) bundles
  `src/index.ts` and copies `addon` assets. A successful build with toolkit
  `5.2.0` and types `4.1.3` produced an XPI with 27 ZIP entries, none containing
  PDF.js runtime files. Its JavaScript/JSON files contain none of
  `pdfjs-dist`, `pdfjsLib`, `PDFWorker`, `isEvalSupported`, `PartialEvaluator`,
  `PostScriptEvaluator`, or `FontFaceObject`.

Recheck the dependency source and artifact contents after a dependency update:

```sh
pnpm --dir plugin why pdfjs-dist
pnpm --dir plugin build
python -m zipfile -l plugin/build/zotero-pdf2zh-pro.xpi
```

These two audit findings remain visible; no advisory ignore or replacement
version was added. This assessment covers the locked development type package
and the plugin artifact only. It does not assess the PDF.js version supplied
by the user's Zotero installation. Reassess if the dependency source changes
or executable PDF.js code is introduced into the plugin.
