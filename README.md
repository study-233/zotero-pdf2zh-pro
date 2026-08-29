# zotero-pdf2zh-next

一个面向 Zotero 7 及以上版本的 PDF 翻译插件，配套一个本地 Python 服务来调用 `pdf2zh_next` 完成翻译。

项目维护重点是：少一点配置负担，稳定地在 Zotero 里提交任务、查看进度、导入结果。

当前统一版本：<!-- release-version --> `5.3.0`

## 目录

- [项目来源与本分支改动](#项目来源与本分支改动)
- [效果预览](#效果预览)
- [安装插件](#安装插件)
- [安装并启动本地服务](#安装并启动本地服务)
- [更新](#更新)
- [本机源码一键更新](#本机源码一键更新)
- [在 Zotero 里使用](#在-zotero-里使用)
- [License](#license)

## 项目来源与本分支改动

本仓库直接基于
[NightWatcher314/zotero-pdf2zh-next](https://github.com/NightWatcher314/zotero-pdf2zh-next)
的 `v5.3.0` 版本继续开发。该上游项目是
[guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)
的分支，并在其基础上演化而来。感谢两位上游维护者及所有贡献者。

NightWatcher 上游将项目向更轻量的插件与本地服务架构重构：

- 只保留 Zotero 插件和本地 Python 服务两部分。
- 服务端提供 Homebrew 和 `uv tool` 分发，尽量减少手动配置。
- 支持任务面板、进度显示、取消、重试和结果导入状态。
- 支持同时输出中文 PDF 和双语 PDF。
- 偏好页整合插件版本、服务端版本、连接检查和常用翻译选项。
- 去掉旧 runner、旧 server 和历史遗留自动化路径，减少维护成本。

在此基础上，本分支进一步增加：

- macOS 本机源码一键部署、任务保护、备份和失败回滚。
- DeepSeek 请求耗时、QPS、缓存、重试、token、吞吐量和费用观测。
- 按翻译配置隔离缓存，避免错误复用其他模型或提示词的译文。
- 可选的参考文献跳过功能，并在输出 PDF 中保留参考文献原文。

完整更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 效果预览

![任务进度页面](assets/任务进度.png)

## 安装插件

登录后从私人仓库 `study-233/zotero-pdf2zh-next` 的 GitHub Release 下载最新的
`zotero-pdf2zh-next.xpi`，然后：

1. 打开 Zotero。
2. 进入 `工具 -> 插件`。
3. 点击右上角齿轮图标。
4. 选择 `Install Add-on From File...`。
5. 选择下载的 `.xpi` 文件。
6. 重启 Zotero。

## 安装并启动本地服务

macOS 推荐使用 Homebrew，这样可以用 `brew services` 管理后台服务：

```bash
brew tap study-233/formula git@github.com:study-233/homebrew-formula.git
brew install --build-from-source study-233/formula/zotero-pdf2zh-next
brew services start zotero-pdf2zh-next
```

Windows 和 Linux 可以使用 `uv tool`：

```bash
uv tool install --python 3.13 zotero-pdf2zh-next
zotero-pdf2zh-next
```

> [!IMPORTANT]
> PyPI 上的 `zotero-pdf2zh-next` 当前由 NightWatcher 上游维护；以上
> `uv tool` 命令安装的是上游已发布的后端，不包含本仓库尚未发布的改动。
> 如需运行本仓库当前源码，请克隆本仓库后执行：
>
> ```bash
> uv run --directory server --locked zotero-pdf2zh-next
> ```

也可以用 Docker 启动本地服务：

```bash
docker compose up --build -d
```

如需在构建时使用自定义 Python 包索引或推送到自己的镜像仓库，可以通过环境变量覆盖默认值：

```bash
UV_INDEX_URL=https://your-pypi-proxy/index/ \
PDF2ZH_IMAGE=your-registry/zotero-pdf2zh-next:latest \
docker compose up --build -d
```

默认服务地址是：

```text
http://127.0.0.1:8890
```

## 更新

私人插件更新：

- 从私人仓库的 GitHub Release 下载新 XPI。
- 在 Zotero 插件管理页面从文件安装并重启。私人 Release 不支持 Zotero 匿名自动更新。

Homebrew 服务端更新：

```bash
brew update
brew upgrade study-233/formula/zotero-pdf2zh-next
brew services restart zotero-pdf2zh-next
```

`uv tool` 服务端更新：

```bash
uv tool upgrade zotero-pdf2zh-next
```

## 本机源码一键更新

macOS 上如果已经通过 Homebrew 安装服务，并希望直接使用当前工作树中的插件和后端改动，可以运行：

```bash
./scripts/local-deploy.sh
```

脚本会自动发现 Zotero 默认 Profile，依次完成插件与后端检查、生产构建、制品校验、备份、安装、Homebrew 服务重启和健康检查。存在运行中、排队中或正在取消的任务时，部署会在修改安装前退出，不会终止任务。

首次从源码开发模式切换时，新 Homebrew 服务从空任务列表开始；后续重复部署会保留 Homebrew 服务的任务记录。每次部署的 XPI、wheel、任务备份、Git 状态和 SHA-256 位于 `.local-dev/deployments/`，安装失败时会自动恢复上一版。

只检查和打包、不更新本机安装时使用：

```bash
./scripts/local-deploy.sh --check-only
```

该工作流不会修改版本号、提交代码或发布远端制品。正式发布仍使用 `scripts/release.sh`。

## 在 Zotero 里使用

1. 打开 Zotero 设置里的 `zotero-pdf2zh-next`。
2. 把 `Python Server URL` 设为本地服务地址，例如 `http://127.0.0.1:8890`。
3. 点击“检查连接与配置”，确认插件端和服务端版本都能显示。
4. 选择翻译服务，并配置对应的 LLM API。
5. 选择输出中文 PDF、双语 PDF，或两者同时输出。
6. 在条目或 PDF 附件上右键，选择 `zotero-pdf2zh-next: Translate PDF`。

任务提交后，可以在右键菜单里打开 `zotero-pdf2zh-next: Task Manager` 查看进度、取消任务、重试失败任务和导入结果。DeepSeek 任务还会显示当前段落吞吐量、预计剩余时间、本地缓存命中、实际 QPS、请求耗时、自动重试、上下文缓存 token 和估算费用。

“不翻译参考文献”默认关闭。启用后会优先使用 PDF 版面标签，并在证据充分时通过 `References`、`Bibliography` 或 `参考文献` 标题识别参考文献区；被跳过的内容仍保留在输出 PDF 中。

DeepSeek 费用按每百万 token 的缓存命中输入、未命中输入和输出费率估算。内置人民币费率按每次响应发生的 UTC 时间自动套用峰谷价：高峰为 `01:00–04:00`、`06:00–10:00`（北京时间 `09:00–12:00`、`14:00–18:00`），其余为非高峰。

| 模型     | 时段   | 缓存命中输入 | 缓存未命中输入 |     输出 |
| -------- | ------ | -----------: | -------------: | -------: |
| V4 Flash | 非高峰 |      0.05 元 |        1.50 元 |  4.50 元 |
| V4 Flash | 高峰   |      0.10 元 |        3.00 元 |  9.00 元 |
| V4 Pro   | 非高峰 |      0.15 元 |        4.50 元 | 13.50 元 |
| V4 Pro   | 高峰   |      0.30 元 |        9.00 元 | 27.00 元 |

内置费率无法覆盖自定义或新模型时，可在该 LLM 配置的 `extraData` 中设置固定费率：

```json
{
  "deepseek_cache_hit_input_price": 0.5,
  "deepseek_cache_miss_input_price": 2,
  "deepseek_output_price": 8,
  "deepseek_price_currency": "CNY",
  "deepseek_pricing_version": "custom-2026-08"
}
```

自定义费率不会自动切换峰谷价。这些数字仅用于本地估算，不会联网更新，也不会替代 DeepSeek 账单；已保存的历史任务费用不会追溯重算。

## License

本项目延续
[NightWatcher314/zotero-pdf2zh-next](https://github.com/NightWatcher314/zotero-pdf2zh-next)
及 [guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)
的许可，采用 `AGPL-3.0-or-later` 发布，见 [LICENSE](LICENSE)。
