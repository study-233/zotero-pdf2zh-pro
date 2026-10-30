# 本地字体

- 中文正文：Google Fonts / Noto Sans SC，400 与 600 字重，按当前中文内容生成 Unicode 子集。
- 中文展示标题：Google Fonts / Noto Serif SC，400 与 600 字重，按首页及页标题生成子集。
- 西文：Inter Variable / Source Serif 4 Variable，通过固定版本 fontsource 依赖提供。

`chinese.css` 与对应 TTF 子集参与本地构建，浏览器不访问字体 CDN；合计覆盖当前提取的 799 个中文字符，新增字形会使用系统字体回退，应在批量更新文案时重新生成子集。
Google Fonts text 子集接口返回 TrueType，文件扩展名和 CSS format 与实际文件格式一致。
许可证分别保留在 OFL-NotoSansSC.txt 和 OFL-NotoSerifSC.txt。

旧 `noto-serif-sc-headings.woff2` 保留为迁移前素材，不再被新站引用。
