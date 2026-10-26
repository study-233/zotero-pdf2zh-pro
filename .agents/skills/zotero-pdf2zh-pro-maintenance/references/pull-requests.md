# PR 处理流程

处理指定 PR、审查意见、CI 失败或批量 PR 时读取本文件。依赖漏洞关联的 PR 同时读取 [安全与质量流程](security-quality.md)。

## 收集完整上下文

从 PR URL 或已确认的仓库与编号获取：标题/描述、作者、状态、draft、base/head 仓库与 ref/SHA、变更文件与补丁、讨论、review、未解决线程、检查和可合并状态。指定 PR 即使已关闭或合并也先读状态，不擅自重开。把当前 head/base 作为本次分析基线，操作前刷新；`mergeable` 为 unknown 时表示尚待计算。

连接器优先使用 PR 详情、完整文件/patch、review threads、reviews、评论、Actions run/job/log 等能力。列举打开的 PR 时使用显式 `is:pr is:open` 查询，不能把空搜索串返回的空结果当作完整清单。留意工具说明：搜索数量上限不表示全量；某些 workflow 工具只返回 PR 事件或第一页，jobs 可能只含最新 attempt。检查分页、截断和遗漏；大型/二进制 patch 需要取正确提交的文件或本地 diff，不能只审查可见片段。

连接器不足时，在 PowerShell 使用只读 CLI。例如先确认 `$maintenanceRepo` 与 `$maintenancePr` 来自实际目标，再运行：

```powershell
$maintenanceRepo = 'study-233/zotero-pdf2zh-pro'
# 将编号设为用户指定或查询所得的真实 PR，以下变量不预设示例编号。
gh pr view $maintenancePr --repo $maintenanceRepo --json number,url,state,isDraft,headRefName,headRefOid,baseRefName,title,body,statusCheckRollup
gh api --paginate -X GET "repos/$maintenanceRepo/pulls" -f state=open -f per_page=100 --jq '.[] | {number, html_url, head: .head.sha, base: .base.sha}'
gh api --paginate -X GET "repos/$maintenanceRepo/pulls/$maintenancePr/files" -f per_page=100
gh api --paginate -X GET "repos/$maintenanceRepo/pulls/$maintenancePr/reviews" -f per_page=100
gh api --paginate -X GET "repos/$maintenanceRepo/pulls/$maintenancePr/comments" -f per_page=100
gh api --paginate -X GET "repos/$maintenanceRepo/issues/$maintenancePr/comments" -f per_page=100
```

REST review comments 不包含完整线程解决状态；需要连接器的 review-thread 能力或分页 GraphQL `reviewThreads`，同时处理线程内评论分页。没有这些信息时标为未核验，不用评论正文猜测已解决。CI 同时检查 Checks API 和 commit statuses；只有 combined status 不足以证明全部检查通过。远端接口细节以运行时工具说明和官方文档为准。

## 定位与修改

1. 按 diff 跟踪被调用代码、测试和依赖，区分本 PR 新引入的问题、既有问题和无关建议。审查意见逐项给出采纳、已有修复、误报或待确认的证据；机器人建议不直接照单执行。
2. CI 失败读取对应 run、attempt、job 和失败 step 的实际日志，区分代码错误、锁文件不一致、环境缺失、网络、权限和被取消。对真实缺陷先建立可失败的回归用例，再修复并运行受影响检查。
3. 需要改 PR 时检出正确 head，保留 base/head 身份和无关本地改动。优先在现有 PR 分支修复；无法写入贡献者 fork 时保留本地修复，并按用户交付要求准备本仓库分支/后续 PR，不修改对方 ref 或假称已更新原 PR。
4. 冲突按双方意图逐处解决，不整文件覆盖。只在用户明确要求且实际需要时重写远端历史；常规修复用普通提交。新工作分支使用 `codex/`，不直接推送默认分支代替 PR 流程。
5. Dependabot PR 核对公告、受影响版本、锁文件的实际解析结果和各分发渠道；遵循安全流程中的分组、Python 兼容和容器验证约束。

## 远端验证与交付

- 只分析时到报告为止；修复请求完成本地修改与验证；明确要求提交推送或更新 PR 时继续至相应交付。公开评论和 review 按主文件的授权规则执行。
- 推送前后核对当前远端 head，若其他提交到达，重新审阅差异与受影响证据，不能将别人的提交覆盖掉。提交 PR 文案说明问题、修复后的行为和验证，不堆叠过程记录。
- 逐个关联最新检查的 SHA、事件、ref、run ID、attempt 和 conclusion。对于 PR merge ref 或 merge queue，验证其 head/base 组合仍对应当前 PR；旧 head 的绿灯与其他分支定时任务不可复用。
- 区分成功、失败、取消、等待、跳过、中性和不存在。先读仓库规则、workflow 触发条件及 required checks，判断跳过是否合理；没有运行不能标成功，路径过滤或缺权限造成的缺口需报告。
- 确认是暂时故障后，有限重跑失败 job；同一原因连续失败两次后停止盲目重跑并调查。代码问题必须修复；观察超时先查询原 run，不重复派发。使用适当等待并持续报告有意义进展。
- 只在明确授权合并且当前 head 的必要检查、review 与冲突要求满足后合并；合并操作绑定预期 head，竞争更新则重新核验，不绕过分支保护。
- 授权解决 review thread 时，先核对原意见已被对应代码与测试覆盖。修复已推送不意味着应自动 resolve，更不意味着可以关闭整个 PR。

交付列出每个 PR/线程的处理决定、涉及文件、验证与未完成项；没有发现问题时说明实际审查范围和验证限制。合并后需要验证安全告警时转到 [安全与质量流程](security-quality.md) 复查目标分支。
