> 以下为迁移前阶段记录。当前进度见 [Next.js / Memos 源码迁移](next-migration.md)。

# 官网 V2 实施记录

2026-10-29：P0 基线、P1 框架与兼容适配、P2 首页实现已完成；P3–P5 待实施。
原始[改版计划](v2-plan.md)已保存到仓库；本记录描述本次准备范围及实际证据。

- 工作分支：`codex/website-v2`；基线提交：`f35697c292891af701ad0872352537f1900302f0`。
- 本地插件、服务与 [GitHub 最新稳定 Release v1.9.7](https://github.com/study-233/zotero-pdf2zh-pro/releases/tag/v1.9.7) 一致；已核实 EXE、XPI 资产名称和 Release 哈希。
- Zotero 清单兼容范围：`8.0` 至 `10.0.*`，来自 `plugin/addon/manifest.json`；不是对各系统实机测试的声明。
- 目标架构：Astro + Starlight，当前使用 Memos 视觉参考；P1 已锁定 Astro 7.4.4、Starlight 0.42.7、TypeScript 6.0.3 与 pnpm 10.34.5。
- 本阶段保留旧构建与发布流程。未修改插件、服务、安装器或评测源数据；未提交、推送或部署。

## 基线与复现

2026-10-29 第二版预览后，用户要求重新分析导航和首页构图。营销导航从 64px 改为 56px，
将衬线品牌与较轻的导航层级分开；首页文案栏由固定 576px 改为 320–400px，真实截图分配更多宽度。
两张截图的“查看大图”浮标已删除，图片交互保留。教程、下载与更新记录的第二版结构不变。
同视口的参考、改动前后截图和后续验证见 [视觉复核记录](design-qa.md)。本地预览等待用户验收，不代表已获设计认可。

| 清单 | 内容 | 后续用途 |
| --- | --- | --- |
| [legacy-routes.json](legacy-routes.json) | 19 个 HTML 页的全部 ID、136 条 URL 映射、源 Markdown 锚点及三个首页跳转 | 保护路由与公开锚点；UI 内部 ID 仅作为基线参考 |
| [legacy-assets.json](legacy-assets.json) | 370 个输出文件的大小及 SHA-256；其中 68 个不可变公开下载；309 个只读评测源文件 | 新旧构建比较，防止改写公开数据与附件 |
| [screenshot-todo.md](screenshot-todo.md) | 12 张旧主图的审核清单及 3 张待拍新图 | P2/P3 真实素材准备 |
| [兼容检查脚本](../scripts/check_website_compat.py) | 检查目标页、中文锚点、下载文件、评测源文件和原 Markdown 锚点 | 独立于 Jinja2 的迁移门禁 |

旧构建生成 18 个主站页面，另复制了 `/benchmark/pilot/index.html`，合计 19 个 HTML 页面。
这个历史试验页也在兼容清单中，不能只按计划中的 15 篇教程计算完整 URL 范围。
实际构建含 `/benchmark/data/`、`/benchmark/data/formal/`、`/benchmark/pilot/`、
`/benchmark/experiment/`，以及根目录 `results.csv`、`results.json`、`pdfs.json`、`pdfs/`。
清单中的 URL 均包含 `/zotero-pdf2zh-pro/` 项目前缀。

Windows 复现使用已存在的 `.tmp/website-venv/Scripts/python.exe`（Python 3.13.15），
其 5 项构建依赖与 `requirements.txt` 完全一致；Node.js 为 24.19.0，CI 仍使用 Node.js 22。
首次准备环境时按 [开发维护笔记](../docs/development-notes.md#官网开发与发布)安装固定依赖。
下面的只读兼容检查也可直接使用 Python 3.12+：

```powershell
& .tmp/website-venv/Scripts/python.exe -X utf8 scripts/build_site.py
& .tmp/website-venv/Scripts/python.exe -X utf8 scripts/check_site.py
python -X utf8 scripts/check_website_compat.py
```

`--capture` 只用于首次冻结，已有清单时拒绝覆盖。迁移期间不要用新站结果重新生成旧基线。
HTML/CSS/JS 允许改版；检查脚本只按 `immutable` 标记强制下载文件字节一致。
`pages` 保存全部 DOM ID 供排查，真正需要保留的外部入口在 `targets` 中。
`browser_redirects` 的静态检查只确认目标存在，跳转行为需浏览器验证。
旧 Markdown 的标题锚点也已冻结；P3 生成聚合文档时须保留这些入口。

Windows 测试需 `-X utf8` / `PYTHONUTF8=1`，并将 `TEMP`、`TMP` 指向
`Workspace().run(...)` 创建的受管理工作目录。默认沙箱临时目录不可写，系统默认 GBK
也不能读取部分出版 JSON；这两类环境问题已通过重跑解决，未为此更改业务代码。

## 页面内容矩阵

以下 URL 均相对于项目前缀。教程迁移仍以现有正文为依据，不从 HTML 复制第二份正文。

| URL | 当前内容源 | 目标与验收重点 | 阶段 |
| --- | --- | --- | --- |
| `/` | `templates/home.html`、`install-start.html` | 既定定位与真实预览；唯一四步安装；平台切换只改变第一步 | P2 |
| `/guide/` | `docs/user-guide.md#quick-start` | 必要软件、隐私与流程总览 | P3 |
| `/guide/windows/` | 同文档 `#windows` | 安装器、联网要求、就绪状态与更新 | P3 |
| `/guide/macos/` | 同文档 `#macos` | Apple Silicon、Homebrew、Intel 限制 | P3 |
| `/guide/installation/` | 同文档 `#installation` | 安装 XPI 与服务连接成功状态 | P3 |
| `/guide/api-configuration/` | 同文档 `#api-configuration` | API / ChatGPT 套餐 / 本地来源，测试与保存 | P3 |
| `/guide/usage/` | 同文档 `#usage` | 提交第一篇 PDF、输出附件与状态 | P3 |
| `/guide/translation-options/` | 同文档 `#translation-options` | 场景、参数和必要警告 | P3 |
| `/guide/task-management/` | 同文档 `#task-management` | 批量、进度、中断补译与结果保护 | P3 |
| `/guide/selection-translation/` | 同文档 `#selection-translation` | 选词、选句、上下文和翻译记忆 | P3 |
| `/guide/glossary/` | `docs/glossary-downloads.md` | 下载、启用、自定义及归属 | P3 |
| `/guide/export/`（新增） | `plugin/src/modules/documentExport.ts`、现有导出说明 | PDF 的 Markdown / JSON / TXT 导出；核对真实菜单和限制 | P3 |
| `/guide/maintenance/` | `docs/user-guide.md#maintenance` | 插件与服务分别更新、迁移和卸载 | P3 |
| `/guide/troubleshooting/` | 同文档 `#troubleshooting` | 按问题检索、条件和排查路径 | P3 |
| `/guide/diagnostics/` | 同文档 `#diagnostics` | 诊断包、日志、隐私与反馈 | P3 |
| `/guide/uv/` | 同文档 `#uv` | 备用手动运行 | P3 |
| `/guide/advanced/` | 同文档 `#advanced` | Linux / Docker 进阶部署 | P3 |
| `/benchmark/` | `templates/benchmark.html`、`static/benchmark.js`、出版数据 | 四视图、计费语义、CSV、复制链接，先功能对等再延迟加载 3D | P1/P4 |
| `/benchmark/pilot/` | `publications/2026-10-26-interface-pilot-v1/index.html` | 原 URL 与文件字节保留 | P1 |
| `/changelog/` | 根目录 `CHANGELOG.md` | 自动生成；版本链接与历史锚点保留 | P1/P3 |

## 现有测试与迁移缺口

| 检查 | P0 结果 / 覆盖 | P1–P5 要补齐或调整 |
| --- | --- | --- |
| 旧站构建、`check_site.py` | 19 页的本地链接、锚点、图片和搜索目标通过 | 改造依赖旧搜索索引的检查，保留项目 base 约束 |
| `scripts/test_check_docs.py` | 15 项通过；版本标记、README 锚点、本地引用 | 加入新 MDX 单一来源与聚合文档验证 |
| `website/test_build.py` | 属于网站套件的 12 项；章节、下载哈希、版本、模板结构、代码块、搜索索引 | 将旧 DOM 断言改为功能断言，保留不变量 |
| `website/test_*publication.py` | 与构建测试合计 41 项全部通过；各批出版及审计状态 | 保留独立数据验证，不修改测评口径 |
| `validate_site.py` | 公开历史数据、PDF 哈希、公开状态检查通过 | 保留现有入口 |
| 三个 Node 测试文件 | 共 16 项通过；搜索、旧跳转、费用未知值、前沿、主题、PDF 来源及哈希 | 保留评测测试，搜索改为构建后的 Pagefind 中文检索 |
| `test_check_website_compat.py` | 新增 8 项通过；丢失页/锚点/附件、篡改费用、源文件变动、编码与 base | P1 将兼容检查接入新站验证入口，P5 接入 CI |
| 浏览器基线 | 本地真实项目前缀；首页、Windows、评测；1440/390，两种主题设置，共 12 张截图及额外 3D 截图 | P4 扩展到 360/768/1280 等宽度和完整键盘路径；修复评测强制暗色 |
| 可访问性与性能 | 尚未做 Lighthouse 或全面 WCAG 审核 | P4 检查对比度、焦点、减少动画、LCP/CLS；记录设备条件 |

网站发布工作流目前没有自动执行新增的兼容脚本；本次已手动验证，后续按阶段接入。
搜索、复制链接、截图放大、手机导航、完整首次使用路径需要新站端到端覆盖。

## 已发现的旧站缺口

这些是迁移前的实际表现，本阶段冻结证据，不将其写成新站已通过的验收结果：

- 搜索“安装失败”返回 0 条；“API”“补译”“划词”分别返回 16、8、9 条。P3 需要验证中文分词、内容命中及可用排错入口。
- 首页 `#method` 会跳转到评测页，但 `selectMode()` 随后将 hash 改为 `#view=speed`，没有展开测评说明。P4 要验证跳转后的实际内容状态。
- 评测 CSS 强制暗色，即使根节点为 `data-theme="light"` 也仍显示暗色。两种主题的基线截图一致，P4 必须改为共享主题。
- 旧浏览器收藏还可能包含 `/benchmark/#view=speed`、`#view=cost`、`#view=value`、`#view=table` 和 `#3d`；它们是运行时状态，不能作为普通 HTML ID 检查。

其余本次浏览器观测：四视图切换与 CSV 请求成功，三个主页旧锚点都到达评测页，
`#compare` 进入表格；已检查的 12 个页面状态无横向溢出、破图、页面异常或本地资源 HTTP 错误。
未执行真实模型调用、付费测试或新的模型评测。

证据目录为 `.local-dev/reports/20261029T093759-website-v2-p0-b805c339/`，已用 `.keep` 保留：
`checks.json` 与日志、`release.json`、`browser-results.json`、13 张 PNG、完整旧站 ZIP 和重建比较结果。
后续提交只纳入小型清单、准备文档和验证脚本，不提交这些生成产物。

## P1 实施与验证

新增独立 pnpm 工程、锁文件、Primer 基础颜色、共享页头/页脚与 Starlight 文档框架。
首页、15 篇教程、评测和更新记录已可构建，历史试验页继续复制；包含 404 页共 20 个 HTML。
构建和本地验收方式见 [官网开发](README.md)。

教程仍读取原 Markdown，构建时生成 Starlight 内容，保留旧中文标题 ID；未提前引入第二套手改正文。
适配器保持代码示例不变，处理中文链接编码和 GitHub 提示块的行号差异。
数据摘要、审计检查与复制顺序提取到共享 Python 模块，旧构建仍完整可用。
新增 ESM 工程会影响旧 JavaScript 测试的加载方式，测试已改为显式加载浏览器脚本的 CommonJS 导出，
未改动原评测脚本或旧站输出。

| 门禁 | P1 结果 |
| --- | --- |
| Astro 类型检查 | 9 个文件，0 错误、0 警告、0 提示 |
| 新站构建与链接检查 | 20 个 HTML、136 条旧 URL 映射通过，Pagefind 索引存在 |
| 数据与下载 | 68 个不可变下载和 309 个评测源文件哈希不变；10 个评测配置的数值与费用语义通过 |
| 旧站回退 | 重新生成 370 个文件，与 P0 全量 SHA-256 一致，无新增或缺失 |
| Python/Node 回归 | 41 项网站/出版、15 项文档、8 项兼容、6 项适配器、16 项浏览器逻辑测试通过 |
| 输出生命周期 | 19 项通过，2 项环境相关跳过；workspace 检查通过 |
| 浏览器 | 首页、Windows 教程、评测页在 1440/390 与深浅主题下无横向溢出或破图；手机导航、章节侧栏、代码复制控件、主题记忆、搜索快捷键、四视图与 CSV 下载通过 |

旧站强制暗色的问题已随共享主题解决。两项既有缺口仍待后续阶段：
“安装失败”搜索无结果（P3）；旧首页 #method 跳转后未展开说明（P4）。
P1 未宣称截图放大、真实产品截图更新、3D 延迟加载或完整 WCAG/性能验收已完成。
构建仍有 Starlight 默认 404 内容与空 i18n 集合的两条非阻断提示；404 页面正常生成。

P1 主报告位于 .local-dev/reports/20261029T100816-website-v2-p1-a9746d48/，
包括最终 verification.json、回归日志和旧构建逐文件比较；
13 张浏览器截图与结果保存于 .local-dev/reports/20261029T104449-website-v2-browser-da24311d/。
两份报告均已用 .keep 保留。

## P2 首页实现与验收

首页采用 1216px 最大宽度的左右布局，把双语阅读和划词精读合并到首屏切换区。
使用仓库中的两张真实截图，支持查看大图、原图链接和 Escape 关闭；图像保持原始像素。
截图完成了图像和源码文案复核，拍摄版本、系统、日期未知，仍待当前稳定版实机复核，
详见 [截图待办](screenshot-todo.md)，不能将本阶段记录当作新拍截图的证明。

安装区只有一套四步流程；Windows、macOS、其他平台仅切换第一步。
保留 EXE/XPI 稳定版直接下载和旧 #windows/#macos 入口，macOS 旁直接展示 Intel 限制。
补充能力提供批量、补译、术语库和文档导出四个入口；导出先指向现有使用教程章节，P3 再新增独立页面。
主导航支持点击外部收起、Escape 关闭以及同页安装跳转后收起；文档页保留原生分页与编辑链接。

切换组件按 [WAI-ARIA Tabs 模式](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)提供角色与状态，
支持左右键、Home、End、空格和 Enter。禁用 JavaScript 后，平台说明与产品图全部可见，
下载、教程、原图仍为普通链接。

| 门禁 | P2 结果 |
| --- | --- |
| 构建 / 类型 | 20 个 HTML 构建与链接检查通过；14 个类型检查文件，0 错误、警告或提示 |
| 首页响应式 | 360/390/768/1280/1440 × 深浅两种主题通过，无横向溢出、破图或页头控件重叠 |
| 安装与预览 | 平台键盘切换、其他三步保持不变、#macos 深链接、图片弹窗焦点返回、手机导航、翻译来源与首次翻译路径通过 |
| 渐进增强 | 禁用 JavaScript 后三个平台及两张截图可见，macOS 教程入口可用 |
| 按钮文字对比度 | 主按钮默认与悬停在两种主题下均 ≥ 4.5:1；暗色悬停蓝调整为 #1a63d8，实测约 5.50:1；不代表全站 WCAG 审核 |
| 兼容与回退 | 136 条旧 URL、68 个不可变下载、309 个源数据文件、10 个评测配置检查通过；旧站 370 个文件仍与 P0 全量哈希一致 |
| 回归 | 86 项网站/出版/文档/兼容/适配器/Node 测试通过；输出生命周期 19 项通过、2 项跳过；通用浏览器搜索、教程和评测四视图通过 |

主报告：.local-dev/reports/20261029T104838-website-v2-p2-2d945788/。
首页专项：.local-dev/reports/20261029T110411-website-v2-home-0e1949b8/，含 12 张截图。
通用浏览器：.local-dev/reports/20261029T110413-website-v2-browser-ccf86237/，含 13 张截图。
三份报告均以 .keep 保留。未变更 Pages 工作流，未提交、推送或部署。

## Memos 源码驱动首版（历史）

2026-10-29：按用户最新要求，以 usememos/dotcom 的真实布局组件和样式为依据重做首页。
保留 Astro + Starlight，接入 Tailwind 4、Lucide、Inter / Source Serif 4，中文标题使用 Noto Serif SC 600 子集。
首屏采用 1400px 容器内左右分栏，公共导航 56px；划词、安装及补充能力采用统一章节节奏。
真实截图与安装入口保留，主题控件移至页脚。Primer 和此前全宽 Hero 均不再是当前设计方向。

20 页构建、18 个 Astro 文件类型检查、六种宽度与深浅主题、搜索、教程及评测交互已通过。
同视口浏览器截图与 Memos 对比记录见 [视觉验收](design-qa.md)。
用户最终设计验收待反馈；线上构建、发布流程及插件/服务端版本未改动。

## Memos 第二版（当前，待用户预览验收）

2026-10-29：按已确认的分析方案完成字号、导航、安装、教程和更新记录重做。
首页品牌 22px、导航 16px、主标题 60px、介绍 20px，手机导航 56px；精简重复说明与独立能力/评测章节。
下载区改为本地服务和 XPI 两项，macOS 提供可复制的 Homebrew 命令。
教程有独立侧栏与 18px 正文，入口按三组任务组织；安装前准备移到独立页面并保留旧链接。
更新记录先显示最近 10 版摘要，可展开历史；34 个版本均有详情页，完整警告与下载链接来自 CHANGELOG。

类型检查 23 文件无错误、警告或提示；55 个 HTML、136 条旧 URL、68 个不可变下载和 10 组评测配置通过。
文档检查与 15 项测试、7 项内容适配测试、3 项提示块渲染测试通过。
首页六种宽度 × 两种主题，六类页面桌面/手机 × 两种主题及搜索、导航、复制、旧链接、评测交互通过。
另检查 320/768px 教程与更新页面，所有检查无页面横向溢出；最终控制台及请求错误为空。
与 Memos 的同视口对比发现并修正教程卡片间距、正文偏移、手机导航高度、字体子集和 Windows 换行提示框问题。
证据和有意差异见 [第二版视觉验收](design-qa.md)。

本轮完成第二版展示与交互，P3 的 MDX 正文迁移、实机素材审核及 P4/P5 全量门禁仍为后续工作。
本地预览运行于 8897；未修改线上发布流程，未提交、推送或部署。用户验收待反馈。

## 下一阶段入口

1. P3 逐章迁移 MDX、补齐导出页、实机复核截图和补齐中文搜索，并同步正文来源规范。
2. P4/P5 完成交互、性能、可访问性及 CI 门禁，再处理发布切换与回滚验收。

缺少已审核的新产品截图仍是 P3 素材任务。正式发布须完成当前稳定版真实产品截图审核、
新旧构建对比和线上项目前缀验收，不能用 P0 检查通过代替后续门禁。

## Primer 全站改造（历史，已被 Memos 方向替代）

用户预览 P2 后要求“全部转用 Primer”。此要求取代原计划中不引入 Primer React 的限制；
该阶段保留为历史记录；当前实现以文末 Memos 改版记录与 DESIGN.md 为准。

主站正式接入 @primer/react、@primer/css、@primer/primitives 和 @primer/octicons-react。
首页重新组织为居中介绍、大幅真实阅读预览、四步安装、功能卡片和评测入口；
公共导航、下载按钮、标签、卡片、教程侧栏、面包屑、Markdown 正文及评测控件共用 Primer。
静态组件由 Astro React 在构建时渲染，主题与 Starlight 同步；交互仍可渐进增强。
截图像素、源文档、版本来源、已出版评测数据和不可变下载未改动。

本轮构建与类型日志、截图和回归记录统一索引于受管理报告
.local-dev/reports/20261029T112125-website-primer-67600de6/。
既有 #method 未自动展开问题仍留在评测交互阶段；本轮不替代 P3/P4 的完整验收。
未变更 Pages 工作流，未提交、推送或部署。
