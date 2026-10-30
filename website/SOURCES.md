# Memos 源码来源与适配

上游：[usememos/dotcom](https://github.com/usememos/dotcom)，固定提交 `e83489a13b30f89e6782510c408cc1cb319de6bd`。
逐文件 blob SHA 与永久链接见 [memos-source.json](memos-source.json)。本地构建使用已经迁入的源码，不会拉取浮动 main。

## 直接使用

- `src/app/global.css`、`typeset.css`：上游语义颜色、排版、主题变量、1400px 容器。
- `home-hero.module.css`：650ms 文案入场、800ms 产品入场及 120ms 延迟，沿用原 easing 和 reduced-motion 规则；以真实 Zotero 图片代替 Memos mock。
- `marketing-page.tsx`、`chapter-header.tsx`：原始排版与链接组件。
- `editorial-index.tsx`：更新记录的内容宽度、字号层级、条目间距、悬停与箭头动画。
- `docs-article-body.tsx`：教程的 typeset 边界。
- `button.tsx`、`breadcrumbs.tsx`、`github-icon.tsx`、`utils.ts`、`use-copy-to-clipboard.ts`：原始基础组件与逻辑。

## 有限适配

| 上游组件 | 本项目改动 |
| --- | --- |
| SiteHeader | 换为项目吉祥物与名称、三项站内导航、下载和项目 GitHub；保留原导航尺寸、移动菜单、Esc/外部点击关闭与路由关闭逻辑 |
| useGithubStarCount | 沿用 Shields 公开计数接口、K/M 格式和 24 小时缓存；换为本项目仓库，未取得数据时显示 GitHub，不展示猜测的 Star 数 |
| HeroSection | 接收真实截图；换产品文案和版本链接，保留栅格、间距和动画类 |
| MemoHeroMock | 直接提取最外层圆角、边框、背景与阴影 class；内部展示真实 Zotero 截图，不迁入虚构笔记内容 |
| Footer | 使用上游 compact 分支，替换站点链接，保留原项目来源与主题切换 |
| HomeOwnCommand | Docker 文本换为三条 Homebrew 命令；保留命令卡与复制交互 |
| ThemeToggle | 中文标签；由同一个 next-themes Provider 管理全站 |
| DocsSidebarLayout | 去掉不适用的 API 版本选择器，保留 Fumadocs DocsLayout |
| Fumadocs CSS scope generator | 输出路径改为 `src/app/guide/`；沿用上游的零优先级隔离方法，避免教程 CSS 污染营销页 |
| EditorialArticleBody | 接收既有 CHANGELOG 解析结果，保留原排版包裹 |
| 根布局 | 同一 ThemeProvider；字体改为本地资源；替换站点元数据 |

核心依赖以上游锁文件为基础固定：React 19.2.8、Tailwind 4.3.3、Fumadocs UI/Core 16.15.4、MDX 15.4.0。Next 从 16.3.3 升至 16.4.1，Zod 从 4.5.2 升至 4.6.6，以包含上游安全修复。
本项目只迁入公开站点所需模块，保留自己的内容、产品图、数据和部署子路径。

## 中文与产品适配

正文使用 Noto Sans SC，展示标题使用 Noto Serif SC，西文使用上游的 Inter / Source Serif 4。字体以本地资源提供；来源和字符覆盖见 [字体说明](src/assets/fonts/README.md)。
中文教程采用 17px 正文与 1.85 行高，导航保持原结构。Fumadocs 文案按当前版本的带上下文翻译键提供。
下载系统切换、旧锚点转发和模型测评是本项目功能，未冒充上游已有模块。
