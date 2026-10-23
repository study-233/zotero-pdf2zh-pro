---
name: zotero-pdf2zh-pro-release
description: Prepare, publish, or resume zotero-pdf2zh-pro releases across Zotero XPI, PyPI, Windows and Homebrew; validate release workflow repairs and write concise Chinese release notes. Use for 发布新版本、发布新小版本、提交并推送小版本、发布恢复、发布流程修复验收 and release-note edits in this project, not ordinary feature development.
---

# Zotero PDF2ZH Pro Release

发布一个版本一致、安装可用、说明清楚的版本。先辨别用户要完成哪一层工作，再使用仓库现有流程。

## 范围与版本

| 用户请求 | 完成范围 |
| --- | --- |
| 提交并推送发布修复、验证工作流 | 提交相关代码与 skill，推送后等待该提交的 CI；按用户要求运行兼容工作流，不自动升版或发布渠道 |
| 提交并推送当前修改／新的小版本 | 整理改动、必要时升版、验证、提交和推送；不自动创建 tag 或发布安装包 |
| 发布新版本／新小版本 | 完成准备、核心 CI、Windows 制品验证、PyPI、GitHub Release 和 Homebrew |
| 继续发布／发布中断 | 先核对已完成阶段，从可验证的状态继续，不默认重建、再升版本或覆盖已有包 |
| 单独优化 skill／修改 Release 文案 | 修改相应文件或明确指定的线上正文；若同时要求继续发布，按该次发布授权完成后续阶段 |

- “发布新版本”已授权该次正常发布所需的提交、推送、tag、各渠道发布与必要修复，不逐阶段重复索要确认。明确排除的渠道除外；旧对话的一次授权不作为新任务的永久权限。
- 本项目“新小版本”通常指补丁号 +1。若工作区已准备好未发布的版本，继续该版本；先检查 GitHub tag/Release、PyPI 和 CHANGELOG，避免二次递增。
- 同版本覆盖、移动已公开 tag 与正常发布分开处理，只在用户明确要求时采用，见 [发布恢复](references/recovery.md)。
- 发布不隐含升级本机 Zotero/Homebrew、重启服务或迁移用户数据。用户要求本机安装时另行处理；保留已有任务、配置和附件。

## 提交修复与远端验收

此模式完成后即可交付；以下“正式发布准备”与“验证与发布”用于用户要求正式发布时。

- 审阅已跟踪和新增文件，显式暂存本次任务范围；保留无关本地配置，不使用 `git add -A` 混入个人改动。
- 本地已通过且代码未变的重型验收直接复用；对新修改运行受影响检查及 skill 校验。提交后推送用户指定分支，核对远端提交。
- CI 证据必须属于推送提交。手动兼容任务使用 `dispatch_id` 关联准确 run，并核对 `head_sha`、run ID、attempt 和结论；定时任务的既有成功不能代替本次手动验收。
- 远端失败先读取具体 job/step 日志，区分代码、夹具和环境问题。修复后取得新提交证据；工作流源码未变的临时故障可有限重跑失败 job。相同原因重复失败时保存证据，禁止无限重跑或删断言。
- 观察超时先查询原 run，不能重复 dispatch。报告提交链接、CI/兼容 run 链接及未完成项；只有要求的远端检查全部成功才说验收完成。

## 正式发布准备

1. 确认实际仓库根目录：聊天目录可能是父仓库，源码在 `zotero-pdf2zh-pro/`。读取仓库约定，核对 origin、分支、已跟踪及未跟踪改动。
2. 正式发布使用干净的 `main`，origin 为 `study-233/zotero-pdf2zh-pro`。存在待发布改动时先审阅和提交，不直接丢弃、stash 或漏掉新增模块、测试。检查暂存差异后再提交。
3. 以最后已发布版本至当前改动为依据整理 CHANGELOG。不能只复制已有 Unreleased：需完整核对本次提交和相关待提交改动，再筛选用户关心的重点；完整核对不等于逐项写入正文。写 `## v<version> - YYYY-MM-DD`，日期按实际发布日。
4. 写文案前读 [Release 写作规范](references/release-notes.md)。默认一屏可读：一句话概括、3–4 条重点、必要的升级提醒和底部链接；短补丁可更少，必要提醒不受篇幅限制。技术验证记录留在 Actions/构建记录。
5. 读取当前 `scripts/release.sh`、`scripts/release-policy.json` 和相关 workflow，以现有参数和机器策略为准，不照抄历史对话里的临时发布命令。

规范身份：PyPI/CLI 为 `zotero-pdf2zh-pro`，Zotero ID 为 `zotero-pdf2zh-pro@study-233`，Homebrew 为 `study-233/formula/zotero-pdf2zh-pro`。

版本同步面由 `release.sh` 与 `check_release_artifacts.py` 管理；只改本项目条目，不全局替换依赖版本。保留公开 PyPI registry 和锁文件一致性。

## 验证与发布

先读 [Actions 测试规范](../../../docs/actions-testing.md)，按变更风险和开发环境选择验证。正式发布默认执行 `bash scripts/release.sh <version>`。Windows 优先在本机做受影响的开发回归；macOS 或本机条件不足时使用远端 Windows。正式 EXE 始终由 Actions 构建一次，无需用户切换电脑。

`--no-push` **不是 dry-run 或“只提交”模式**：它在 Windows 本地构建制品，并可能产生版本提交。只提交推送的请求应单独更新版本文件、运行相关检查、commit/push。

- 核心 CI 验证插件、服务端、Windows PowerShell/事务/进程/进度等快速检查、两平台 Codex 兼容及发布守卫。日常 CI 不编译 Slint/Skia；不能将其成功当作原生构建或安装验收。成功证据必须对应实际发布提交，修复后只重跑受影响检查。
- Windows 标准流程复用核心 CI，构建一批制品，Rust 单测共用该次 release 编译。保留最终 EXE/DLL、安装器载荷和大小、窗口启动、全新 Python 3.13/OCR、安装和自更新检查；选中的生命周期在 Actions 执行一次，本机只补历史升级，不重复整套安装流程。
- 发布入口按共享策略自动选测，上一正式版升级为基线；数据或依赖改动追加历史兼容，安装和目录布局改动保留重装、迁移与回滚。普通补丁不枚举所有旧版，新增未分类服务端模块保守扩大验证。验证层级与证据路径以 [Actions 测试规范](../../../docs/actions-testing.md) 为准；`--full-validation` 只扩大范围，本机条件不足时加 `--remote-validation`，不能借此绕过产品失败。
- 发布前核验冻结计划、所需场景及最终制品摘要。复用测试结果须匹配八项制品哈希、完整计划、验收代码和原 run/attempt，不能只凭同一提交或步骤成功状态。缺少新标准回执时复用原包补验，不伪造回执。测试须隔离并清理进程；失败时保留恢复记录，从对应阶段继续。
- 入口或模块布局变更时，核对 CLI 元数据、打包模块、安装验收和对应分发目标。制品身份、大小、哈希与更新清单由现有守卫核验；不重复叠加人工下载全检。
- 本地检查挂起或环境缺失时，区分“未完成”与“通过”，记录具体原因；在同一提交的相应 CI 通过后才能替代该检查。macOS 临时目录别名导致断言差异时先确认原因，可用解析后的临时目录复测，不删断言掩盖问题。

发布顺序：**核心 CI → Windows 已验证制品 → PyPI 公开包可用 → GitHub Release/更新清单 → Homebrew**。

PyPI 使用现有 Trusted Publishing：仓库 `study-233/zotero-pdf2zh-pro`、workflow `publish-pypi.yml`、environment `pypi`；`id-token: write` 仅在发布 job。所有发布入口（包括仅 PyPI）必须传成功验证的 `build_run_id`，复用原包，不临时重打包发布。Windows 安装器依赖精确 PyPI 版本，公开 wheel/sdist 尚未验证可用时不能先分发安装器。

GitHub 正式附件共五个：`zotero-pdf2zh-pro.xpi`、`update.json`、`zotero-pdf2zh-pro-windows-x64-setup.exe`、`zotero-pdf2zh-pro-windows-x64.zip`、`windows-update.json`。安装 EXE 用于首次安装或手动升级，ZIP 用于自动更新。保留稳定更新地址。源码以匹配的公开 tag 为准；对应源码包及 `checksums.json` 保留在构建记录。不要创建 friends bundle，或在正文链接未实际上传的校验/源码附件。

Homebrew tap 为 `study-233/homebrew-formula`，文件为 `Formula/zotero-pdf2zh-pro.rb`；source-only、公开 HTTPS 源、固定 `python@3.13`、git revision。正常升版更新 version/revision，保留配方其他设置；推送 tap `main` 并等待该提交的 `formula-checks.yml`。不引入 bottles、tarball SHA 或个人绝对路径。

## 中断与交付

API EOF、下载无进展、PyPI 可见性延迟、制品复用或同版本替换，读 [发布恢复](references/recovery.md)。不能把本机观察失败等同于远端构建失败；不得直接重跑整个发布脚本造成重复构建或覆盖。

最终核对各渠道版本、tag 对应提交、正式附件与更新清单以及 tap 检查。报告以版本和 Release 下载入口开头，简述 PyPI/Homebrew 状态、重要限制和未完成项；只有所有要求的渠道完成才说“已发布完成”。

默认不输出完整 commit SHA、SHA-256 表、逐条命令和 CI 日志。定位代码确有必要时用短提交链接；完整值保留在机器记录中。已完成的检查用一句话概括，标准验证通过不等于扩展回滚/迁移或所有真实电脑均已验收。
