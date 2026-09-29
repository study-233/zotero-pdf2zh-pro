# 插件 PDF 翻译测评

五个模型、三篇固定版本论文，通过插件真实 `/tasks` 链路测试。统一 QPS 10、并发 50，其余采用插件默认设置。预算 $10。
结果由三个 `gpt-6-sol` 子代理按论文匿名评审，主代理复核；不是人工评审。
不使用额外的收费评审 API。

## 运行

使用项目 Python 环境（`uv sync --directory server --locked`）。在仓库根目录运行：

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/bench.py prepare
server/.venv/Scripts/python.exe benchmarks/translation/prices.py
```

`prepare` 固定 PDF 版本、SHA-256、12 个评审锚点、commit 与环境。已有快照不覆盖。
`sample-anchors.json` 保存已选片段的页码、坐标和原文校验值，以便从同一 PDF 复现选段。
首轮执行开始后、查看译文前，曾仅依据原文补齐摘要与方法/图说明覆盖；时间和原因记录于
公开快照的 `selectionAmendment`，不宣称首轮所有选段都在收费调用前确定。
`prices.py` 保存官方完整价格页面、上下文档位及高峰费率；解析失败时停止，不猜价格。
只有核验价格后的快照允许收费运行。模型目录可用不等于当前 Key 有调用权限。

在独立终端启动测评服务，绝对路径按本机仓库位置替换：

```powershell
$env:PDF2ZH_TRANSLATION_CACHE_DIR = '<repo>/.local-dev/translation-bench/cache-pilot'
$env:PDF2ZH_USAGE_LEDGER_DIR = '<repo>/.local-dev/translation-bench/usage'
server/.venv/Scripts/python.exe server/service_launcher.py --host 127.0.0.1 --port 8891 --data-dir '<repo>/.local-dev/translation-bench/tasks'
```

将 Key 设置到运行终端的 `COMMAND_CODE_API_KEY` 环境变量，不写入命令文件或 Git。
`probe.py` 是明确的收费小请求，记录状态与 usage；重复运行跳过已有探测。

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/probe.py
server/.venv/Scripts/python.exe benchmarks/translation/bench.py pilot
```

试跑后停止服务，改用全新的 `cache-full` 缓存目录重启，再执行：

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/bench.py run
```

同一份本地 `runs.json` 用于恢复。提交前持久化意图，POST 结果不明时通过唯一文件名对账，
不盲目重发。终态任务不自动重跑，不修复后覆盖首轮成绩。只连接 workspace 路径匹配的独立服务。
`--paper attention` 可限制到单篇。新一轮实验应使用全新工作目录与缓存，不复用旧任务。

用户明确要求补齐未生成的 PDF 时，可在原工作目录执行：

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/bench.py run --supplement-missing-pdfs
```

该命令仅补跑已结束且没有输出 PDF 的整篇任务，使用相同配置和独立任务缓存，
要求已批准账号账单增量预算机制。沿用本轮账单基线和预算，不重置额度。
补跑保存在 `supplemental-runs.json`；恢复执行跳过已结束的补跑，不改写首轮成绩。
PDF 目录优先展示已产出的补跑文件，并标注补跑来源；独立 GPT-6 Sol 评审及主代理复核后方可更新评分。

每请求账本仅保存时间和 token，没有原文、译文、模型名或 Key。费用按上下文档位与 UTC
高峰时段估算；推理 token 已包含于输出，不重复计费。缓存信息未知时按未命中保守估算。
缺失 token 或与账本无法对账时费用保持未知并停止扩展。价格以模型响应计费时刻近似，
边界时段与供应商账单可能存在差异，缓存写入等未暴露项目不承诺精确账单一致。

运行器保留 $1 在途余量，并在每项前预留至少 $1 或历史最高单项费用三倍。
本地取消不能保证账单绝不超过 $10；供应商支持时应配置独立 Key 的硬限额。
401/403 明确鉴权拒绝不计入生成 token 估算，但这不等于供应商确认免费。
超时、成功响应缺失 usage 等情况保持费用未知，默认停止扩展。

本次用户另行批准：在缺失逐请求用量时，允许用账号账单增量作为保守预算占用继续。
本地快照的 `accountBudget` 保存批准状态及本轮开始前的账单基线；仅在明确批准后开启。
运行器每 30 秒读取账号汇总，并在提交下一项前刷新。同期其他活动或延迟入账的旧请求也计入
预算占用，因此这不是单个模型的实际账单。账单读取失败或周期重置会停止收费任务。
不能把这个增量分摊成精确模型费用，未知费用的结果不参加性价比图表。

## 评审

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/review.py packets --paper attention
```

每篇全部模型达到终态后生成匿名材料，独立随机映射仅存于本地 `private-mapping.json`。
给 GPT-6 Sol 子代理的上下文只有当前论文目录中的 `INSTRUCTIONS.md`、`packet.json` 和图片，
不要提供模型名单、费用或原聊天。每个子代理按 `review-instructions.md` 写 `review.json`。
自动段落匹配只是建议，评审者必须结合整页段落确认，不能将错位当成漏译。

主代理根据 `audit_selection()` 生成必查清单（所有 major/critical，加其余样本的固定随机 20%），
在同目录写 `audit.json`：每条含 `alias`、`sample`、`status: verified` 与非空 `note`。
修订要保留原始 review，并在复核记录说明理由。未复核的评分不进入首页质量排行。

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/review.py export
server/.venv/Scripts/python.exe -m http.server 8893 --bind 127.0.0.1 --directory benchmarks/translation/site
```

公开导出使用字段白名单，不包含 Key、原始服务日志或全文。来源材料及完整译文默认仅本地保存。

“翻译 PDF”使用本地打包的 PDF.js 展示真实双语文件，支持连续滚动、缩略图、页码跳转、缩放、全屏和下载。
`pdfs.json` 记录任务 ID、文件校验值和补跑出处；本地完整对照版包含原始 PDF。
默认公开版只提供文件目录，可选择校验值匹配的本地 PDF 在浏览器阅读，文件不会上传。
本次用户明确要求将全部 15 份翻译 PDF 一起上线，本地快照 `publishFullPdfs` 记录此发布范围；
它不是第三方版权授权声明，原论文来源和许可链接保留。初评与复核证据保留在“方法与来源”的折叠区。
页面内容通过 `textContent` 渲染。Zotero 界面提交与自动导入必须实际验证才能标记完成；
服务端测试不能替代这项验证。未完成状态会明确出现在方法说明中。

完整本地对照页面（保留原文、译文和 PDF 页面图片）可导出到已忽略目录：

```powershell
server/.venv/Scripts/python.exe benchmarks/translation/review.py export --private --destination .local-dev/translation-bench/private-site
server/.venv/Scripts/python.exe -m http.server 8894 --bind 127.0.0.1 --directory .local-dev/translation-bench/private-site
```

不要发布 private-site。公开网页只发布评分、页码、问题说明、复核记录和文件校验值。

## 验证与发布

```powershell
server/.venv/Scripts/python.exe -m unittest discover -s benchmarks/translation -p 'test_*.py'
```

首次发布将 `site` 子树推送到 `codex/translation-benchmark-pages`，将仓库 GitHub Pages
设置为该分支根目录。后续合并到 main 的静态结果变更由 `benchmark-pages.yml` 检查并同步。
工作流只验证静态结果，不下载论文、不调用收费接口、不运行评审。

所有 PDF、任务目录、Key 和未脱敏评审材料都应留在已忽略的 `.local-dev/translation-bench/`。
不要把该目录上传到 GitHub。插件与服务端版本不因测评而升级。
# 网页展示口径

首页使用紧凑表格，逐段评审与方法说明放在独立标签页。默认质量分为所有参测模型均有可评分译文的共同论文均分（本轮为 Attention、BERT，各 12 个片段，包含部分完成任务）；费用、耗时与完整任务统计仍覆盖全部三篇。选择单篇可查看 ResNet 成绩与接口失败。原始逐篇评分不变，整项失败不作为语言能力零分参与页面排名。

输入与输出单价来自测试时冻结的价格快照；分档、高峰价和缓存价格在模型详情中。`recordedCostUsd` 是有用量记录的请求费用估算小计，`usageKnownRequests` / `usageMissingRequests` 显示记录覆盖情况。总费用未知时保留 `costUsd: null`，页面在“已记录费用”列显示小计，用量缺失在悬停和详情中说明。该列按显示金额排序，未知值始终置后，不代表完整任务性价比；未核清费用不进入性价比图。不能将小计当成供应商实际扣费或总费用下界（缓存未知时存在保守估算）。未发起新收费测试。

网页默认深色，可切换浅色并在本地记住选择。Star 只读获取公开仓库计数，缓存 30 分钟；获取失败时保留旧缓存，无缓存只显示 Star。图标使用插件原图标与本地 Bootstrap Icons 1.13.1（MIT，许可证位于 `site/icons/`）。界面逻辑测试：`node --test benchmarks/translation/test_ui.cjs`。本地私有导出同步 UI 与图标，不公开原文材料。

已复核补跑通过 `supplement-review-index.json` 关联匿名评审工作目录。导出保留 `originalResults`、
`supplementalResults` 和各次 `attempts`；主表使用已复核译文的质量与状态，费用、用量覆盖和耗时累计
全部尝试。不能将补跑成功当作首轮成功，或用较低单次补跑费用掩盖累计消耗。
