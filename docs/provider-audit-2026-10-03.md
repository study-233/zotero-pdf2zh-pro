# 翻译接口审计（2026-10-03）

[使用指南](user-guide.md#api-configuration) · [服务端接口](../server/README.md)

本次检查覆盖插件原有 22 个接口类型的配置入口、鉴权、请求与响应、模型来源及服务端注册。调整后保留 19 个入口。下表的“离线验证”表示模拟官方响应并检查代码实际生成的请求，不代表账号权限、额度、网络或线上服务已实测通过。

## 逐项结果

| 接口 | 官方依据与原有差异 | 最终处理 | 离线验证 |
| --- | --- | --- | --- |
| OpenAI 兼容 | [模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)；原候选重复且长期硬编码 | 保留通用入口、用户自填端点与双协议；移除硬编码列表，Bearer Key 调用 `/models` | Chat/Responses 请求、模型列表、错误脱敏 |
| OpenAICompatible | 项目内另一套同协议预设，实际预置火山方舟地址，与通用入口重复 | 删除入口、设置类型及服务字段映射；迁移删除其配置与备份记录 | 旧名称规范化、迁移不回退成 OpenAI、服务端拒绝 |
| SiliconFlow 免费翻译 | [上游项目](https://github.com/PDFMathTranslate/PDFMathTranslate-next/blob/main/README.md)仍列出赞助免费翻译；代码使用上游 `chatproxy` | 保留，明确标注“上游代理”；不要求用户 API Key，不与自备 Key 的 SiliconFlow 合并 | 代理 URL、`text` 请求和 `content` 响应 |
| AliyunDashScope | [兼容接口文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)推荐业务空间与地域域名，旧共享域名仍有效；原候选混入专用翻译模型 | 新配置手填控制台地址和模型，去掉过时列表；旧配置和旧共享地址继续按原值使用 | 业务空间端点、Bearer Key、Chat 消息 |
| DeepSeek | [当前入口](https://api-docs.deepseek.com/guides/codex)、[模型列表](https://api-docs.deepseek.com/api/list-models/)；旧 `deepseek-chat/coder` 候选过时 | 新配置从 `/models` 获取或手填；服务端空模型默认更新为 `deepseek-flash` | `/v1` 路径、Bearer Key、Chat、模型目录 |
| Gemini | [OpenAI 兼容文档](https://ai.google.dev/gemini-api/docs/openai)；原候选含 `gemini-3.0-*` 错名，服务端默认 1.5 过时 | 使用 `v1beta/openai` 地址和在线模型目录；空模型默认更新为文档中的 `gemini-3.8-flash` | 兼容路径、Bearer Key、Chat、模型目录 |
| SiliconFlow | [调用文档](https://docs.siliconflow.cn/docs/userguide/quickstart)、[模型列表](https://docs.siliconflow.cn/docs/api/models-get)；原地址正确、候选陈旧 | 保留自备 Key 适配器，在线获取 `/models?sub_type=chat`，允许手填 | Chat、Bearer Key、聊天模型过滤 |
| 智谱 Zhipu | [官方示例](https://docs.bigmodel.cn/cn/best-practice/case/ai-search-engine)使用 `open.bigmodel.cn/api/paas/v4`，界面却填 `api.zhipu.com/v1` | 修正新配置地址、清理静态模型列表；手填控制台模型 | 正式端点、Bearer Key、Chat |
| ModelScope | [官方项目配置](https://github.com/modelscope/ms-agent/blob/main/docs/en/Components/Config.md)使用 `api-inference.modelscope.cn/v1`；界面原地址错误 | 新配置地址与服务端统一；手填已开放推理的模型 ID | 正式端点、Bearer Key、Chat |
| Qwen-MT | [官方 API](https://help.aliyun.com/en/model-studio/qwen-mt-api)要求专用模型和 `translation_options`；原候选全是通用 Qwen 模型 | 只建议 `qwen-mt-plus/flash/lite/turbo`；新配置手填业务空间地址；校验模型前缀，支持来源语言 `auto` | 单条 user 消息、语言和翻译参数 |
| Azure OpenAI | [v1 生命周期](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle)；旧实现固定日期版并强制温度 0 | 默认 OpenAI 客户端调用 `/openai/v1`，模型字段表示部署名称；省略温度以兼容任意命名的推理模型部署；保留显式旧 API 版本 | 根地址补路径、v1 请求、部署名、显式旧版本 |
| Azure 文本翻译 | [SDK 2.0](https://learn.microsoft.com/en-us/python/api/azure-ai-translation-text/azure.ai.translation.text.texttranslationclient?view=azure-python)、[主权云 v3](https://learn.microsoft.com/en-us/azure/ai-services/translator/reference/sovereign-clouds)；区域原来固定为 `chinaeast2` | 新增 `azureRegion`；全球端点使用 SDK 2.0，中国区及美国政府翻译端点按官方 v3 格式调用；缺字段的旧配置保留 `chinaeast2` | 实际 SDK 序列化、密钥/区域头、语言；中国区 v3 路径与请求体 |
| DeepL | [官方快速开始](https://developers.deepl.com/docs/getting-started/quickstart)；现有官方 SDK 的 `Translator` 兼容类与 `translate_text` 仍支持 | 保留 SDK 鉴权及 Free/Pro 端点选择；隐藏不适用的模型与 URL 字段 | SDK Key、语言映射和译文读取 |
| Ollama | [官方 API](https://docs.ollama.com/api/introduction)；SDK 主机地址正确，静态示例不能代表已安装模型 | 保留原生 SDK；删除静态候选，填写本机已安装模型 | 主机、模型、chat 参数与 message 响应 |
| Xinference | [官方客户端文档](https://inference.readthedocs.io/en/stable/user_guide/client_api.html)；模型字段实际是部署 UID | 保留 SDK，移除示例模型，明确填写 model UID | 主机、`get_model`、chat 与响应解析 |
| AnythingLLM | [官方工作区 API 源码与 Swagger](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/endpoints/api/workspace/index.js)要求字符串 `message`；原代码发送消息数组并共用 session | 发送拼接后的翻译提示字符串；每次请求独立 session，检查错误及空结果；提示填写完整工作区 chat URL | URL、Bearer Key、字符串、会话隔离、错误响应 |
| Dify | [工作流 API](https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow)的输入输出由工作流定义；原适配器固定 `text/lang_in/lang_out` 和 `outputs.text` | 按本次产品取舍删除，未实现变量映射；这不表示 Dify 官方 API 已停用 | 删除配置、备份及注册，服务端拒绝 |
| Grok | [模型列表](https://docs.x.ai/developers/rest-api-reference/inference/models)；旧静态候选/默认模型陈旧 | 使用在线目录；空模型默认更新为 `grok-4.7`；保留 Chat 兼容入口 | `api.x.ai/v1`、Bearer Key、Chat、模型目录 |
| Groq | [官方模型文档](https://console.groq.com/docs/models)；服务端默认模型把 `3.3` 误写为 `3-3` | 增加官方地址与在线目录；修正空模型默认值 `llama-3.3-70b-versatile` | 地址、Bearer Key、Chat、模型目录 |
| 腾讯机器翻译 | [官方更新历史](https://cloud.tencent.com/document/product/551/17231)在 2026-07-08 删除 `TextTranslate` | 删除旧适配器、注册、配置入口和腾讯 SDK 依赖；迁移清理配置与备份 | 已移除服务拒绝、依赖锁文件清理 |
| Claude Code | [当前 CLI 参数](https://code.claude.com/docs/en/cli-reference)；旧版维护工具名称黑名单并读取中间流事件 | `-p`、文本标准输入、JSON 最终结果；关闭内置/MCP 工具和会话持久化；失败结果不作译文、超时杀进程并回收 | 命令参数、完整输入、结果/错误、超时回收 |
| Codex | [官方 app-server 文档](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)；当前已使用 `model/list` 和登录账号 | 保留现有实现与推理档位、代理配置；目录不等于模型调用授权 | 现有 Codex provider/client 测试覆盖目录、短请求、错误与取消 |

## 配置迁移约定

首次加载执行分阶段迁移：v0 先适配历史结构（先过滤已移除服务，避免当作未知服务转换为 OpenAI），v1 再升级到 v2。仅删除上述三种服务，保持其余 v1 配置的顺序、Key、模型、地址和附加参数；清空指向已删除项的当前选择，不代选其他服务。重复加载不重复迁移。

`llmApisLegacyBackup` 的外层 JSON 与内层 `llmApis` 均能解析时，同步删除相关记录、旧服务选择和选中 Key；其他字段保留。不可解析的备份原样保留，不用清空全部备份的方式处理损坏数据。

现有配置中的错误 URL 或旧模型也不会自动替换，需用户编辑后测试。Azure `azureRegion` 缺省仍为 `chinaeast2`，显式空字符串表示不发送区域头。新 Azure 配置默认全球端点，区域按控制台填写；高级 `extraData.azure_region` 可继续读取，但显式 `azureRegion` 优先。

Azure OpenAI 默认 v1；显式设置 `extraData.azure_openai_api_version` 为日期版本时仍使用旧 Azure 客户端。已有 API URL、模型及该高级参数均不会被迁移改写。尚未配置部署名称时给出错误，不猜测部署名。

## 验证与发布

本次本地执行结果：插件 276 项通过、1 项跳过；服务端 408 项通过；文档检查及其 15 项测试通过；TypeScript 检查与 XPI 打包通过。未调用真实模型账号，也未发布或安装到 Zotero。额外运行的 ESLint 检查仍报告既有代理 URL 校验正则的 `no-control-regex` 错误（`profileEditor.js`，该正则与改动前一致）；本次未修改其行为。

- 插件：19 项目录、v0/v1 迁移、备份清理、幂等与原值保留、字段显示和模型发现；运行 `node --test`。
- 服务端：`tests/test_provider_contracts.py` 参数化检查 18 个保留服务，Codex 由 `test_codex_provider.py`、`test_codex_client.py` 覆盖；相关路由、协议、划词与指标测试继续运行。
- 构建：TypeScript 检查与 XPI 打包；文档检查器、文档测试与 diff 空白检查。
- 依赖：`pyproject.toml` 与 `uv.lock` 同步更新 Azure SDK 2.0、删除腾讯 SDK。Docker 使用同一 frozen lock；compose 无独立依赖。已核对 [Homebrew 配方](https://github.com/study-233/homebrew-formula/blob/main/Formula/zotero-pdf2zh-pro.rb)：使用同一锁文件执行 `uv sync --locked`，继续固定 Python 3.13；本次无需另写依赖清单，发布时再更新版本和提交。
- 发布前人工验收：用各自真实账号/部署执行“获取模型”（适用时）与“测试 API”，再各抽测一份 PDF；特别检查 Azure 不同云与区域、AnythingLLM 工作区、Claude/Codex 本机登录。此次离线验证没有调用真实账号。
