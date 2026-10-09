# 翻译接口审计（2026-10-03）

[使用指南](user-guide.md#api-configuration) · [服务端接口](../server/README.md)

原审计覆盖 22 个接口类型；当前按用户使用范围精简为 9 种底层服务、11 个平台预设，并新增 OpenRouter。
下表保留原审计依据，最终处理栏反映当前支持范围。“离线验证”指模拟接口响应，不代表真实账号、额度或网络已验证。

OpenRouter 预设使用 [官方模型目录](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)和 OpenAI 兼容调用，默认 Base URL 为 `https://openrouter.ai/api/v1`。

## 逐项结果

| 接口 | 官方依据与原有差异 | 最终处理 | 离线验证 |
| --- | --- | --- | --- |
| OpenAI 兼容 | [模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)；原候选重复且长期硬编码 | 保留通用入口、用户自填端点与双协议；移除硬编码列表，Bearer Key 调用 `/models` | Chat/Responses 请求、模型列表、错误脱敏 |
| OpenAICompatible | 项目内另一套同协议预设，实际预置火山方舟地址，与通用入口重复 | 删除入口、设置类型及服务字段映射；迁移删除其配置与备份记录 | 旧名称规范化、迁移不回退成 OpenAI、服务端拒绝 |
| SiliconFlow 免费翻译 | 来自上游 `chatproxy`；2026-10-09 本地日志出现 429、500 与连接测试超时 | 按产品取舍移除入口、代理实现和默认回退；保留自备 Key 的 SiliconFlow | 旧配置迁移、备份清理、服务端拒绝；未选服务不发请求 |
| AliyunDashScope | [兼容接口文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)推荐业务空间与地域域名，旧共享域名仍有效；原候选混入专用翻译模型 | 新配置手填控制台地址和模型，去掉过时列表；旧配置和旧共享地址继续按原值使用 | 业务空间端点、Bearer Key、Chat 消息 |
| DeepSeek | [当前入口](https://api-docs.deepseek.com/guides/codex)、[模型列表](https://api-docs.deepseek.com/api/list-models/)；旧 `deepseek-chat/coder` 候选过时 | 新配置从 `/models` 获取或手填；服务端空模型默认更新为 `deepseek-flash` | `/v1` 路径、Bearer Key、Chat、模型目录 |
| Gemini | [OpenAI 兼容文档](https://ai.google.dev/gemini-api/docs/openai)；原候选含 `gemini-3.0-*` 错名，服务端默认 1.5 过时 | 使用 `v1beta/openai` 地址和在线模型目录；空模型默认更新为文档中的 `gemini-3.8-flash` | 兼容路径、Bearer Key、Chat、模型目录 |
| SiliconFlow | [调用文档](https://docs.siliconflow.cn/docs/userguide/quickstart)、[模型列表](https://docs.siliconflow.cn/docs/api/models-get)；原地址正确、候选陈旧 | 保留自备 Key 适配器，在线获取 `/models?sub_type=chat`，允许手填 | Chat、Bearer Key、聊天模型过滤 |
| 智谱 Zhipu | [官方示例](https://docs.bigmodel.cn/cn/best-practice/case/ai-search-engine)使用 `open.bigmodel.cn/api/paas/v4`，界面却填 `api.zhipu.com/v1` | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| ModelScope | [官方项目配置](https://github.com/modelscope/ms-agent/blob/main/docs/en/Components/Config.md)使用 `api-inference.modelscope.cn/v1`；界面原地址错误 | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| Qwen-MT | [官方 API](https://help.aliyun.com/en/model-studio/qwen-mt-api)要求专用模型和 `translation_options`；原候选全是通用 Qwen 模型 | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| Azure OpenAI | [v1 生命周期](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle)；旧实现固定日期版并强制温度 0 | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| Azure 文本翻译 | [SDK 2.0](https://learn.microsoft.com/en-us/python/api/azure-ai-translation-text/azure.ai.translation.text.texttranslationclient?view=azure-python)、[主权云 v3](https://learn.microsoft.com/en-us/azure/ai-services/translator/reference/sovereign-clouds)；区域原来固定为 `chinaeast2` | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| DeepL | [官方快速开始](https://developers.deepl.com/docs/getting-started/quickstart)；现有官方 SDK 的 `Translator` 兼容类与 `translate_text` 仍支持 | 保留 SDK 鉴权及 Free/Pro 端点选择；隐藏不适用的模型与 URL 字段 | SDK Key、语言映射和译文读取 |
| Ollama | [官方 API](https://docs.ollama.com/api/introduction)；SDK 主机地址正确，静态示例不能代表已安装模型 | 保留原生 SDK；删除静态候选，填写本机已安装模型 | 主机、模型、chat 参数与 message 响应 |
| Xinference | [官方客户端文档](https://inference.readthedocs.io/en/stable/user_guide/client_api.html)；模型字段实际是部署 UID | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| AnythingLLM | [官方工作区 API 源码与 Swagger](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/endpoints/api/workspace/index.js)要求字符串 `message`；原代码发送消息数组并共用 session | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| Dify | [工作流 API](https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow)的输入输出由工作流定义；原适配器固定 `text/lang_in/lang_out` 和 `outputs.text` | 按本次产品取舍删除，未实现变量映射；这不表示 Dify 官方 API 已停用 | 删除配置、备份及注册，服务端拒绝 |
| Grok | [模型列表](https://docs.x.ai/developers/rest-api-reference/inference/models)；旧静态候选/默认模型陈旧 | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| Groq | [官方模型文档](https://console.groq.com/docs/models)；服务端默认模型把 `3.3` 误写为 `3-3` | 移除专用入口、设置类型和适配器；旧配置保留为需重新配置 | 前后端拒绝调用；配置、凭据及引用保留 |
| 腾讯机器翻译 | [官方更新历史](https://cloud.tencent.com/document/product/551/17231)在 2026-07-08 删除 `TextTranslate` | 删除旧适配器、注册、配置入口和腾讯 SDK 依赖；迁移清理配置与备份 | 已移除服务拒绝、依赖锁文件清理 |
| Claude Code | [当前 CLI 参数](https://code.claude.com/docs/en/cli-reference)；旧版维护工具名称黑名单并读取中间流事件 | `-p`、文本标准输入、JSON 最终结果；关闭内置/MCP 工具和会话持久化；失败结果不作译文、超时杀进程并回收 | 命令参数、完整输入、结果/错误、超时回收 |
| Codex | [官方 app-server 文档](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)；当前已使用 `model/list` 和登录账号 | 保留现有实现与推理档位、代理配置；目录不等于模型调用授权 | 现有 Codex provider/client 测试覆盖目录、短请求、错误与取消 |

## 配置迁移约定

迁移顺序为 v0 → v1 → v3 → v4；重复加载幂等。

- v1/v3 延续 OpenAICompatible、腾讯机器翻译、Dify、SiliconFlow 免费代理的清理规则，包括可解析的旧备份。损坏备份保留原值。
- v4 为保留平台补充 `providerPreset`，OpenAI 标准官方端点识别为官方入口，其他旧兼容地址归入自定义兼容。地址、密钥、模型、协议及附加参数保持原值。
- 本轮移除的九种服务保留配置与当前/选文引用，展示“需重新配置”，不自动转换成 OpenAI 或调用其他来源。用户可查看参数、手动更换平台或删除。
- 更换平台使用独立草稿，原密钥和专属参数不会带入另一平台；新预设默认 Chat Completions，不预选模型。

## 验证与发布

- 插件覆盖平台目录、迁移、引用、协议兼容和编辑器字段；服务端覆盖保留服务、移除服务拒绝、模型发现、翻译与流式请求。
- `scripts/check_profile_ui.cjs` 验证实际 XHTML 的草稿、保存、平台隔离、取消及明暗/窄窗口布局；Zotero 原生验收见[检查清单](profile-acceptance.md)。
- Azure SDK 与 XInference 客户端及不再需要的间接依赖从 `pyproject.toml` / `uv.lock` 移除。Docker 与 Homebrew 使用同一锁文件，无独立服务商依赖清单；Python 3.13 约束不变。
- 发布验证以对应提交的 CI 和安装包检查为准；真实账号测试和 Zotero 实际界面仍需人工验收。
