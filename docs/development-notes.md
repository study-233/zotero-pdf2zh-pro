# 开发维护笔记

## 产品边界

`zotero-pdf2zh-pro` 包含 Zotero 插件和本地 Python 服务。插件 ID 为
`zotero-pdf2zh-pro@study-233`，设置前缀为
`extensions.zotero.pdf2zhpro`。它是独立产品，不迁移旧插件设置或旧服务数据。

仓库保持私有；朋友通过发行脚本生成的 friends ZIP 获取 XPI、Windows 安装包、
对应源码和校验值。插件不配置自动更新 URL。

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

Windows 包只包含管理脚本。安装时通过官方 Astral 安装 uv，由 uv 安装托管的
Python 3.13，并从公共 PyPI 安装与脚本相同版本的 `zotero-pdf2zh-pro`。

默认目录：

- 数据：`%LOCALAPPDATA%\zotero-pdf2zh-pro\data`
- 日志：`%LOCALAPPDATA%\zotero-pdf2zh-pro\logs`
- 管理脚本：`%LOCALAPPDATA%\zotero-pdf2zh-pro\bin`

不得创建登录自启动、计划任务、Windows Service 或防火墙规则。停止和卸载前
必须同时校验 PID 与命令行归属，不得结束未知 Python 进程。

## 测试

```bash
pnpm --dir plugin install --frozen-lockfile
pnpm --dir plugin lint:check
pnpm --dir plugin build
uv run --directory server --locked python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
python scripts/check_pypi_artifacts.py server/dist 1.0.0
git diff --check
```

Windows 还要运行 PowerShell 5.1 语法检查、ZIP 构建以及安装、手动启动、重复启动、
健康检查、停止、保留数据卸载、重装和 `-PurgeData` 生命周期测试。

## 发布

新版本先写 `CHANGELOG.md` 的 `## v<version> - YYYY-MM-DD`，再运行：

```bash
scripts/release.sh <version>
```

统一脚本同步插件、服务端、锁文件和 Windows 脚本版本，验证 XPI、PyPI 包、
Windows ZIP 和 friends ZIP，提交并推送主仓库，然后发布 PyPI 和私有 GitHub Release。

PyPI Trusted Publisher 必须绑定：

- PyPI project：`zotero-pdf2zh-pro`
- GitHub owner：`study-233`
- Repository：`zotero-pdf2zh-pro`
- Workflow：`publish-pypi.yml`
- Environment：`pypi`

Homebrew tap 是私有 source Formula：`study-233/homebrew-formula`。Formula 固定
`python@3.13` 和主仓库 git revision，不发布 bottles。发布脚本直接更新 tap
`main` 并等待 `formula-checks.yml`。

同版本恢复发布只能复用指向同一 commit 的 tag、PyPI 发行和 GitHub Release。
旧版本 backfill 必须从对应 tag 构建。

## 许可证

产品改名不改变 AGPL 或第三方归属。不得删除上游许可证、第三方 notice 或 Git
历史中的贡献者信息。向朋友发送二进制发行物时，friends ZIP 必须包含对应源码归档。
