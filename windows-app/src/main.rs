#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod install_root;
mod controller;
mod platform;
mod ui;
mod ui_state;
mod progress;
mod uv_progress;
mod uv_terminal;
mod cancellation;
mod scheduler;
mod update;

use controller::AppHandle;
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    env,
    ffi::OsStr,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpStream},
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::Duration,
};
const PRODUCT_NAME: &str = "zotero-pdf2zh-pro";
const DEFAULT_PORT: u16 = 8890;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const OPERATION_BUSY: &str = "另一项操作仍在进行，请稍候。";

#[derive(Default)]
struct AppContext {
    operation_busy: AtomicBool,
    install_cancel_requested: AtomicBool,
    exiting: AtomicBool,
}

struct OperationGuard<'a>(&'a AtomicBool);

impl Drop for OperationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

fn default_install_root() -> Result<PathBuf, String> {
    Ok(
        PathBuf::from(env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not available")?)
            .join(PRODUCT_NAME),
    )
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
    operation_error: PathBuf,
}

impl ProductPaths {
    fn discover() -> Result<Self, String> {
        let app_root = install_root::discover()?.root;
        Ok(Self::from_root(app_root))
    }

    fn from_root(app_root: PathBuf) -> Self {
        let bin_dir = app_root.join("bin");
        let logs_dir = app_root.join("logs");
        Self {
            data_dir: app_root.join("data"),
            server_log: logs_dir.join("server.log"),
            control_log: logs_dir.join("control-panel.log"),
            installed_version: app_root.join("installed-version.txt"),
            installed_gui: bin_dir.join(format!("{PRODUCT_NAME}.exe")),
            control_pid: app_root.join("control-panel.pid"),
            control_executable: app_root.join("control-panel-executable.txt"),
            operation_error: app_root.join("last-operation-error.txt"),
            app_root,
            bin_dir,
            logs_dir,
        }
    }

    fn script(&self, name: &str) -> PathBuf {
        self.bin_dir.join(name)
    }
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
enum InstallationStatus {
    NotInstalled,
    Current,
    UpdateAvailable,
    DowngradeBlocked,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
enum ServiceStatus {
    Stopped,
    Running,
    PortConflict,
}

#[derive(Serialize, Deserialize)]
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
    install_root: String,
    default_install_root: String,
    can_relocate: bool,
    last_operation_error: Option<String>,
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

fn path_is_within(path: &Path, parent: &Path) -> bool {
    let path = normalize_path(path);
    let parent = normalize_path(parent);
    path.len() > parent.len()
        && path.starts_with(&parent)
        && path.as_bytes().get(parent.len()) == Some(&b'\\')
}

fn validate_install_root(candidate: &Path, current: &Path, relocating: bool) -> Result<(), String> {
    if !candidate.is_absolute() {
        return Err("安装位置必须是绝对路径。".to_owned());
    }
    if relocating
        && (path_equal(candidate, current)
            || path_is_within(candidate, current)
            || path_is_within(current, candidate))
    {
        return Err("新旧安装位置不能相同或互相嵌套。".to_owned());
    }
    Ok(())
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
        .map(|value| value.trim_start_matches('\u{feff}').trim().to_owned())
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
        (Ok(_), Ok(_)) if !paths.script("windows-dependencies.json").is_file() => {
            (InstallationStatus::UpdateAvailable, installed)
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

fn build_state(_app: &AppHandle) -> Result<ControlState, String> {
    let paths = ProductPaths::discover()?;
    let (installation, installed_version) = installation_status(&paths);
    let installed_path = running_from_installed_path(&paths);
    let can_relocate = installed_path && matches!(&installation, InstallationStatus::Current);
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
        autostart_enabled: controller::autostart_enabled(&paths),
        data_dir: paths.data_dir.to_string_lossy().into_owned(),
        log_file: paths.server_log.to_string_lossy().into_owned(),
        control_log: paths.control_log.to_string_lossy().into_owned(),
        running_from_installed_path: installed_path,
        install_root: paths.app_root.to_string_lossy().into_owned(),
        default_install_root: default_install_root()?.to_string_lossy().into_owned(),
        can_relocate,
        last_operation_error: read_trimmed(&paths.operation_error),
    })
}

static LOG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn emit_log(app: &AppHandle, log_path: &Path, line: String) {
    let _lock = LOG_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(parent) = log_path.parent() {let _ = fs::create_dir_all(parent);}
    if fs::metadata(log_path).is_ok_and(|info| info.len() >= 5 * 1024 * 1024) {
        let _ = fs::remove_file(log_path.with_extension("log.3"));
        for number in (1..=2).rev() {
            let _ = fs::rename(
                log_path.with_extension(format!("log.{number}")),
                log_path.with_extension(format!("log.{}", number + 1)),
            );
        }
        let _ = fs::rename(log_path, log_path.with_extension("log.1"));
    }
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

fn powershell_command(script: &Path, app_root: Option<&Path>) -> Command {
    let mut command = Command::new(powershell_path());
    command.creation_flags(CREATE_NO_WINDOW);
    // A launcher can inherit PowerShell 7's module directories. Windows PowerShell
    // must rebuild its own module path instead of loading incompatible modules.
    command.env_remove("PSModulePath");
    command.env_remove("PSModuleAnalysisCachePath");
    command
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(script);
    if let Some(root) = app_root {
        command.env("PDF2ZH_WINDOWS_APP_ROOT", root);
    }
    command
}

fn apply_update_command(script: &Path, parent_process_id: u32, app_root: &Path) -> Command {
    let mut command = powershell_command(script, None);
    command
        .arg("-ParentProcessId")
        .arg(parent_process_id.to_string())
        .arg("-InstallRoot")
        .arg(app_root);
    command
}

fn run_powershell(
    app: &AppHandle,
    script: &Path,
    arguments: &[String],
    log_path: &Path,
    app_root: Option<&Path>,
) -> Result<(), String> {
    if !script.is_file() {
        return Err(format!("找不到管理脚本：{}", script.display()));
    }
    emit_log(app, log_path, format!("> {}", script.display()));
    let signal = (script.file_name() == Some(OsStr::new("install.ps1")))
        .then(cancellation::Signal::new);
    let mut command = powershell_command(script, app_root);
    if let Some(signal) = &signal { command.env(cancellation::ENV, signal.path()); }
    let mut child = command.args(arguments)
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
                if let Some(progress) = progress::parse_line(&line) {
                    let _ = app_out.emit("operation-progress", progress);
                } else {
                    emit_log(&app_out, &log_out, line);
                }
            }
        }
    });
    let stderr_thread = thread::spawn(move || {
        let mut detail = None;
        if let Some(stderr) = stderr {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if detail.is_none() && !line.trim().is_empty() {
                    detail = Some(line.trim().chars().take(240).collect::<String>());
                }
                emit_log(&app_err, &log_err, line);
            }
        }
        detail
    });
    let mut cancel_sent = false;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| format!("等待 PowerShell 完成时出错：{error}"))? {
            break status;
        }
        if !cancel_sent && app.context().install_cancel_requested.load(Ordering::Acquire) {
            if let Some(signal) = &signal {
                match signal.request() {
                    Ok(()) => cancel_sent = true,
                    Err(error) => {
                        let _ = app.emit("cancel-error", format!("无法取消安装：{error}"));
                        app.context().install_cancel_requested.store(false, Ordering::Release);
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(50));
    };
    let _ = stdout_thread.join();
    let detail = stderr_thread.join().ok().flatten();
    if status.code() == Some(scheduler::RECOVERY_BUSY_EXIT_CODE)
        && script.file_name() == Some(OsStr::new("recover-install.ps1"))
    {
        return Err(OPERATION_BUSY.into());
    }
    if status.code() == Some(cancellation::EXIT_CODE) { return Err(cancellation::ERROR.into()); }
    if !status.success() {
        return Err(format!(
            "操作失败：{}（代码 {}）。详情见控制中心日志。",
            detail.as_deref().unwrap_or("管理脚本未提供错误原因"),
            status.code().unwrap_or(-1)
        ));
    }
    Ok(())
}

fn begin_operation(context: &AppContext) -> Result<OperationGuard<'_>, String> {
    context
        .operation_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| OPERATION_BUSY.to_owned())?;
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
    tokio::task::spawn_blocking(move || {
        run_powershell(
            &worker_app,
            &script,
            &["-Quiet".to_owned()],
            &log,
            Some(&paths.app_root),
        )
    })
    .await
    .map_err(|error| format!("后台操作失败：{error}"))??;
    build_state(&app)
}

async fn start_server(app: AppHandle, context: &AppContext) -> Result<ControlState, String> {
    run_installed_script(app, &context, "start-server.ps1").await
}

async fn stop_server(app: AppHandle, context: &AppContext) -> Result<ControlState, String> {
    run_installed_script(app, &context, "stop-server.ps1").await
}

async fn install_or_upgrade(
    app: AppHandle,
    context: &AppContext,
    install_root: Option<String>,
    options: Option<(bool, bool, controller::AppearanceTheme)>,
) -> Result<ControlState, String> {
    let _guard = begin_operation(&context)?;
    let discovered_paths = ProductPaths::discover()?;
    let requested_root = install_root
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| discovered_paths.app_root.clone());
    validate_install_root(&requested_root, &discovered_paths.app_root, false)?;
    let (existing_status, _) = installation_status(&discovered_paths);
    if !matches!(existing_status, InstallationStatus::NotInstalled)
        && !path_equal(&requested_root, &discovered_paths.app_root)
    {
        return Err("已有安装请使用“更改安装位置”安全迁移。".to_owned());
    }
    let paths = ProductPaths::from_root(requested_root);
    let source_executable = current_executable()?;
    let source_dir = source_executable
        .parent()
        .ok_or("无法确定安装文件目录")?
        .to_owned();
    let install_script = source_dir.join("install.ps1");
    let first_gui_install = !paths.installed_gui.is_file();
    let was_running = query_health(&paths).listening;
    let mut args = vec![
        "-GuiSource".to_owned(),
        source_executable.to_string_lossy().into_owned(),
        "-InstallRoot".to_owned(),
        paths.app_root.to_string_lossy().into_owned(),
        "-NonInteractive".to_owned(),
    ];
    if options.is_some() && matches!(existing_status, InstallationStatus::Current) {
        args.push("-Repair".into());
    }
    let log = paths.control_log.clone();
    let worker_app = app.clone();
    let paths_for_worker = paths.app_root.clone();
    tokio::task::spawn_blocking(move || {
        run_powershell(&worker_app, &install_script, &args, &log, Some(&paths_for_worker))
    })
    .await
    .map_err(|error| format!("后台安装失败：{error}"))??;

    if let Some((autostart, automatic_updates, appearance_theme)) = options {
        if first_gui_install {
            controller::set_autostart(&paths, autostart)?;
            scheduler::write_json(&paths.app_root.join("settings.json"), &controller::Settings {
                auto_update_disabled: !automatic_updates,
                appearance_theme,
                ..controller::settings_at(&paths.app_root)
            })?;
        }
        if env::var("PDF2ZH_ISOLATED_CANDIDATE").as_deref() == Ok("1") {
            scheduler::write_json(&paths.app_root.join("candidate-profile.json"), &serde_json::json!({"candidate":true}))?;
        }
        if has_argument("--setup") {
            return build_state(&app);
        }
    }

    let mut installed = Command::new(&paths.installed_gui);
    installed.arg("--post-install");
    installed.arg(format!("--wait-parent={}", std::process::id()));
    if !first_gui_install && !was_running {
        installed.arg("--keep-stopped");
    }
    if first_gui_install && options.is_none() {
        installed.arg("--enable-autostart");
    }
    installed
        .spawn()
        .map_err(|error| format!("安装完成，但无法启动控制中心：{error}"))?;
    context.exiting.store(true, Ordering::Release);
    app.exit(0);
    build_state(&app)
}

async fn check_for_update() -> Result<update::UpdateCheck, String> {
    update::check(env!("CARGO_PKG_VERSION")).await
}

fn relocate_installation(
    app: AppHandle,
    context: &AppContext,
    destination_root: String,
) -> Result<(), String> {
    let _guard = begin_operation(&context)?;
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) {
        return Err("请从已安装的控制中心更改安装位置。".to_owned());
    }
    let destination = PathBuf::from(destination_root);
    validate_install_root(&destination, &paths.app_root, true)?;
    let script = paths.script("relocate.ps1");
    if !script.is_file() {
        return Err("当前安装缺少迁移脚本，请先升级或重新安装控制中心。".to_owned());
    }
    Command::new(powershell_path())
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(script)
        .arg("-ParentProcessId")
        .arg(std::process::id().to_string())
        .arg("-SourceRoot")
        .arg(&paths.app_root)
        .arg("-DestinationRoot")
        .arg(&destination)
        .arg("-CurrentVersion")
        .arg(env!("CARGO_PKG_VERSION"))
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("无法启动迁移程序：{error}"))?;
    context.exiting.store(true, Ordering::Release);
    app.exit(0);
    Ok(())
}

fn set_autostart(_app: AppHandle, enabled: bool) -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) {
        return Err("请先完成安装，再设置开机自启。".to_owned());
    }
    controller::set_autostart(&paths, enabled)
}

fn open_with(program: &str, target: &Path) -> Result<(), String> {
    Command::new(program)
        .arg(target)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开 {}：{error}", target.display()))
}

fn open_log() -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    fs::create_dir_all(&paths.logs_dir).map_err(|error| error.to_string())?;
    if !paths.control_log.exists() {
        fs::write(&paths.control_log, []).map_err(|error| error.to_string())?;
    }
    open_with("notepad.exe", &paths.control_log)
}

fn open_data_dir() -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    fs::create_dir_all(&paths.data_dir).map_err(|error| error.to_string())?;
    open_with("explorer.exe", &paths.data_dir)
}

fn uninstall_product(app: AppHandle, context: &AppContext, purge_data: bool) -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    let script = paths.script("uninstall.ps1");
    if !script.is_file() {
        return Err("找不到卸载脚本。".to_owned());
    }
    let _ = controller::set_autostart(&paths, false);
    let mut command = powershell_command(&script, Some(&paths.app_root));
    command.arg("-NonInteractive");
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

fn has_argument(name: &str) -> bool {
    env::args_os().skip(1).any(|arg| arg == OsStr::new(name))
}
fn main() {
    if let Some(path) = env::args_os().nth(1).filter(|s| s == "--uv-progress").and_then(|_| env::args_os().nth(2)) {
        std::process::exit(uv_progress::run(Path::new(&path)));
    }
    // Candidate shortcuts retain isolation even when Explorer supplies no environment.
    // This runs before the UI/runtime creates any worker threads.
    if env::var_os("PDF2ZH_WINDOWS_APP_ROOT").is_none() {
        if let Some(root) = env::current_exe().ok().and_then(|exe| exe.parent().filter(|p| p.file_name().is_some_and(|n| n == "bin")).and_then(|p| p.parent()).map(Path::to_owned)) {
            if fs::read(root.join("candidate-profile.json")).ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok()).is_some_and(|v| v["candidate"] == true) {
                env::set_var("PDF2ZH_WINDOWS_APP_ROOT", root);
                env::set_var("PDF2ZH_WINDOWS_PORT", "19892");
                env::set_var("PDF2ZH_WINDOWS_REGISTRY_KEY", "HKCU:\\Software\\pdf2zh-native-candidate");
                if let Some(local) = env::var_os("LOCALAPPDATA") {env::set_var("PDF2ZH_WINDOWS_START_MENU_DIR", PathBuf::from(local).join("zotero-pdf2zh-pro-native-candidate-shortcuts"));}
            }
        }
    }
    ui::run();
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
    fn detects_nested_install_roots() {
        assert!(path_is_within(
            Path::new(r"D:\Apps\zotero-pdf2zh-pro\data"),
            Path::new(r"d:\apps\zotero-pdf2zh-pro")
        ));
        assert!(!path_is_within(
            Path::new(r"D:\Apps\zotero-pdf2zh-pro-archive"),
            Path::new(r"D:\Apps\zotero-pdf2zh-pro")
        ));
    }

    #[test]
    fn rejects_equal_or_nested_relocation_roots() {
        let current = Path::new(r"C:\Users\Andy\AppData\Local\zotero-pdf2zh-pro");
        assert!(validate_install_root(current, current, true).is_err());
        assert!(validate_install_root(&current.join("moved"), current, true).is_err());
        assert!(
            validate_install_root(Path::new(r"D:\Apps\zotero-pdf2zh-pro"), current, true).is_ok()
        );
    }

    #[test]
    fn missing_listener_is_stopped() {
        let result = HealthResult::default();
        assert!(!result.listening);
        assert!(!result.valid);
    }

    #[test]
    fn installed_scripts_receive_the_selected_root_only_in_the_child() {
        let root = Path::new(r"D:\Apps with spaces\翻译服务");
        let script = root.join("bin/start-server.ps1");
        let command = powershell_command(&script, Some(root));
        assert!(command.get_envs().any(|(key, value)| {
            key == "PDF2ZH_WINDOWS_APP_ROOT" && value == Some(root.as_os_str())
        }));
        assert!(command.get_args().any(|arg| arg == script.as_os_str()));
        // Install/update entrypoints carry an explicit argument and must retain
        // the inherited override's existing location-commit semantics.
        let command = powershell_command(&script, None);
        assert!(!command
            .get_envs()
            .any(|(key, _)| key == "PDF2ZH_WINDOWS_APP_ROOT"));
    }

    #[test]
    fn update_handoff_binds_packaged_script() {
        use std::io::Read;
        use std::time::{SystemTime, UNIX_EPOCH};

        let directory = env::temp_dir().join(format!(
            "pdf2zh-update-contract-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let source = if let Some(package) = env::var_os("PDF2ZH_UPDATE_CONTRACT_PACKAGE") {
            let mut archive = zip::ZipArchive::new(fs::File::open(package).unwrap()).unwrap();
            let mut source = String::new();
            archive
                .by_name("apply-update.ps1")
                .unwrap()
                .read_to_string(&mut source)
                .unwrap();
            source
        } else {
            fs::read_to_string(
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../scripts/windows/apply-update.ps1"),
            )
            .unwrap()
        };
        let original = directory.join("packaged-script.txt");
        fs::write(&original, source).unwrap();
        let probe = directory.join("binding probe 中文.ps1");
        let extractor = directory.join("extract.ps1");
        // Execute only the real parameter declaration. The installer body must
        // never run during this contract test, including for legacy arguments.
        fs::write(&extractor, r#"param([string]$Source, [string]$Destination)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors -or -not $ast.ParamBlock) { throw 'Invalid packaged update parameter block' }
$body = '[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $PSBoundParameters | ConvertTo-Json -Compress'
Set-Content -LiteralPath $Destination -Value ($ast.ParamBlock.Extent.Text + "`n" + $body) -Encoding UTF8
"#).unwrap();
        let result = powershell_command(&extractor, None)
            .arg("-Source")
            .arg(&original)
            .arg("-Destination")
            .arg(&probe)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let root = directory.join("安装 root with spaces");
        let wheel = directory.join("旧 package with spaces.whl");
        let mut modern = apply_update_command(&probe, 2147483647, &root);
        let mut legacy = powershell_command(&probe, None);
        legacy.arg("-ParentProcessId").arg("2147483647");
        let mut legacy_wheel = powershell_command(&probe, None);
        legacy_wheel
            .arg("-ParentProcessId")
            .arg("2147483647")
            .arg("-PackageSource")
            .arg(&wheel);
        for (command, expected_root, expected_wheel) in [
            (&mut modern, Some(root.to_str().unwrap()), None),
            (&mut legacy, None, None),
            (&mut legacy_wheel, None, Some(wheel.to_str().unwrap())),
        ] {
            let result = command.output().unwrap();
            assert!(
                result.status.success(),
                "{}",
                String::from_utf8_lossy(&result.stderr)
            );
            let bound: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
            assert_eq!(bound["ParentProcessId"], 2147483647);
            assert_eq!(bound["InstallRoot"].as_str(), expected_root);
            assert_eq!(bound["PackageSource"].as_str(), expected_wheel);
        }
        assert_eq!(directory.parent(), Some(env::temp_dir().as_path()));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn install_cancellation_reaches_the_worker_and_releases_the_signal() {
        let directory = env::temp_dir().join(format!("pdf2zh-cancel-test-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let script = directory.join("install.ps1");
        fs::write(&script, r#"
Set-Content -LiteralPath (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'signal.txt') -Value $env:PDF2ZH_INSTALL_CANCEL_FILE
for ($attempt=0; $attempt -lt 100; $attempt++) {
    if (Test-Path -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE) { exit 1223 }
    Start-Sleep -Milliseconds 50
}
exit 1
"#).unwrap();
        let app = controller::test_handle();
        app.context().install_cancel_requested.store(true, Ordering::Release);
        let result = run_powershell(&app, &script, &[], &directory.join("control.log"), Some(&directory));
        assert_eq!(result.unwrap_err(), cancellation::ERROR);
        let signal = read_trimmed(&directory.join("signal.txt")).unwrap();
        assert!(!Path::new(&signal).exists(), "Per-install cancellation signal leaked");
        // A later invocation has its own signal and cannot inherit cancellation.
        app.context().install_cancel_requested.store(false, Ordering::Release);
        fs::write(&script, "if (Test-Path -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE) { exit 1 }; exit 0").unwrap();
        assert!(run_powershell(&app, &script, &[], &directory.join("control.log"), Some(&directory)).is_ok());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn failed_operation_includes_the_actual_script_reason() {
        let directory =
            env::temp_dir().join(format!("pdf2zh-operation-error-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let script = directory.join("failure.ps1");
        fs::write(&script, "throw 'Injected service startup reason'").unwrap();
        let error = run_powershell(
            &controller::test_handle(),
            &script,
            &[],
            &directory.join("control.log"),
            None,
        )
        .unwrap_err();
        assert!(error.contains("Injected service startup reason"), "{error}");
        assert!(fs::read_to_string(directory.join("control.log"))
            .unwrap()
            .contains("Injected service startup reason"));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn reads_powershell_utf8_files_without_the_bom() {
        let path = env::temp_dir().join(format!(
            "zotero-pdf2zh-pro-bom-test-{}.txt",
            std::process::id()
        ));
        fs::write(&path, "\u{feff}operation failed\r\n").unwrap();
        assert_eq!(read_trimmed(&path).as_deref(), Some("operation failed"));
        fs::remove_file(path).unwrap();
    }
}
