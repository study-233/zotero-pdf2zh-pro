# Zotero 论文翻译插件调研与 zotero-pdf2zh-pro 开发建议

> 历史调研：本文以 2026-11-14 的 v1.9.7 及当日主分支为基线。
> 按页试译、译文反查、双语笔记和配置级请求限制已列入 v1.9.8 的[更新记录](../CHANGELOG.md)。
> 下文的“目前可用”和开发建议保留调研当日口径；当前操作见[使用指南](user-guide.md)。

这款插件最值得做的下一步，是让读者能从一句中文译文回到英文证据，并把两者一起放进 Zotero 笔记。最适合先交付的两个小版本，是原文选区的双语摘录和按页试译。前者服务文献写作，后者服务初读筛选，都能利用你已经维护的翻译流程。

调研日期为 2026 年 11 月 14 日。以 GitHub 正式版 v1.9.7 和与远端一致的本地主分支 `d515665` 为基线，主分支比该标签多 11 个提交。下文对照了 12 个公开仓库的代码或文档快照，另用 Jadense 和 Zotero 官方文档核对安装及宿主能力。外部项目描述的是对应版本的实现或作者声明。没有在同一批论文上横测各插件的速度、译文质量或跨系统兼容性。[你的正式版](https://github.com/study-233/zotero-pdf2zh-pro/releases/tag/v1.9.7) · [核对的主分支](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/README.md)

## 你的插件已经做到了什么

| 阅读步骤 | 目前可用 | 本次辨认出的增量 |
| --- | --- | --- |
| 初读与筛选 | 批量提交、跳过末尾若干页、保护参考文献 | 按次指定页码，在没有 PDF 时先读译题和摘要 |
| 全文翻译 | 单语与双语附件、OCR 兼容、表格文字、学科术语包、自定义词表、可选语义校对 | 给每篇论文保留专用译法，给读者看某个可疑段及其原文 |
| 阅读选区 | 多种词典、词条发音、原文编辑后重译、语境解释、流式结果、全文翻译记忆 | 从生成的中文附件查原段，把当前结果存为可引用的笔记 |
| 收藏与带走 | 本地划词收藏、回原文、JSON 导入和 JSON/CSV 导出。另可导出 PDF 为 Markdown/JSON/TXT | 形成带原句与出处的复习卡，或可切换原译文的阅读稿 |
| 长任务 | 请求与 token 指标、诊断、部分结果、补译、重试导入、保留旧附件与批注 | 按配置记住限速，按页试译后让用户看清哪些内容还能复用 |

尤其值得保留的是两种独立恢复操作：模型漏译时补译，PDF 已生成但进库失败时只重试导入。术语也已有任务级快照，旧任务可以按提交时的词表重试。评估新功能时要计入这些既有能力。[使用指南](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/docs/user-guide.md) · [词库合并和任务快照](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/docs/glossary-downloads.md) · [语义校对实现](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/babeldoc/format/pdf/quality_review.py)

## 十二个 GitHub 项目分别值得学什么

### 翻译和排版

**Translate for Zotero（windingwind）**　适合研究“读完一句之后做什么”。可把翻译放入高亮或下划线批注的评论，可从弹窗加入笔记，可译条目标题摘要，还能在独立窗口比较多个服务。你已经有词典、划词浮窗和多来源，能直接用上的差异是笔记动作和按需比较第二种译文。阅读源码时要区分正式版 v2.4.8 与所查主分支 v2.5.0-beta.2。[正式版](https://github.com/windingwind/zotero-pdf-translate/releases/tag/v2.4.8) · [功能说明](https://github.com/windingwind/zotero-pdf-translate/blob/4e3edb955fe20ff7a2f9a7595cd0264e39b13a29/README.md) · [笔记按钮实现](https://github.com/windingwind/zotero-pdf-translate/blob/4e3edb955fe20ff7a2f9a7595cd0264e39b13a29/src/modules/popup.ts#L279)

**Zotero PDF2zh（guaguastandup）**　你的上游之一。值得回看的是如何处理已生成的双语 PDF。它区分左右同页、原译交替页、裁剪和两栏论文的裁剪对照，并根据输入附件类型禁用无意义的重复操作。这些选择解决的是小屏幕上需要反复横向拖动的问题。可以只给已有双语附件加一个“生成单栏阅读版”，先在跨栏表格、宽公式和带边注的页上检查裁剪。[输出状态与布局](https://github.com/guaguastandup/zotero-pdf2zh/blob/6e8037db2ada5a4b7b0cc0e6f0332efc766e2cb0/docs/zh/guide/translation-options.md) · [裁剪的适用范围](https://github.com/guaguastandup/zotero-pdf2zh/blob/6e8037db2ada5a4b7b0cc0e6f0332efc766e2cb0/docs/zh/guide/faq/plugin-features.md) · [v4.1.8](https://github.com/guaguastandup/zotero-pdf2zh/releases/tag/v4.1.8)

**Zotero PDF2zh Next（NightWatcher314）**　你的项目从它的 v5.3.0 继续开发。其后续 v5.4.0 可为每个 API 配置指定 QPS、并发和重试参数，留空继承全局，然后随任务保存。你的提交入口从全局偏好读取 QPS 和并发。经常在本地 Ollama、低配额云服务之间切换的读者，可以少一次调整设置。这一项对阅读器版本依赖较小。[上游配置与重试语义](https://github.com/NightWatcher314/zotero-pdf2zh-next/blob/a8236962ad6a390a54abaf20d1403aa2d54d9539/README.md) · [当前任务参数](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/pdf2zhHelper.ts#L343)

**沉浸式翻译 Zotero 插件**　它把授权码测试、任务进度、恢复未完成任务、复制任务 ID 和打开译文放在短流程里，服务依赖沉浸式翻译 Pro。你的任务恢复与诊断已经更细，可从它学习的是减少用户找结果的步骤：例如为“已导入”行直接展示“打开双语 PDF”，排查时再展开技术指标。你已有翻译后自动打开附件的设置。值得补的是以后回到历史任务时，也能直接打开那次输出。[使用及恢复流程](https://github.com/immersive-translate/zotero-immersivetranslate/blob/d7bf50f516788e195fb841deb36f070e48b16da6/README.md) · [任务窗口](https://github.com/immersive-translate/zotero-immersivetranslate/blob/d7bf50f516788e195fb841deb36f070e48b16da6/src/modules/translate/task-manager.ts) · [现有导入后打开逻辑](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/pdf2zhHelper.ts#L317)

### 双语阅读与原文定位

**Z-Transplit**　在 Zotero 10 中支持滚到哪里译到哪里、段间原译对照、译文单显、双阅读器联动，另有按页翻译。它把当前视口附近的段落排在前面，重复输入优先查缓存。最值得拆开学习的是“只译所需页”和“等读到再请求”。原文与译文有固定页序时，它还可以合并不同批次的翻译页。你的双语输出和旧附件批注另有约束，合并前需要核对页映射。[功能与版本范围](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/README.md) · [可视段落队列](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/src/core/pdf/sdt/lazyQueue.ts) · [按页合并的前提](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/src/core/pdf/translation/mergeTranslatedPages.ts)

**BiRead**　基于 Translate for Zotero 的独立分支。它允许在中文侧划线，利用已确认词对、术语和文本匹配关联英文，允许用户双向纠正。无法匹配的中文高亮保存在本地。它还明确标为 Beta，文字层抽取不能代替扫描件 OCR。对你最重要的设计是让用户看得到匹配有多精确，并能修正，不必在首版承担词级对齐。[功能和验证边界](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/README.md) · [选区匹配实现](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/biread/src/bilingual/alignment/selectionMapping.ts) · [原生批注桥接](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/biread/src/bilingual/annotations/zoteroAnnotationBridge.ts)

**Mktero**　将 PDF 转为带出处的连续 Markdown，支持原译文视图、图表和笔记。普通选区只有在 PDF 中找到可靠位置后才能作为带引用的摘录。重复或模糊匹配保留在本地。修改识别错误后，只把受影响块标为待译。默认转换可能发送整份 PDF 到其服务，产品也提供其他转换路径。对你，最值得学的是定位失败的呈现方式、段落编辑和缓存失效规则。[原生笔记与批注](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/docs/annotations.md) · [校对后怎样复用译文](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/docs/reading.zh-CN.md) · [数据去向](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/docs/privacy.md)

**Full Text Translate（Chikit-L）**　用 MinerU 解析 PDF，由另装的 Translate for Zotero 提供翻译，最终把能切换中英双语和纯中文的 HTML 挂回原条目。作者在仓库中提供完整样例，轻量解析模式对图片和表格有限制。你的 Markdown 导出可以嵌入图片，但现在读的是选定 PDF 的内容。从翻译记忆生成配对 HTML，才能稳定做到“一段原文对应一段译文”。[依赖与输出](https://github.com/Chikit-L/zotero-fulltext-translate/blob/0990def1fa811c8d789f3dd296efc33a9bea9aa7/README.md) · [HTML 渲染器](https://github.com/Chikit-L/zotero-fulltext-translate/blob/0990def1fa811c8d789f3dd296efc33a9bea9aa7/src/modules/HTMLRenderer.ts) · [仓库里的成品 HTML](https://github.com/Chikit-L/zotero-fulltext-translate/blob/0990def1fa811c8d789f3dd296efc33a9bea9aa7/doc/examples/GreatBarrierReef.translated.html) · [现有导出实现](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/pdf2zh_pro/document.py)

**Zotero Split Screen（sukui10）**　适合学习低成本的联动方式。两边分别翻到同一章节，点“对齐当前页”，以后同步相对页码。其说明明确只同步页码，清单范围是 Zotero 7–9。你可以先从同一论文的原文和单语译文做双开、按页跟随、可解除联动。横跨不同版本时，这比维护滚动像素、焦点和缩放的全套同步更容易验证。[页码联动与兼容范围](https://github.com/sukui10/zotero-split-screen/blob/0eb6ceb6f48c5257cb7e57a3ea163941ec5c4ce2/README.md) · [先翻页再定位选区](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/src/platform/zotero-source-navigation.js)

### 文库筛选与词汇学习

**Zotero 标题翻译（zhouyi654）**　条目列表显示独立译题，支持翻译摘要、手改译题、跳过已有译文、查看批量范围。自动翻译新条目默认关闭，标题和摘要可分别开启。它还把术语桥接到了特定 PDF2zh 流程，做法涉及包装另一插件入口、写服务端配置。你自己控制两端，直接复用原生词表与配置接口即可。[译题及批量规则](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/README.md) · [术语触发和错误译法](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/docs/TERMINOLOGY.md) · [桥接的具体边界](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/docs/PDF2ZH_BRIDGE.md)

**Pick2anki（soyami）**　把词条、论文原句、来源和释义映射到用户自己的 Anki 模板，并让用户选择重复卡处理策略。它的来源包括条目链接。你的收藏已有阅读页、附件和可选坐标，能进一步让复习卡直接回到词出现的地方。先做可导入的卡片文件，再看是否值得长期维护 AnkiConnect 或墨墨接口。[卡片字段与写入流程](https://github.com/soyami/zotero-pick2anki/blob/4792c6ae8df536a6f23b026a4bfa8156a2607f81/README.md) · [Anki 接口](https://github.com/soyami/zotero-pick2anki/blob/4792c6ae8df536a6f23b026a4bfa8156a2607f81/src/modules/anki.ts) · [收藏数据结构](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/selectionFavorites.ts#L4)

**Zotero Anki Bridge（cocmin）**　提供一个更小的实现：复用 Translate for Zotero 的译文，验证牌组、字段、空结果和重复词，写入本机 Anki。该版本没有上下文句和文献链接。和 Pick2anki 放在一起看，前者说明简单连接需要哪些失败处理，后者说明为什么科研读者值得多保存一句上下文。[数据和功能边界](https://github.com/cocmin/zotero-anki-bridge/blob/7f08e2a21719c87608a94d0d2d4a6dc32ef80196/README.md) · [写卡逻辑](https://github.com/cocmin/zotero-anki-bridge/blob/7f08e2a21719c87608a94d0d2d4a6dc32ef80196/addon/lib/core.js)

## 最适合你落地的功能

| 建议顺序 | 用户动作 | 已有基础 | 相对工作量 |
| --- | --- | --- | --- |
| 先做 | 选中原文，把当前译文加入 Zotero 笔记 | 选区、最终译文、页码、出处 | 小至中，集中在插件端 |
| 先做 | 只翻当前页或指定页码 | 引擎已有页码解析，现有任务可重试 | 中，涉及提交、任务状态、导入 |
| 核心差异 | 从中文译文反查英文证据 | 后端可按译文查询、已有附件导入记录 | 中至大，需要处理输出版本 |
| 随后做 | 在文库读双语标题摘要 | 短文本翻译、多配置、术语 | 中，涉及 Zotero 条目字段兼容 |
| 随后做 | 收藏变成带出处的复习卡 | 收藏已含释义、原句、条目、页码 | 文件导出小，双向同步大 |
| 独立小改动 | 切换模型时自动应用各自的限速 | 全局 QPS 和并发、任务快照 | 小至中，可与阅读器功能分开发 |
| 原型验证 | 按段阅读、术语纠正、单段重译 | 全文段落记忆、术语快照、失败补译 | 大，需设计段落版本和无障碍交互 |

### 原文选区一键生成双语摘录

例如读者选中论文的结论句，弹窗已有中文译文，点击“加入笔记”，当前论文的笔记中就出现英文原句、中文译文和可点击的页码。让 Zotero 的引用对象承担文献引用，译文另起一段。用户编辑过 OCR 原文时，同时保留原始引文，避免把修订文本当成论文原话。没有打开笔记时，可以让用户选择这篇论文的可写子笔记或新建一篇。[竞品的弹窗按钮](https://github.com/windingwind/zotero-pdf-translate/blob/4e3edb955fe20ff7a2f9a7595cd0264e39b13a29/src/modules/popup.ts#L279) · [Zotero 对批注、回链和引用的说明](https://www.zotero.org/support/pdf_reader)

实现从现有选区面板接入。按钮消费当前请求已完成的结果，流式中断的半句话不自动落笔。已有批注只在用户明确选择后追加译文，保留其评论。验收时先断开翻译服务，再连续点两次“加入笔记”：已显示的译文仍能保存，一次操作只生成一条，引用能回到原页。另测只读群组、无父条目的独立 PDF 和两个同时打开的阅读器。同行历史 issue 报告过入笔记和加批注引起重复请求，适合用作这项功能的回归用例。[选区入口](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/selectionTranslate.ts#L1507) · [重复翻译后入笔记 #758](https://github.com/windingwind/zotero-pdf-translate/issues/758) · [加批注时重复请求 #1104](https://github.com/windingwind/zotero-pdf-translate/issues/1104)

### 从选页试译开始

给文库右键加“翻译指定页…”，给阅读器加“翻译当前页”。例如一篇 40 页综述，读者只想判断第 3 至 5 页的方法是否值得细读。首次界面显示“PDF 第 3–5 页，共 3 页”，明确物理页和印刷页码的区别。你的服务目前会把“跳过末尾页”换算成引擎页码，底层也已有逗号分隔区间解析。新增工作是把单次范围从界面一直传到任务、恢复、文件标识与完成通知。[当前页码传递](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/pdf2zh_pro/pdf.py#L243) · [底层区间解析](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/pdf2zh_pro/config.py#L365) · [页码输入处理](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/src/core/pdf/translation/pageRange.ts)

首版保留完整页序，范围之外原样显示，附件标出“选页试译 3–5”。这是一次成功完成的范围任务，不能显示为“全文已译”或“失败待补译”。后续“继续翻全文”再核验哪些成功段能复用，模型、语言和术语一致时计数给用户看。应用到单语和双语输出时分别验证物理页数与阅读页映射。相同范围重复提交、重叠范围、越界和取消都要有确定结果。[页替换和不满足前提时的处理](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/src/core/pdf/translation/mergeTranslatedPages.ts) · [导入及不完整任务判定](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/zoteroTaskImporter.ts#L11)

### 从中文译文反查英文原段

这项最有机会形成你的特色。后端的 `POST /translation-lookup` 已接受 `side=translation`，按原 PDF 的 SHA-256、目标语言、页码和选中文字查段落，且不会发模型请求。阅读器当前查询明确使用 `side: "source"`，并从打开的附件计算指纹。在译文 PDF 上只改一个参数会拿错文档身份。已有导入记录可定位输出附件，但要补上“这份输出对应哪份原文、哪次译文”的稳定关联。[读取接口契约](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/README.md#L41) · [前端指定原文侧](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/selectionTranslate.ts#L95) · [输出附件的持久化记录](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/taskOutputStore.ts#L3)

我在临时 SQLite 库做了一个可复查的实验：第 2 页存“均值下降。”，第 6 页存“中位数下降。”。查询完整第一句得到对应英文。查“下降。”并传第 6 页得到第二段。移除页码仍然只得到一个候选。把第一段重存为“平均值降低。”后，再查旧中文整句就未命中。现有实现只返回一条，且同一来源段更新会替换保存的译文。这个边界会直接影响重译前的历史附件。[更新与匹配逻辑](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/translation_memory.py#L78)

建议首版只支持新生成的单语译文。导入时记录原附件库与 key、原文指纹、输出指纹、目标语言和本次输出的段落版本。长选区唯一命中时展示英文原段并提供“回到第 N 页”。短词、重复句返回候选或仅定位到页。结果来自哪一次生成，应能与屏幕上读到的那份 PDF 对上。旧附件缺记录时可让读者手动关联，或说明只支持页级定位。通过标准是原译查阅零外部请求、重译后旧附件仍有正确出处、两个附件同名时不会串篇。随后再做双语左右页、词级高亮。[确认词对和保守匹配](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/README.md) · [重复文本如何处理](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/docs/annotations.md)

### 让标题和摘要共用全文术语

从“选中若干条目 → 翻译尚无译文的标题”起步，随后加按需摘要。标题短、调用少，读者在没有本地 PDF 时也能筛文献。第一版采用可隐藏的译题列或条目信息区，保留原始 title 和 abstractNote，识别已有译文与人工改译。与 Translate for Zotero 并装时读取既有译题，验证实际保存格式和字段所有者，避免两列同名、同一值被两个插件轮流回写。再为被选中条目预览约需请求的数量。[自动翻译、人工修改与侧栏同步](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/README.md) · [用户反馈译题占空间 #1502](https://github.com/windingwind/zotero-pdf-translate/issues/1502) · [从只读条目返回后无法编辑 #1500](https://github.com/windingwind/zotero-pdf-translate/issues/1500)

一个有区分度的扩展是“这篇论文中把 grounding 译作……”。作用范围可从单篇开始，明确这次改动影响的标题、摘要、正文段落。任务创建时冻结生效词表，已有附件继续对应原词表版本。用户改词后先列出受影响段，再决定是否重译。全文模型记忆、当前论文词表和个人生词收藏应各自保留用途。[单篇论文词表](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/README.md) · [依源术语触发的译后校正](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/docs/TERMINOLOGY.md) · [已有术语快照](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/docs/glossary-downloads.md)

### 收藏变成可复习的研究词汇

你已计划接入墨墨，也已经能导出 CSV。真正值得做的是把已有字段组合成可直接复习的卡：正面放术语和论文原句，背面放语境释义、题名、页码和回原文链接。给可导入 Anki 的字段预设和预览，显示哪些条目缺句子。同一个词在两篇论文含义不同时，保留两个语境。重复导出同一出处时使用稳定标识。进入外部工具以后仍能回读原论文，这是普通词表导出难以替代的价值。[已存字段与 CSV 导出](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/selectionFavorites.ts#L270) · [自定义卡模板及来源](https://github.com/soyami/zotero-pick2anki/blob/4792c6ae8df536a6f23b026a4bfa8156a2607f81/README.md) · [重复词与空译文处理](https://github.com/cocmin/zotero-anki-bridge/blob/7f08e2a21719c87608a94d0d2d4a6dc32ef80196/README.md)

有使用需求再接本机 AnkiConnect，区分已加入、已存在和 Anki 未运行。墨墨可作为另一个导出目标，需要单独验证它当前允许的导入格式和字段。本次没有验证其直连接口，不应把 Anki 的能力当成墨墨的能力。[墨墨开发待办](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/README.md#L187) · [写卡逻辑](https://github.com/soyami/zotero-pick2anki/blob/4792c6ae8df536a6f23b026a4bfa8156a2607f81/src/modules/anki.ts)

### 按段阅读与人工修订

先做“在侧栏读本页”：对已完成任务按页展示成对段落、显示本页缺译段、点击跳回 PDF。窄窗口就能受益，且可在 Zotero 8、9、10 分别验收。后续再在支持的版本试验原生 Reading Mode 里的段间译文、只看译文、只译可视段。Zotero 10 原生已经支持 PDF 重排、调字号和行距，并能在两种视图中共享批注。占主要工程量的是将你的译文与这些段落可靠关联。[滚动按需翻译](https://github.com/Liozhang/z-transplit/blob/0a50a23bf2926ac025e4fc5d8ac74fae66ec7245/src/core/pdf/sdt/lazyQueue.ts) · [源位置导航](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/src/platform/zotero-source-navigation.js) · [Zotero 10 原生阅读模式](https://www.zotero.org/blog/zotero-10/)

修订顺序可从“指出可疑段 → 展开原译 → 用户改一段 → 保存可撤销修订”开始，另存校订版 PDF 放在最后。保护公式、单位、引用编号。在原图含文字、跨页公式或 HTML 中无法可靠复原的表格旁提供回原页入口。你已有自动校对与失败补译，两者可作为定位入口。将用户确认的修订与模型输出分别留存，才便于解释某次导出为何与旧附件不同。[识别校对与受保护内容](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/docs/annotations.md) · [已有机器校对记录](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/babeldoc/format/pdf/quality_review.py)

### 每个模型配置记住限速

让“实验室网关 A”记住较低的 QPS 和并发，让本地服务 B 使用另一组值。打开任务详情能看到这次实际采用了哪组参数。先支持覆盖 QPS 与并发，空值继承现有全局设置。再评估额外重试次数。上游专门区分继承、禁止重试和引擎默认，还处理 SDK 与引擎叠加重试。旧配置迁移、A/B 切换后新建任务、重试旧任务和服务商限额反馈，是这项功能最有价值的四个测试。[配置级限速和重试行为](https://github.com/NightWatcher314/zotero-pdf2zh-next/blob/a8236962ad6a390a54abaf20d1403aa2d54d9539/README.md) · [当前读入全局值的入口](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/plugin/src/modules/pdf2zhHelper.ts#L343)

## 还有哪些方向值得保留

| 方向 | 可借鉴的细节 | 适合开始的条件 |
| --- | --- | --- |
| 多模型译文对读 | 仅在疑难句旁点“再看一个译法”，并排两段、标来源，保留人工选定结果。按需请求第二个模型 | 已能保存当前结果，能明确展示第二次调用 |
| 纸面或平板单栏版 | 从既有双语附件派生，预览含跨栏图或公式的一页，裁剪失败可退出 | 有小屏用户反馈和真实难例 |
| 可带走的双语 HTML | 每段保存原译与原页，正文切换双语／原文／译文。先处理文字和图注，复杂区块回到 PDF | 段落版本、公式和图片来源可核验 |
| 扫描页框选 OCR | 区域截取后先展示识别结果，改错再译。全文 OCR、阅读器框选 OCR 与扫描 PDF 的文本导出分别处理 | 用户主要卡在无法划选的扫描页，有本机运行时与带坐标的识别结果 |
| EPUB 与网页快照 | 先让独立短文本翻译可用，再单独处理 EPUB 章节位置、网页文本选择和持久化回链 | 阅读器类型和来源可区分。现有 PDF 页码与矩形坐标不能直接套用 |
| 译文朗读和高亮 | 在已生成的单语 PDF 上先验证 Zotero 10 原生朗读。仍有需求时，再实现双语按句切换或词汇复习 | 明确平台、语言和使用场景，能证明超出原生朗读的增量 |
| 首次安装与结果可达性 | 文本层足够时沿用轻量流程，让扫描件用户按需准备额外资源。受限网络可评估带版本校验的离线包 | 根据安装失败类型和包大小判断维护成本 |

多引擎比较可参考 Translate for Zotero。扫描方面可参考 Jadense 对文字层、可选 OCR 和离线依赖的分工。你的全文 OCR 兼容和扫描件无法划词可以同时成立。原生 Zotero 10 已能逐句高亮、跨栏续读和跳过部分非正文元素。上游 #374 同时提出朗读和单双语同时输出，维护者指出后者早已有选项。这条反馈提示，部分需求靠改进功能入口和命名就能满足。[多来源对读与支持的阅读器](https://github.com/windingwind/zotero-pdf-translate/blob/4e3edb955fe20ff7a2f9a7595cd0264e39b13a29/README.md) · [Jadense 的安装及文本层策略](https://github.com/jadense-ai/jadense-in-zotero/blob/main/README.en.md) · [Zotero 10 发布说明](https://www.zotero.org/blog/zotero-10/) · [上游讨论 #374](https://github.com/guaguastandup/zotero-pdf2zh/issues/374)

## 建议的开发与验证顺序

**第一轮，原文摘录和试译。** 让一组日常会做文献笔记的读者完成两件事：把一条带公式或页码的论点放进 Zotero 笔记，再从一篇长综述里选两页判断是否继续读。记录从选择到笔记入库的动作数、能否点回正确页、保存时新增的模型调用数，以及用户能否分辨“试译完成”和“全文完成”。已有译文落笔应新增零次模型调用，越界页码应在提交前被指出。

**第二轮，译文回原文。** 使用小而有针对性的样本：普通单栏 PDF、双栏论文、扫描或混合页、含跨页公式的文章、含重复免责声明的文章，以及同一 PDF 的两次译本。分开记“正确段落”“只确认到页”“明确无法确定”“错误自动定位”。最后一类应作为发布阻断项，不能靠一个笼统命中率掩盖。单测重复短句和输出版本，实际阅读器验证两份附件、多库和旧译本。[可复用的现有评测入口](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/benchmarks/translation/README.md)

**第三轮，复用同一份证据。** 用确认过的页码和段落信息支持复习卡、按页双语阅读稿、单篇术语修订。另行验证标题和摘要在只读群组、回收站、人工改译、多插件并装时的表现。对限速改动，在两个配额不同的模型配置之间切换，核对新任务、重试旧任务各自的有效值。只有实际测试后才能给出质量、耗时和费用的比较数字。

**如果下个版本只做一项，选“双语摘录到 Zotero 笔记”。** 它直接缩短读论文到写东西的路径，现有选区、原文与译文都能复用。若可以并行设计一项较大的功能，把时间留给“从译文查原文”的输出版本关联。这个基础会同时影响反查、校对、可引用笔记和双语阅读稿。

## 源码复用的边界

你的项目标注 AGPL-3.0-or-later。Translate for Zotero、BiRead、Z-Transplit、Full Text Translate 和沉浸式的仓库包含 AGPL 许可证。Mktero 与标题翻译的主项目列出 MIT。Anki Bridge 的自有代码为 MIT，特定测试素材单独注明 MPL。Split Screen 本次快照的根目录未见许可证，适合参考页码对齐的交互思路。复用具体实现前按文件查来源。第三方词典、模型、字体和随包二进制各有自己的说明。[项目许可与第三方说明](https://github.com/study-233/zotero-pdf2zh-pro/blob/d5156650d3ce5ce11620d76d4ffe196e944c043c/server/THIRD_PARTY_NOTICES.md) · [BiRead 许可](https://github.com/WWeiyiWang/BiRead/blob/abace0025e513da77f55467583a719a8cb4005a9/LICENSE) · [Mktero 许可](https://github.com/tenglvjun/mktero/blob/1224fb3a11ad91174e8fc573baa7324b2bcb2333/LICENSE) · [标题翻译许可](https://github.com/zhouyi654/zotero-title-translator/blob/b6b8bff58e060fb0f0323a0ad15a19ad4cac6fd4/LICENSE) · [第三方说明](https://github.com/cocmin/zotero-anki-bridge/blob/7f08e2a21719c87608a94d0d2d4a6dc32ef80196/THIRD_PARTY_NOTICES.md)
