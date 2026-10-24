# 按需下载术语库

让同一术语在全文中使用一致的译法。在插件设置的术语库区域：

1. 确认本地服务已连接，选择计算机与 AI、建筑、物理、环境或医学词库。
2. 点击“下载”，等待完成后**勾选启用**，可同时选择多个词库。
3. 提交新的翻译任务，所选术语会应用到全文翻译。

![术语库中已下载并启用“计算机与 AI”，其余类别可按需下载](../assets/guide/glossary-settings.webp)

下载词库用于英语到简体中文；其他语言保留勾选但不应用词条。自定义 CSV 按其语言设置使用。

词库按需下载，下载后可离线使用。“检查更新”刷新目录，点击“更新”才下载新版。

## 数据位置

词库由当前连接的服务端下载到有效数据目录的 `glossaries/` 子目录。以 `/health` 返回的 `workspace.path` 为准；`--data-dir` 优先于 `PDF2ZH_DATA_DIR`。多个 Zotero 连接同一服务端时共用已下载文件，每个 Zotero 按规范化后的服务地址保存自己的勾选项。

```text
<data-dir>/
  tasks.json
  <task-id>/
  glossaries/
    catalog.json
    <pack-id>/<version>/
      <sha256>.json
      <sha256>.meta.json
```

- Windows 安装器沿用安装根目录下的 `data/`，迁移数据目录时词库一起迁移。
- Docker Compose 沿用 `/app/server/translates` 的数据卷，无需额外挂载。
- Homebrew 默认使用 `<Homebrew前缀>/var/zotero-pdf2zh-pro/`，升级或清理旧版本不会删除此目录。
- Python / uv 安装默认使用系统用户数据目录：Windows 为 `%LOCALAPPDATA%/zotero-pdf2zh-pro/data/`，macOS 为 `~/Library/Application Support/zotero-pdf2zh-pro/`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/zotero-pdf2zh-pro/`。源码运行仍使用 `server/translates/`。
- 设置 `PDF2ZH_DATA_DIR` 或 `--data-dir` 时始终使用指定目录，不自动迁移其他安装的数据；开发环境和 Windows 控制中心继续使用各自的固定目录。备份以实际 `workspace.path` 为准。

### 升级后显示“未下载”

旧版 Python / Homebrew 服务曾把词库保存在安装环境内的 `translates/`。替换 Python 环境或清理旧版 Homebrew 时，服务可能找不到原来的下载，而 Zotero 的勾选项仍然存在。

首次启动修复后的服务时，若固定目录尚无数据，会复制仍存在的旧数据，校验完整性并更新任务内的文件路径。旧目录保留；原始任务记录另外保存在新目录的 `migration-backup/`。迁移出错时停止启动，原数据不会被空库覆盖；请先停止旧服务，并检查磁盘空间、权限和任务文件。如果发现多个有数据的旧版本目录，会提示先备份并合并，避免自动选错数据。固定目录已有数据后不再导入旧目录，已主动移除的词库也不会被恢复。

旧版本目录已经被清理、且没有备份时，需要重新下载一次；之后数据保存在固定目录中。更新前可先备份 `/health` 中的 `workspace.path`。

更新会保留旧版供已经开始提交的批次使用；移除会删除此词库的所有安装版本。已经创建的翻译任务保存了独立词条快照，重试、补译不依赖公共词库。清理任务不会删除公共词库。

## 合并规则

开始批量提交时固定所选词库版本和自定义 CSV。服务端创建每个任务时核对版本和 SHA-256，将实际词条及来源版本存入任务快照。

1. 当前目标语言适用的自定义译法优先。
2. 同词同译合并；多个词库对同词提供不同译法且没有自定义覆盖时，不强制该词译法，交由上下文翻译。
3. 合并结果不受勾选顺序影响。
4. 指定版本缺失或损坏时拒绝新任务，重新下载或取消勾选后再提交。

看不到词库管理功能时，先更新本地服务。

## HTTP 接口

开发接入见[服务端词库接口](../server/README.md#glossary-pack-api)。

完整来源、许可和整理方法见[词库来源说明](glossary-sources.md)。词条正文维护在公开仓库的 `glossary-data` 分支。
