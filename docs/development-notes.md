# 开发维护笔记

## 文档维护

首页与详细指南按 [README 维护规范](readme-maintenance.md) 分工。修改后运行独立检查：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_check_docs.py'
```

文档 CI 不安装应用依赖；业务 CI 的触发规则保持不变。

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
默认验证启动、安装、升级、自更新、卸载及全新 Python 3.13/OCR；勾选 `full_validation` 才追加完整回滚、迁移和重复的全量测试。
Rust 单测复用 release 编译依赖，不再额外编译 debug 依赖。在新电脑上补充启动验收后发布。

## macOS 独立开发环境

日常开发使用独立 Profile 和文献库，正式 Zotero 与开发 Zotero 轮流打开。
复用 `/Applications/Zotero.app`；正式后端继续运行在 8890，开发后端固定监听
`127.0.0.1:8891`。Windows 原生开发管理尚未适配，不要将 Mac 的 Profile、虚拟环境
或含密钥的配置直接复制到 Windows。

先安装 Python 3.12/3.13、Node.js、pnpm 和 uv，并正常退出 Zotero。在仓库根目录运行：

```bash
./scripts/dev.sh init
./scripts/dev.sh prepare-samples
./scripts/dev.sh start
./scripts/dev.sh status
```

`init` 使用锁文件准备依赖，首次从正式 Profile 复制完整模型列表及当前选择，包括
密钥、接口、协议和额外请求参数。正式配置保持不变；开发配置可以独立修改，重复
`init` 不会覆盖模型选择、文献或任务。多 Profile 无法唯一选择时使用
`init --source-profile '/path/to/profile'`；Zotero 自定义安装位置使用
`init --zotero-bin '/path/to/Zotero.app/Contents/MacOS/zotero'`。
环境路径记录在已忽略的 `.local-dev/runtime/environment.json`，不可指向正式数据目录。

模型配置需要更新时，退出两个 Zotero 实例，再执行：

```bash
./scripts/dev.sh sync-models
```

此命令先备份开发配置，再用正式模型配置覆盖；不会将正式后端地址复制过来。
密钥仅保存在本地私有文件，不输出到终端，不提交 Git。开发 Profile 不登录同步账号。

插件修改由脚手架监听并重新加载；窗口资源修改后可能需要重新打开对应窗口。
开发构建位于 `.local-dev/runtime/plugin-build/`，名称带“开发版”，不覆盖正式构建。
Python 修改后，以及开发结束时分别执行：

```bash
./scripts/dev.sh restart-server
./scripts/dev.sh stop
```

存在排队、运行或正在取消的任务时，停止和重启会拒绝操作：先在开发版任务管理器中
完成或取消任务。`stop` 只退出经过身份检查的开发进程，保留数据；随后正常打开
Zotero 即回到正式环境。若手动关闭开发 Zotero，后端可能仍在运行，用 `stop` 收尾。

所有开发数据在 `.local-dev/runtime/`：`profile/`、`library/`、`tasks/`、`config/`、
`cache/`、`logs/`、`samples/` 和 `backups/`。后端通过 `PDF2ZH_CONFIG_DIR` 隔离配置，
通过 `PDF2ZH_TRANSLATION_CACHE_DIR` 分别隔离 pdf2zh-next 与 BabelDOC 翻译数据库。
未设置这两个变量时保持正式版默认路径；字体和模型下载缓存继续共享。
开发管理不删除任何共享缓存，也不安装登录自启。

### 固定样本与验收

`prepare-samples` 使用测评分支提交 `ec8001d346856baad9dcb263e3aa2c3d9af69c09`
中的三篇固定论文版本：Attention `1706.03762v7`、BERT `1810.04805v2`、
ResNet `1512.03385v1`。记录下载来源和 SHA-256；校验不一致时停止。
下次启动开发 Zotero 时校验 Profile、文献库和 PDF 后，导入“开发测试”分类，按标签去重。
初始化、启动和样本准备不调用模型。少量页面翻译由用户从开发 Zotero 发起，
推荐先测试 Attention 第 3 页、BERT 第 3 页、ResNet 第 4 页；不自动运行完整多模型测评。

验收需覆盖插件重新加载、后端重启、任务进度、取消、PDF 生成和附件自动导入；
单元测试不能代替真实 Zotero 操作。针对开发环境的离线检查：

```bash
python3 -m unittest discover -s scripts -p 'test_dev.py'
uv run --directory server --locked python -m unittest discover -s tests -p 'test_development_paths.py'
```

### 故障恢复

- 正式 Zotero 仍在运行：正常退出后重试，脚本不会替你强行关闭。
- 8891 被占用：用 `status` 检查自己的开发进程；未知占用者由用户核实，不自动结束。
- 后端或插件未就绪：查看 `logs/server-console.log`、`logs/server.log`、`logs/plugin.log`。
- PID 失效或目录不匹配：停止操作并核对进程和 `environment.json`，不要删除文献库。
- 启动中断：先 `status` 再 `stop`；修复问题后重新启动，已下载 PDF 和任务保留。

共享逻辑位于 `scripts/dev.py`，macOS 入口为 `scripts/dev.sh`。Windows 后续增加原生
入口和进程适配后需独立验收；不复用管理正式安装的 Windows 启停脚本。

## macOS 本机源码部署

以下流程会替换本机正式安装，独立开发请使用上面的 `dev.sh`。

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

统一脚本同步插件、服务端、控制中心、锁文件和 Windows 脚本版本，提交并推送主仓库。
核心 CI 通过后，Windows CI 只构建一次 XPI、PyPI 包、Windows ZIP 和源码归档，验证
全新 Python 3.13/OCR 与安装生命周期。脚本取回完整的七项产物，校验提交、版本、大小
和哈希，再将同一批文件发布到 PyPI 和 GitHub；Homebrew 固定同一源提交。
本地不重复安装前端依赖或编译 Rust，也不重复执行核心测试。仅构建调试包时可在
Windows 运行 `scripts/release.sh <version> --no-push`。

已完成 Windows 构建和新电脑验收时，可在 `publish-pypi.yml` 指定 `tag`、成功的
`build_run_id` 并启用 `publish_github`，直接发布该次构建的同一份产物。流程先核对
构建状态、标签提交、版本和所有哈希，再发布 PyPI 与 GitHub，避免重新构建。

### 手动构建 Windows 候选版本

在 GitHub Actions 手动运行 `Build Windows release`，输入已提交的版本号和完整
40 位提交 SHA。工作流执行 `scripts/release.sh <版本> --no-push`，完成后上传
XPI、Windows ZIP、Python 包、对应源码和 `checksums.json`。
默认验证启动、运行环境、最终 ZIP 窗口及安装升级；需要完整的插件/服务测试、OCR、
回滚与迁移检查时勾选 `full_validation`。此步骤只构建和验证，不推送标签或发布渠道。
验证通过后按上面的发布流程使用同一提交和同一批产物，日常 CI 不执行完整 Windows 发布构建。

### 发布凭据与渠道

PyPI Trusted Publisher 必须绑定：

- PyPI project：`zotero-pdf2zh-pro`
- GitHub owner：`study-233`
- Repository：`zotero-pdf2zh-pro`
- Workflow：`publish-pypi.yml`
- Environment：`pypi`

Homebrew tap 是公开 source Formula：`study-233/homebrew-formula`。Formula 使用主仓库
公开 HTTPS 地址，固定 `python@3.13` 和 git revision，不发布 bottles。发布脚本直接更新
tap `main` 并等待 `formula-checks.yml`。

普通同版本恢复发布只接受原提交。经用户明确要求替换同版本客户端时，使用
`scripts/release.sh <version> --replace-existing <原提交完整 SHA>`：先确认原标签和后端
源码及打包输入未变，复用并核验原 PyPI wheel/sdist，不重新上传 Python 包。替换模式
在同一轮 Windows 构建中执行完整验证，通过后备份原发布，定点更新标签并先替换安装包、
再更新清单和发布说明。失败时恢复原资产和标签；核验记录分别保留客户端新提交与原
PyPI 包来源。已安装同版本的客户端需手动重装修订包：Zotero 重新安装 XPI，Windows
解压新 ZIP 后运行 `install.cmd -InstallRoot "原安装目录"`。同版本 EXE 会打开已安装的
控制中心，不能将该行为视为修订包已安装。旧版本 backfill 必须从对应 tag 构建。

## 许可证

产品改名不改变 AGPL 或第三方归属。不得删除上游许可证、第三方 notice 或 Git
历史中的贡献者信息。每个二进制发行物的对应源码必须可从公开版本标签获取。

## 解析性能、任务进程和诊断

实现与验收契约见 [任务诊断与取消规格](task-diagnostics-and-cancellation.md)。TaskManager 生产路径始终使用 spawn 执行器；translator 注入只供离线单测使用。新增模块必须随 Python wheel 一并打包。低优先级诊断不与可靠控制消息共用缓冲，父进程按单调时钟监督回收。

macOS 测试时临时目录可使用 `TMPDIR=/private/tmp`，避免 `/var` 与 `/private/var` 的路径别名影响既有路径断言。缓存隔离验证应使用 `pdf2zh_next.translator.cache.init_test_db()` 和 `clean_test_db()`，避免把测试内容写入用户翻译缓存。进程回收测试有界等待，真实永久阻塞任务仅在受控子进程中构造。
