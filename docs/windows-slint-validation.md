# Slint 框架验证

[开发维护笔记](development-notes.md) · [Slint 迁移验收](windows-slint-acceptance.md)

本页保留 2026-10-12 的第一阶段验证记录。用户自行试用后反馈“可以没什么问题”，
并明确授权完整迁移。主控制中心已迁移至 Slint，最终候选、数据及待验证范围见
[迁移验收](windows-slint-acceptance.md)。以下大小与资源数据属于早期验证程序。

## 运行与检查

验证程序位于 `dist/windows-slint-probe/pdf2zh-slint-probe.exe`，可以单独双击运行，
无需安装。它不启动翻译服务，不写产品配置、注册表或快捷方式。

1. 在路径输入框使用中文输入法输入文字，确认候选框靠近光标；用 Ctrl+A、Ctrl+C、
   Ctrl+V 检查复制粘贴。观察简体、繁体、英文与数字是否完整。
2. 切换“浅色”“深色”“跟随系统”，使用 Tab、Shift+Tab、空格和方向键检查焦点
   与操作。尝试滚动说明区域，确认文字未被裁切。
3. 点击“演示进度”，确认界面仍可操作。进度仅为十秒控件演示，不代表真实下载。
4. 点击“收起到托盘”或关闭窗口，然后从验证版托盘图标打开；也可以再次运行同一
   EXE，检查是否恢复原窗口。最后用“结束验证”完全退出。
5. 在可取得的 100%、150%、200% 缩放条件下检查文字、按钮及输入法候选位置。
   未测的缩放与设备直接记录为未测，不必为了验证修改日常使用配置。

反馈时注明系统版本、缩放比例、通过的项目和异常操作即可。高对比度、屏幕阅读器、
Windows 10 干净设备与远程桌面仍需后续专项验证。

## 已测数据与边界

构建为 Slint 1.18.1、Winit、显式 `skia-software`、x64 MSVC 静态 CRT；
界面采用 Fluent 控件、系统字体。编译依赖中仍包含图形 API 支持；实际选择软件渲染，
不将“软件渲染”解释为二进制中完全没有 Direct3D/OpenGL 导入。

| 项目 | 结果 |
| --- | --- |
| 验证 EXE | 26,051,072 字节（26.1 MB） |
| 含当前服务 wheel 的测试安装载荷 | 33,652,970 字节（33.7 MB），低于 40,000,000 字节 |
| 静态导入 | 通过 Windows 系统 DLL 白名单；无 WebView2、VC++ 可再分发 DLL |
| 直接进入托盘，未打开面板 | 原生平均 14.2 MiB；Slint 平均 26.9 MiB；增加 12.7 MiB |
| 同条件五秒 CPU 增量 | Slint 三次均为 0 毫秒，无子进程 |
| 打开面板后关闭的内存增量 | 待测，不用直接托盘启动数据替代 |
| 中文、输入法、主题、缩放、复制粘贴、托盘恢复 | 用户试用反馈无明显问题；未提供逐项记录，不将全部项目标记通过 |
| 面板关闭后无持续重绘 | 待测，不以短时零 CPU 推断通过 |

两组托盘启动采样均在同机、无翻译服务条件下执行，等待三秒后观察五秒，每组
三次。这与早期原生验收中的“打开后关闭面板”条件不同，不能交叉比较。

安装载荷只用于测量打包上限，存放于 `.tmp/slint-gate/slint-size-fixture.exe`，
其中控制程序是验证界面，不具备安装功能，因此不作为可安装候选交付。
完整迁移后还须重新测量后端、界面、许可文件一起打包的最终大小。

`dist/windows-slint-probe/gate-report.json` 保留生成时的证据与待验证项，
其中 `migrationAllowed: false` 是用户试用前的快照。此后用户明确授权迁移；
保留原候选与未执行的检查记录，不提高体积或内存上限。

## 源码与复现

- `windows-app/slint-probe/` 是独立 Cargo 工程，锁定 Slint 与完整依赖树。
- `scripts/build_windows_slint_probe.ps1` 载入 Visual Studio 的当前进程构建环境，
  使用传入的 LLVM 和 Skia 源码；不会全局安装工具链。
- `scripts/prepare_windows_skia_source.py` 校验固定 Skia 源码归档，将仅指向归档内部
  文件的符号链接转换为文件副本，不要求启用开发者模式。
- `scripts/measure_windows_control_process.py` 只测量它创建的隔离进程；
  `scripts/check_windows_slint_gate.py` 将未执行检查保留为待验证。

构建命令：

```powershell
./scripts/build_windows_slint_probe.ps1 -LlvmRoot <LLVM目录> -SkiaSourceRoot <Skia目录>
```

本机使用 LLVM 21.1.8，Skia `m153-0.101.2`。源码归档 SHA-256 为
`30c8b51a0a32fae63562c303bdc91605519a9598951422610e3fb8685b3c4f6f`。
指定外部 Skia 源码会采用离线构建；需先使用源码的 `bin/fetch-gn` 和
`tools/git-sync-deps --shallow` 准备 GN 与所需依赖。依赖版本以该源码的 `DEPS` 为准：
brotli、d3d12allocator、expat、libjpeg-turbo、libpng、wuffs、zlib、spirv-cross。
只同步这些构建依赖时设置 `GIT_SYNC_DEPS_SKIP_EMSDK=1`，不需要 Android 或 Emscripten。

依赖静态导入白名单补充的 COM、Direct3D、OpenGL 与辅助功能 DLL 均为目标系统组件；
相关微软说明：[系统 API](https://learn.microsoft.com/en-us/uwp/win32-and-com/win32-apis)、
[OpenGL](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-wglcreatecontext)、
[UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview)。
静态导入检查不能代替干净设备上的实际启动验证。

本轮验收工具测试共 13 项通过：迁移门槛 4 项、Skia 解包路径 3 项、PE 导入 6 项。
第三方许可随验证目录提供；没有公开发布。
