---
name: zotero-pdf2zh-pro-release
description: Prepare, publish, or resume zotero-pdf2zh-pro releases across Zotero XPI, PyPI, Windows and Homebrew; write concise Chinese release notes. Use for 发布新版本、发布新小版本、提交并推送小版本、发布恢复 and release-note edits in this project, not ordinary feature development.
---

# Zotero PDF2ZH Pro Release

发布一个版本一致、安装可用、说明清楚的版本。先辨别用户要完成哪一层工作，再使用仓库现有流程。

## 范围与版本

| 用户请求 | 完成范围 |
| --- | --- |
| 提交并推送当前修改／新的小版本 | 整理改动、必要时升版、验证、提交和推送；不自动创建 tag 或发布安装包 |
| 发布新版本／新小版本 | 完成准备、核心 CI、Windows 制品验证、PyPI、GitHub Release 和 Homebrew |
| 继续发布／发布中断 | 先核对已完成阶段，从可验证的状态继续，不默认重建、再升版本或覆盖已有包 |
| 优化 skill／修改 Release 文案 | 修改相应文件或明确指定的线上正文；不推送代码、不重新发布或替换制品 |

- “发布新版本”已授权该次正常发布所需的提交、推送、tag、各渠道发布与必要修复，不逐阶段重复索要确认。明确排除的渠道除外；旧对话的一次授权不作为新任务的永久权限。
- 本项目“新小版本”通常指补丁号 +1。若工作区已准备好未发布的版本，继续该版本；先检查 GitHub tag/Release、PyPI 和 CHANGELOG，避免二次递增。
- 同版本覆盖、移动已公开 tag 与正常发布分开处理，只在用户明确要求时采用，见 [发布恢复](references/recovery.md)。
- 发布不隐含升级本机 Zotero/Homebrew、重启服务或迁移用户数据。用户要求本机安装时另行处理；保留已有任务、配置和附件。

## 定位和准备

1. 确认实际仓库根目录：聊天目录可能是父仓库，源码在 `zotero-pdf2zh-pro/`。读取仓库约定，核对 origin、分支、已跟踪及未跟踪改动。
2. 正式发布使用干净的 `main`，origin 为 `study-233/zotero-pdf2zh-pro`。存在待发布改动时先审阅和提交，不直接丢弃、stash 或漏掉新增模块、测试。检查暂存差异后再提交。
3. 以最后已发布版本至当前改动为依据整理 CHANGELOG。不能只复制已有 Unreleased：需核对所有本次提交和相关待提交改动，避免漏记同时完成的修复。写 `## v<version> - YYYY-MM-DD`，日期按实际发布日。
4. 写文案前读 [Release 写作规范](references/release-notes.md)。技术验证记录留在 Actions/构建记录，不堆进用户更新说明。
5. 读取当前 `scripts/release.sh` 与 `.github/workflows/{ci,build-windows-release,publish-pypi}.yml`，以现有参数和检查为准，不照抄历史对话里的临时发布命令。

规范身份：PyPI/CLI 为 `zotero-pdf2zh-pro`，Zotero ID 为 `zotero-pdf2zh-pro@study-233`，Homebrew 为 `study-233/formula/zotero-pdf2zh-pro`。

版本同步面（以脚本和 `check_release_artifacts.py` 为准）：

- `plugin/package.json`；XPI manifest 和 `update.json` 由构建生成。
- `server/pyproject.toml`、`server/server.py`、`server/uv.lock` 的本项目条目。
- `windows-app/package.json`、`windows-app/src-tauri/{tauri.conf.json,Cargo.toml,Cargo.lock}` 的本项目条目。
- `scripts/windows/common.ps1` 的 release-version、README 版本标记、CHANGELOG。

只改本项目版本，不全局替换依赖版本。保留公开 PyPI registry 和锁文件一致性。

## 验证与发布

正式发布默认执行 `bash scripts/release.sh <version>`。本机 macOS 可发起发布，Windows EXE 由 Actions 构建，无需用户切换电脑。

`--no-push` **不是 dry-run 或“只提交”模式**：它在 Windows 本地构建制品，并可能产生版本提交。只提交推送的请求应单独更新版本文件、运行相关检查、commit/push。

- 核心 CI 验证插件构建/lint/测试、服务端、Windows 前端及发布守卫。成功证据必须对应实际发布提交；修复代码后的新提交需重新验证。
- Windows 标准流程复用核心 CI，构建一批制品，验证最终 ZIP 的 EXE/DLL 依赖与窗口启动、全新 Python 3.13/OCR、安装和自更新。以当前 workflow 的实际步骤为准。
- 不为每个普通补丁默认加 `--full-validation`，不把同一套完整测试在本机、构建、发布阶段反复跑。安装迁移/回滚相关改动、同版本替换或用户要求时再运行扩展验证。
- 入口或模块布局变更时，提前核对 `server/pyproject.toml` 的 CLI/打包模块、`check_pypi_artifacts.py`、`check_installed_runtime.py`、`server/Dockerfile` 和相关启动方式。v1.7.4 曾因新入口 `service_launcher:main` 与旧检查不一致而中断；以后以元数据为准，不固定旧入口。
- 标准流程保留已有制品身份、大小、哈希和自动更新清单检查；不额外反复下载所有附件做人工校验。哈希用于机器验证，不能因正文不显示就删除校验字段。
- 本地检查挂起或环境缺失时，区分“未完成”与“通过”，记录具体原因；在同一提交的相应 CI 通过后才能替代该检查。macOS 临时目录别名导致断言差异时先确认原因，可用解析后的临时目录复测，不删断言掩盖问题。

发布顺序：**核心 CI → Windows 已验证制品 → PyPI 公开包可用 → GitHub Release/更新清单 → Homebrew**。

PyPI 使用现有 Trusted Publishing：仓库 `study-233/zotero-pdf2zh-pro`、workflow `publish-pypi.yml`、environment `pypi`；`id-token: write` 仅在发布 job。Windows 安装器依赖精确 PyPI 版本，因此不能在 wheel/sdist 尚未验证可用时先分发安装器。

GitHub 正式附件为 `zotero-pdf2zh-pro.xpi`、`update.json`、`zotero-pdf2zh-pro-windows-x64.zip`、`windows-update.json`。保留稳定更新地址。源码以匹配的公开 tag 为准；对应源码包及 `checksums.json` 保留在构建记录。不要创建 friends bundle，或在正文链接未实际上传的校验/源码附件。

Homebrew tap 为 `study-233/homebrew-formula`，文件为 `Formula/zotero-pdf2zh-pro.rb`；source-only、公开 HTTPS 源、固定 `python@3.13`、git revision。正常升版更新 version/revision，保留配方其他设置；推送 tap `main` 并等待该提交的 `formula-checks.yml`。不引入 bottles、tarball SHA 或个人绝对路径。

## 中断与交付

API EOF、下载无进展、PyPI 可见性延迟、制品复用或同版本替换，读 [发布恢复](references/recovery.md)。不能把本机观察失败等同于远端构建失败；不得直接重跑整个发布脚本造成重复构建或覆盖。

最终核对各渠道版本、tag 对应提交、正式附件与更新清单以及 tap 检查。报告以版本和 Release 下载入口开头，简述 PyPI/Homebrew 状态、重要限制和未完成项；只有所有要求的渠道完成才说“已发布完成”。

默认不输出完整 commit SHA、SHA-256 表、逐条命令和 CI 日志。定位代码确有必要时用短提交链接；完整值保留在机器记录中。已完成的检查用一句话概括，标准验证通过不等于扩展回滚/迁移或所有真实电脑均已验收。
