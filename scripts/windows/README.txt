zotero-pdf2zh-pro Windows 10/11 x64

快速开始

1. 解压整个 ZIP，不要只单独复制 EXE。
2. 双击 zotero-pdf2zh-pro.exe。
3. 点击“安装并启动”。控制中心会安装 uv、托管的 Python 3.13 和服务端。
4. Zotero 插件的服务地址保持为 http://127.0.0.1:8890

首次安装默认启用“登录 Windows 后自动启动”。登录后窗口不会弹出，控制中心会驻留托盘并确保服务运行。可以随时在主卡片关闭此开关；升级不会改变该选择。

自动更新

控制中心启动时会静默检查 GitHub 上的稳定版本。发现新版后点击“更新到 vX.X.X”，程序会自动下载、校验、升级控制中心和服务端并重启。任务、翻译结果、日志和自启选择都会保留，失败时会恢复旧版。

首次获得自动更新能力仍需手动安装一次包含该功能的 Windows ZIP。旧版 EXE 不允许覆盖新版。

卸载

在控制中心“更多”菜单或开始菜单选择“卸载”。默认保留任务数据和日志，也可以选择彻底删除。

目录

数据：%LOCALAPPDATA%\zotero-pdf2zh-pro\data
日志：%LOCALAPPDATA%\zotero-pdf2zh-pro\logs

故障恢复

install.cmd、start-server.cmd、stop-server.cmd、view-log.cmd 和 uninstall.cmd 仍随包提供，仅用于控制中心无法打开时的恢复操作。

系统要求与安全说明

- 需要 Windows 10/11 x64 和 Microsoft Edge WebView2 Runtime。缺失 WebView2 时，程序会在创建窗口前给出提示；请从 Microsoft 官方网站下载安装。
- 当前 EXE 未进行代码签名，Windows SmartScreen 可能显示警告。请只从本项目官方 GitHub Release 下载，并核对发布页 SHA-256。
- 程序不会创建 Windows Service、计划任务或防火墙规则，也不会停止占用 8890 端口的未知进程。
- 登录自启仅写入当前用户可见、可关闭的启动项，固定使用 --autostart 参数。
