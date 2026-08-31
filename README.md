# zotero-pdf2zh-pro

面向 Zotero 7 及以上版本的 PDF 翻译插件，配套本地 Python 服务调用
`pdf2zh_next` 完成翻译。

当前统一版本：<!-- release-version --> `1.0.0`

## 安装前说明

`zotero-pdf2zh-pro` 是独立产品，不会读取或迁移旧插件设置和旧服务数据。
如果安装过旧产品，请先停止并卸载旧服务、从 Zotero 移除旧插件，避免两个服务同时占用
`127.0.0.1:8890`。

朋友用户由维护者直接提供 XPI 和 Windows 安装包。仓库为私有仓库，向无仓库权限的
接收者分发二进制文件时，应同时单独提供对应版本的源码归档。

## 安装 Zotero 插件

1. 获取维护者提供的 `zotero-pdf2zh-pro.xpi`。
2. 在 Zotero 中进入 `工具 -> 插件`。
3. 点击右上角齿轮，选择 `Install Add-on From File...`。
4. 选择 XPI 并重启 Zotero。

仓库为私有仓库，插件不配置自动更新。新版本由维护者重新发送 XPI。

## Windows 10/11 x64

解压 `zotero-pdf2zh-pro-windows-x64.zip` 后双击：

- `安装.cmd`：安装官方 uv、uv 托管的 Python 3.13，以及公开 PyPI 上的
  `zotero-pdf2zh-pro==1.0.0`；安装完成后不会自动启动。
- `启动服务.cmd`：需要翻译时手动启动。
- `停止服务.cmd`：停止本项目管理的服务进程。
- `查看日志.cmd`：打开服务日志。
- `卸载.cmd`：卸载程序，并选择是否删除数据和日志。

安装不需要管理员权限，不创建任务计划、Windows Service 或登录启动项。数据位于
`%LOCALAPPDATA%\zotero-pdf2zh-pro\data`，日志位于
`%LOCALAPPDATA%\zotero-pdf2zh-pro\logs`。

## macOS

Homebrew tap 是维护者自用的私有仓库，需要两个私有仓库的 SSH 权限：

```bash
brew tap study-233/formula git@github.com:study-233/homebrew-formula.git
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

## 更新

- 插件：安装维护者发送的新 XPI。
- Windows：重新运行新版本中的 `安装.cmd`，新产品数据会保留。
- uv：`uv tool upgrade zotero-pdf2zh-pro`。
- Homebrew：`brew upgrade zotero-pdf2zh-pro` 后重启服务。

## License

本项目采用 `AGPL-3.0-or-later`，并保留所有上游项目和第三方组件的许可证与归属，
见 [LICENSE](LICENSE) 和 [server/THIRD_PARTY_NOTICES.md](server/THIRD_PARTY_NOTICES.md)。
