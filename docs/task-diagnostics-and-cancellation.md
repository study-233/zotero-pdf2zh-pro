# 任务解析、取消与诊断规格

本规格定义文档级字体缓存、任务执行生命周期和诊断导出行为。接口保持旧任务状态兼容。

## 解析与进展

同一 `ILCreater` 实例按字体对象编号缓存成功解析的字形边界和字符映射，使用只读结果；不同文档、重试不共享缓存。对象别名、坐标变换和图形依然分别解释，不删除图形、不栅格化论文。Type3、TrueType、CID 和基本字体的字形遍历在每个字形前检查取消，临时页面始终在 finally 清理。页内解释最多每 256 个指令检查取消，资源和内容流入口也检查。

页开始、页结束通过可靠控制通道传递；页内进展最多每秒一次，跨通道序号防止旧事件覆盖新页码。`currentPage` 为实际 PDF 页码，`selectedPage` 为选中页序号，`completedPages` 为完成数，`totalPages` 为选中总数。现有阶段百分比继续保留。`lastProgressAt`、`heartbeatAt`、`lastRequestAt` 分开维护，心跳和重复进度刷新不代表进展；阶段耗时独立计时。

连续 60 秒没有实际进展时提示但不自动结束任务。每次停顿首次记录快照，此后每 300 秒最多一次，每次尝试最多 3 次。进展恢复后取消停顿提示。原生代码独占解释器时，实时 Python 堆栈可能无法采集；导出返回最后可用快照和缺失说明。

## 执行、回收和持久化

主服务维持串行任务队列，每次尝试由 spawn 子进程执行。Windows 使用 kill-on-close Job Object，POSIX 使用独立进程组。子进程在回收边界建立之前等待父进程放行，HTTP 服务和任务管理器不会在 spawn 重入时初始化。

子进程负责配置、字体、PDF 解析、模型调用、结果生成和清理。凭据通过进程内存传递，不放入命令行、lease 或诊断日志。低优先级事件使用有界缓冲及独立通道；页边界、结果、错误使用控制通道，主监督循环不直接阻塞读取子进程管道。事件携带服务实例、任务 ID 和尝试编号，过期事件不修改任务状态。

用户取消排队任务时立即标记 cancelled。运行任务收到取消后设置共享事件，并按单调时钟执行：

| 取消后时间 | 动作 |
|---|---|
| 0 秒 | 受理、协作取消、申请诊断快照 |
| 10 秒 | 终止任务进程树 |
| 12 秒 | 升级强制结束 |
| 15 秒 | 退出确认或明确回收失败 |

确认进程树退出后才释放队列和文件所有权。`cancelPhase` 为 requested/terminating/killing/cancelled/cleanup_failed，`cancelReason` 区分 user/forced/shutdown。操作系统拒绝回收时标记 failed，错误码 `CANCEL_CLEANUP_FAILED`，暂停队列并提示恢复服务。

取消受理后的成功消息不能发布结果；只有任务进程退出后才提交结果给插件。强制结束只保证保留已提交的 SQLite 检查点，不导入半成品 PDF。服务关闭有界等待，重启中断沿用 incomplete/补译语义。lease 记录 PID、进程创建时间和父进程身份；恢复时不触碰身份不匹配或仍由其他活服务管理的进程。

## 诊断数据与 API

始终启用独立结构化诊断记录，不直接导出原始控制台日志或任务数据库。

- 服务诊断目录：有效数据根目录下的 `diagnostics/`。
- 服务日志 5 MiB/份、2 份备份；任务每次尝试 2 MiB/份、1 份备份。
- 保留 7 天，总量上限 100 MiB，优先清理非活跃任务；活跃文件自身通过轮转控制大小。
- 资源采样间隔 5 秒，记录主服务和任务进程 CPU/RSS、停顿时长及请求数字摘要。
- 插件在 Zotero 数据目录保存诊断 JSONL，2 MiB/份、1 份备份；写入排队有界；服务日志由后台线程落盘，磁盘延迟不阻塞任务取消。
- 白名单记录状态、页码、操作、请求类别、状态码、耗时、Token、错误类别和源码调用栈。排除异常消息、源代码行、局部变量、PDF 名称/内容、提示词、响应正文、密钥、接口地址和个人目录。

### 配置与结果证据

- `requestedConfiguration` 来自该任务保存的提交参数，包含服务类型、显式模型、协议、QPS、并发及语言；不读取导出时的当前偏好设置。模型未显式填写时不猜测默认值。
- `effectiveConfiguration` 来自翻译子进程完成初始化后的实际 translator 和 BabelDOC 配置，包含解析后的模型、已确定的协议和实际 QPS/并发。服务商预设可能调整参数，因此两组配置单独保留；初始化失败或旧任务未记录时明确显示缺失。
- `provider` 是服务类型/预设（如 `deepseek`、`openaicompatible`），不是从接口地址推断的实际运营商；自定义中转站仍不导出其地址。模型标识支持 `org/model:tag`，限制长度并过滤 URL、绝对路径和常见凭据形式。
- 每轮尝试以 `task_configuration` 记录配置，实际配置随任务快照持久化。补译清除上轮实际配置，记录新一轮的 QPS/并发，保留历史轮次的日志。
- 任务快照保留 `translationSummary`、`qualitySummary`、请求分类统计、HTTP 状态码/错误类型计数及 Token 统计。`request_summary.requests.attempts` 是请求次数，`request_summary.tokens.total` 是 Token 总数，两者不再通过同名字段合并。
- `failedParagraphs` 仅保留页码、尝试次数、错误类型、HTTP 状态码、白名单 `providerCode`（如 `insufficient_quota`、`rate_limit_exceeded`）和 `validationCode`（如 `placeholder_mismatch`、`unchanged_translation`、`target_language_missing`）。校验码从 `InvalidTranslation` 的原因链首项提取并严格匹配白名单；不导出段落正文、哈希标识和自由文本原因。摘要按校验码统计已记录的失败段落。明细最多 2000 项，超限设置 `failedParagraphsTruncated`，完整数量仍见段落统计。旧诊断包中已丢弃的具体原因无法恢复；应从来源电脑的任务详情 `GET /tasks/{taskId}` 查看 `failedParagraphs.reason`，更新服务和插件后重新导出。
- 摘要展示任务配置、翻译完成情况和 429/超时数量；明确 12/12 页处理完成或 PDF 已导入不等于所有段落翻译成功。429 本身不用于断言具体是频率限制还是额度问题。
- 插件保存两组任务配置供离线导出使用；在线导出优先采用服务端最新任务快照。插件磁盘与内存中的相同记录去重后按时间排序、限量保留。高频状态日志不重复写入完整请求统计和失败段落明细。

以上均为 `schemaVersion=1` 的可选字段扩展。旧服务和离线任务仍可导出，缺失信息不会用当前配置或零值补造。旧 ZIP 中已丢弃的配置无法追溯恢复。

### 任务、请求与段落证据链

`schemaRevision=2` 增加 `evidence` 和 `analysis` 可选字段。在线 ZIP 将它们分别保存为 `evidence.json`、`analysis.json`，不在 `server.json` 重复存储；摘要只展示已登记规则的解释与建议。

- 每个翻译工作进程生成独立 `executionId`，同时记录任务 ID、任务尝试编号、提交配置、生效配置和该次执行的统计。继续执行仍可沿用任务尝试编号，但执行编号始终不同；历史执行不会与当前执行混为一轮请求。
- 段落使用随机 `paragraphRef`，随原有恢复检查点保存；旧检查点首次登记时补充编号，保留成功译文及原有结构版本。任务重启、补译和检查点复用沿用这个编号。不导出原段落 debug ID、正文哈希或原文。
- 每次真实模型调用有独立 `callId`，记录所属执行、操作 `operationId`、用途、相关段落、状态、HTTP 状态码、受控服务商错误码、耗时及可用的 Token 统计。并发请求不会依赖时间戳区分。初始化回放标记 `observedReplay`，不冒充原始调用时刻。
- 请求通过 `parentCallId` 和 `retryCause` 关联服务商重试、鉴权刷新、校验重试、批量部分修复和单段回退。批量请求关联输入中的各段；回退只关联需要修复的段落。`validationState` 和逐段 `validationFailures` 保留每次输出的校验结果，即使后续成功也能追溯此前失败。
- 请求完成和译文有效分开记录：HTTP 200 仍可能对应占位符、语言或结构校验失败。`InvalidTranslation` 不自动推断为服务商故障；429 不单凭状态码推断频率或额度。
- 缓存和检查点复用记录为段落来源 `cache` / `checkpoint`，不虚构模型请求；跳过段落标记 `preserved`，未完成段落标记 `unprocessed`。旧任务只有检查点结果时可导出 `checkpoint_snapshot`，但不补造执行或调用历史。
- 任务终止、取消或服务恢复后，未确认完成的调用标记 `interrupted`，不当作已成功或已失败。缺失父调用、失败段落未关联请求、未启用请求跟踪、容量淘汰或写入丢失均明确标注 `evidenceStatus` / `evidenceCode`。

证据写入任务工作目录的独立 `diagnostic-evidence.sqlite3`，不依赖日志尾部；删除任务时随工作目录清理。后台队列最多 512 条，收尾最多等待 1 秒；磁盘故障和队列溢出不会阻塞翻译线程写盘。单任务保留最多 32 次执行、12000 条记录、8 MiB JSON 记录载荷；SQLite 页与 WAL 有额外开销。优先保留失败/待译段落、失败或被拒绝的调用，执行统计独立保留。段落关联列表最多 16 条，超限计入 `omittedLinks`。这是一份有界证据，不承诺保留无限历史。

单次服务响应中图记录合计最多约 2 MiB，全局接口仅取最近 3 个任务的图；任务接口导出指定任务。`omittedTasks`、`omittedExecutions`、`omittedRequests`、`omittedParagraphs` 与写入健康计数说明缺失范围。请求/段落明细被截断时，执行统计不改写成剩余记录数。父进程保存的最终写入健康信息也会合并导出，防止丢失的写入尾部被误报为完整。

可在拿到其他用户 ZIP 后离线分析，不需要连接来源电脑或调用模型：在服务端环境运行 `python -m diagnostic_evidence /path/to/bundle.zip`，附加 `--text` 输出登记过的解释与建议；源码环境可用 `uv run --directory server --locked python -m diagnostic_evidence /path/to/bundle.zip --text`。工具只读指定 ZIP 的诊断 JSON，不解压文件、不执行附件内容。旧 ZIP 缺少证据时返回 `evidence_unavailable`；已经被旧导出器过滤的历史无法恢复。

### 共享规范和版本兼容

诊断字段、枚举、错误码、事件和功能覆盖声明统一维护在
[`shared/diagnostics/contract.json`](../shared/diagnostics/contract.json)。生成器输出服务端轻量模块和插件 TypeScript 定义；运行时不下载规则，也不依赖源码目录中的 JSON。
服务端与插件继续各自执行脱敏，规则来源相同。原有 v1 核心字段和枚举由
[`compatibility-v1.json`](../shared/diagnostics/compatibility-v1.json) 固定保护，不修改这份基线来绕过兼容检查。

- `schemaVersion` 表示结构兼容性，目前仍为 1；`schemaRevision` 表示同一结构的可选扩展，独立于插件和服务版本。同一主版本的更新修订保留已知字段，过滤未知字段并记录数量，不导出未知字段名称或值。
- `GET /health` 的 `diagnostics` 声明 `schemaVersions`、`schemaRevision` 和实际支持的诊断特性。诊断接口接受可选查询 `schemaVersions=2,1`，选择双方都支持的最高主版本。无参数维持原 v1 行为；无共同版本返回 406，参数无效返回 400。
- 新插件请求 `schemaVersions=1`；旧服务会忽略这个可选参数，仍能采集。新服务继续返回 v1，旧插件可读取原字段；旧插件未登记的新字段仍可能被其旧白名单过滤，完整证据需要两端更新。
- 校验异常在产生时携带 `validationCode`，占位符问题可附缺失/额外数量；旧检查点仍可从原因链首项提取已登记代码。已有结构化代码优先于旧原因字符串。未知校验错误记为 `unknown_validation`，不把异常原文当作错误码。
- 普通警告保留源码文件名、函数名和行号；已结构化的警告附安全错误码，否则标记 `unknown_warning`。异常消息、源代码行、局部变量不进入诊断。

### 采集清单与容量证据

ZIP 增加 `manifest.json`，保留原 `summary.txt`、`task.json`、`server.json`、`plugin.json`。
清单记录导出方版本、生成时间、服务端结构版本/修订、服务版本、各来源可用状态和固定原因码。
网络超时、服务不可达、HTTP 错误、接口缺失、格式不支持、无效响应、容量限制和整体采集期限分别记录；任一来源失败仍导出可用插件快照。
旧服务只剩 `InvalidTranslation` 时，清单和摘要明确标注缺少具体校验码，不推断实际校验原因。

服务端 `collection` 记录写入丢弃、写入失败、超大记录、内存淘汰、过滤字段和无效值数量；插件清单另记 `droppedWrites`、`writeFailures`、`memoryEvictions` 等计数。
内存淘汰不等于磁盘记录丢失。轮转/导出截断继续通过 `truncated` 标注。计数范围是相应组件当前进程或采集过程，不冒充任务所有历史记录的精确丢失总数。

### 新功能接入与发布检查

1. 新字段在共享规范中声明类型及脱敏类别；新错误登记阶段、重试建议、必要证据和处理建议。可选扩展增加 `schemaRevision`，破坏性改动须先实现格式适配器和兼容测试。
2. 新诊断事件登记 `events`，动态事件登记受控 `eventPatterns`。新增服务能力必须在 `productCapabilities` 声明诊断覆盖；`availability_only` 明确表示尚无专用证据采集，不代表已实现该功能的完整诊断。
3. 运行 `python3 scripts/generate_diagnostic_contract.py`，将共享定义和生成文件一并提交。不要手工编辑生成文件。
4. 新增合成故障样例，验证关键证据经过服务存储/导出、插件过滤和最终 ZIP 后仍存在，并检查隐私字段始终被排除。
5. CI 检查生成结果、已发布 v1 基线、未登记的校验异常、事件、直接声明的诊断字段和服务能力。统一发布入口也执行规范检查。动态组装的业务数据仍需要专门的故障测试，静态检查不能自动证明所有新功能已经覆盖。
6. 新的模型调用路径须接入统一请求收集器，传递段落编号及父调用/操作上下文；新增证据字段登记 `evidenceFields`，关联编号登记 `opaqueIdentifiers` / `identifierLists`，分析规则登记 `diagnosisRules` 与 `findingCode`。不要在专用旁路中另写一套请求编号或脱敏规则。缓存路径必须记录复用来源。

验证入口：`python3 scripts/generate_diagnostic_contract.py --check`、
`python3 -m unittest discover -s scripts -p 'test_diagnostic_contract.py'`、服务端 `test_diagnostics` 和插件 `diagnostics.test.mjs` / `diagnosticRegistration.test.mjs`。
合成四段失败样例位于 [`shared/diagnostics/fixtures/validation-failures.json`](../shared/diagnostics/fixtures/validation-failures.json)，不包含用户论文或真实响应内容。
证据链测试为服务端 `test_diagnostic_evidence` / `test_diagnostic_evidence_integration`，覆盖原生 OpenAI、ChatGPT 鉴权刷新、Codex 失败、批量回退、旧检查点、取消、容量和离线 ZIP；纯标准库存储与分析测试还进入 macOS/Windows 原生 CI。插件集成测试通过真实 Python 存储/导出生成 ZIP，并检查四段失败对应的 9 次调用和逐段校验码。

| 接口 | 返回 |
|---|---|
| `GET /diagnostics` | 全局诊断快照 |
| `GET /tasks/{taskId}/diagnostics` | 当前及历史尝试记录；任务不存在返回 404 |
| `GET /health` | 增加 diagnosticsExport/boundedCancellation/detailedTaskProgress 能力标志和 queueBlocked |

诊断响应 `schemaVersion=1`，包含 environment、records、tasks、truncated、queueBlocked、limitations 及可选 evidence / analysis。服务响应以 10 MiB 为预算，优先保留任务和图、再保留日志尾部；全局任务快照最多 2000 项，容量不足时减少旧任务快照。导出请求对堆栈采集等待最多 1.1 秒。记录时间使用 UTC ISO 时间，耗时判断使用单调时钟。

插件任务卡片与设置页均可导出 ZIP，原生对话框选择保存位置。ZIP 包含 manifest.json、summary.txt、task.json、server.json、plugin.json、evidence.json、analysis.json。命名只使用时间和安全任务 ID，采集整体最多等待 10 秒，未压缩包总量不超过 20 MiB。服务不可用或旧接口缺失时导出部分证据，摘要明确缺失项。用户取消保存不创建文件，保存失败清理临时文件。新插件不会声称旧服务具有限时回收能力。

## 验收与证据

自动化覆盖字体缓存调用次数、实例隔离、只读结果、带字体别名和坐标变换的图形解析结果对照、Type3 取消清理、进程退出、协作与强制取消（含真实 10/12/15 秒期限）、启动超时、下一任务继续、派生进程、SQLite 未提交事务回滚、回收失败、旧尝试事件、心跳与进展分离、接口脱敏、日志轮转以及 ZIP CRC/中文内容/离线导出。

本地 OmniDocLayout 回归：2026-09-28，源码 Python 环境，11 页解析两次分别为 5.44 秒、5.53 秒，字体对象 217 仅昂贵解析 1 次，全部字体昂贵解析 64 次，缓存命中 15,256 次。解析过程未调用模型 API；用户论文不提交到仓库。该结果只证明解析阶段，不能替代完整翻译、输出排版和安装包验收。

人工验收清单：

- [ ] macOS Zotero 任务/设置入口均可保存并打开诊断 ZIP；离线和旧服务提示正确。
- [ ] Windows 安装版本验证 spawn、Job Object、原生保存对话框和中文路径。
- [ ] 原论文完整翻译与导入，检查第 4 页散点、图表、文字和公式位置。
- [ ] 实机在初始化、请求和输出阶段取消，确认后续任务继续且无派生进程。
- [ ] 强制取消后补译，保留已提交段落和既有附件批注。

本次源码变更不自动部署、升级服务或发布远端制品。

合成诊断包示例见 `examples/diagnostics-example.zip`，仅使用虚构任务数据，不包含真实论文或用户信息。

### 最终本地验证（2026-09-28）

- 服务端 275 项测试、插件 122 项测试全部通过。
- TypeScript 类型检查、改动 TypeScript 文件 ESLint、最终 diff 空白检查通过。
- XPI 与 Python wheel 均构建成功，检查 wheel 中包含诊断模块、独立执行模块与轻量启动入口，未打包用户 PDF 或任务数据库。
- spawn 启动到退出的空任务开销（三次）：0.102 / 0.093 / 0.092 秒；包含翻译模块导入：1.242 / 1.218 / 1.266 秒。此测量不含字体准备、解析和模型请求。
- 测试使用隔离的翻译缓存与规范化临时目录，不修改生产服务缓存。Windows、Zotero 原生界面和完整翻译仍按上方清单验收。
