# Codex 翻译模型与套餐性价比

[返回使用指南](user-guide.md#api-configuration) · [Codex 安装与验收](codex-acceptance.md)

核查日期：2026-10-03。用于 `develop` 分支的 Codex 接入选型；基线为上游 `49def81afc610085364c5e96923da258cf84aadc`（1.8.0）。本报告复用公开资料和已有翻译记录，未发起新的翻译或性能测评。

## 建议

**全文翻译先用 GPT-6 Luna、Standard 速度、CLI 返回的模型默认推理档位。**在本文三个候选中，Luna 的公开 credits 费率最低，且仓库已有 Luna 论文翻译记录。这个结论支持将它作为初始选择；Codex 路线的实际译文质量与订阅额度消耗仍待用户验收。

划词句段翻译也先用 Luna。若上下文解释或疑难长句达不到质量要求，可在划词独立配置中手动选择 GPT-6.1 Sol；Astra 留给少量需要更强分析能力的内容。此处是依据官方模型定位作出的应用建议，并非三模型论文翻译实测排名。[官方模型选择指南](https://learn.chatgpt.com/docs/model-selection)

若已经订阅并有可用额度，接入 Codex 可增加一个翻译渠道。若只是为了论文翻译购买新套餐，现有证据不足以证明它比正在使用的 CommandGo 更省钱；先保留现有配置，由用户用相同内容比较质量和实际额度变化。

## Codex 订阅与 credits

官网个人套餐标价为 Free $0、Go $8、Plus $20、Pro $100／$200／$500 每月。Free／Go 的 Luna 说明限桌面端；本集成需要 CLI，Plus 明确包含 CLI。实际结算价与模型权限以账号为准。[官方套餐](https://learn.chatgpt.com/docs/pricing)

| 模型 | Standard 输入／缓存输入／输出 credits（每百万 token） | 本项目用途 |
| --- | --- | --- |
| GPT-6 Luna | 2.5／0.25／12.5 | 初始全文与划词配置 |
| GPT-6.1 Sol | 50／2.5／250 | 用户选择的复杂解释配置 |
| GPT-6 Astra | 250／25／1,250 | 用户选择的少量疑难内容 |

表中费率用于 credits 计费，不是订阅内扣量公式；Codex credits 没有独立缓存写入收费。按相同未缓存输入及输出量计算，Sol 为 Luna 的 20 倍、Astra 为 100 倍；实际请求的推理、缓存及重试量会不同。[官方 token 费率](https://learn.chatgpt.com/docs/pricing#token-rates)

官方给出的 Plus 五小时本地消息估计为 Luna 350–3,000、Sol 15–160、Astra 5–45；这是任务估计，不能当成翻译段落额度。Pro 当前没有五小时限制，仍可能受周额度约束。CLI `/status` 或用量面板中的额度及重置时间优先；本插件与账号其他 Codex／Work 使用共享额度。[用量说明](https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan)

Fast 对订阅内消耗为 Standard 的 2.5 倍、对额外 credits 为 2 倍；Astra Ultrafast 分别为 8 倍和 6 倍。本集成固定 Standard。购买 credits 的现金价格依套餐或协议而异，不能把 1 credit 当成 1 美元。[速度与计费](https://learn.chatgpt.com/docs/pricing)

GPT-6.1 Sol 的 CLI 上线覆盖 Plus、Pro、Business、Enterprise 和 Edu；组织管理员或分批开放可能影响权限。模型列表中的条目不保证当前账号能调用，需用户主动连接测试确认。[模型可用性](https://learn.chatgpt.com/docs/models)

本次开发的无推理协议检查使用 CLI 0.153.4，其模型目录未返回 Luna／Sol 6.1，而返回了 Astra 与 5.6 系列。默认配置名称不意味着本机已取得 Luna 权限；实际选择和测试以账号可用模型为准，不自动换成 Astra。这个结果也限制了本文对当前账号的推荐确定性。

## API 单价仅作另一种计费口径

下表为每百万 token、Standard、输入上下文不超过 272K 时的 API 美元标价。它不表示本插件选择 Codex 后会按 API 付费，也不用于换算订阅包含的论文数量。

| 模型 | 输入 | 缓存输入 | 缓存写入 | 输出 | 来源 |
| --- | --- | --- | --- | --- | --- |
| GPT-6 Luna | $0.10 | $0.01 | $0.125 | $0.50 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| GPT-6.1 Sol | $2.00 | $0.10 | $2.50 | $10.00 | [模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| GPT-6 Astra | $10.00 | $1.00 | $12.50 | $50.00 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-astra) |

## CommandGo 对照与已有证据

用户提供的名称为 CommandGo、模型为 `gpt6luna`。仓库的历史评测将价格来源记录为 Command Code，并使用 `gpt-6-luna`；据此将 **Command Code Go 作为待账号确认的对照**，不把其他同名产品或未知套餐当作已核实身份。

Command Code 的 Luna 模型页列出 `gpt-6-luna`，说明 Go 及以上可用，输入／缓存输入／输出为 $0.10／$0.01／$0.50 每百万 token。页面计算器当前用 $6 的模型可用额度估算请求数。[Command Code Luna](https://commandcode.ai/models/gpt-6-luna)

Go 说明页列出 $1 月费、每周期 $10 套餐 credits，并说明 Go 不包含 Provider API 访问；模型可用额度还受每模型分配约束，不能假设 $10 全部能用于 Luna。用户的现有 API 路线、具体套餐、优惠和账单尚未核实。[Go 套餐](https://commandcode.ai/docs/plans/go) · [计费与限制](https://commandcode.ai/docs/resources/pricing-limits)

仓库 [2026-09-29 评测记录](../benchmarks/translation/site/results.json) 使用 OpenAI 兼容 Chat Completions 路线，记录如下：

| 论文 | Luna 完成情况 | token 估算费用（美元） | 已有评审记录 |
| --- | --- | --- | --- |
| Attention | 完成 | 0.0177663 | 12 个固定片段可对齐 |
| BERT | 未完成 1 段 | 0.0473001 | 网址脚注触发校验；另有一个样本末句残缺 |
| ResNet | 完成 | 0.0384038 | 12 个固定片段已有逐项评审 |

合计约 **$0.10347**，是按请求 token 和历史费率估算，供应商账单未核对。资料只覆盖三篇经典机器学习论文，由同系列模型参与评审；不能据此保证其他学科质量，也不能移植为 Codex 的费用。已有记录没有 Sol 6.1／Astra 在相同路线的对照，所以不做质量排名。

Codex 的系统指令、会话和推理开销与 API 路线不同。实际成本比较应包括成功、校验修复和失败重试的总消耗；缺失 usage 记为未知。用户验收时记录套餐及重置窗口、模型／推理档位、相同样本的质量问题、任务前后额度和额外 credits 变化。无需速度指标。

## 尚待用户验收或账号核对

- CommandGo 是否就是 Command Code Go，以及当前套餐的 Luna 实际可用额度、API 权限和优惠。
- Codex 当前账号的套餐、模型权限、剩余额度与重置时间；地区及组织限制以账号显示为准。
- Codex Luna 在全文、术语、结构化释义上的质量，以及相较现有渠道的实际额度消耗。
- Sol／Astra 是否在用户关心的疑难样本中带来足以抵消消耗的改进。

在这些信息明确前，交付建议为：**默认 Luna，保留 CommandGo，按需手动使用更强模型；不因公开标价直接升级套餐。**
