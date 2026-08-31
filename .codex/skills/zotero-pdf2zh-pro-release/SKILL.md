---
name: zotero-pdf2zh-pro-release
description: Prepare and publish unified zotero-pdf2zh-pro releases across the Zotero plugin, PyPI server, Windows ZIP, public GitHub releases, and the public study-233 Homebrew tap. Use for version bumps, release validation, release recovery, or distribution synchronization. Do not use for ordinary feature development.
---

# Zotero PDF2ZH Pro Release

Keep every distribution on one version. The canonical identities are:

- GitHub: `study-233/zotero-pdf2zh-pro` (public)
- PyPI and CLI: `zotero-pdf2zh-pro`
- Zotero add-on ID: `zotero-pdf2zh-pro@study-233`
- Homebrew: `study-233/formula/zotero-pdf2zh-pro` (public, source-only)

## Prepare

1. Require an explicit request before pushing, publishing, creating a release, or
   changing the Homebrew tap.
2. Work from a clean `main` whose `origin` is the canonical GitHub repository.
3. Add `## v<version> - YYYY-MM-DD` to `CHANGELOG.md` before invoking the release
   script.
4. Run `scripts/release.sh <version>`. Use `--no-push`, `--no-release`,
   `--no-pypi`, or `--no-tap` only when the user intentionally excludes that
   distribution.

The script synchronizes `plugin/package.json`, `server/pyproject.toml`,
`server/server.py`, `server/uv.lock`, the README marker, and
`scripts/windows/common.ps1`. Never update only one version surface.

## Validate

The release is not ready until all of these succeed:

- plugin frozen install, lint, type check, and XPI build;
- server tests, wheel/sdist content checks, and fresh Python 3.13 runtime/OCR smoke;
- PowerShell 5.1 script parsing and Windows install/start/health/stop/uninstall CI;
- deterministic Windows ZIP and corresponding-source archive validation;
- XPI manifest ID/name/version/update URL checks and update-manifest hash validation;
- public PyPI registry in `server/uv.lock`.

Do not build or publish a friends bundle. GitHub Releases contain the XPI,
`update.json`, and Windows ZIP. The release script generates a corresponding-source
archive locally; the matching public tag is the canonical release source.

## Publish PyPI

Trusted Publishing is bound to project `zotero-pdf2zh-pro`, owner `study-233`,
repository `zotero-pdf2zh-pro`, workflow `publish-pypi.yml`, environment `pypi`.
The first release uses a PyPI Pending Publisher; later releases reuse the
converted publisher. Keep `id-token: write` on the publishing job only.

Publish and verify PyPI before treating the Windows package as distributable,
because its installer requests the exact matching PyPI version. Recovery may
reuse an existing release only when its wheel and sdist are complete and its tag
points to the same commit.

## Update Homebrew

The tap is `study-233/homebrew-formula`, formula
`Formula/zotero-pdf2zh-pro.rb`. It uses the public HTTPS source repository,
`python@3.13`, and a pinned git revision. It intentionally has no bottles.

Update only the formula version and source revision, push tap `main`, and wait
for `formula-checks.yml`. Do not restore bottle PR, `brew pr-pull`, tarball SHA,
or personal absolute-path logic.

## Report

Return the main commit and tag, PyPI status, XPI/update manifest/Windows/source archive hashes,
Homebrew commit and check status, validation commands, and any manual external
step that remains.
