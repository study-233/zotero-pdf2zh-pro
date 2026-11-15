# README 与官网维护

README、官网首页、教程和更新记录以 GitHub 最新正式 Release 为准。先核对该版本的安装包和平台，再写公开说明。开发中的功能记入根目录 CHANGELOG 的「未发布」。

| 内容 | 编辑位置 |
| --- | --- |
| 功能、预览、安装入口 | [README](../README.md) |
| 操作教程 | `website/src/content/docs/guide/*.mdx` |
| 版本变化 | [CHANGELOG](../CHANGELOG.md) |
| 开发、CI 与界面规范 | [开发维护笔记](development-notes.md)、[Actions](actions-testing.md)、[插件 UI](ui-guidelines.md) |

首页只写用户需要的操作、支持范围和入口。教程优先用真实截图展示菜单、按钮和完成状态。发布版的 Zotero 兼容范围取自该版插件清单，同步更新下载区与教程。命令、可复制的地址、平台限制和出错后的处理保留为文字。为图片写替代文本。

在独立分支预览下一版文档。正式制品可下载后，按[发布 skill](../.codex/skills/zotero-pdf2zh-pro-release/SKILL.md)更新主分支 README 和官网。README 末尾的 `<!-- release-version -->` 标记记录公开文档版本。保留既有锚点、上游署名和许可证。

修改 MDX 后执行 `python scripts/sync_guide_docs.py` 生成 [使用指南](user-guide.md)和[术语包指南](glossary-downloads.md)。新增章节时同步 `website/routes.json`。在仓库根目录验证：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_check_docs.py'
python3 scripts/sync_guide_docs.py --check
git diff --check
```

预览变动页面，核对图片、下载和旧链接。官网构建命令见[官网开发](../website/README.md)。
