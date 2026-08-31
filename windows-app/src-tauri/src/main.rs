#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use semver::Version;
use serde::Serialize;
use serde_json::Value;
use std::{
    env,
    ffi::OsStr,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpStream},
    os::windows::ffi::OsStrExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::Duration,
};
use tauri::{
    image::Image,
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent, State, WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

const PRODUCT_NAME: &str = "zotero-pdf2zh-pro";
const DEFAULT_PORT: u16 = 8890;
const WEBVIEW2_CLIENT_ID: &str = "{F3017226-FE2A-4295-8BDF-00C72A961EAB}";

#[derive(Default)]
struct AppContext {
    operation_busy: AtomicBool,
    exiting: AtomicBool,
}

struct OperationGuard<'a>(&'a AtomicBool);

impl Drop for OperationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Clone)]
struct ProductPaths {
    app_root: PathBuf,
    bin_dir: PathBuf,
    data_dir: PathBuf,
    logs_dir: PathBuf,
    server_log: PathBuf,
    control_log: PathBuf,
    installed_version: PathBuf,
    installed_gui: PathBuf,
    control_pid: PathBuf,
    control_executable: PathBuf,
}

impl ProductPaths {
    fn discover() -> Result<Self, String> {
        let app_root = match env::var_os("PDF2ZH_WINDOWS_APP_ROOT") {
            Some(path) => PathBuf::from(path),
            None => {
                PathBuf::from(env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not available")?)
                    .join(PRODUCT_NAME)
            }
        };
        let bin_dir = app_root.join("bin");
        let logs_dir = app_root.join("logs");
        Ok(Self {
            data_dir: app_root.join("data"),
            server_log: logs_dir.join("server.log"),
            control_log: logs_dir.join("control-panel.log"),
            installed_version: app_root.join("installed-version.txt"),
            installed_gui: bin_dir.join(format!("{PRODUCT_NAME}.exe")),
            control_pid: app_root.join("control-panel.pid"),
            control_executable: app_root.join("control-panel-executable.txt"),
            app_root,
            bin_dir,
            logs_dir,
        })
    }

    fn script(&self, name: &str) -> PathBuf {
        self.bin_dir.join(name)
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
enum InstallationStatus {
    NotInstalled,
    Current,
    UpdateAvailable,
    DowngradeBlocked,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
enum ServiceStatus {
    Stopped,
    Running,
    PortConflict,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ControlState {
    installation: InstallationStatus,
    service: ServiceStatus,
    app_version: String,
    installed_version: Option<String>,
    service_version: Option<String>,
    address: String,
    autostart_enabled: bool,
    data_dir: String,
    log_file: String,
    control_log: String,
    running_from_installed_path: bool,
}

#[derive(Clone, Serialize)]
struct OperationLog {
    line: String,
}

#[derive(Default)]
struct HealthResult {
    listening: bool,
    valid: bool,
    version: Option<String>,
}

fn port() -> u16 {
    env::var("PDF2ZH_WINDOWS_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

fn normalize_path(path: &Path) -> String {
    let normalized = path
        .to_string_lossy()
        .trim_end_matches(['\\', '/'])
        .replace('/', "\\")
        .to_lowercase();
    normalized
        .strip_prefix(r"\\?\")
        .unwrap_or(&normalized)
        .to_owned()
}

fn path_equal(left: &Path, right: &Path) -> bool {
    let canonical_left = fs::canonicalize(left).unwrap_or_else(|_| left.to_owned());
    let canonical_right = fs::canonicalize(right).unwrap_or_else(|_| right.to_owned());
    normalize_path(&canonical_left) == normalize_path(&canonical_right)
}

fn current_executable() -> Result<PathBuf, String> {
    env::current_exe().map_err(|error| format!("无法读取当前程序路径：{error}"))
}

fn running_from_installed_path(paths: &ProductPaths) -> bool {
    current_executable()
        .map(|path| path_equal(&path, &paths.installed_gui))
        .unwrap_or(false)
}

fn read_trimmed(path: &Path) -> Option<String> {
    fs::read_to_string(path)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn installation_status(paths: &ProductPaths) -> (InstallationStatus, Option<String>) {
    if !paths.installed_gui.is_file() || !paths.script("common.ps1").is_file() {
        return (InstallationStatus::NotInstalled, None);
    }
    let installed = read_trimmed(&paths.installed_version);
    let Some(installed_text) = installed.as_deref() else {
        return (InstallationStatus::UpdateAvailable, installed);
    };
    match (
        Version::parse(env!("CARGO_PKG_VERSION")),
        Version::parse(installed_text),
    ) {
        (Ok(candidate), Ok(current)) if candidate > current => {
            (InstallationStatus::UpdateAvailable, installed)
        }
        (Ok(candidate), Ok(current)) if candidate < current => {
            (InstallationStatus::DowngradeBlocked, installed)
        }
        (Ok(_), Ok(_)) => (InstallationStatus::Current, installed),
        _ => (InstallationStatus::UpdateAvailable, installed),
    }
}

fn query_health(paths: &ProductPaths) -> HealthResult {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port()));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(600)) else {
        return HealthResult::default();
    };
    let mut result = HealthResult {
        listening: true,
        ..HealthResult::default()
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "GET /health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
        port()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return result;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return result;
    }
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return result;
    };
    if !headers.starts_with("HTTP/1.1 200") && !headers.starts_with("HTTP/1.0 200") {
        return result;
    }
    let Ok(json) = serde_json::from_str::<Value>(body) else {
        return result;
    };
    result.version = json
        .get("version")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let expected_version = read_trimmed(&paths.installed_version)
        .unwrap_or_else(|| env!("CARGO_PKG_VERSION").to_owned());
    let version_matches = result.version.as_deref() == Some(expected_version.as_str());
    let workspace = json.get("workspace");
    let writable = workspace
        .and_then(|value| value.get("writable"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let workspace_path = workspace
        .and_then(|value| value.get("path"))
        .and_then(Value::as_str)
        .map(PathBuf::from);
    result.valid = version_matches
        && writable
        && workspace_path
            .as_deref()
            .map(|value| path_equal(value, &paths.data_dir))
            .unwrap_or(false);
    result
}

fn build_state(app: &AppHandle) -> Result<ControlState, String> {
    let paths = ProductPaths::discover()?;
    let (installation, installed_version) = installation_status(&paths);
    let health = query_health(&paths);
    let service = if health.valid {
        ServiceStatus::Running
    } else if health.listening {
        ServiceStatus::PortConflict
    } else {
        ServiceStatus::Stopped
    };
    Ok(ControlState {
        installation,
        service,
        app_version: env!("CARGO_PKG_VERSION").to_owned(),
        installed_version,
        service_version: health.version,
        address: format!("http://127.0.0.1:{}", port()),
        autostart_enabled: app.autolaunch().is_enabled().unwrap_or(false),
        data_dir: paths.data_dir.to_string_lossy().into_owned(),
        log_file: paths.server_log.to_string_lossy().into_owned(),
        control_log: paths.control_log.to_string_lossy().into_owned(),
        running_from_installed_path: running_from_installed_path(&paths),
    })
}

fn emit_log(app: &AppHandle, log_path: &Path, line: String) {
    if let Some(parent) = log_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = writeln!(file, "{line}");
    }
    let _ = app.emit("operation-log", OperationLog { line });
}

fn powershell_path() -> PathBuf {
    env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join(r"System32\WindowsPowerShell\v1.0\powershell.exe")
}

fn run_powershell(
    app: &AppHandle,
    script: &Path,
    arguments: &[String],
    log_path: &Path,
) -> Result<(), String> {
    if !script.is_file() {
        return Err(format!("找不到管理脚本：{}", script.display()));
    }
    emit_log(app, log_path, format!("> {}", script.display()));
    let mut child = Command::new(powershell_path())
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(script)
        .args(arguments)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null())
        .spawn()
        .map_err(|error| format!("无法启动 PowerShell：{error}"))?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let app_out = app.clone();
    let app_err = app.clone();
    let log_out = log_path.to_owned();
    let log_err = log_path.to_owned();
    let stdout_thread = thread::spawn(move || {
        if let Some(stdout) = stdout {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                emit_log(&app_out, &log_out, line);
            }
        }
    });
    let stderr_thread = thread::spawn(move || {
        if let Some(stderr) = stderr {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                emit_log(&app_err, &log_err, format!("错误：{line}"));
            }
        }
    });
    let status = child
        .wait()
        .map_err(|error| format!("等待 PowerShell 完成时出错：{error}"))?;
    let _ = stdout_thread.join();
    let _ = stderr_thread.join();
    if !status.success() {
        return Err(format!(
            "操作失败，PowerShell 退出代码为 {}。请查看控制中心日志。",
            status.code().unwrap_or(-1)
        ));
    }
    Ok(())
}

fn begin_operation(context: &AppContext) -> Result<OperationGuard<'_>, String> {
    context
        .operation_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| "另一项操作仍在进行，请稍候。".to_owned())?;
    Ok(OperationGuard(&context.operation_busy))
}

async fn run_installed_script(
    app: AppHandle,
    context: &AppContext,
    script_name: &'static str,
) -> Result<ControlState, String> {
    let _guard = begin_operation(context)?;
    let paths = ProductPaths::discover()?;
    let script = paths.script(script_name);
    let log = paths.control_log.clone();
    let worker_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        run_powershell(&worker_app, &script, &["-Quiet".to_owned()], &log)
    })
    .await
    .map_err(|error| format!("后台操作失败：{error}"))??;
    build_state(&app)
}

#[tauri::command]
fn get_state(app: AppHandle) -> Result<ControlState, String> {
    build_state(&app)
}

#[tauri::command]
async fn start_server(
    app: AppHandle,
    context: State<'_, AppContext>,
) -> Result<ControlState, String> {
    run_installed_script(app, &context, "start-server.ps1").await
}

#[tauri::command]
async fn stop_server(
    app: AppHandle,
    context: State<'_, AppContext>,
) -> Result<ControlState, String> {
    run_installed_script(app, &context, "stop-server.ps1").await
}

#[tauri::command]
async fn install_or_upgrade(
    app: AppHandle,
    context: State<'_, AppContext>,
) -> Result<ControlState, String> {
    let _guard = begin_operation(&context)?;
    let paths = ProductPaths::discover()?;
    let source_executable = current_executable()?;
    if path_equal(&source_executable, &paths.installed_gui) {
        return Err("请从新版 Windows ZIP 运行 EXE，以升级控制中心。".to_owned());
    }
    let source_dir = source_executable
        .parent()
        .ok_or("无法确定 Windows ZIP 目录")?
        .to_owned();
    let install_script = source_dir.join("install.ps1");
    let first_gui_install = !paths.installed_gui.is_file();
    let args = vec![
        "-GuiSource".to_owned(),
        source_executable.to_string_lossy().into_owned(),
        "-NonInteractive".to_owned(),
    ];
    let log = paths.control_log.clone();
    let worker_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        run_powershell(&worker_app, &install_script, &args, &log)
    })
    .await
    .map_err(|error| format!("后台安装失败：{error}"))??;

    let mut installed = Command::new(&paths.installed_gui);
    installed.arg("--post-install");
    if first_gui_install {
        installed.arg("--enable-autostart");
    }
    installed
        .spawn()
        .map_err(|error| format!("安装完成，但无法启动控制中心：{error}"))?;
    context.exiting.store(true, Ordering::Release);
    app.exit(0);
    build_state(&app)
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) {
        return Err("请先完成安装，再设置开机自启。".to_owned());
    }
    if enabled {
        app.autolaunch()
            .enable()
            .map_err(|error| format!("启用开机自启失败：{error}"))
    } else {
        app.autolaunch()
            .disable()
            .map_err(|error| format!("关闭开机自启失败：{error}"))
    }
}

fn open_with(program: &str, target: &Path) -> Result<(), String> {
    Command::new(program)
        .arg(target)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开 {}：{error}", target.display()))
}

#[tauri::command]
fn open_log() -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    fs::create_dir_all(&paths.logs_dir).map_err(|error| error.to_string())?;
    if !paths.control_log.exists() {
        fs::write(&paths.control_log, []).map_err(|error| error.to_string())?;
    }
    open_with("notepad.exe", &paths.control_log)
}

#[tauri::command]
fn open_data_dir() -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    fs::create_dir_all(&paths.data_dir).map_err(|error| error.to_string())?;
    open_with("explorer.exe", &paths.data_dir)
}

#[tauri::command]
fn uninstall_product(
    app: AppHandle,
    context: State<'_, AppContext>,
    purge_data: bool,
) -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    let script = paths.script("uninstall.ps1");
    if !script.is_file() {
        return Err("找不到卸载脚本。".to_owned());
    }
    let _ = app.autolaunch().disable();
    let mut command = Command::new(powershell_path());
    command
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(script)
        .arg("-NonInteractive");
    if purge_data {
        command.arg("-PurgeData");
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("无法启动卸载程序：{error}"))?;
    context.exiting.store(true, Ordering::Release);
    app.exit(0);
    Ok(())
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn make_tray_icon() -> Image<'static> {
    let size = 32u32;
    let mut rgba = Vec::with_capacity((size * size * 4) as usize);
    for y in 0..size {
        for x in 0..size {
            let inside = (3..29).contains(&x) && (3..29).contains(&y);
            let accent = (9..13).contains(&x) || (19..23).contains(&x);
            if inside {
                let (r, g, b) = if accent {
                    (230, 247, 237)
                } else {
                    (23, 107, 69)
                };
                rgba.extend_from_slice(&[r, g, b, 255]);
            } else {
                rgba.extend_from_slice(&[0, 0, 0, 0]);
            }
        }
    }
    Image::new_owned(rgba, size, size)
}

fn spawn_tray_action(app: AppHandle, script_name: &'static str) {
    tauri::async_runtime::spawn(async move {
        let context = app.state::<AppContext>();
        if let Err(error) = run_installed_script(app.clone(), &context, script_name).await {
            if let Ok(paths) = ProductPaths::discover() {
                emit_log(&app, &paths.control_log, format!("错误：{error}"));
            }
        }
    });
}

fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItemBuilder::with_id("open", "打开控制中心").build(app)?;
    let start = MenuItemBuilder::with_id("start", "启动服务").build(app)?;
    let stop = MenuItemBuilder::with_id("stop", "停止服务").build(app)?;
    let log = MenuItemBuilder::with_id("log", "查看日志").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "退出控制中心").build(app)?;
    let menu = MenuBuilder::new(app)
        .items(&[&open, &start, &stop, &log, &quit])
        .build()?;
    TrayIconBuilder::with_id("control-center")
        .icon(make_tray_icon())
        .tooltip(PRODUCT_NAME)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main_window(app),
            "start" => spawn_tray_action(app.clone(), "start-server.ps1"),
            "stop" => spawn_tray_action(app.clone(), "stop-server.ps1"),
            "log" => {
                let _ = open_log();
            }
            "quit" => {
                app.state::<AppContext>()
                    .exiting
                    .store(true, Ordering::Release);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn register_control_process(paths: &ProductPaths) {
    let _ = fs::create_dir_all(&paths.app_root);
    let _ = fs::write(&paths.control_pid, std::process::id().to_string());
    if let Ok(executable) = current_executable() {
        let _ = fs::write(
            &paths.control_executable,
            executable.to_string_lossy().as_bytes(),
        );
    }
}

fn remove_control_process(paths: &ProductPaths) {
    let expected = read_trimmed(&paths.control_pid);
    if expected.as_deref() == Some(&std::process::id().to_string()) {
        let _ = fs::remove_file(&paths.control_pid);
        let _ = fs::remove_file(&paths.control_executable);
    }
}

fn has_argument(name: &str) -> bool {
    env::args_os()
        .skip(1)
        .any(|argument| argument == OsStr::new(name))
}

fn redirect_same_version_candidate() -> bool {
    let Ok(paths) = ProductPaths::discover() else {
        return false;
    };
    if running_from_installed_path(&paths) || !paths.installed_gui.is_file() {
        return false;
    }
    if read_trimmed(&paths.installed_version).as_deref() != Some(env!("CARGO_PKG_VERSION")) {
        return false;
    }
    Command::new(&paths.installed_gui).spawn().is_ok()
}

fn webview2_available() -> bool {
    use winreg::{enums::*, RegKey};
    let locations = [
        (
            HKEY_LOCAL_MACHINE,
            format!(r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        ),
        (
            HKEY_LOCAL_MACHINE,
            format!(r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        ),
        (
            HKEY_CURRENT_USER,
            format!(r"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        ),
    ];
    let registered = locations.iter().any(|(root, path)| {
        RegKey::predef(*root)
            .open_subkey(path)
            .ok()
            .and_then(|key| key.get_value::<String, _>("pv").ok())
            .map(|version| !version.trim().is_empty() && version != "0.0.0.0")
            .unwrap_or(false)
    });
    if registered {
        return true;
    }
    ["ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"]
        .iter()
        .filter_map(env::var_os)
        .map(PathBuf::from)
        .map(|root| root.join(r"Microsoft\EdgeWebView\Application"))
        .filter_map(|root| fs::read_dir(root).ok())
        .flatten()
        .filter_map(Result::ok)
        .any(|entry| entry.path().join("msedgewebview2.exe").is_file())
}

fn show_webview2_prompt() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, IDYES, MB_ICONERROR, MB_YESNO};
    let wide = |value: &str| {
        OsStr::new(value)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>()
    };
    let title = wide(PRODUCT_NAME);
    let message =
        wide("未检测到 Microsoft Edge WebView2 Runtime。是否打开 Microsoft 官方下载页面？");
    let answer = unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_YESNO | MB_ICONERROR,
        )
    };
    if answer == IDYES {
        let _ = Command::new("explorer.exe")
            .arg("https://developer.microsoft.com/microsoft-edge/webview2/")
            .spawn();
    }
}

fn main() {
    if redirect_same_version_candidate() {
        return;
    }
    if !cfg!(debug_assertions) && !webview2_available() {
        show_webview2_prompt();
        return;
    }

    let autostart_launch = has_argument("--autostart");
    let post_install = has_argument("--post-install");
    let enable_autostart = has_argument("--enable-autostart");
    let installed_launch = ProductPaths::discover()
        .map(|paths| running_from_installed_path(&paths))
        .unwrap_or(false);
    let mut builder = tauri::Builder::default();
    if installed_launch {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app)
        }));
    }
    let app = builder
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .manage(AppContext::default())
        .invoke_handler(tauri::generate_handler![
            get_state,
            install_or_upgrade,
            start_server,
            stop_server,
            set_autostart,
            open_log,
            open_data_dir,
            uninstall_product
        ])
        .setup(move |app| {
            let paths = ProductPaths::discover().map_err(std::io::Error::other)?;
            let installed = running_from_installed_path(&paths);
            let actual_executable = current_executable()
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_else(|error| format!("<error: {error}>"));
            emit_log(
                app.handle(),
                &paths.control_log,
                format!(
                    "控制中心启动：current={actual_executable}; installed={}; recognized={installed}",
                    paths.installed_gui.display()
                ),
            );
            if installed {
                register_control_process(&paths);
                setup_tray(app)?;
                if let Some(window) = app.get_webview_window("main") {
                    let app_handle = app.handle().clone();
                    window.on_window_event(move |event| {
                        if let WindowEvent::CloseRequested { api, .. } = event {
                            if !app_handle
                                .state::<AppContext>()
                                .exiting
                                .load(Ordering::Acquire)
                            {
                                api.prevent_close();
                                if let Some(window) = app_handle.get_webview_window("main") {
                                    let _ = window.hide();
                                }
                            }
                        }
                    });
                }
            }
            if !autostart_launch {
                show_main_window(app.handle());
            }
            if installed && (autostart_launch || post_install) {
                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if enable_autostart {
                        let _ = app_handle.autolaunch().enable();
                    }
                    let context = app_handle.state::<AppContext>();
                    if let Err(error) =
                        run_installed_script(app_handle.clone(), &context, "start-server.ps1").await
                    {
                        if let Ok(paths) = ProductPaths::discover() {
                            emit_log(&app_handle, &paths.control_log, format!("错误：{error}"));
                        }
                    }
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build zotero-pdf2zh-pro control center");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Ok(paths) = ProductPaths::discover() {
                remove_control_process(&paths);
            }
            app.state::<AppContext>()
                .exiting
                .store(true, Ordering::Release);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_windows_paths_case_insensitively() {
        assert!(path_equal(
            Path::new(r"C:\Users\Andy\App"),
            Path::new(r"c:/users/andy/app/")
        ));
    }

    #[test]
    fn compares_verbatim_and_regular_windows_paths() {
        assert!(path_equal(
            Path::new(r"\\?\C:\Users\Andy\App"),
            Path::new(r"C:\Users\Andy\App")
        ));
    }

    #[test]
    fn missing_listener_is_stopped() {
        let result = HealthResult::default();
        assert!(!result.listening);
        assert!(!result.valid);
    }
}
