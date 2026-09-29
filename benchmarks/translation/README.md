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
