# zotero-pdf2zh-pro

面向 Zotero 8、9 和 10 的 PDF 翻译插件，配套本地 Python 服务调用
`pdf2zh_next` 完成翻译。

当前统一版本：<!-- release-version --> `1.4.0`

## 安装前说明

`zotero-pdf2zh-pro` 是独立产品，不会读取或迁移旧插件设置和旧服务数据。
如果安装过旧产品，请先停止并卸载旧服务、从 Zotero 移除旧插件，避免两个服务同时占用
`127.0.0.1:8890`。

仓库和发行版均公开。GitHub Release 提供 Zotero XPI、自动更新清单和 Windows
安装包；对应源码可从版本标签直接获取。

## 项目来源与当前改动

本仓库直接基于
[NightWatcher314/zotero-pdf2zh-next](https://github.com/NightWatcher314/zotero-pdf2zh-next)
的 `v5.3.0` 版本继续开发；该项目又基于
[guaguastandup/zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)
演化而来。感谢两位上游维护者及所有贡献者。

当前 Pro 分支在轻量插件和本地服务架构上进一步增加：

- macOS 本机源码一键部署、任务保护、备份和失败回滚。
- DeepSeek 请求耗时、QPS、缓存、重试、token、吞吐量和费用观测。
- 按翻译配置隔离缓存，避免错误复用其他模型或提示词的译文。
- 可选的参考文献跳过功能，并在输出 PDF 中保留参考文献原文。

完整更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 安装 Zotero 插件

1. 从 [GitHub Releases](https://github.com/study-233/zotero-pdf2zh-pro/releases/latest)
   下载 `zotero-pdf2zh-pro.xpi`。
2. 在 Zotero 中进入 `工具 -> 插件`。
3. 点击右上角齿轮，选择 `Install Add-on From File...`。
4. 选择 XPI 并重启 Zotero。

插件安装后通过公开的 `update.json` 接收后续稳定版本更新。

## Windows 10/11 x64

解压 `zotero-pdf2zh-pro-windows-x64.zip` 后，双击
`zotero-pdf2zh-pro.exe`，再点击“安装并启动”。控制中心会安装官方 uv、uv 托管的
Python 3.13，以及公开 PyPI 上与控制中心相同版本的服务端；不需要打开 PowerShell
或 CMD。

首次安装默认开启当前账户登录自启。重新登录后不会弹出窗口，控制中心会驻留系统托盘
并确保服务运行；可在主卡片随时关闭，后续升级不会重新开启。关闭主窗口只会隐藏到托盘，
退出控制中心也不会停止服务。

控制中心启动时会静默检查 GitHub 上的稳定版本；发现新版后点击“更新到 vX.X.X”即可
自动下载、校验、更新控制中心和服务端并重启。任务、翻译结果、日志和自启选择都会保留，
失败时自动恢复旧版。首次获得这一能力仍需手动安装一次包含自动更新功能的 Windows ZIP。
包内的 CMD/PowerShell 文件仅作为故障恢复入口。

安装不需要管理员权限，不创建计划任务、Windows Service 或防火墙规则，也不会停止
占用 8890 端口的未知进程。唯一允许的自启机制是当前用户可见、可关闭的登录启动项。
数据位于 `%LOCALAPPDATA%\zotero-pdf2zh-pro\data`，日志位于
`%LOCALAPPDATA%\zotero-pdf2zh-pro\logs`。

控制中心依赖 Microsoft Edge WebView2 Runtime；Windows 10/11 缺失时会在创建窗口前
提供微软官方下载入口。本阶段 EXE 未签名，SmartScreen 可能提示风险，请只从官方 Release
下载并核对发布页 SHA-256。

## macOS

Homebrew tap 和源码仓库均为公开仓库：

```bash
brew tap study-233/formula
brew install --build-from-source study-233/formula/zotero-pdf2zh-pro
brew services start zotero-pdf2zh-pro
```

也可以从公开 PyPI 安装服务端：

```bash
uv tool install --python 3.13 zotero-pdf2zh-pro
zotero-pdf2zh-pro
```

## Docker

```bash
docker compose up --build -d
```

默认服务地址为 `http://127.0.0.1:8890`。

## 使用

1. 打开 Zotero 设置中的 `zotero-pdf2zh-pro`。
2. 确认服务地址为 `http://127.0.0.1:8890`。
3. 点击“检查连接与配置”。
4. 配置翻译服务、语言和输出格式。
5. 在条目或 PDF 附件上右键，选择
   `zotero-pdf2zh-pro: Translate PDF`。
6. 在 `zotero-pdf2zh-pro: Task Manager` 查看进度、重试任务并导入结果。

DeepSeek 任务还会显示段落吞吐量、预计剩余时间、本地缓存命中、实际 QPS、
请求耗时、自动重试、token 和估算费用。费用只用于本地估算，不替代服务商账单。

“不翻译参考文献”默认关闭。启用后会优先使用 PDF 版面标签，并在证据充分时通过
`References`、`Bibliography` 或 `参考文献` 标题识别参考文献区；跳过的内容仍保留在
输出 PDF 中。

## macOS 本机源码更新

已经通过 Homebrew 安装服务，并希望直接部署当前工作树时，可以运行：

```bash
./scripts/local-deploy.sh
```

脚本会检查运行中任务，构建插件和后端，备份现有安装，更新 Homebrew 服务并
执行健康检查；失败时自动恢复上一版。只检查和打包而不修改本机安装时使用：

```bash
./scripts/local-deploy.sh --check-only
```

该流程不会修改版本、提交代码或发布远端制品。正式发布仍使用
`scripts/release.sh`。

## 更新

- 插件：由 Zotero 自动检查稳定版本，也可以从 GitHub Release 手动安装新 XPI。
- Windows：控制中心启动时静默检查稳定版本，发现新版后点击“更新到 vX.X.X”。
- uv：`uv tool upgrade zotero-pdf2zh-pro`。
- Homebrew：`brew upgrade zotero-pdf2zh-pro` 后重启服务。

## License

本项目采用 `AGPL-3.0-or-later`，并保留所有上游项目和第三方组件的许可证与归属，
见 [LICENSE](LICENSE) 和 [server/THIRD_PARTY_NOTICES.md](server/THIRD_PARTY_NOTICES.md)。
