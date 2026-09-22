# 按需下载术语库

在 Zotero 设置中连接支持词库管理的服务端，然后下载需要的类别并勾选。下载成功不会自动启用，可同时选择计算机与 AI、建筑、物理、环境和医学。下载词库目前用于英语到简体中文；切换到其他语言时保留勾选，但不应用这些词条。自定义 CSV 沿用原有语言规则。

词库正文不随 XPI、Python 包或 Docker 镜像安装。服务启动、打开设置和提交翻译都不会自动下载或更新词库。点击“检查更新”只刷新目录；点击“更新”才下载正文。已有词库可离线使用。

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
- Python / Homebrew 沿用服务端 CLI 的数据目录选项。需要自行指定持久化位置时设置 `PDF2ZH_DATA_DIR`，或启动时传入 `--data-dir`。默认位置仍为已安装服务模块旁的 `translates/`；备份应以实际 `workspace.path` 为准。

更新会保留旧版供已经开始提交的批次使用；移除会删除此词库的所有安装版本。已经创建的翻译任务保存了独立词条快照，重试、补译不依赖公共词库。清理任务不会删除公共词库。

## 合并规则

开始批量提交时固定所选词库版本和自定义 CSV。服务端创建每个任务时核对版本和 SHA-256，将实际词条及来源版本存入任务快照。

1. 当前目标语言适用的自定义译法优先。
2. 同词同译合并；多个词库对同词提供不同译法且没有自定义覆盖时，不强制该词译法，交由上下文翻译。
3. 合并结果不受勾选顺序影响。
4. 指定版本缺失或损坏时拒绝新任务，重新下载或取消勾选后再提交。

旧服务端可以继续普通翻译及其已支持的自定义 CSV；下载词库需要 `/health` 的 `capabilities.glossaryPacks` 为 `true`。

## HTTP 接口

| 接口 | 行为 |
| --- | --- |
| `GET /glossaries` | 本地目录、安装版本、条数、字节数和下载状态，不联网 |
| `POST /glossaries/check-updates` | 手动刷新远端目录，失败时保留原目录 |
| `POST /glossaries/<id>/download` | 异步下载当前目录版本；可传 `{"version":"…"}` |
| `POST /glossaries/<id>/cancel` | 取消进行中的下载，保留已安装版本 |
| `DELETE /glossaries/<id>` | 移除全部安装版本；进行中的下载须先取消 |

下载接口不接受调用者指定 URL 或路径。正文地址限定项目公开仓库的固定提交，并校验 SHA-256、文件大小、格式和词条数量。临时文件通过全部校验后才安装。

`POST /tasks` 增加可选 `glossaryPacks` 数组：

```json
{
  "glossaryPacks": [
    {"id": "medicine", "version": "目录返回的版本", "sha256": "目录返回的完整 SHA-256"}
  ],
  "glossaryEntries": [
    {"source": "自定义原词", "target": "自定义译法", "tgt_lng": "zh-CN"}
  ]
}
```

完整来源、许可和整理方法见 [词库来源说明](glossary-sources.md)。词条正文与其来源说明单独维护在公开仓库的 `glossary-data` 分支。
