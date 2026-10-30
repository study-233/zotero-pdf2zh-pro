# GitHub Star 数展示验收

final result: passed

2026-10-30。直接复用 Memos 固定提交中的 GitHub + 实心 Star + 数量布局及 useGithubStarCount，替换为本项目仓库，保留 K/M 缩写和 24 小时缓存。

- source visual truth path：[Memos 浅色导航](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/memos-light-header.png)和[深色导航](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/memos-dark-header.png)；implementation screenshot path：[本地浅色导航](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/zotero-light-header.png)和[深色导航](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/zotero-dark-header.png)。均在 Chromium 1440×1000、deviceScaleFactor=1 下截取顶部 1440×80 像素；手机检查为 390×1000。
- [图标和数量局部并排对照](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/github-comparison.png)以原生 1:1 尺寸展示。字体均为 14px/600，按钮高 32px、图标 14px、间距 6px；沿用原颜色变量和悬停样式。GitHub SVG 与实心 Star 直接来自原组件，没有新绘制图标。数量文字为本项目真实数据，因此按钮宽度小于 Memos，属于预期差异。
- [浏览器检查](../.local-dev/reports/20261030T045533-website-github-stars-c151d2b8/validation.json)：真实接口返回 11，深浅主题与手机菜单一致；刷新使用缓存、没有重复取数。另验证 0、K 缩写、无效响应、503 和过期缓存回退。无脚本错误，无横向溢出。
- 首次取数失败时保持可点击的 GitHub 链接，不把缺失数据显示为 0；已有缓存时保留缓存值。可访问名称包含 GitHub 和数量含义。
- 构建、TypeScript、静态链接检查、文档检查和 15 项文档测试通过。已检查字体、布局、颜色、图标质量和内容，无待修正 P0/P1/P2；本次仅本地预览。

---
# Zotero 阅读窗口外观验收

final result: passed

2026-10-30。根据用户提供的 Zotero 截图，为首页双语论文增加菜单、文档标签和阅读工具栏。上方全部为装饰性界面，只有论文区域支持滚动；本次通过表示内部验收通过，待用户预览认可。

## 对照证据与范围

- source visual truth path：[用户提供的截图](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/source-zotero.png)，2174×1464 像素，来源设备密度未知。
- implementation screenshot path：[深色阅读器](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/dark-1440-reader.png)，884×590 像素；浏览器视口 1440×1000，deviceScaleFactor=1，阅读器 CSS 宽约 883px。仅将参考图等比例缩小到 884px 宽，未变形或裁切论文内容。
- [完整窗口并排对照](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/compare-final.png)与[顶部栏局部并排对照](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/chrome-final.png)：同为深色、第一页面、未滚动。另检查 390×1000 的深浅主题与 320、768、1024px 布局。
- 内容差异为预期：参考图为无人机论文，本地为用户指定的完整 15 页 Attention Is All You Need；两篇论文的标题大小、版式和首屏文字密度不作像素匹配。保留原始论文版面。

## 发现、修正与复查

- P2：初版误取旧 Zotero 灰色标志，且标志过大。证据：[初版局部对照](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/chrome-initial.png)。改为从 Zotero 10.0.6 程序提取的原生 16px 红色 Z 图标，保留正确边距；最终对照确认已修正。PDF 标签图标直接来自同一安装包，资产与哈希记录于 static/zotero-reader/sources.json。
- 字体与排版：顶部采用 Segoe UI / Microsoft YaHei，菜单与工具文字 11px、标签 12px，保持原生应用紧凑层级。首页标题与正文原有字体不变，长标签省略，手机未发生文字覆盖。
- 间距与布局：三层顶部栏分别为 29/34/36px，总高 99px；参考图按同宽缩放后约 94px，额外高度用于小尺寸可读性。纸面边距 14px，滚动时顶部栏不移动。窄屏隐藏部分装饰图标，保留标题与主要阅读器轮廓。
- 配色：深灰菜单与工具栏、灰色选中标签、蓝色文件夹、蓝灰纸面与参考图一致方向；浅色主题使用白纸和浅灰窗口。纸面用 CSS 映射深色显示，未改变源图文件。
- 图片质量：15 页均为 2800×1812 WebP，沿用完整双语 PDF 的真实渲染，首张优先加载、其余延迟加载。英文、中文及图表全部保留，无占位图片。
- 文案与交互：文库名用“我的文库”，标签名采用论文真实标题；菜单和工具栏全部 aria-hidden，无按钮、输入框、链接或点击处理。页码属于静态外观。正文区域可聚焦，支持滚轮、键盘和触摸滑动。

## 验证

- [浏览器结果](../.local-dev/reports/20261030T043929-website-zotero-frame-6edc7fed/validation.json)：四组深浅/桌面/手机用例中全部 15 页成功加载；滚轮和 Ctrl+End 均可到达最后一页，手机分别验证横向与纵向触摸滑动。无脚本或 HTTP 错误。
- 320–1440px 检查范围内主页与顶部栏均无横向溢出；手机论文区域允许内部横向滚动以保留阅读尺寸。
- 生产构建与 TypeScript 检查通过；58 个 HTML、136 个旧 URL、68 个不可变下载链接和 10 组测评配置检查通过。文档检查与 15 项文档检查器测试通过。
- 最终截图已复查字体、间距、颜色、素材和内容五个方面，无未解决 P0/P1/P2 问题。仅本地预览，未提交或部署。

---
# Next.js / Memos 源码迁移验收

2026-10-30。当前实现使用 Next.js、React、Tailwind CSS、Fumadocs 和固定提交的 Memos 源码。用户已认可框架和页面设计，并要求继续优化教程、更换图片；当前仍未提交或部署。此前 Astro 记录保留在后文，不代表当前实现。

## 教程与产品图片更新

- 16 篇教程的摘要改为具体操作；正文围绕安装、连接、配置、翻译与阅读组织，详细参数和排错信息收进折叠区。保留既有锚点，同步 Markdown 汇总。
- 原 12 张主图全部替换，补充 6 张教程图和 2 张首页缩略图。新图使用 Windows 11、Zotero 10.0.6、插件与服务 1.9.7 的实际界面，拍摄来源和边界见 [截图记录](screenshot-todo.md)。
- Windows 安装与就绪图只保留操作区域；手机检查发现完整阅读场景中的词典文字过小，教程改用卡片局部图，首页保留完整阅读场景。
- 直接使用 Fumadocs ImageZoom；验证点击、键盘 Enter、Esc 和焦点返回。放大图没有常驻文字浮标。截图元数据作为 MDX 配置依赖，换图尺寸可触发内容重新编译。
- [本轮截图与验收证据](../.local-dev/reports/20261030T021457-website-guide-refresh-5dabdcca/)：原生 PNG 母版、旧稿归档、web-* 浏览器截图、browser-guides.json、zoom-results.json；浏览器视口为 1440×1000 与 390×1000，覆盖深浅主题。
- [网站功能回归](../.local-dev/reports/20261030T030032-website-next-browser-e750f360/)通过，包含搜索、导航、主题、平台切换、复制和测评。最终构建生成 59 个路由，58 个 HTML 的链接、136 个旧 URL 和 68 个不可变附件检查通过。
- 本次隔离 Zotero、安装向导与临时服务已关闭，本地开发插件构建已恢复；用户原有服务和网页预览保留。

## 当前证据

- [全站浏览器回归](../.local-dev/reports/20261029T172020-website-next-browser-704a7236/)：`results.json` 为 passed，脚本和 HTTP 错误为空。截图覆盖首页、教程入口、Windows 教程、更新列表、版本详情和模型测评的桌面/手机、深浅主题。
- [Memos 实站与本地截图、动画对照](../.local-dev/reports/20261029T172554-website-next-visual-motion-f3d14c17/)：`results.json` 为 passed。原始截图使用 Chromium、deviceScaleFactor=1、1440×1000 和 390×1000，不缩放网页；分别保存 `memos-*`、`zotero-*`。教程参考为 Memos 文档入口，本地为实际安装文章，用于核对文档布局与文字可读性，不比较内容高度。
- [构建及测试记录](../.local-dev/reports/20261029T172118-website-next-validation-92661d87/)与[恢复归档](../.local-dev/reports/20261029T163428-website-next-migration-50a2dd39/)。

## 视觉对照与迭代

已查看参考和实现的原始截图、手机页面及动画 50ms / 1000ms 帧。Memos 与本地导航均为 56px 高；1440px 视口下导航容器为 1400px、x=20，首页标题列 x=36、宽 576px。中文标题采用 Noto Serif SC 和 1.2 行高，正文采用 Noto Sans SC；英文沿用 Inter / Source Serif 4。中文行高、项目名称与真实截图比例导致的高度差异是产品适配，不声明像素一致。

本轮发现并修正：

1. 首页初次替换误用了附件局部图，改用完整的真实双语阅读器截图；沿用 MemoHeroMock 外层圆角、边框和阴影，保留图片比例，没有“查看大图”浮标。
2. 教程入口的历史锚点在 flex 布局内产生额外留白，改为隐藏容器，保留旧链接跳转。
3. 测评页浅色控件与全局语义变量冲突，修正背景和文字变量；四视图、筛选和排序均复核。
4. Fumadocs 中文翻译键适配当前版本；修正教程链接重复 base path、静态分段预加载路径及客户端切换 favicon 请求。

原版文案动画为 650ms，产品动画为 800ms、延迟 120ms；两站浏览器记录一致，原 CSS 曲线保留。减少动画模式无运行中的动画，内容完全可见。首页不加载 Plotly。

## 功能与检查范围

- 生产构建和 TypeScript 检查通过；生成 59 个静态路由，58 个 HTML 文件通过链接检查。
- 136 个旧 URL 映射、68 个不可变附件、309 个测评源文件及 10 组测评数值保持有效。
- 浏览器检查通过：320–1920px 首页无横向溢出；手机菜单、键盘操作、中文搜索、命令复制、平台切换、旧锚点、主题持久化、测评四视图、CSV 和客户端页面往返。真实图片无损坏，未发现阻止预览的布局问题。
- 文档、内容同步、数据准备、兼容映射、站点 Python、工作区单元测试、既有 Node 测评与 PDF 查看器测试通过；Markdown 内容插件测试通过。
- 工作区目录审计仍报告本轮迁移前遗留的 22 个临时目录缺少 `.workspace.json`，因此该批检查的总退出码为 1。站点测试通过不代表工作区目录审计通过；未删除这些旧目录。
- 远端 CI 未运行、线上未发布；当前截图已在后续教程更新中替换，拍摄证据见本文“教程与产品图片更新”。

---

# 历史：Memos 参考第二版：首页与导航复调

final result: passed

2026-10-29。用户否定前次预览的导航比例和首屏构图，并要求移除“查看大图”。
本次 passed 只表示反馈对应的修正已完成内部检查，可以预览；不表示用户已认可设计。

## 当前复调：证据与处理

source visual truth path / implementation screenshot path：
[首页复调报告](../.local-dev/reports/20261029T153128-website-home-refinement-303538c2/)。
参考为当天浏览器实际加载的 [Memos 官网](https://usememos.com/)，同时读取公开 SiteHeader / Hero 组件。
Windows Chromium、浅色、deviceScaleFactor=1、减少动画；参考和本地均使用 1920/1440/1280/1024/900/390 × 900 视口。

- 原图：memos-{width}.png、before-{width}.png、after-{width}.png。
- 初始并排：initial-compare-1440.png、initial-compare-1024.png。
- 最终三方并排：final-compare-1440.png、final-compare-1024.png、final-compare-390.png。
- 导航原尺寸局部并排：header-comparison.png，三张各截顶部 80px，另加 40px 证据标签。
- 几何与字体：before-metrics.json、after-metrics.json、inspect.json。

1. P1：导航原为 64px，参考为 56px；原品牌和导航均使用相近的无衬线层级。
   统一为 56px，品牌使用 24px 衬线字标与 36px 原图标，导航 14px / 600。
   保留 Zotero 名称与图标，未复制 Memos 品牌。手机品牌 18px / 32px，触控区域保留 44px。
2. P1：固定 576px 左栏直接沿用英文布局，中文标题实际仅约 312px 宽。
   1440px 下文字末端与截图之间空出约 312px；1024px 下截图仅剩 368px 宽。
   左栏改为随视口变化的 320–400px，列间距 48px；1440px 下截图由 744px 增至 920px，
   实际文字到图片的间隙缩至约 136px；1024px 下截图增至 624px。
   左边缘仍与品牌、容器对齐，文字块与截图垂直居中。1920px 宽屏下，左边缘与参考同为 276px。
3. P2：“查看大图”浮标覆盖真实产品截图，已从两张截图中移除。
   鼠标点击、键盘打开图片、Esc 关闭和焦点返回经过回归。

五项复核：字体保持中文标题 Noto Serif SC 与正文微软雅黑（CDP 实际字体确认），导航层级重新调整；
间距按中文标题长度重新分配；灰阶与蓝色强调沿用；真实截图维持原比例，无遮挡；首页文案未增加。
最终并排图和单张原图均已查看；本轮发现的 P1/P2 已修正，未发现阻止预览的 P0/P1/P2 问题。
产品截图比例、品牌名称及搜索入口与 Memos 不同，属于保留产品身份与现有功能的有意差异。

## 当前验证

- Astro 检查 23 个文件，0 错误、0 警告、0 提示；生产构建与 55 页链接检查通过。
- 136 条旧 URL、68 个不可变下载、10 组原评测配置和 Pagefind 索引检查通过。
- [首页回归](../.local-dev/reports/20261029T154056-website-v2-home-a2ea4122/)：320/360/390/768/1280/1440 × 深浅主题；布局、下载、平台切换、命令复制、图片弹窗、移动菜单和无脚本入口通过。
- [全站回归](../.local-dev/reports/20261029T154229-website-v2-browser-0514f826/)：教程、更新记录、搜索和评测功能通过；共用导航未造成页面溢出或破图。另查看了完整深色桌面与手机首页。
- 文档链接检查、15 项文档测试、7 项内容适配测试通过。

下面保留前次第二版实施的历史检查记录。其结论曾用于提供预览，随后被用户反馈修正；当前首页和导航参数以上文为准。

## 前次第二版复核（历史）

首版中字号偏小、首页说明过多、教程与更新记录仍偏默认文档布局的问题，按第二版方案重做。

## 来源、视口与对比方法

参考 [Memos 官网](https://usememos.com/)、[教程](https://usememos.com/docs)、
[入门文章](https://usememos.com/docs/getting-started)与[更新记录](https://usememos.com/changelog)。
结构依据为 usememos/dotcom 的 SiteHeader、DocsSidebarLayout、EditorialIndexShell/List；本项目继续使用 Astro + Starlight。

source visual truth path / implementation screenshot path：
[本轮报告目录](../.local-dev/reports/20261029T141016-website-second-version-4e39a161/)。

- 参考：05-memos-home-1440.png、06-memos-docs-1440.png、07-memos-start-1440.png、08-memos-changelog-1440.png。
- 实现：01-home-1440.png、02-guide-1440.png、03-windows-1440.png、04-changelog-1440.png。
- 全景并排：compare-home.png、compare-docs.png、compare-article.png、compare-changelog.png。
- 局部并排：focused-header.png、focused-sidebar.png、compare-install.png。
- 手机并排：compare-docs-mobile.png；首页、教程、更新记录另有各自 390px 截图。
- 其他状态：final-dark-home/guide/changelog.png、macos-install-1440/390.png、preparation-callout.png。

Windows Chromium，浅色初始状态，减少动画，deviceScaleFactor=1。
桌面原图与 CSS 视口均为 1440×900；手机均为 390×844。并排原图为 2880×940 / 780×884，
顶部额外 40px 只是证据标签，不属于页面。没有按产品截图比例缩放网页。
局部侧栏对比直接截取每张图左侧 280×900；导航截取 1440×80，保持 1:1。
安装区为自然高度的元素截图，比较信息层级和操作布局，不将不同产品的区块高度视为像素误差。
Memos 的笔记、安装方式与文档内容不同于 Zotero，未将这类内容差异算作布局缺陷。

## 发现、修正与再次检查

1. P1，首版整体密度与层级不符合用户要求。第二版放大导航和正文，精简首页文案，
   下载区改为本地服务与 XPI 两行；教程改为独立侧栏，更新记录改为列表与详情。
   证据：原始分析报告 20261029T131523-website-v2-design-audit-2321fe69 与本轮四组 compare 截图。
2. P2，初轮教程卡片受到正文标题规则影响，标题产生 32px 顶部间距、卡片过高；
   正文左边缘也因 Starlight 默认居中偏离侧栏。初轮证据为 iteration-1-docs.png、iteration-1-article.png、iteration-1-docs-mobile.png。
   已限定正文样式作用域，正文在侧栏后 40px 开始，宽度上限 752px；入口卡片恢复紧凑对齐。
   再次证据为最终 compare-docs.png、compare-article.png、compare-docs-mobile.png。
3. P2，手机导航变量被主题选择器覆盖，实际仍为 64px，目录图标没有居中。
   已修正变量优先级和图标对齐，最终手机导航 56px；320/360/390px 均无品牌或按钮重叠。
4. P2，更新记录标题缺少三个本地字体字形，回退到宋体。已补全 Noto Serif SC 子集。
   inspect.json 的浏览器实际字体记录确认四个标题汉字均使用 Noto Serif SC SemiBold。
5. P2，教程中的 [!TIP] 原样显示。实际原因为生成文件使用 Windows CRLF，而插件仅匹配 LF。
   已兼容两种换行并补充完整 Markdown 渲染测试，保留强调、代码和链接。
   构建明确刷新内容缓存，最终提示框见 preparation-callout.png。

上述问题均在修正后重新捕获和查看。当前无待修复的 P0/P1/P2 视觉问题。

## 五项必查表面

| 项目 | 最终结果 |
| --- | --- |
| 字体 | 桌面品牌 22px、导航 16px、主标题 60px、介绍 20px；教程正文 18px，H1 36px，H2 28px；版本摘要 18px。浏览器确认中文正文使用微软雅黑，营销及更新记录标题使用本地 Noto Serif SC，西文使用 Inter / Source Serif 4。 |
| 间距与布局 | 首页保持左右分栏与真实截图；下载区 800px；教程侧栏 264px、正文 752px、右侧目录 208px；更新记录 800px 单列。手机只有一个教程目录按钮，卡片与下载操作纵向排列。 |
| 配色 | 白色/近黑背景、中性灰文字、少量蓝色链接与选中态。两种主题可读；首页主要操作与介绍文字的对比度检查通过。未将此结果扩大为全站 WCAG 认证。 |
| 图片 | 保留 Zotero 标志及仓库真实双语/划词截图，原比例，无替代 UI 绘图。支持放大、原图链接和焦点返回；截图自身的浅色 PDF 画布在深色站点保持原样。 |
| 文案 | 首页减少说明，安装只呈现两项必要组件及平台限制；配置、费用与数据去向保留在安装前准备和配置教程。更新摘要由 CHANGELOG 提取，完整升级警告、下载与比较链接在详情中保留。 |

与 Memos 的有意差异：中文阅读字号更大；Zotero 产品图宽高比不同；首页下载为两项必需组件，
不复制 Memos 的 Docker 安装结构；教程入口按 Zotero 任务组织；更新列表先显示最近 10 版。

## 功能验证与证据

- Astro 类型检查：23 个文件，0 错误、0 警告、0 提示。
- 生产构建：55 个 HTML 的链接与资源通过；136 条旧 URL、68 个不可变下载、10 组原评测配置与 Pagefind 索引通过。
- 文档检查及 15 项文档测试通过；7 项内容适配测试、3 项 Markdown 提示框渲染测试通过。
- [首页浏览器报告](../.local-dev/reports/20261029T143353-website-v2-home-80215d83/)：320/360/390/768/1280/1440 × 深浅主题；下载、平台键盘切换、macOS 深链接、命令复制、图片放大、教程路径及无脚本基本入口通过。
- [页面浏览器报告](../.local-dev/reports/20261029T144442-website-v2-browser-4e31989d/)：首页、教程入口、Windows 文章、更新列表、版本详情、评测页在 1440/390 × 深浅主题下通过，无横向溢出或破图。
  手机目录、主题持久化、10 版列表、历史展开、旧版本/准备锚点、提示块、搜索四组词、评测四视图及 CSV 下载通过。
  最终脚本错误、控制台错误、HTTP 错误列表均为空。
- final-states.json 补充 320/768px 教程、文章和更新记录，以及 390px macOS 安装区检查；无页面横向溢出。
- 搜索回归首次出现一次延迟初始化超时；单独连续五次快速打开均正常，测试等待页面网络稳定后完整重跑通过，未改写搜索实现。

## 边界与后续

无阻止本轮预览的剩余问题。既有评测 #method 跳转未展开说明仍属 P4 待办；素材的拍摄日期与稳定版实机审核仍见 screenshot-todo.md。
本轮保留既有截图，没有宣称完成新一轮 Zotero 实机拍摄、全站性能或 WCAG 审核。
未提交、推送、部署；最终设计是否符合预期由用户直接预览确认。
