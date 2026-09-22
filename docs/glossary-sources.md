# 按需下载术语库的数据来源

五个术语库均为按需下载的英文→简体中文精选集。词条正文、原始快照及逐条筛选记录保存在独立的 [`glossary-data` 分支](https://github.com/study-233/zotero-pdf2zh-pro/tree/glossary-data)，不进入插件、服务端 wheel/sdist、Windows 安装包或 Docker 镜像。

| 术语库 | 来源与范围 |
| --- | --- |
| 计算机与 AI | NAER 两岸计算机名词、Python 官方词汇表、Google Developers 机器学习及生成式 AI 词汇表 |
| 建筑 | NAER 两岸机械名词中的暖通及设备词汇；NAER 土木工程名词的简体中文编辑适配 |
| 物理 | NAER 两岸物理学名词 |
| 环境 | NAER 两岸环境保护名词、欧洲环境署 GEMET 首选词 |
| 医学 | NAER 两岸医学名词中的医学基础与部分呼吸相关词汇 |

NAER 两岸表使用原表独立的“中国大陆译名”字段。土木工程原表只有繁体中文，精选项逐条适配并在数据分支标记改动，未将繁简转换结果冒充大陆国家标准。术语库也不是全国科学技术名词审定委员会或医学专业机构认证的完整标准词库。

## 授权与署名

- **国家教育研究院（NAER）**：[乐词网资料开放声明](https://terms.naer.edu.tw/mysite/about/2/)允许注明出处的重制、改作与传播。数据来自该院公开 CSV；每份快照的下载地址与 SHA-256 记录在数据分支的 `sources.json`。
- **Python Software Foundation 及中文翻译贡献者**：[Python 3.13 词汇表](https://docs.python.org/zh-cn/3.13/glossary.html)适用 [PSF 许可](https://docs.python.org/zh-cn/3.13/license.html)。[翻译项目贡献协议](https://github.com/python/python-docs-zh-cn/blob/master/README.rst)将翻译贡献以 CC0 提供给 PSF。数据包保留 PSF 完整许可，数据分支另附译者名单。
- **Google LLC**：[机器学习词汇表](https://developers.google.com/machine-learning/glossary/fundamentals?hl=zh-cn)和[生成式 AI 词汇表](https://developers.google.com/machine-learning/glossary/generative?hl=zh-cn)按 [Google Developers 内容许可](https://developers.google.com/terms/site-policies)适用 CC BY 4.0。仅选取核对后的双语标题，并保留来源与改动说明。
- **欧洲环境署（EEA）**：[GEMET 下载页](https://www.eionet.europa.eu/gemet/en/exports/rdf/latest)明确标注 CC BY 4.0。按概念 ID 对齐英语与简体中文首选词，不收录第三方定义。

数据分支 `LICENSES/` 保存完整许可、来源授权声明及署名；包内 `sources` 保留出处、许可链接和改动说明。所有来源均不代表对本项目的认可或背书。

## 筛选与复现

首版采用明确的逐项选取清单，排除已发现的错译、拼写问题和不明确的同义词串；跨学科含义冲突较明显的通用词也予以剔除。词条可用于辅助翻译，不表示完整覆盖某一学科，也不宣称已经通过临床专家校审。

数据分支的 `curation.json` 记录每个入选词条的来源 ID、原文原译和编辑说明。`raw/` 保存输入快照。主仓库只保存通用构建脚本 [build_glossary_data.py](../scripts/build_glossary_data.py)，无词条常量或筛选词表。

Google 网页快照已移除与术语无关的脚本和公开站点配置，保留词条与许可正文。`sources.json` 分别记录原始下载和清理后快照的 SHA-256，并注明可复现的清理规则。

```sh
python scripts/build_glossary_data.py --data-dir <数据分支目录> --revision <数据提交的40位SHA>
```

脚本校验所有输入 SHA-256、入选原文与改动说明，拒绝同源词冲突，并生成确定性的 JSON 数据包及目录。重新抓取缺失快照可加 `--fetch-missing`，上游内容变化时会拒绝继续；历史版本可直接使用保存的快照离线重建。

发布时先提交数据包和许可，再用该真实提交 SHA 生成并提交目录。`--write-module server/glossary_catalog.py` 仅生成应用所需元数据，且要求 40 位固定提交 SHA。应用按 SHA-256、大小与词条数校验下载内容。后续更新数据不需要将词条正文放进应用发布物。
