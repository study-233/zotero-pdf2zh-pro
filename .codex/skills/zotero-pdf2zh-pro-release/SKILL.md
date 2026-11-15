---
name: zotero-pdf2zh-pro-release
description: 准备、发布或恢复 zotero-pdf2zh-pro 正式版，同步 README 与官网，处理发布验证和 Release 文案。
---

# 正式发布

只提交或验证修复时，按要求提交、推送并核对对应提交的检查。正式发布按以下步骤执行。

1. 在主仓库的干净 `main` 核对 origin、最新正式 Release、PyPI 和已准备的版本。小版本通常递增补丁号。审阅上次正式版以来的改动，写入 CHANGELOG 的 `## v<版本> - YYYY-MM-DD`。见[文案规范](references/release-notes.md)。
2. 安装方式或用户操作有变化时，在独立文档 PR 准备对应版本的 README、官网教程和截图，更新 README 的版本标记并同步 MDX 汇总。官网用真实截图呈现界面，文字写操作、命令和结果。
3. 执行 `bash scripts/release.sh <版本>`。有文档 PR 时传 `--docs-pr <编号>`。Windows 优先本机构建，macOS 使用远端 Windows。按[Actions 测试规范](../../../docs/actions-testing.md)验证同一提交、冻结计划和同一批制品，托管引擎核对 `windows-xpi-engine`。在 Windows 上，`--no-push` 会构建制品，也可能产生本地提交。
4. 核对 PyPI、GitHub 正式附件和 Homebrew 检查。XPI 与 Windows 安装器公开后更新主分支 README 和官网，确认 Pages 部署成功，核对线上版本、下载入口与安装步骤。
5. 中断后先查原有 run、制品与发布状态，按[恢复流程](references/recovery.md)续接。若只剩公开文档，使用文档发布入口单独重试。同版本替换需明确授权并保留原包备份。

GitHub 正式附件为 XPI、`update.json`、Windows 安装 EXE、自动更新 ZIP 和 `windows-update.json`。PyPI 使用验证过的 wheel/sdist，Homebrew 使用相同源提交、`python@3.13` 和 source-only 配方。交付时给出 Release、官网及未完成项。
