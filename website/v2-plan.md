> 历史方案：本文的 Astro / Starlight 技术路线已被用户后续要求替代。当前执行 [Next.js / Memos 源码迁移](next-migration.md)。

# zotero-pdf2zh-pro 官网 V2.0 改版实施计划

> 状态：待实施。基线：2026-10-29，插件稳定版 v1.9.7。仓库：https://github.com/study-233/zotero-pdf2zh-pro ，线上：https://study-233.github.io/zotero-pdf2zh-pro/ 。版本号为基线，发布时必须以仓库/Release 的实际最新稳定版本为准。

## 0. 执行结论与范围

- **技术方案**：一个 Astro + Starlight 静态站，TypeScript/Markdown/MDX、CSS Variables、少量原生交互、pnpm；继续 GitHub Pages + Actions。产品首页和评测页为 Astro 自定义页面，教程为 Starlight 文档。
- **用户目标**：首次访问快速理解产品；看到真实双语和划词效果；能够按系统完成“本地服务 → XPI → 翻译来源 → 第一篇 PDF”；老用户容易找到更新、补译、排错和评测。
- **内容目标**：首页介绍性文案缩减约 35%–50%（目标非硬性字数）；同一个概念只在一个主要区域详细介绍；操作说明用“条件—步骤—成功状态—排错”。
- **工程目标**：不重复实现文档侧栏、目录、全文检索、代码块和主题基础能力；保留评测业务 JS/数据验证；维护单份内容源与可验证的版本/素材信息。
- **禁止范围**：不修改插件的核心翻译逻辑、Windows/Mac 安装器业务行为、不重做模型测试或改写已审计的公开测评数据、不添加虚假演示截图、不收费/暗示免费额度、不引入不必要的后台服务。

## 1. 已核实的现状与关键风险

| 现有路径 | 职责 | 处理策略 |
|---|---|---|
| `website/templates/home.html`、`install-start.html` | 首页、重复的安装入口和步骤 | 重写为统一首页、平台选择组件及唯一四步流程 |
| `website/templates/base.html`、`guide.html` | 主导航、主题、搜索弹窗、侧栏和 TOC | 用 Starlight 能力替代，统一自定义页面的站点头部 |
| `website/templates/benchmark.html`、`website/static/benchmark.{js,css}` | 二维、三维、费用、完整表格及交互 | 先原样迁移并保障功能，再单独优化 UI/延迟加载三维资源 |
| `website/static/site.{js,css}` | 搜索、代码复制、截图放大、表格溢出、主题等 | 保留不可替代的交互，其他交给框架 |
| `website/routes.json` | 15 个现有教程路由 | 迁移成 Starlight 固定路由，制作兼容清单 |
| `docs/user-guide.md` | 多章节原始教程和大量公共锚点 | 拆成单章内容源；通过构建生成旧格式兼容索引/完整聚合文档，避免两份手改 |
| `scripts/build_site.py` | 生成 HTML、切章、复制 PDF 与评测、写版本、URL 映射 | 分拆为 Astro 站点构建和**最小**数据/兼容资源准备脚本；不能直接整文件删除 |
| `scripts/check_docs.py`、`scripts/check_site.py`、`website/test_build.py`、`website/test_site.cjs` | README 锚点、链接、页面结构及评测一致性 | 保留核心不变量，迁移测试入口并替换与旧 DOM 强耦合的断言 |
| `benchmarks/translation/site/` 与 `benchmarks/translation/publications/` | 数据、PDF、公开下载和审计记录 | 只读，生成前后校验哈希及 URL；绝不因页面改版改写数据 |
| `.github/workflows/website.yml` | 文档检查、网站构建、测试和 Pages 部署 | 延续同一 Pages 作业，替换构建步骤，增加预览与回滚保障 |
| `website/screenshots.json`、`assets/guide/` | 现有 12 张截图与尺寸记录 | 更新图片版本清单，补入控制中心、导出等新截图 |

注意：站点根目录目前保留来自 `benchmarks/translation/site/` 的 `results.csv`、`results.json`、`pdfs.json`、`pdfs/` 等兼容资产。迁移前必须生成文件清单和 SHA-256 基线；`/benchmark/data/`、`/benchmark/data/formal/`、`/benchmark/pilot/`、`/benchmark/experiment/`（实际存在时）保持现有路径及文件内容。具体路径以构建输出为准。

## 2. 信息架构与 URL

**顶部主导航**：Logo（回首页）、使用教程、模型评测、更新记录、GitHub；右侧搜索、主题切换及安装入口。无额外“首页”标签；移动端收纳次要导航。

| 页面 | 目标 URL | 页面内容 |
|---|---|---|
| 产品首页 | `/` | 一句话定位、产品效果、统一安装流程、辅助能力、评测入口 |
| 安装准备 | `/guide/` | 必要软件与流程总览 |
| Windows | `/guide/windows/` | 兼容范围、安装器、服务就绪状态、更新注意 |
| macOS | `/guide/macos/` | Apple Silicon、Homebrew、验证与维护，Intel 限制 |
| 安装 XPI | `/guide/installation/` | 安装与连接检查 |
| 翻译来源 | `/guide/api-configuration/` | API / 套餐 / 本地来源分流、保存与测试 |
| 第一次翻译 | `/guide/usage/` | 右键提交、输出附件、常见状态 |
| 设置 | `/guide/translation-options/` | 按场景解释参数，保留必要警告 |
| 任务与补译 | `/guide/task-management/` | 批量、进度、失败恢复、结果保护 |
| 划词 | `/guide/selection-translation/` | 选词、选句、上下文、翻译记忆 |
| 术语库 | `/guide/glossary/` | 下载、使用与自定义 |
| 文档导出（新增） | `/guide/export/` | Markdown、JSON、TXT 入口与适用范围 |
| 更新维护 | `/guide/maintenance/` | 插件与服务分别升级、迁移注意、卸载 |
| 常见问题 | `/guide/troubleshooting/` | 按问题检索和定位 |
| 诊断 | `/guide/diagnostics/` | 诊断包、日志、隐私、反馈 |
| uv | `/guide/uv/` | 备用手动运行 |
| Linux/Docker | `/guide/advanced/` | 进阶部署 |
| 模型评测 | `/benchmark/` | 质量、速度、费用、说明和公开数据 |
| 更新记录 | `/changelog/` | 自动读取根目录 CHANGELOG，优先展示最近版本 |

项目站部署 URL 前缀固定 `/zotero-pdf2zh-pro/`；上表 URL 为相对于此前缀的路径。保留旧深链接，例如旧的 `/guide/task-management/#...`、主页锚点 `#quick-start`、`#windows`、`#macos` 和已有评测 `#results`、`#method`、`#compare`；旧首页 `#results` 等现有跳转继续指向 `/benchmark/` 对应位置。不要静默改变旧数据文件的绝对下载地址。

## 3. 首页页面规范（建议按此直接实现）

### 3.1 结构

1. **Header**：左侧 Logo/项目名；中间教程、评测、更新；右侧搜索/主题/GitHub/“下载”主入口。
2. **Hero**：一句主标题、一句解释、两个按钮，加上首屏可见的真实双语 PDF。优先采用 Memos 式左侧叙述＋右侧产品证明；若宽屏 PDF 缩小后不可辨认，则改为紧凑文字＋下方全宽大图。版本及兼容范围用简短普通文字显示，不做徽章墙。
3. **产品效果**：双语阅读 / 划词精读两个可切换的标签；每个标签一张真实截图、一句说明、一个对应教程链接。首屏已显示双语图时不应再次以完整尺寸重复同一张，可将下方切换区作为深入展示，或根据视觉效果合并为一个区域。
4. **下载与安装**：单独且仅一套四步流程（见第 4 节）；系统选择仅改变第一步的内容。
5. **补充能力**：批量任务、失败补译、词典/术语库、导出文件，四条简短能力链接，不使用重复编号大卡。
6. **模型实测入口**：一句话说明质量、速度和费用对比，提供进入评测的链接；模型/论文数字仅在数据可靠并自动读取时展示。
7. **Footer**：FAQ、反馈、贡献、许可证、上游项目归属、第三方声明。保留法定/开源归属，不重复介绍首页卖点。

### 3.2 定稿文案

| 元素 | 文案 |
|---|---|
| H1 | 在 Zotero 中翻译论文 |
| Hero 描述 | 生成双语 PDF，自动导回原条目；支持查词、选段翻译和上下文解释。 |
| 主按钮 | 下载与安装 |
| 次按钮 | 使用教程 |
| 辅助行 | 开源 · v{当前稳定版本} · 支持 Zotero {实际兼容范围} |
| 效果标签 A | 双语阅读 |
| 效果 A | 翻译整篇论文，保留原文对照。 |
| 效果标签 B | 划词精读 |
| 效果 B | 选词查义，选句翻译，按需解释上下文。 |
| 安装标题 | 下载与安装 |
| 补充能力 | 批量翻译 · 中断补译 · 术语库 · 文档导出 |
| 评测标题 | 模型实测 |
| 评测说明 | 比较翻译质量、速度与费用，查看测试方法和原始数据。 |
| 评测入口 | 查看模型评测 |
| 更新入口 | 已安装？查看更新方法 |

规则：使用用户熟悉的词，不用“探索、赋能、革命性、丝滑”等空泛宣传词；同一段只表达一件事；普通功能说明尽量不超过 35–45 个汉字；技术限定、授权、额外费用及兼容条件不能为了短文案被删掉。不得称云端模型全部免费，也不要暗示模型调用无需成本。

## 4. 唯一安装流程

顶部标签：**Windows / macOS / 其他平台**。可以根据 `navigator.userAgent` 做非强制默认选择，但用户必须可以手动切换；未知环境默认 Windows 或中性指引。实际平台支持范围取自维护的版本/兼容清单。

| 步骤 | 文案与操作 | 主链接 |
|---|---|---|
| 1. 安装本地服务 | Windows：Windows 10/11 x64，首次安装需联网；macOS：M 系列用 Homebrew；其他：进阶安装；Intel Mac 特别提示 | Windows 稳定版 EXE 下载，或相应系统教程 |
| 2. 安装 Zotero 插件 | 下载 XPI，在 Zotero“工具 → 插件”安装（不同系统菜单名称参考教程） | 稳定版 XPI 下载 |
| 3. 配置翻译来源 | 选择 API Key、ChatGPT 套餐登录或已有本地模型之一；测试并保存 | `/guide/api-configuration/` |
| 4. 翻译第一篇 PDF | 从 Zotero 右键提交 PDF，完成后打开自动导入的新附件 | `/guide/usage/` |

下载地址尽可能保留 `https://github.com/study-233/zotero-pdf2zh-pro/releases/latest/download/zotero-pdf2zh-pro-windows-x64-setup.exe` 和 `/zotero-pdf2zh-pro.xpi`。安装区下方只保留一条提示：**“需要分别安装本地服务和 Zotero 插件；云端 API 的待译文本会发送至所选服务商，并使用其额度。”** 详细隐私说明放教程，不要在多个版块重复。macOS Intel 新运行时不兼容条件在安装选项旁直接可见，不让用户执行命令后才发现。

## 5. 设计系统（V2：以 Memos 官网为唯一主要视觉参考）

### 5.1 参考范围与原则

- **主参考**：[Memos 官网](https://usememos.com/) 与其 [公开网站源码](https://github.com/usememos/dotcom)，尤其是 [DESIGN_SYSTEM.md](https://github.com/usememos/dotcom/blob/main/DESIGN_SYSTEM.md)、[Hero 组件](https://github.com/usememos/dotcom/blob/main/src/features/marketing/components/hero-section.tsx) 和 [Hero CSS](https://github.com/usememos/dotcom/blob/main/src/features/marketing/components/home-hero.module.css)。
- Memos 的核心不是某个蓝色 HEX，而是**编辑式文字层次、安静的中性色画布、克制的蓝色强调、产品实物作为证明、开放布局、按内容选择页面构图**。
- **不再要求 GitHub Primer 视觉规范，不引用 Primer 的组件或设计 Token**。现有类似 GitHub 仓库的模拟装饰（Public、repo header、仓库状态等）必须删除。
- 仅模仿**布局方法和设计语言**，不能直接复制 Memos 的 Logo、文字、产品模拟界面或其他品牌资源。
- 技术选型仍然是 Astro + Starlight，**不因为 Memos 使用 Next.js 就改技术栈**。
- 官网默认浅色，支持暗色和跟随系统。交互强调色可以采用柔和、偏灰的天蓝/蔚蓝，不使用荧光、蓝紫渐变与大面积纯蓝。

### 5.2 视觉 Token 建议（Memos 风格，而非精确复制）

| 语义用途 | 浅色模式 | 深色模式 |
|---|---|---|
| 主背景 `--site-bg` | `#FFFFFF` | `#17191D` |
| 内容表面 `--site-surface` | `#FFFFFF` | `#202329` |
| 次级表面 `--site-surface-muted` | `#F7F8FA` | `#262A31` |
| 主文字 `--site-text` | `#20242A` | `#F1F3F5` |
| 次级文字 `--site-muted` | `#626B77` | `#A6ADB8` |
| 分隔线 `--site-border` | `#E4E7EB` | `#343940` |
| 链接/交互蓝 `--site-accent` | `#356F9E` | `#A5C8E8` |
| 强调背景 `--site-accent-soft` | `#EEF5FA` | `#203241` |
| 实心按钮 `--site-primary-bg` | `#356F9E` | `#A5C8E8` |
| 实心按钮文字 `--site-on-primary` | `#FFFFFF` | `#15202A` |

上述是**适配 Zotero 官网的设计提案**，不是声称与 Memos 当前每个像素相同。Codex 应先阅读 Memos 的 CSS 语义色与真实主页，以视觉效果、对比度和一致性为验收，而不是机械替换色值。模型评测多系列数据颜色不受品牌单色约束。

### 5.3 排版与 Hero 规则

- Memos 的核心首页使用左侧说明＋右侧产品实物展示的构图。**对 Zotero 宽屏 PDF 截图，应首先验证右侧是否能清晰展示正文**；若缩得过小，则改成紧凑的左对齐文字区＋接近全宽的大截图。禁止为了强行对齐 Memos 而牺牲截图清晰度。
- 顶部仅一排导航，约 64–72px。不要同时显示图标导航和文字导航两层。
- Hero 只保留一个直接的 H1、一句解释、两个主次操作。没有必要的版本徽章、Public 标签、彩色状态点和重复文案。
- 标题可有**温和的编辑式字形对比**：英文短语/艺术标题可用轻量衬线；中文正文和操作使用清晰的系统无衬线。若选择中文衬线标题，必须测试 Windows/macOS 字体回退，避免宋体意外造成旧式感；不能为了还原 Memos 强制加载庞大字体包。
- 官网用排版、对齐、内容之间的关系创造艺术感，**不是**大幅渐变、光球、阴影和叠加卡片。
- 参考 Memos 的内容驱动规则：每页明确一个主张、一个可信证明、一个下一步动作。真实 Zotero 双语 PDF 是首页最强证明，优先于标题装饰。
- 不设置 `height:100vh`/`min-height:100vh` 的营销 Hero。在 1440×900 首屏应出现明显可辨的真实产品画面，争取主截图从 y≈420–520px 之前开始；顶部过宽的空白必须消除。
- 以 1180–1280px 容器协调宽屏展示，正文 16–17px、H1 桌面 42–54px/移动端 32–38px。不要所有内容居中；合理使用左对齐与不等分栏。
- 使用少量无边框分组、文字列表和分隔线。功能卡仅在确实需要将独立动作并列比较时使用。

### 5.4 页面级应用

| 页面/区域 | 采用 Memos 的方式 |
|---|---|
| Header | 简洁的一行导航；没有第二层、彩色下载徽章或多余装饰 |
| Hero | 产品定位→说明→下载与教程；真实截图能在首屏看到；不要模拟 GitHub 仓库外壳 |
| 产品展示 | 双语阅读和划词精读以真实截图为主，精简说明；图片放大是实用交互，不加虚构窗口 UI |
| 安装区 | 四步流程保持唯一，系统切换是功能；通过对齐与排版而不是四张彩色大卡组织 |
| 补充功能 | 轻量分组或开放列表，避免六张大卡片墙 |
| 模型评测 | 图表功能优先，清晰解释指标来源，不把数据图表变成营销装饰 |
| 文档页 | 继续使用 Starlight 的目录/搜索/代码块，内容可读性优先，不照搬首页的艺术排版 |
| 页脚 | 必要导航、致谢、许可协议，低视觉权重 |

### 5.5 视觉验收

- Codex 必须先阅读 `website/DESIGN.md`（本计划另附），并实地查看 Memos 官网和上面列出的真实源码。
- 在旧截图的 **1440×900 / 390×844** 等尺寸重拍新版本，浅色、深色各一组。
- 比较时回答：导航是否只有一层；截图是否比原版更早出现且清晰；是否去掉 `Public` 伪仓库外壳、重复徽章与空白；是否真正减少卡片墙和文字冗余。
- 若只改颜色、标题大小或组件圆角而不重构视觉层级，视为没有完成改版。
- 确认 CTA、正文、表格、焦点和状态满足 WCAG AA；平板/手机不得横向溢出，尊重 `prefers-reduced-motion`。

## 6. 截图与素材专项

### 6.1 按优先级执行

| 优先 | 素材 | 旧/新资源建议 | 核验内容 |
|---|---|---|---|
| P0 | Windows 安装器/控制中心 | 新增 `assets/guide/windows-installer.webp`、`windows-service-ready.webp` | v1.9.6 控制中心改版后的状态/版本和展开界面 |
| P0 | 翻译配置页 | 更新 `api-configuration.webp` | 当前平台选择、模型获取/测试、保存及 Responses 选项实际位置 |
| P0 | 本地服务连接 | 更新 `server-connected.webp` | 连接成功提示与新设置布局匹配 |
| P0 | 任务管理、失败补译 | 更新 `task-actions.webp`、`task-metrics.webp` | v1.9.3 后的列表布局、token 输出速度及补译入口 |
| P1 | 双语阅读 | 更新或重审 `reader-bilingual.webp` | 同一 PDF 可清晰见原文/译文、图表或公式 |
| P1 | 划词阅读 | 更新或重审 `selection-reading.webp`、`selection-sentence.webp` | 当前实际弹窗/翻译面板，可见动作与结果 |
| P1 | 首次翻译入口和结果 | 更新或重审 `translate-menu.webp`、`translated-attachments.webp`、`attachment-title.webp` | 操作路径与自动导入结果 |
| P1 | 文档导出 | 新增 `export-menu.webp` | v1.9.6 引入 Markdown/JSON/TXT 导出入口 |
| P2 | 词典/术语库、XPI 安装 | 复核 `glossary-settings.webp`、`install-plugin.webp` | 操作控件及教程描述一致 |

### 6.2 截图规则与不足时的处理

- 使用真实稳定版 Zotero/插件/服务，固定样例论文和相同缩放及界面语言；隐藏私钥、Cookie、个人文件、账户及私人文献。
- 同时保留清晰母版和网页优化 WebP；必要时不同宽度输出；截图不作为可复制命令的替代物。
- 截图元数据至少包括：资源路径、用途、系统、Zotero/插件/服务版本、拍摄时间、原始尺寸、预览尺寸、替代文本、审核状态；构建时检验引用文件真实存在。
- 没有可以运行的真实产品界面时，**不得用 AI、CSS、伪造 DOM 制作貌似真实的产品截图**。保留经核验仍正确的旧图，失效图移出上线页面，将待拍项目写入 `website/screenshot-todo.md` 并标注阻塞条件。

## 7. 模型评测保护与调整

- 第一阶段不改评分、样本、费用、来源、筛选口径及原始 CSV/JSON/PDF，不重算数据。现有二维“质量×速度”、二维“质量×费用”、三维性价比和完整表格四视图全部保留。
- 接入来源需要区分“实际费用 / 仅记录到的费用 / API 等值估算 / 未知”，不可把缺失费用显示为 0；同一模型不同接入来源分成不同数据点，不将配置数量误标为模型数量。
- 视觉上简化顶部操作密度：主视图与核心筛选优先，次要操作归入可展开区域，图表说明和数据来源紧邻图表。
- 3D Plotly 脚本应仅在进入 3D 模式时按需加载；先确保现有脚本可正确运行、切换标签、返回二维、复制链接、下载 CSV，再进行懒加载重构。
- 保留已有图表 JS 测试，以及 `benchmarks/translation/validate_site.py` 和审计/出版状态检查。所有公开评测附件相对与绝对 URL 不变。

## 8. 建议的代码组织

```text
website/
  package.json
  pnpm-lock.yaml
  astro.config.mjs
  tsconfig.json
  src/
    pages/
      index.astro                 # 自定义产品首页
      benchmark/index.astro      # 模型评测容器
      changelog/index.astro      # 引用/生成根 CHANGELOG.md
    content/docs/
      guide/index.mdx
      guide/windows.mdx
      guide/macos.mdx
      guide/installation.mdx
      guide/api-configuration.mdx
      guide/usage.mdx
      guide/translation-options.mdx
      guide/task-management.mdx
      guide/selection-translation.mdx
      guide/glossary.mdx
      guide/export.mdx
      guide/maintenance.mdx
      guide/troubleshooting.mdx
      guide/diagnostics.mdx
      guide/uv.mdx
      guide/advanced.mdx
    components/
      SiteHeader.astro
      SiteFooter.astro
      HeroPreview.astro
      FeatureShowcase.astro
      InstallSteps.astro
      PlatformSelector.astro
      Screenshot.astro
      BenchmarkExplorer.astro
    styles/
      tokens.css
      global.css
      benchmark.css
    data/
      compatibility.ts
      download-links.ts
      site.ts
    scripts/
      benchmark.ts              # 先移植旧 JS，再独立优化
      interactions.ts
  .generated-public/             # 构建前准备的兼容资源，不提交大文件
  screenshot-todo.md
scripts/
  prepare_website_data.py       # 从原始数据准备只读评测摘要与静态资源
  check_website_compat.py       # 验证 URL / SHA-256 / 必要功能
```

路径仅为建议，不为形式强制分文件。将所有新增站点文件限制在 `website/` 和专用构建/验证脚本内，避免影响插件工作区。配置 `site=https://study-233.github.io`、`base=/zotero-pdf2zh-pro/`、输出 `dist/site/`；验收时必须从**非网站根路径**的真实项目 URL 测试。编译器和工具的版本必须按当时稳定版的官方兼容范围固定在站点锁文件里。

**兼容机制要点**：旧版 `docs/user-guide.md` 的 `#windows`、`#macos` 等 GitHub 锚点不能凭空消失。新的各章 MDX 作为正文唯一来源，通过脚本生成兼容性的聚合 Markdown/锚点跳转表，或保留旧文档作为持续可用的入口，但不允许同时手动维护两份完整正文。`scripts/check_docs.py`/README 的链接审计必须改到新的单一来源，仍验证已公开链接。新的 `changelog/` 内容从根目录 CHANGELOG.md 自动得出。`

## 9. 开发阶段和门禁（按顺序提交）

### P0：冻结基线与页面/资源清单

- 建 `feat/website-v2` 分支；确认稳定 Release 与 `plugin/package.json` 的版本口径；不直接改 main 部署。
- 构建当前旧站，保存 `dist/site/` 页面 URL、锚点、附件路径、PDF/CSV/JSON 哈希及关键截图，尤其评测相关资源。
- 输出 `website/legacy-routes.json`（旧 URL/锚点 → 目标地址）、`website/screenshot-todo.md`、页面内容矩阵和现有测试覆盖矩阵。
- **门禁**：能自动复现旧版构建且基线清单可被读取。

### P1：框架脚手架与兼容数据适配

- 在 `website/` 引入 Astro + Starlight + TS/MDX/pnpm；配置 GitHub Pages base、产物目录、静态搜索和主题。
- 先保持旧站流程可运行，增加新站独立构建入口；从 `build_site.py` 抽出只读资源准备步骤，保留审计状态/完整性判断。
- 迁移现有评测静态数据、PDF、品牌资产，比较 SHA-256；替换自定义搜索前确认 Starlight/Pagefind 在最终产物可用。
- **门禁**：新站生成最小可用首页、教程、评测，并通过 URL/文件一致性检查，旧站仍可回退。

### P2：首页重构与文案定稿

- 落地第 3–5 节；统一采用第 5 节的 Memos 编辑式极简风格（安静的白/灰底、克制的蔚蓝色、强调真实产品画面），用少量 Astro 组件实现 Header、Hero、截图展示、四步安装、补充能力、评测入口、Footer。
- 系统切换只控制第一步；菜单支持触屏/键盘；下载链接直接指向稳定 Release。
- 文案按照新表定稿，核对真实界面按钮名称。主图优先使用已验证真实截图。
- **门禁**：不必滚动很多屏就能找到安装；首页没有第二套安装流程；所有按钮实际可用。

### P3：教程迁移与截图更新

- 将旧的 15 个教程页面逐章迁入 MDX，新增文档导出页面，整理成“条件—步骤—成功状态—排错”。同步 README、文档路径引用、链接和兼容锚点。
- 使用 Starlight 侧栏/TOC/搜索/提示/步骤；清理旧自写的搜索索引、复制按钮、目录和 TOC 滚动代码（先验证新功能对等）。
- 按第 6 节替换/重审截图；没有真实环境的图片列入待拍清单而不是伪造。
- **门禁**：15 个旧教程入口与新增导出页都能访问，旧外链可跳转，搜索命中指定术语和页面。

### P4：模型评测整合与可访问性/性能

- 将评测页迁入统一站点外壳，保留四视图和 JS 交互；检验图表、表格、CSV、复制链接和所有数据路径。
- 在功能对等后进行 Plotly 3D 懒加载、图表辅助信息和移动端简化。
- 开展深浅主题、360/390/768/1280/1440 等宽度检查；支持键盘与屏幕阅读器常用路径。
- **门禁**：评测测试、数据验证、版本和链接测试全部通过；首页 JS 不包含 3D Plotly；没有横向溢出与遮挡。

### P5：双构建对比、CI 与发布

- CI 在 PR 中构建新站、执行 `astro check`（或适用的 Starlight/TS 校验）、站点测试、文档检查和旧/新兼容资产校验；保留原评测数据验证。
- GitHub Actions 仅在 main 的通过门禁版本正式部署 GitHub Pages；不要同时运行旧版和新版发布工作流抢占同一环境。
- 必须在真实项目路径 `https://study-233.github.io/zotero-pdf2zh-pro/` 验证关键操作，特别是安装下载、搜索、目录、截图、数据下载。
- 发布后保留上一可用 workflow/产物及 Git revert 回滚流程；只在新构建稳定后清理废弃 Jinja2 生成模板和测试。

## 10. 测试验收矩阵

| 分类 | 具体检查 | 最低结果 |
|---|---|---|
| 内容 | 首页定位/说明/四步流程/前提/隐私说明 | 无重复整段说明、兼容表述正确 |
| 安装 | Windows EXE、XPI、macOS/uv/Linux 教程、四步下一步 | 能完成首次使用路径；链接有效 |
| 兼容 | 旧路由、重要旧锚点、`README.md` 引用、评测 `#results/#compare/#method` | 不出现 404 或空目标 |
| 文档 | 15 个旧教程路由、新增导出页、搜索术语 | 全覆盖、无断链、中文检索可用 |
| 评测 | 质量×速度、质量×费用、三维、数据表、CSV、图例/选择/复制 | 功能对等、语义及数字不变化 |
| 数据 | 已审计 CSV/JSON/PDF/出版附件及历史兼容资产 | SHA-256 与基线一致，路径不变 |
| 图片 | alt、尺寸、懒加载、放大，P0 图更新状态 | 不使用伪造和明显过期 UI |
| 性能 | 首页资源、懒加载、图片、Lighthouse 指标 | 核心页无无谓大 JS；推荐以 LCP≤2.5s、CLS≤0.1 为优化目标，明确设备/网络条件 |
| 无障碍 | 键盘、对比度、焦点、角色、暗色、减少动画 | 无阻断交互问题；以 WCAG AA 为设计方向 |
| 部署 | Pages 子路径、CI 锁依赖、无冲突工作流 | PR 可验证，main 发布可回滚 |

建议增加端到端自动化覆盖：主页 → 系统选择 → 下载入口/教程 → 翻译来源 → 使用教程；搜索“安装失败、API、补译、划词”；评测四视图切换与文件下载；手机导航和截图放大。关键测试以静态构建产物而非仅开发服务器为验证对象。

## 11. 决策与依赖控制

- **内容优先**：若新框架迁移与真实截图不足同时发生，先迁移已核验文本和素材；不为赶进度添加假图。
- **测评优先保真**：旧 JS 先迁移，再单独重构懒加载；保留可读原始数据作为无 JS 备选。
- **兼容优先于精简文件数量**：数据复制与审计脚本不能为“零 Python”而删除；删掉重复造轮子的 UI 功能即可。
- **版本稳定性**：Release 与插件 package 版本不一致时构建应告警，不能自动拼出不存在的下载资产。
- **回滚**：保留功能完整的旧构建版本作为回退目标；若新站搜索、下载或评测失败，阻止部署或 revert 网页改版提交。

## 12. 可直接提交给 Codex 的总任务说明

请先读取 `AGENTS.md`、`CLAUDE.md`、`website/DESIGN.md`、网站代码和测试，并阅读 Memos 的设计系统与首页源码，按本计划分阶段将 `study-233/zotero-pdf2zh-pro` 官网从 Jinja2 自建站迁移为 Astro + Starlight。目标是现代、简洁、真实、直观：采用第 5 节确定的 Memos 风格，放弃强制 Primer 配色；用编辑式排版、安静的中性色与真实产品证明主导首页，精简首页文案、合并为唯一四步安装流程、刷新真实产品截图、迁移教程与搜索、保留模型评测全部能力。先生成现状/兼容矩阵并创建功能分支，再按 P0-P5 实施，不要直接部署。保留现有教程 URL 和锚点、README 链接、GitHub Pages `/zotero-pdf2zh-pro/` base、所有已有公开测评数据文件及 SHA-256、Release 下载链接、15 个教程页、四种评测视图和审计校验。对 `scripts/build_site.py` 只替换页面生成职责，保留数据/发布验证并迁移测试；不要改插件/服务业务逻辑，不伪造产品截图；真实界面不可用时生成截图待拍清单并使用仍准确的现有图。每阶段运行构建、类型/文档、链接、评测、端到端和响应式检查，提交阶段性变更与验证结果；全部门禁通过后才允许切换 GitHub Pages 主发布工作流。

## 13. 参考与证据

- 现有首页：https://github.com/study-233/zotero-pdf2zh-pro/blob/main/website/templates/home.html
- 现有安装区：https://github.com/study-233/zotero-pdf2zh-pro/blob/main/website/templates/install-start.html
- 现有路由：https://github.com/study-233/zotero-pdf2zh-pro/blob/main/website/routes.json
- 现有构建：https://github.com/study-233/zotero-pdf2zh-pro/blob/main/scripts/build_site.py
- 现有 CI：https://github.com/study-233/zotero-pdf2zh-pro/blob/main/.github/workflows/website.yml
- 当前发布：https://github.com/study-233/zotero-pdf2zh-pro/releases/tag/v1.9.7
- Astro + Starlight 自定义页面：https://starlight.astro.build/zh-cn/guides/pages/
- Starlight 文档组件：https://starlight.astro.build/components/using-components/
- Astro GitHub Pages 配置：https://docs.astro.build/zh-cn/guides/deploy/github/
- Astro 图片处理：https://docs.astro.build/en/guides/images/
- Memos 官网：https://usememos.com/
- Memos 设计系统：https://github.com/usememos/dotcom/blob/main/DESIGN_SYSTEM.md
- Memos 官网 Hero 源码：https://github.com/usememos/dotcom/blob/main/src/features/marketing/components/hero-section.tsx
- Memos 官网样式：https://github.com/usememos/dotcom/blob/main/src/features/marketing/components/home-hero.module.css
