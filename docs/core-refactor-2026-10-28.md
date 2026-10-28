# 翻译核心重构与 PDF 引擎升级

状态：v1.9.6 发布候选实施记录，尚未发布；插件与服务版本均为 1.9.6。本文对应 2026-10-28 核查的上游版本。

## 架构选择

保留经过精简的 BabelDOC 作为译文 PDF 引擎，采用完整 PyMuPDF 2.0 提供独立的文档导出能力。

| 方案 | 全文 PDF 排版与现有定制 | Markdown / JSON / TXT | 维护代价 | 结论 |
| --- | --- | --- | --- | --- |
| 用 Layout/4LLM 完全替换 BabelDOC | 必须重新实现公式保护、译文回填、字号与分页、双语输出、补译与质量检查 | 原生支持 | 最高，风险集中在核心翻译功能 | 不采用 |
| BabelDOC + PyMuPDF Lite | 可保留现有功能，原生依赖更小 | 缺少新版 Layout/4LLM 结构化导出 | 较低，但不满足导出需求 | 不采用 |
| 精简 BabelDOC + 完整 PyMuPDF | 沿用经过迁移的 PDF 翻译流程 | 按需独立导出原文或译文 | 不新增排版引擎、不增加抽象框架 | 采用 |

Layout/4LLM 处理版面识别与内容提取，并不提供 BabelDOC 的译文回填与双语排版流程。
两者仅在各自的功能入口运行：全文翻译不再附加第二遍 Layout/4LLM 分析；导出也不走翻译模型或 BabelDOC。
通过受控子进程隔离 PyMuPDF Layout 的全局初始化与翻译任务的生命周期。

来源：[BabelDOC 0.6.4](https://github.com/funstory-ai/BabelDOC/releases/tag/v0.6.4)、
[PyMuPDF 2.0](https://pypi.org/project/PyMuPDF/2.0/)、
[Document API](https://pymupdf.readthedocs.io/en/latest/document.html)、
[4LLM API 迁移说明](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html)。
上游 pdf2zh-next 最新版为 2.9.0，但本次从仓库现有定制 2.8.2 源码内化，不直接替换为另一份上游快照。

## 目标目录与职责

```text
plugin/src/modules/
  pdf2zh.ts                 原生菜单注册
  pdf2zhTaskManager.ts       PDF 任务、输出附件导入与恢复
  pdf2zhHelper.ts            PDF 附件与既有配置转换
  documentExport.ts         附件导出、保存、取消与旧服务提示
  selection*.ts             划词、记忆、词典与精读
server/
  server.py                 HTTP 兼容接口与输入边界
  task_manager.py           持久任务队列、恢复和运行状态
  task_runtime.py           唯一受控子进程监督器
  document_export.py        导出上传、并发、超时、下载与清理
  text_translation.py       划词/短文本编排
  translation_memory.py     文档翻译记忆
  glossary_manager.py       术语包与自定义术语
  pdf2zh_pro/
    config.py               核心配置和服务配置转换
    types.py                输出与验证结果
    errors.py               安全错误分类
    glossary.py             纯术语标准化
    pdf.py                  全文翻译编排与 BabelDOC 配置边界
    document.py             无模型依赖的结构化导出
    translator/
      providers.py          支持的模型配置类型
      base.py               公共请求、缓存与翻译约束
      openai.py             OpenAI 兼容适配
      openai_protocol.py    Chat/Responses 协议与请求选项
      account.py            账户适配公共行为
      chatgpt.py / codex.py / claudecode.py
      ollama.py / deepl.py
      cache.py / rate_limit.py
      placeholder_repair.py / preserved_text.py / validation.py / ...
    vendor-manifest.json    来源、迁移映射、旧文件哈希与定制功能清单
  babeldoc/
    format/pdf/new_parser/  0.6.4 原生解析、类型与受校验 CMap
    format/pdf/document_il/ 活跃 IR 创建、翻译、排版与 PDF 输出
    format/pdf/translation_recovery.py
    format/pdf/quality_review.py
    docvision/              PDF 翻译所需版面识别
    pdfminer/               活跃路径仍引用的兼容辅助代码
```

不新增统一引擎接口、插件容器或 AI 导读框架。未来导读可以组合 `document.py` 的结构化内容、
`translation_memory.py` 的原译文配对，以及模型层现有协议/限速；本轮不建立导读任务或 UI。
导出 JSON 保留 PyMuPDF 的版面结构，不声称是稳定的自有导读协议。

## 具体保留、迁移、重构与删除

| 文件或目录 | 处理与原因 |
| --- | --- |
| `pdf2zh_next/config/model.py`、`translate_engine_model.py` | 拆入 `pdf2zh_pro/config.py` 与 `translator/providers.py`；去掉 CLI/TOML/环境优先级，沿用服务传入配置 |
| `pdf2zh_next/translator/*` | 移入新核心、展开无意义的嵌套；保留模型、账户、协议回退、推理选项、占位符与限速行为 |
| `pdf2zh_next_service.py`、`pdf2zh_next/high_level.py` | 合并到 `pdf2zh_pro/pdf.py`；去掉重复子进程、IPC 和多文件 CLI 编排 |
| `text_translation.py`、`provider_models.py` | 直接构造模型配置；划词与配置测试不导入 PDF 引擎或创建假 PDF |
| `babeldoc/translator/{cache_lifecycle,literal_tags,placeholder_repair,preserved_text,request_budget,url_utils,validation}.py` | 移入核心模型层，清除模型层对 PDF 引擎的倒置依赖 |
| `babeldoc/translator/{translator,cache}.py`、`babeldoc/main.py` | 删除未使用的上游模型实现、重复缓存和 CLI |
| `pdf2zh_next` 的 CLI/GUI/TOML 配置、多余 Google/Bing 全文适配 | 删除未在产品中使用的路径；保留 `text_translation.py` 的必应划词实现 |
| `babeldoc/format/pdf/new_parser/*`、`frontend/il_creater_active*` | 升级为原生解析主路径，迁入字体缓存、Type3 几何、取消与解析计量 |
| `high_level.py`、`backend/pdf_creater.py` | 适配新排版配置与水印入口；修复 Windows 下 PDF 文档未关闭造成临时文件/输出替换失败 |
| `paragraph_finder.py`、`styles_and_formulas.py`、`il_translator*.py`、`typesetting.py` | 保留正文上下文、引用/公式保护、占位符修复、术语优先、批处理收尾与度量 |
| `translation_recovery.py`、`quality_review.py`、`result_merger.py` | 保留补译/质量检查；新增恢复指纹归档，升级解析器时避免错误重用段落位置 |
| `babeldoc/pdfminer/cmap/*` 与旧独立 CMap 加载器 | 删除重复资源；旧辅助模块委托新运行时的受限、校验加载器 |
| `babeldoc/pymupdf_compat.py` | 删除只适用于 PyMuPDF 1.25 的补丁 |
| `rapidocr_onnxruntime`、`docvision/table_detection/rapidocr.py`、未使用 `table_parser.py` | 删除未启用的表格 OCR 实现、模型与依赖；当前 OCR 检测与文字层兼容流程保留 |
| `document_export.py`、`pdf2zh_pro/document.py`、插件 `documentExport.ts` | 新增附件导出；统一复用任务监督器，不创建第二个进程池或历史表 |
| `server.py`、`hooks.ts`、`pdf2zh.ts`、两种语言 `addon.ftl` | 接入健康能力、上传/取消路由、菜单和生命周期取消 |
| `shared/diagnostics/contract.json` 与两端生成文件 | 新增导出事件与能力覆盖；冻结的 v1 兼容基线不改动 |
| `pyproject.toml`、`uv.lock`、`Dockerfile`、`check_*runtime.py`、`check_pypi_artifacts.py` | 更新新包路径、资源和完整 PyMuPDF 依赖；wheel 禁止混入旧核心 |
| `vendor_pdf2zh_runtime.py` | 只在新临时目录提取带哈希的上游快照；拒绝覆盖已维护源码 |
| `.github/workflows/ci.yml` | 为 Windows / Apple Silicon 原生作业加入真实解析、导出及数据兼容测试 |

完整逐文件源路径和原始哈希见 [迁移清单](../server/pdf2zh_pro/vendor-manifest.json)。

## 依赖与交付

- BabelDOC 固定源码基线 0.6.4；PyMuPDF 完整包 2.0（MuPDF 1.28.5），保留 Layout/4LLM；生产包不安装外部 BabelDOC 或 pdf2zh-next 分发包。
- Python 仍限制 3.12/3.13。支持 Windows x64、Apple Silicon Mac 与具有对应原生依赖的 Linux；Intel Mac 不纳入新运行时支持。
- 升级 ONNX 和 cryptography 安全下限，去除 configargparse、toml、tomlkit、huggingface-hub、fsspec、pyclipper、shapely 等不再使用的直接依赖；pypdf 仅保留在测试依赖组。
- Docker 和本地 uv 同时更换源码路径与锁文件。Windows 使用相同 Python wheel 和安装检查，发行构建的完整升级生命周期仍是后续发布前门槛。
- 外部 Homebrew tap 的包目录与冒烟测试需要同步。已准备 [本地补丁](patches/homebrew-core-refactor.patch)，基于 formula blob `cef6f177e7006144ec092e2ab98d4c7847d8afec`。
  **本轮不应用到远端。** 后续授权发布时先复核 tap 新变化，再将补丁与新版本/源码 revision 一起更新；不能单独把补丁应用到仍指向旧源码的公式。

## 历史数据与兼容边界

| 数据 | 策略 |
| --- | --- |
| `tasks.json`、备份、任务目录、输出 PDF | 沿用格式；更换安装位置时重定位主记录与备份中的路径，保留历次原始副本；新导出不写任务历史 |
| 模型配置、密钥、ChatGPT/Codex 凭据 | 不迁移、不删除；服务 ID 与请求字段沿用 |
| 核心响应缓存 | 保留 `pdf2zh_next/cache.v1.db` 的历史目录、表结构、缓存键；模块改名不改缓存命名空间 |
| 旧 BabelDOC 缓存文件 | 不再使用重复实现；磁盘上的历史文件不删除 |
| 术语、自定义词库、划词缓存、翻译记忆 | 沿用原存储和 API，不复制一份新数据库 |
| 任务恢复数据库 | 指纹切换时事务归档旧段落与复核状态；匹配历史指纹可恢复；旧 JSON 兼容读取 |
| 新解析器产生的段落 | 使用新的解析版本指纹；保留旧记录，但不将旧段落位置强行套用到新 IR |
| 旧插件 | 原任务/划词/健康接口继续工作；新增导出通过能力协商 |
| 旧服务搭配新插件 | 导出前检查能力并提示更新；不会上传后才误认为 PDF 翻译任务 |

恢复归档是防止数据丢失与段落错配的措施，并不保证新解析器升级后所有旧任务都零请求续跑。
命中原响应缓存或翻译记忆仍可复用相同内容。正式升级前保留完整数据目录副本，不能在真实库上运行迁移试验。

## 分阶段实施与验证

1. **基线与来源固定**：保存旧核心/引擎源码和哈希，记录旧环境测试与既有失败，核对上游依赖和各平台 wheel。
2. **核心内化**：迁移协议/模型/缓存/限速，去掉旧 CLI 与反向依赖。验证配置、12 类服务、Chat/Responses、账户、划词独立性与历史缓存读取。
3. **数据兼容**：恢复指纹归档、旧 JSON/SQLite、WAL、事务失败回滚与切回旧指纹恢复；不得改变生产数据目录。
4. **PDF 引擎迁移**：合并上游原生解析器，迁入全部定制。验证字体缓存、CMap、Type3、表格、OCR 兼容、引用、质量检查、补译和取消。
5. **附件导出**：真实 PyMuPDF 导出原文和中文译文；验证 Markdown/JSON/TXT 单文件内容、布局 JSON、OCR/加密反馈、并发/取消/超时、错误脱敏与文件清理。验证原生 Zotero“导出文件 → 格式”子菜单和对话框。
6. **交付闭环**：全服务端/插件/脚本/离线基准回归；从干净源码构建 wheel/sdist，独立安装后验证 CLI、资源和导出能力；更新文档和外部 tap 补丁。

不调用收费模型、不使用用户论文作为合成回归输入。真实 PDF 排版集成用本地生成 PDF、内嵌中文字体、固定翻译器与固定布局结果，
执行实际解析、IR、排版、输出与渲染；它覆盖引擎连通性，不能替代真实模型与复杂论文的最终质量验收。
Windows 本机与 WSL Ubuntu 的 Linux 环境通过不等于 macOS、Docker 或 Homebrew 已实机通过；
原生 CI 和 Docker/Homebrew 安装验收需在对应平台运行。

## 回归重点与发布前门槛

- 双栏/跨页表格、内嵌字体、Type3、公式/上标/引文、长 URL、参考文献和复杂扫描页仍需真实论文样本比对。
- 水印/无水印、单语/双语、指定页、混合已译/失败段落、质量复核和补译需保持输出与状态一致。
- 取消时验证所有所属进程退出；恢复/缓存故障保持原文件，不用空记录掩盖错误。
- PyMuPDF JSON 上游结构可能随版本变化；未来对外稳定接口应另做窄适配，而不是现在预建导读平台。
- 结构化导出使用 PDF 中现有文字层，无法保证把所有公式还原为 LaTeX，也不自动执行 OCR。
- 后续提交前审查大规模上游源码 diff、许可证与迁移清单；发布前按仓库扩展安装生命周期运行升级、卸载、回滚与多平台检查。

## 本机验收结果（2026-10-28）

服务端全量 704 项（2 跳过），最终导出/诊断补丁相关 31 项全部通过；插件 450 项（1 跳过）、最终相关 35 项及静态检查通过。
通用脚本 231 项（2 跳过）、离线基准 91 项通过。干净 wheel/sdist 检查及独立安装验证通过，安装 88 个分发包。
真实开发 Zotero 完成中英文、明暗主题的四次导出与取消验证；保存位置由测试自动选择，解析和文件写入均为真实实现。
追加 Windows 验证已完成真实单文件 EXE 安装，以及发布版 1.9.4、1.9.5 到未发布核心候选的实际升级。
两条升级路径都校验了历史任务、已有译文文件、术语、模型/划词缓存、翻译记忆、恢复/质量状态及合成账户数据。
修复后再次完成实际安装目录迁移：删除原安装后，历史任务路径仍可用，先前的迁移备份保持原字节内容。
安装预检使用独立数据目录，已验证不会读取继承的损坏任务库或改变现有任务文件。
Windows 数据迁移回归 16 项（1 跳过）；Linux Python 3.13 安装 87 个分发包，真实解析/排版、
结构化导出与恢复存储检查通过，Linux 原生临时目录中的 16 项数据迁移/权限测试全部通过。
WSL 的 Windows 挂载盘不提供本测试要求的 POSIX 文件权限，权限验收使用 Linux 临时目录。
完整 Windows 生命周期的首次运行在迁移后启动等待超时；服务实际健康，单次系统端口查询耗时约 28 秒。
保留所有进程归属断言并调整测试等待时限后，最终包完整生命周期通过（约 50 分钟）：
全新安装、覆盖安装、自更新、失败准备、降级拦截、保留数据卸载、重装、迁移回滚、成功迁移及彻底卸载。
最终 wheel/ZIP 另从已发布 1.9.5 实际升级并通过完整数据夹具验证。
该复验首次因新依赖下载耗时触及交接上限，复用已校验缓存后通过，未放宽数据或交接断言。
修复 PowerShell 5.1 将归档进度误当错误的问题，工作区 21 项回归通过并覆盖 PowerShell 5.1/7。
尚未运行 Apple Silicon、Docker 或 Homebrew 实机安装。
详细日志与截图保存在本机受管理报告目录；正式发布前仍需完成上述平台与真实论文质量门槛。
