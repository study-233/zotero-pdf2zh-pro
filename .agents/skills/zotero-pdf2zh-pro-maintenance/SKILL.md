---
name: zotero-pdf2zh-pro-maintenance
description: 整理 zotero-pdf2zh-pro 工作区和文档，处理本仓库 PR、CI 失败和 GitHub 安全或质量告警。
---

# 项目维护

在包含 `plugin/` 和 `server/` 的工作树核对远端、分支和已有改动。先读[仓库约定](../../../CLAUDE.md)。

| 任务 | 做法 |
| --- | --- |
| 工作区整理 | 用 `python scripts/workspace.py check` 查找散落文件，`clean` 预览过期项。查引用后将仍有价值的报告和备份移入本地归档，记下原路径。保留运行数据、测评输入和恢复资料。见[目录规则](../../../docs/development-notes.md#development-outputs)。 |
| README 与官网 | 以 GitHub 最新正式 Release 和可下载制品为准。新功能先记入 CHANGELOG 的「未发布」。教程编辑 MDX，再生成 Markdown。见[维护规范](../../../docs/readme-maintenance.md)。 |
| PR、审查与 CI | 读取[PR 流程](references/pull-requests.md)，核对 head、失败步骤和关联讨论。 |
| 安全与质量告警 | 读取[告警流程](references/security-quality.md)。批量任务覆盖完整分页，按根因分组并保留每项编号。 |

根据改动选择[项目检查](../../../docs/actions-testing.md)。插件运行构建、lint 和测试。托管引擎涉及 `scripts/test_managed_engine.py` 和 CI 的 `windows-xpi-engine` 实机检查。共享诊断改动要重新生成契约并验证冻结的兼容基线。

只分析时交付证据和建议。修复时记录实际验证结果。提交、评论、合并及关闭告警按本次授权执行，远端结果须对应当前提交。
