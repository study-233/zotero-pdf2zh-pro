# 内置翻译引擎与旧依赖兼容性排查

排查日期：2026-09-29。对象：`zotero-pdf2zh-pro` 1.7.3 源码工作树、`server/.venv`，以及公开上游源码。工作树排查前已有诊断、进程取消、字体缓存等未提交改动，本报告不把它们当作本次新增修改。

## 结论

**确实存在旧引擎遗留问题，但不是“DeepSeek 官方服务太旧”，也不能只运行一次依赖升级解决。**

本项目把 pdf2zh-next、BabelDOC、RapidOCR 的源码直接打包，运行时不会从 PyPI 获取它们的修复。PyMuPDF 又被限制在 `>=1.25.1,<1.25.3`，锁文件和本地实际安装都是 1.25.2。因此，即使重新安装本项目，也会继续得到这套旧 PDF 处理实现。

原项目 Zotero 插件版本 4.1.7 与本项目 1.7.3 是不同产品的版本号，不能直接比较大小。可以确认当前内置引擎快照及其差异；本次没有把当前源码精确追溯到原项目的某个分叉提交。

| 组件 | 本项目 | 核对的上游 | 判断 |
| --- | --- | --- | --- |
| pdf2zh-next | 内置 2.8.2，加本地修改 | main / 2.9.0，`f8dffcf4c3a33b254391d43514439b975ce8d966` | 版本较旧，但本项目已有协议、重试、推理开关等自定义功能；不可直接覆盖 |
| BabelDOC | 内置 0.5.24，加本地修改 | main / 0.6.4，`38d3896dcde9b5a940c62cf5563cadea673a64d3` | 差异最大：新 PDF 解析器、字体、内存、排版和安全修复 |
| PyMuPDF | 1.25.2；声明上限 `<1.25.3` | 官方发布记录 1.28.2；另核对 1.26.7 与 main 源码 | 陈旧上限阻止升级；1.26.7 仍有此次警告函数错误，main 已修正这两处调用 |
| OpenAI SDK | 本地 2.32.0，声明 `>=2.32.0,<3` | 本地调用与测试核对 | 没有证据表明此次报错是 SDK 过旧 |
| RapidOCR | 内置 1.4.4 | 本次未做新版 OCR 整体迁移验证 | BabelDOC 0.6 已改变表格检测行为，升级须核对产品功能 |

来源：[原项目说明](https://github.com/guaguastandup/zotero-pdf2zh)、[pdf2zh-next 版本差异](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/compare/v2.8.2...f8dffcf4c3a33b254391d43514439b975ce8d966)、[BabelDOC 版本差异](https://github.com/funstory-ai/BabelDOC/compare/v0.5.24...38d3896dcde9b5a940c62cf5563cadea673a64d3)、[PyMuPDF 发布记录](https://pymupdf.readthedocs.io/en/latest/changes.html)。

## 已复现并修复

### 1. 不支持的字体或坏批注导致 `message()` 参数错误

- PyMuPDF 1.25.2 的 `JM_get_fontextension()` 在处理未知 FontFile3 子类型时调用 `message(format, value)`，而 `message` 只接受一个参数。
- `_addAnnot_FromString()` 的 Python 回退路径有同样的两参数错误。因此这是两个触发点，不只发生在字体处理中。
- 使用内存中构造的 PDF 调用 `page.get_fonts()`，复现了用户提供的完整 TypeError；无需 API 密钥，也不经过 DeepSeek。
- 在 `babeldoc/pymupdf_compat.py` 加入仅作用于 1.25.1/1.25.2 的幂等兼容处理，先格式化字符串，再交给原始警告函数。由 `babeldoc` 导入时安装，新的 PDF 子进程也会生效。
- 保留警告与其他 PDF 异常；这个修改不代表新增了对未知字体的识别能力。

测试覆盖：未知子类型、缺少子类型、坏批注、重复安装、版本范围、新 Python 进程及普通 PDF 文本往返。

**用户那份 PDF 尚未提供对应 Traceback，本次确认的是能产生完全相同错误的实际缺陷，不把它表述为已拿到用户任务的最终故障堆栈。**

### 2. CMap 加载器允许读取并反序列化包外文件

- 旧 `babeldoc/pdfminer/cmapdb.py` 拼接 PDF 提供的名字和文件路径，并对读入文件执行 `pickle.loads`；支持外部 `CMAP_PATH`。
- 本地用只含普通字典的临时 `.pickle.gz` 验证：绝对路径可以加载包外内容。没有执行攻击载荷。
- 上游已披露 [GHSA-m8gf-v64p-gfmg](https://github.com/funstory-ai/BabelDOC/security/advisories/GHSA-m8gf-v64p-gfmg)。利用仍需要可供加载的攻击者控制文件等前提；不能据此声称用户环境已遭攻击。
- 从 BabelDOC 0.6.4 的 `17480db9df92` 回移清单和校验加载器，适配当前旧目录。148 份内置 CMap 与上游 SHA-256、字节大小全部一致。
- 仅允许清单内文件、限制目录范围和读取大小、先验散列再解压/反序列化。故意取消 `CMAP_PATH` 外部覆盖。

测试覆盖：全部 148 个资源、正常中日韩映射、绝对路径/目录穿越、外部覆盖、文件缺失、同长度篡改、不同长度篡改和符号链接越界。拒绝分支不会进入反序列化。

### 3. 损坏的 LZW 压缩流抛出未处理 `IndexError`

- 旧 `LZWDecoder.feed()` 对首个非法字典索引直接访问列表。
- 复现了“缺少初始化码”和“初始化后立即给出非法索引”两种输入。
- 回移上游索引及空值检查，使其进入已有 `CorruptDataError` 处理路径，不把列表越界作为未知错误冒出。
- 这沿用解析器遇到损坏数据停止解码的行为，不保证修复损坏 PDF 的内容。

测试同时验证正常 `ABAB` 解码不变。

### 4. 可选 ImageWriter 输出路径受 PDF 图片名称影响

- `_create_unique_image_name()` 原本直接拼接 XObject 名称，`../` 或绝对路径可使输出路径脱离目标目录。
- 回移文件名清理，并在 POSIX 上也处理 Windows 路径分隔符；保留重名编号逻辑。
- 这是内置可选导出器的实际缺陷；本次未证明正常翻译主流程调用了它，不能把它当作当前翻译故障的原因。

### 5. `ClaudeCodeSettings` 出现在 `__all__`，却没有导入

`from pdf2zh_next import *` 实际抛出 `AttributeError`。已补齐公开导入。普通 DeepSeek 路径不依赖这个星号导入，因此它是独立的小型接口缺陷。

### 6. macOS 系统 Bash 3.2 的空数组兼容问题

现有发布脚本使用 `set -u`，退出清理读取空 `TEMP_PATHS` 数组时，系统 Bash 抛 `unbound variable`，把原错误码覆盖成 1。既有 3 个发布恢复测试复现此问题。已调整数组展开方式；26 项发布脚本测试通过。本次没有执行真实发布、回滚或远端写操作。

## 尚未迁移的高价值修复

这些项目保留在报告中，**不表示本次已经整体修复**。

| 优先级 | 项目 | 证据与可能表现 | 建议 |
| --- | --- | --- | --- |
| 高 | 非默认 Type3 FontMatrix | 当前字体实现仍用正则提取单个小数因子；上游 0.6.3 修复了特殊 Type3 字体导致译文极小、近似空白的情况。对当前 `merge_bbox(..., factor=0)` 可复现除零，但未取得用户的实际 Type3 问题 PDF | 随字体度量和输出字号逻辑一起迁移，不能只改版本号 |
| 高 | 超大页面内存 | 当前本地布局识别以固定 DPI 渲染，扫描检测直接 `get_pixmap()`；缺少上游 0.6.4 的统一像素预算 | 迁移渲染预算和坐标缩放一起验证；本次未实际触发 OOM |
| 高 | 段落上下文深拷贝及递归对象 | 当前翻译器仍 `copy.deepcopy(paragraph)` 保存标题；上游 0.6.1 改成轻量标题快照并增加递归保护 | 回移时保留本项目段落恢复、审校、批处理改动；本次未复现实际文档递归故障 |
| 中高 | 复杂 PDF 图形/内联图片资源 | 上游 0.6.0 重写解析器，改善 ExtGState、shading、clip、Form XObject 等重建与 token 序列化；当前仍是旧解析器 | 通过实际复杂 PDF 对照输出做整体升级验收；单元测试不能证明视觉无回归 |
| 中 | Azure 等独立旧适配器的空返回 | 用无网络的 Azure 返回对象复现 `choices=[]` 导致 IndexError、`content=None` 导致 AttributeError；相同取值方式还见于 SiliconFlow/Qwen-MT 的独立适配器 | 对仍在产品中启用的适配器统一空返回诊断；当前 DeepSeek 使用的 OpenAI 适配器已有对应防护测试 |
| 中 | 独立适配器长重试 | Azure、Ollama、Qwen-MT 等仍有 `stop_after_attempt(100)`；部分客户端没有显式超时 | 按实际启用服务迁移有界重试及取消逻辑；不把此项误报为当前 DeepSeek 路径的问题 |

依据：[BabelDOC 0.6.0](https://github.com/funstory-ai/BabelDOC/blob/17480db9df92/docs/release-notes/v0.6.0.md)、[0.6.1](https://github.com/funstory-ai/BabelDOC/blob/17480db9df92/docs/release-notes/v0.6.1.md)、[0.6.3](https://github.com/funstory-ai/BabelDOC/blob/17480db9df92/docs/release-notes/v0.6.3.md)、[0.6.4](https://github.com/funstory-ai/BabelDOC/blob/17480db9df92/docs/release-notes/v0.6.4.md)，并核对当前相应调用路径。

## 为什么不直接装最新版

1. 核对到的 pdf2zh-next 2.9.0 仍声明 `pymupdf<1.25.3`（旁注是 Linux ARM64 wheel）；BabelDOC 0.6.4 已声明 `pymupdf>=1.26.7`。这两个约束不能同时成立，需要由本项目明确选择并验证整套组合。
2. 本项目的 `scripts/vendor_pdf2zh_runtime.py` 会删除并重建现有内置目录。直接执行会丢掉本项目的恢复、缓存、协议、取消和本次回移补丁。应在临时独立检出中抽取上游，再审查差异。
3. BabelDOC 0.6 不再按旧方式加载 RapidOCR 表格文本检测资源，`table_model` 变为弃用项。必须确认现有表格翻译设置和用户预期。
4. PyMuPDF 的更新也曾引入回归。官方 1.28.2 记录包含字体子集化崩溃等修复；不能认为任意高于 1.25.2 的版本都等价可靠。

建议分两步：先交付本次可独立验证的兼容补丁；再在独立开发分支迁移 BabelDOC 0.6.4 和经过验证的 PyMuPDF 版本。第二步用普通论文、Type3/CJK、旋转页、复杂图形、超大页面、mono/dual 输出、表格及任务取消/恢复构成验收集合，覆盖 macOS、Windows、Linux/Docker。没有这组验收，不建议只放开全部版本上限。

## 当前工作树/环境的独立问题

这些发现不能归因于原项目的中间版本，发布前仍需处理：

- 本地虚拟环境 `anyio=4.13.0`，当前声明及锁文件要求 `>=4.14.2`。运行环境和依赖文件有漂移；本次未修改用户环境。
- 已有未提交改动把 CLI 改为 `service_launcher:main`，但 `scripts/check_installed_runtime.py` 仍期待 `server:main`。
- 当前 Dockerfile 的单文件 COPY 清单没有新增的 `service_launcher.py`、`diagnostics.py`、`task_runtime.py`。这会影响该工作树的容器交付。上述文件属于排查前正在进行的开发，本次未代为调整它们的发布链路。
- 当前 pnpm 启动器联网获取项目固定版本失败；日志同时提示 pnpm 配置格式变更。失败包含本地代理/网络不可达证据，不据此断定锁文件被篡改。插件测试直接执行其 `node --test` 脚本内容完成。

## 验证与边界

- 新增 `server/tests/test_legacy_runtime_compat.py`：13 项通过。
- 服务端全套：288 项通过。使用临时 SQLite 翻译缓存，并统一 macOS 临时目录的真实路径，避免读写用户实际缓存和 `/var`/`/private/var` 别名造成测试误差。运行器和日志保存在 `.local-dev/compat-audit/`。
- 插件：直接执行 `node --test`，122 项通过。
- 发布脚本：26 项通过（隔离测试，不是真实发布）。
- `git diff --check` 通过。
- 扫描内置 Python 模块同文件直接函数调用的明显位置参数数量不匹配，没有发现额外命中；这是有限静态检查，不能覆盖动态分派、跨模块调用或第三方库全部路径。
- 未消耗 DeepSeek API、未执行用户原始论文的完整翻译、未运行新版引擎视觉对照、未完成 Windows/Docker 实机安装测试。
- 本次修改仅在源码工作树，未部署到 Zotero/后台服务、未发布新版本、未自动替换正在进行的其他修改。

原始 `message()` 问题已补上可复现回归保护；其余迁移项与当前发布链路问题需要按上述清单处理，不能用本次单元测试通过代替全部上线验收。
