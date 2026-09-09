# 开发维护笔记

## 产品边界

`zotero-pdf2zh-pro` 包含 Zotero 插件和本地 Python 服务。插件 ID 为
`zotero-pdf2zh-pro@study-233`，设置前缀为
`extensions.zotero.pdf2zhpro`。它是独立产品，不迁移旧插件设置或旧服务数据。

仓库保持公开；插件清单配置稳定的自动更新 URL，每个 GitHub Release 必须同时发布
XPI、`update.json` 和 Windows 安装包。不要生成或发布朋友整合包；对应源码由公开标签提供。

## Python 服务

```bash
uv run --directory server --locked python -m unittest discover -s tests
uv run --directory server zotero-pdf2zh-pro
```

`server/uv.lock` 的 registry 必须保持公共 `https://pypi.org/simple`。重建时使用：

```bash
UV_DEFAULT_INDEX=https://pypi.org/simple uv --directory server lock
```

面向用户的 PyPI 包和 CLI 都叫 `zotero-pdf2zh-pro`。包内固定包含
pdf2zh-next、BabelDOC 和 RapidOCR 核心快照；来源、SHA 和许可证记录在
`server/THIRD_PARTY_NOTICES.md`。更新快照后必须重建锁文件、wheel/sdist，
再执行产物校验。

## Windows

Windows 包包含 Tauri 2 控制中心 EXE、故障恢复管理脚本、README、许可证和第三方声明。
正式构建固定使用 `stable-x86_64-pc-windows-msvc` 工具链和 `x86_64-pc-windows-msvc`
目标，根目录 `.cargo/config.toml` 启用静态 CRT。打包器只读取显式目标目录并检查普通和
延迟导入表：不能依赖外部 WebView2Loader、VC++ 或其他未交付的非系统 DLL。
WebView2 准备脚本嵌入 EXE，在 Tauri 窗口创建前通过 Windows PowerShell 5.1 / WinForms
运行；不依赖解压目录里的额外脚本。下载文件必须验证微软 Authenticode 签名后才能执行。
控制中心使用 Rust 后端和原生 TypeScript/CSS 单页前端，不授予通用 Shell 权限。安装时
通过官方 Astral 安装私有 uv，由 uv 安装托管的 Python 3.13，并从公共 PyPI 安装与控制中心
相同版本的 `zotero-pdf2zh-pro`。安装根目录优先读取测试覆盖，其次读取当前用户注册表
`HKCU\Software\zotero-pdf2zh-pro\InstallRoot`，最后回退到 `%LOCALAPPDATA%`。

默认目录：

- 数据：`%LOCALAPPDATA%\zotero-pdf2zh-pro\data`
- 日志：`%LOCALAPPDATA%\zotero-pdf2zh-pro\logs`
- 管理脚本：`%LOCALAPPDATA%\zotero-pdf2zh-pro\bin`

用户选择其他产品根目录时，`bin`、`runtime`、`cache`、`data` 和 `logs` 必须整体迁移。
迁移前停止服务，验证新服务的版本、进程归属和 workspace 后才允许切换注册表、快捷方式
及自启路径；这也保护任务目录中的段落恢复检查点。迁移失败必须重新启动原安装。

首次 GUI 安装默认创建当前用户 HKCU Run 登录自启，参数固定为 `--autostart`；它必须在
控制中心可见、可关闭，升级必须保留用户选择。除此以外不得创建计划任务、Windows Service
或防火墙规则。停止、升级和卸载前必须校验 PID、命令行、可执行文件路径和健康接口归属，
不得结束未知进程。关闭窗口隐藏到托盘，退出控制中心不停止服务。

## 测试

日常核心检查与 GitHub CI 保持一致：

```bash
pnpm --dir plugin install --frozen-lockfile
pnpm --dir plugin build
pnpm --dir plugin test
uv run --directory server --locked python -m unittest discover -s tests
pnpm --dir windows-app install --frozen-lockfile
pnpm --dir windows-app test
git diff --check
```

修改 Windows 原生后端或安装脚本时，可在 Windows 本地显式运行 Rust 单测、PowerShell
语法/安全检查和 `scripts/test_windows_lifecycle.ps1`。Tauri release、ZIP 与 PyPI
产物只在正式发布流程中构建和校验，不进入日常 CI。
发布检查还运行 `scripts/test_windows_bootstrap.ps1` 和 `scripts/test_windows_package.ps1`；
后者从最终 ZIP 解压，以独立工作目录、精简 PATH 验证真实窗口。生命周期验证使用
`-WindowsPackage dist/zotero-pdf2zh-pro-windows-x64.zip`，从同一 ZIP 读取 EXE 和管理脚本。
候选版本可以在开发分支触发只构建的 Windows 工作流，输入该分支包含的精确提交；
公开发布仍须通过完整检查，并在干净 Windows 10/11 环境验收已有/缺失 Runtime 两种情况。

## macOS 本机源码部署

`scripts/local-deploy.sh` 用于把当前工作树部署到本机 Zotero Profile 和 Homebrew
管理的 `zotero-pdf2zh-pro` 服务。脚本在修改安装前完成构建，并在存在
运行中、排队中或正在取消的任务时退出，不会终止用户任务。

```bash
./scripts/local-deploy.sh --check-only
./scripts/local-deploy.sh
```

部署备份和 SHA-256 记录位于被忽略的 `.local-dev/deployments/`。失败时必须恢复
上一版 XPI、Python 包和任务数据；该脚本不得修改版本、创建提交或发布远端制品。

## 发布

新版本先写 `CHANGELOG.md` 的 `## v<version> - YYYY-MM-DD`，再运行：

```bash
scripts/release.sh <version>
```

统一脚本必须在 Windows 上运行；它同步插件、服务端、控制中心、锁文件和 Windows 脚本
版本，构建 Tauri release EXE，验证 XPI、PyPI 包和 Windows ZIP，生成本地源码归档，
提交并推送主仓库，然后发布 PyPI 和公开 GitHub Release。日常核心测试由 CI 承担，
发布脚本不重复运行单测、lint 或全新虚拟环境冒烟。

PyPI Trusted Publisher 必须绑定：

- PyPI project：`zotero-pdf2zh-pro`
- GitHub owner：`study-233`
- Repository：`zotero-pdf2zh-pro`
- Workflow：`publish-pypi.yml`
- Environment：`pypi`

Homebrew tap 是公开 source Formula：`study-233/homebrew-formula`。Formula 使用主仓库
公开 HTTPS 地址，固定 `python@3.13` 和 git revision，不发布 bottles。发布脚本直接更新
tap `main` 并等待 `formula-checks.yml`。

同版本恢复发布只能复用指向同一 commit 的 tag、PyPI 发行和 GitHub Release。
旧版本 backfill 必须从对应 tag 构建。

## 许可证

产品改名不改变 AGPL 或第三方归属。不得删除上游许可证、第三方 notice 或 Git
历史中的贡献者信息。每个二进制发行物的对应源码必须可从公开版本标签获取。
