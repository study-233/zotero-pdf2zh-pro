# 仓库协作

先核对工作树、已有改动和受影响的调用路径，按改动范围验证。项目整理及 PR 见[维护 skill](.agents/skills/zotero-pdf2zh-pro-maintenance/SKILL.md)，正式发布见[发布 skill](.codex/skills/zotero-pdf2zh-pro-release/SKILL.md)。

## Project Notes

- Diagnostic fields, error codes, events and feature coverage live in `shared/diagnostics/contract.json`. Run `python3 scripts/generate_diagnostic_contract.py` after changes and commit both generated files. New features need a coverage declaration and synthetic fault/export tests; preserve the frozen v1 compatibility baseline. See `docs/task-diagnostics-and-cancellation.md` for the release checks and compatibility rules.

- This repo ships the server through multiple targets: the managed XPI engine, the Windows control center, uv, Docker, and Homebrew.
- Keep the plugin and server version aligned. For release candidates, update both `plugin/package.json` and the server version fields in `server/pyproject.toml` and `server/server.py`.
- The server is published to PyPI as `zotero-pdf2zh-pro`; `scripts/release.sh <version>` is the unified release path for XPI, PyPI, the Windows package, a local source archive, and optional Homebrew updates.
- When changing server packaging or startup behavior, also review:
  - `server/pyproject.toml`
  - `server/uv.lock`
  - `server/Dockerfile`
  - `compose.yaml`
  - the public Homebrew tap `study-233/homebrew-formula`, formula `Formula/zotero-pdf2zh-pro.rb`
- The Homebrew formula should stay aligned with the server CLI entrypoint and currently must pin `python@3.13` instead of unversioned `python`, because the maintained core and native dependency set is validated on Python 3.12/3.13, not Python 3.14.
- The Homebrew tap remote is `https://github.com/study-233/homebrew-formula.git`. Use `--tap-path` or let the release script use a temporary clone; never add a personal absolute path.
- The tap is source-only and intentionally does not publish bottles. Update its `version` and git `revision`, push `main`, and wait for `formula-checks.yml`.
- The plugin repository is public. Keep the stable `update_url` in the XPI and publish `update.json` with every GitHub Release. Do not build or publish a friends bundle; release source is available from the matching public tag.
- Do not update only one distribution target if the change affects all startup/install paths.

## Development outputs

- Follow the output layout and retention rules in [development notes](docs/development-notes.md#development-outputs).
- Put disposable test installations and experiments in a unique managed `.tmp/runs/` directory using `scripts/workspace.py` or `scripts/workspace.ps1`; finish in `finally`, after stopping owned processes, to retain evidence and remove temporary dependencies.
- Put generated logs, screenshots and acceptance reports in `.local-dev/reports/` using the shared workspace helper. Do not add loose files to the repository root, `.tmp/` or `.local-dev/`.
- Preserve long-term tools, dependency/build caches, `.local-dev/runtime/`, benchmark inputs/results, archives and recovery backups. They are outside automatic cleanup. Mark a managed report/run/build with `.keep` when it must be retained long term.
- Ordinary managed reports expire after 30 days; disposable runs abandoned for one day are eligible only when their owner has exited. Build history keeps the latest two versions plus `.keep` versions. Manual cleanup previews by default: `python scripts/workspace.py clean`; deletion requires `--apply`.
- Use `dist/` for current release aliases and `dist/releases/<version>/` for managed build history. Never mix ad-hoc candidate directories into `dist/`.
- Promote reusable utilities into `scripts/` and tests/fixtures into their existing source directories; keep one-off local files in an explicitly managed run or the local archive.
- Run `python scripts/workspace.py check` and the `test_workspace.py` suite when changing output paths or lifecycle behavior.

## Documentation

- Before editing plugin copy or UI, follow [plugin copy and UI guidelines](docs/ui-guidelines.md).
  Keep Chinese and English resources aligned; retain task metric scrolling with hidden scrollbars.
  Verify the real development Zotero and record theme, language, width and state coverage.

- Before changing the README or adding documentation, follow [README maintenance rules](docs/readme-maintenance.md). Keep the homepage concise and put detailed instructions in the user guide.
- Preserve the release version marker, existing public anchors, upstream attribution, and any in-progress documentation edits.
- Run `python3 scripts/check_docs.py` and `python3 -m unittest discover -s scripts -p 'test_check_docs.py'` for documentation changes. Preview the affected Markdown and check new external links manually.
