# 英译中测评工具

统一入口：在仓库根目录运行 `python -m benchmarks.translation`。

- [完整使用与维护手册](../../docs/translation-benchmark.md)：数据、模型适配、预算、续跑、评审、报告和晋升。
- [cs-en-zh-v2 清单](datasets/cs-en-zh-v2/manifest.json)：16 篇论文、240 条样本、48 个页面锚点。
- [legacy-v1 清单](datasets/legacy-v1/manifest.json)：旧三篇/60 条及历史展示资产。
- [独立 ChatGPT 授权 P0](DIRECT_P0.md)：单独授权、正文批次配对运行和脱敏报告；不切换产品默认方式。
- [连接复用实验](../../docs/translation-benchmark.md#chatgpt-连接复用实验)：冻结两份客户端、三轮冷/热缓存 PDF 对照及离线报告。

```bash
uv run --project server --locked python -m benchmarks.translation run --suite offline
uv run --project server --locked python -m benchmarks.translation dataset verify --metadata-only
```

真实模型调用必须显式添加 `--execute`。输入数据就绪不代表已经取得模型成绩；只有经过完整运行、
评审和复核的结果才能用 `baseline promote` 晋升。`site/` 及现有历史展示文件不被新运行器覆盖。
