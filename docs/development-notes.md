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
再执行 fresh Python 3.13 安装和 OCR smoke。

## Windows

Windows 包包含 Tauri 2 控制中心 EXE、故障恢复管理脚本、README、许可证和第三方声明。
控制中心使用 Rust 后端和原生 TypeScript/CSS 单页前端，不授予通用 Shell 权限。安装时
通过官方 Astral 安装 uv，由 uv 安装托管的 Python 3.13，并从公共 PyPI 安装与控制中心
相同版本的 `zotero-pdf2zh-pro`。

默认目录：

- 数据：`%LOCALAPPDATA%\zotero-pdf2zh-pro\data`
- 日志：`%LOCALAPPDATA%\zotero-pdf2zh-pro\logs`
- 管理脚本：`%LOCALAPPDATA%\zotero-pdf2zh-pro\bin`

首次 GUI 安装默认创建当前用户 HKCU Run 登录自启，参数固定为 `--autostart`；它必须在
控制中心可见、可关闭，升级必须保留用户选择。除此以外不得创建计划任务、Windows Service
或防火墙规则。停止、升级和卸载前必须校验 PID、命令行、可执行文件路径和健康接口归属，
不得结束未知进程。关闭窗口隐藏到托盘，退出控制中心不停止服务。

## 测试

```bash
pnpm --dir plugin install --frozen-lockfile
pnpm --dir plugin lint:check
pnpm --dir plugin build
pnpm --dir windows-app install --frozen-lockfile
pnpm --dir windows-app test
cargo test --manifest-path windows-app/src-tauri/Cargo.toml
pnpm --dir windows-app tauri build --no-bundle
uv run --directory server --locked python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
python scripts/check_pypi_artifacts.py server/dist <version>
git diff --check
```

Windows 还要运行前端单测、Rust 单测、PowerShell 5.1 语法/安全检查、Tauri release
构建、ZIP 内容校验，以及首次安装、默认自启、关闭自启后升级、重复启动、健康检查、
启动失败、端口冲突、日志/数据目录、升级失败保护、保留数据卸载和 `-PurgeData` 生命周期测试。

## macOS 本机源码部署

`scripts/local-deploy.sh` 用于把当前工作树部署到本机 Zotero Profile 和 Homebrew
管理的 `zotero-pdf2zh-pro` 服务。脚本在修改安装前完成构建与制品校验，并在存在
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
提交并推送主仓库，然后发布 PyPI 和公开 GitHub Release。

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
