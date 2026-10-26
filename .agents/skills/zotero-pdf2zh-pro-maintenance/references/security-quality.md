# 安全与质量处理流程

针对 Security and quality 页面、指定告警、Dependabot 安全 PR 或质量检查结果使用。先识别实际来源：Code scanning、Dependabot、Secret scanning、Code quality 标准/AI finding，以及 PR 上相关检查。Actions 的 `Security` 工作流只是其中一种执行入口。

## 访问、分页与范围

优先使用可用的 GitHub 连接器能力；告警能力不足时，用现有 `gh` 登录身份读取 GitHub API。不要打印 token、凭据环境变量、认证头或密钥值。

分别记录每个来源为：已读取且完整、已读取但不完整、确认未启用、权限不足或状态未知。只有完整成功读取才可报告“0 条”。401/403/404 不等于空列表；404 也可能隐藏权限或功能限制，不能单凭它认定未启用。429/5xx 可按服务端等待提示有限重试；同样失败后保留范围缺口，继续不受影响的工作。

以下为只读 PowerShell 查询模板；每条分别检查退出码，`--paginate` 跟随下一页。若过滤器、API 版本或返回字段变动，先查官方文档再适配，不凭空构造端点。

```powershell
$maintenanceRepo = 'study-233/zotero-pdf2zh-pro'
gh api --paginate -X GET "repos/$maintenanceRepo/code-scanning/alerts" -f state=open -f per_page=100 --jq '.[] | {number, html_url, state, rule: .rule.id, severity: .rule.security_severity_level, instance: .most_recent_instance}'
gh api --paginate -X GET "repos/$maintenanceRepo/dependabot/alerts" -f state=open -f per_page=100 --jq '.[] | {number, html_url, state, dependency, vulnerability: .security_vulnerability, advisory: .security_advisory.ghsa_id}'
gh api --paginate -X GET "repos/$maintenanceRepo/secret-scanning/alerts" -f state=open -f per_page=100 --jq '.[] | {number, html_url, state, secret_type_display_name, validity}'
gh api --paginate -X GET "repos/$maintenanceRepo/code-quality/findings" -H 'X-GitHub-Api-Version: 2026-03-10' -f state=open -f per_page=100 --jq '.[] | {number, url, state, rule, location, message}'
```

列表用于分流，处理单项前读取其规则/公告、位置与上下文。密钥详情与 locations 也必须在命令输出之前通过白名单字段脱敏，不能先输出完整 JSON 再删字段；日志和截图同样不能泄露密钥。

Code scanning 应核对目标 `ref`、instance 的 commit SHA、位置和所有相关数据流；默认分支与 PR 分支可能状态不同。必要时查询指定告警的 instances。Dependabot 按依赖清单和默认分支依赖图核验。Code quality 标准项和 AI 项分别核对覆盖范围：不能未经确认就假设一个 findings 端点涵盖所有 AI 结果；缺少接口时使用获授权的页面读取或用户提供的脱敏结果，并报告缺口。

接口说明：[Code scanning](https://docs.github.com/en/rest/code-scanning/code-scanning)、[Dependabot](https://docs.github.com/en/rest/dependabot/alerts)、[Secret scanning](https://docs.github.com/en/rest/secret-scanning/secret-scanning)、[Code quality](https://docs.github.com/en/rest/code-quality/code-quality)。使用这些接口不意味着本仓库已启用每种功能。

## 判断根因与修复

### Code scanning / CodeQL

追踪规则、用户输入来源、危险操作、清洗/校验和真实调用路径；检查所有报告路径，不能只改告警行。区分真实可达风险、已有防护、测试夹具、生成文件和过期分支，分类必须有代码证据。生成代码的问题优先修改生成源。修复真实漏洞并加入能证明边界的回归用例；误报给出可复核理由，不自动 dismiss 或加 ignore。

### Dependabot / 依赖审查

- 读取上游安全公告与修复版本范围，检查锁文件、直接/传递依赖、运行/开发用途和实际打包结果；开发依赖同样可能影响构建安全。把一个公告在多个清单里的实例关联起来，逐个验证。
- 选择满足安全修复且兼容的最小必要升级，优先兼容的父依赖更新。必要 override 要解释适用范围与兼容依据；用对应包管理器更新锁文件，不手改哈希或运行全量 `up --latest`。
- 按当前 [.github/dependabot.yml](../../../../.github/dependabot.yml) 覆盖插件 npm、服务端 uv、官网 pip、两个 Cargo 工程与 Actions。保留安全更新分组和关闭普通版本更新的策略；现有文档约定不自动合并。
- 服务端依赖改动联查 uv、Windows、Docker、Homebrew。按 [CLAUDE.md](../../../../CLAUDE.md) 保留当前 Homebrew Python 3.13 约束，验证 Docker 的冻结 Python 3.12 运行时与 OCR；需要远端执行时，先检查当前 `Security` workflow 的 `validate_container` 输入、目标 ref 和用户授权。
- 无修复版本时评估有证据的缓解或替代，明确仍存在的风险；不能把“上游未修复”或“已接受风险”写成漏洞已修复。

### Secret scanning

只记录脱敏类型、位置、告警编号及有效性状态。先确定凭据是否已撤销/轮换；删掉源码中的值不能使历史暴露失效，也不能仅凭本地测试关闭告警。可以修复凭据加载方式、移除暴露入口和增加合成检测用例。实际撤销/轮换、历史重写与告警关闭按明确授权执行；没有提供商访问能力时列出具体待办，不假称已撤销。不向公开 PR/评论复制密钥或敏感载荷。

### Code quality / AI finding

读取规则、类别、严重程度、具体位置及当前代码，判断正确性、可靠性或维护性问题是否成立。自动建议与 AI autofix 必须审阅并测试；不为提高分数重写无关架构。误报、已修复的过期项和风格偏好分别说明依据；用户只要求分析时不改源码。相关质量项与 CodeQL/PR 同根因时共享修复，但保留各来源的结果。

## 重新扫描与确认

遵循当前 [Security workflow](../../../../.github/workflows/security.yml) 与 [安全维护约定](../../../../docs/development-notes.md#安全检查与依赖维护)：CodeQL 覆盖 Python、JS/TS、Actions；Rust 由 Dependabot 和 Windows 验证覆盖；PR dependency review 同时检查运行与开发依赖，新高危/严重漏洞阻断；日常扫描不自动构建镜像。发现配置已变化时以仓库实际文件为准。

1. 本地先证明修复行为与兼容性；CodeQL 未在本地执行时如实记录，不能用单测替代扫描。
2. 获准推送/建 PR 后，核对当前提交对应的 CI、dependency review 与扫描结果。依赖审查通过只证明它检查的新增依赖问题，不代表整个仓库没有既有漏洞。
3. 获准合并后，等待目标分支的相关扫描或依赖图更新，再查询每个关联告警/质量项。核对分析提交、扫描配置和完成时间；确认问题消失不是因文件被排除或扫描停用。
4. Code scanning/Dependabot 可按实际 `fixed` 状态核验；其他来源以其真实状态模型和新扫描证据判断，不能给所有 API 假定存在 `fixed` 字段。质量项从列表消失也要核对扫描范围，不能仅凭空列表断言修复。
5. 扫描仍运行、队列待执行、权限缺失或验证超时，标记“待远端确认”并保留对应 run/告警链接。需要后续观察时继续当前任务或报告待办；只有用户要求定时跟进时才创建自动化。

最终报告包括来源/ID、风险和根因、处理决定、修改、验证层级与剩余动作。不能靠批量关闭、忽略、修改严重级别或禁用功能实现“清零”。
