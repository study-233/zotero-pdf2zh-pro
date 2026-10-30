# 官网开发

官网已迁为 Next.js App Router、React、Tailwind CSS 和 Fumadocs。Memos 的原始组件、样式及动画直接迁入项目，来源与适配范围见 [源码记录](SOURCES.md)。旧的 Astro 页面和依赖已移除。

本地实现与用户预览验收已完成，线上尚未发布。阶段状态见 [迁移记录](next-migration.md)，此前方案保留在 [V2 历史计划](v2-plan.md)。

## 环境与构建

需要 Node.js 24+、pnpm 11.24.0、Python 3.12/3.13。
Python 依赖在 [requirements.txt](requirements.txt)。构建包装脚本优先使用仓库的 `.tmp/website-venv`，也可通过 `PDF2ZH_WEBSITE_PYTHON` 指定解释器。

在仓库根目录执行：

```bash
pnpm --dir website install --frozen-lockfile
pnpm --dir website docs:check
pnpm --dir website test
pnpm --dir website build
pnpm --dir website preview
```

预览地址：<http://127.0.0.1:8897/zotero-pdf2zh-pro/>。
开发模式为 `pnpm --dir website dev`，但静态搜索、旧网址与发布路径必须用生产构建验收。
可设置 `NEXT_TELEMETRY_DISABLED=1` 关闭 Next 遥测。

构建先准备公开数据，再导出到 `website/out/`；校验通过后复制到 `dist/site/`。
`finish-next.mjs` 同时处理 Next 16 Windows 导出的分段预加载文件名，确保浏览器导航与 GitHub Pages 的静态路径一致。
最终产物包含 `.nojekyll`，发布不依赖 Node 服务。

## 内容与设计

- 16 篇教程以 `src/content/docs/guide/*.mdx` 为唯一正文源。修改后运行 `pnpm --dir website docs:sync`，同步使用指南与术语库兼容文档；不要手改汇总。
- `routes.json` 维护侧栏顺序和分组。JSON frontmatter 和 Markdown 兼容正文保留；指南不嵌入网页专用交互。
- 侧栏、目录、搜索、代码复制由 Fumadocs 提供。中文词语使用多语言静态索引，保留旧标题锚点和旧入口跳转。
- 更新记录直接读取 CHANGELOG，版本来自插件元数据。先展示最近 10 版，旧版本可展开，每版有独立详情页。
- Memos 的全局颜色、排版、1400px 容器、56px 导航、Hero 动画与更新列表组件均来自固定提交。中文适配集中在 `src/app/zotero.css` 与教程样式，字体为本地 Inter、Source Serif 4、Noto Sans SC、Noto Serif SC。
- 首页保留项目吉祥物、双语阅读器与划词精读截图；没有“查看大图”浮标。下载区仅保留本地服务、XPI 和必要平台选择。
- 主题由 next-themes 统一控制，支持浅色、深色与跟随系统。动画尊重减少动画设置。
- 模型测评仍使用既有出版数据、数据标记和 Plotly；只在测评页加载图表资源，离开页面清理观察器和绘图实例。
- `.generated-data/`、`.generated-public/`、`public/`、`.source/`、`.next/`、`out/` 均为忽略的构建产物。

## 验证与回退

```bash
python scripts/check_docs.py
python -m unittest discover -s scripts -p 'test_check_docs.py'
python -m unittest discover -s website -p 'test_*.py'
python benchmarks/translation/validate_site.py
node --test website/test_site.cjs benchmarks/translation/test_ui.cjs benchmarks/translation/test_pdf_viewer.cjs
node website/test_next_browser.cjs
node website/test_motion_browser.cjs
```

浏览器测试需要 Playwright 和 Chromium。可通过 `PLAYWRIGHT_MODULE`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指向既有工具环境；默认验证 8898 端口，可用 `WEBSITE_ORIGIN` 改为 8897。候选预览：`python scripts/serve_site.py --port 8898 --directory website/out`。
证据保存在 workspace helper 管理的 `.local-dev/reports/`，覆盖两种主题、桌面和手机、中文搜索、旧锚点、下载切换、复制、测评四视图和客户端页面往返。
动画对照脚本默认使用 8897，并访问 Memos 官网，记录相同视口截图、导航几何、动画时序、减少动画和无脚本入口。

构建强制校验 136 个旧 URL 映射、68 个不可变下载附件、309 个测评源文件及当前 10 组测评数值。不得为通过构建而刷新这些基线。

旧 Jinja 构建仅保留为数据验证和应急回退：`pnpm --dir website build:legacy`，会替换 `dist/site/`，不要与新构建并发运行。
迁移前源码与可用产物已有独立恢复归档。唯一发布入口仍为 `.github/workflows/website.yml`；功能分支通过 PR 校验，合并到 `main` 后由工作流发布。
