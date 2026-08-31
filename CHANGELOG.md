# 更新记录

这里记录 `zotero-pdf2zh-pro` 的用户可见变化。旧项目的历史仍保留在 Git 历史中，
不作为本产品的版本序列。

## Unreleased

## v1.0.1 - 2026-08-31

- 将插件兼容范围更新为 Zotero 8.0 至 10.0.x，并新增源码、构建目录与 XPI 清单的一致性测试。
- Windows 安装包改用 ASCII 文件名，避免不同系统区域设置下的解压和启动问题。
- 修复 uv 启动器使用 Python 子进程监听端口时的 Windows 服务归属判断。
- 仓库改为公开，并通过 Release 中的 `update.json` 为 Zotero 插件提供自动更新。
- GitHub Release 发布 XPI、自动更新清单与 Windows ZIP，不再构建或上传朋友版制品。

## v1.0.0 - 2026-08-31

- 以独立产品 `zotero-pdf2zh-pro` 发布，使用新的插件 ID、设置空间、CLI、数据目录和发行物名称。
- 服务端发布到由 `study-233` 控制的新 PyPI 项目，不再安装旧开发者拥有的发行包。
- 新增 Windows 10/11 x64 一键安装、手动启停、日志查看和一键卸载，并明确不注册自启动。
- GitHub Release 提供 XPI 和 Windows 安装包；向无仓库权限的接收者分发时单独提供对应源码。
- 保留固定的 pdf2zh-next、BabelDOC 和 RapidOCR 核心快照及全部第三方许可证声明。
