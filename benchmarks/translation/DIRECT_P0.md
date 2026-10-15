# 独立 ChatGPT 授权与 Codex 的 P0 对照

这是实验入口，不修改产品设置，不切换默认通道，不晋升基准。
使用 `cs-en-zh-v2` 开发集八个领域的 16 条真实正文，组成八个固定双段落批次。
正文请求使用当前 PDF 产品的 `PROMPT_TEMPLATE`，不开启术语抽取、额外上下文或人工术语。
它测量**正文批次调用**，不运行 PDF 提取、恢复、排版；不能称为完整 PDF 性能验证。

## 环境与独立授权

使用服务端 Python 环境（httpx、cryptography）运行。仅支持公开文档中的 SIWC
Responses 路线，不读取 Codex auth.json，不使用网页会话 Cookie 或私有 backend-api。

```bash
python -m benchmarks.translation direct login
python -m benchmarks.translation direct status
python -m benchmarks.translation direct models --model gpt-6-luna
```

登录命令输出一个临时本地链接。本人在浏览器中选择与 Codex 相同的账号和工作区，
并授权使用 ChatGPT 套餐。浏览器回调只监听 127.0.0.1，校验 state、PKCE、ID Token
签名/audience/issuer/nonce。登录成功不代表推理权限或模型可用，实际完成请求才能验证。

默认私有目录为忽略的 `.local-dev/chatgpt-direct-auth`，可用 `--auth-dir` 指定；
目录权限 0700，凭据文件 0600，不进入结果包。一次实验只用一个账号注册；不要手动
混合不同注册的 client_id 与令牌。刷新在独占文件锁内完成，原子替换旋转令牌。
这是本机 P0 工具；没有实现正式产品的多账号界面、撤销入口和跨平台凭据库。
若结束使用，可在 ChatGPT 设置中断开该应用；不要将凭据目录作为测评数据导出。

## 固定输入与执行

```bash
python -m benchmarks.translation direct prepare \
  --cli-path /absolute/path/to/codex \
  --output .local-dev/translation-bench/direct-p0-01
python -m benchmarks.translation direct run \
  --output .local-dev/translation-bench/direct-p0-01
python -m benchmarks.translation direct run \
  --output .local-dev/translation-bench/direct-p0-01 --execute
```

默认三个预检 × 两通道，再八个正文批次 × 三轮 × 两通道，共 **54 次请求**。
硬上限默认 60 次、1800 秒，单请求 120 秒；全部串行，按批次和轮次交替通道顺序。
首次失败即停止，不自动重试，不自动切换模型或收费路径。已开始的实验不能直接重放；
需要新建目录，旧的未完成/未知调用继续留档。准备后的代码变更拒绝执行。

两通道发送完全相同的用户提示词。直接通道另带当前 Codex 翻译引擎的固定指令；
Codex 的额外运行层和不可见服务端行为无法控制，如实计入差异。
默认模型 gpt-6-luna、推理 low、服务档位 default，不发送温度或输出长度上限。
新路线若不支持目标模型、权限或参数，保留错误，不能将不同配置混作公平对照。
应用缓存禁用，提供方缓存仍可能存在；报告分别保留输入与缓存输入。

## 报告与稳定性范围

```bash
python -m benchmarks.translation direct report \
  --output .local-dev/translation-bench/direct-p0-01
python -m unittest benchmarks.translation.tests.test_chatgpt_direct
```

结果含 `experiment.json`、调用/事件流水、`summary.json`、`calls.csv`、离线 `report.html`。
记录代码及未提交变更指纹、输入哈希、环境、请求顺序、时延、用量完整性和原文/输出。
未完成时只有已知 Token 小计，不代表总用量。失败/中断不会变为零 Token。
同账号通过脱敏的匹配状态记录；工作区由操作者选择，不谎称机器已核对。

结构检查复用产品的段落 ID、占位符与目标语言验证。语义质量默认“未评审”，不能
因结构通过就称为质量无退步。三轮短实验只反映短时成功率和波动，不证明小时级
授权刷新、长论文和服务重启的稳定性。尚未达到 P1 验收时不切换默认入口。

官方依据：
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations

## 真实 PDF 与生命周期验证

```bash
python -m benchmarks.translation direct validate \
  --cli-path /absolute/path/to/codex \
  --output .local-dev/translation-bench/direct-validation-01 --execute
```

默认先执行一次真实令牌刷新、选段流取消、独立服务重启后的实际请求，再对四个固定 PDF
页面进行三轮配对。每轮使用独立冷缓存，通道顺序交替；总请求上限 260 次，总时间上限
5400 秒，单轮单通道最多 60 次/1800 秒。产品实际校验与修复重试仍保留，独立报告。

`direct_service` 仅在本次创建的服务及其 spawn 子进程中，将模型调用层替换为独立
ChatGPT Responses；产品入口、提示词、提取、批次、恢复及排版沿用 Codex 适配器流程。
它不是正式插件登录界面，也不改变正常 `server.py` 的默认入口。实验配置用
`experiment_transport=chatgpt_direct` 标明身份；产品调用流水标记实际协议，并通过
同一 call ID 与直接通道流水对账。不得把它标为原生 Codex 成绩或自动晋升基准。

刷新测试使用真实 refresh grant，不改写本地过期时间；若尚未到期，应称为主动刷新，
不能称为跨自然过期验证。取消时关闭本地流，不声称远端计算或扣量已终止；没有最终
usage 的调用继续标为未知。重启测试检查重新读取保存的凭据后可实际推理，不代表
所有中断 PDF 任务均可自动续传。三轮完成后仍须渲染并检查产物，不能只看任务状态。
