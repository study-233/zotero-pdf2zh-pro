use crate::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
struct UpdateState {
    phase: String,
    version: String,
    directory: PathBuf,
    last_check: u64,
    retry_at: u64,
    failures: usize,
    cleanup_at: u64,
    error: String,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn state_path(paths: &ProductPaths) -> PathBuf {
    paths.app_root.join("updates/state.json")
}
fn read_state(paths: &ProductPaths) -> UpdateState {
    fs::read(state_path(paths))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}
pub fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let temp = path.with_extension(format!("{}.tmp", std::process::id()));
    let mut stream = fs::File::create(&temp).map_err(|e| e.to_string())?;
    stream
        .write_all(&serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    stream.sync_all().map_err(|e| e.to_string())?;
    drop(stream);
    // ReplaceFile is atomic on Windows and does not require removing the destination.
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
    if unsafe {
        MoveFileExW(
            controller::wide(&temp.to_string_lossy()).as_ptr(),
            controller::wide(&path.to_string_lossy()).as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(())
}

pub fn start(app: AppHandle, installed: bool) {
    app.clone().0.runtime.spawn(async move {
        if installed && has_argument("--enable-autostart") {
            if let Ok(paths) = ProductPaths::discover() {
                let _ = controller::set_autostart(&paths, true);
            }
        }
        if installed {
            let _guard = match begin_operation(app.context()) {
                Ok(guard) => guard,
                Err(error) => { app.error(error); return; }
            };
            if let Ok(paths) = ProductPaths::discover() {
                let script = paths.script("recover-install.ps1");
                if script.is_file() {
                    let worker = app.clone();
                    let root = paths.app_root.clone();
                    let log = paths.control_log.clone();
                    let recovery = tokio::task::spawn_blocking(move || {
                        run_powershell(&worker, &script, &[], &log, Some(&root))
                    })
                    .await;
                    if !matches!(recovery, Ok(Ok(()))) {
                        app.error("中断恢复失败，请查看控制中心日志并处理后重新启动。".into());
                        return;
                    }
                }
            }
        }
        if installed
            && (has_argument("--autostart") || has_argument("--post-install"))
            && !has_argument("--keep-stopped")
        {
            if let Err(error) = start_server(app.clone(), app.context()).await {
                app.error(error);
            }
        }
        // UI tests suppress periodic work, while exercising the real startup handoff.
        if std::env::var("PDF2ZH_WINDOWS_LIFECYCLE_TEST").as_deref() == Ok("1") {
            return;
        }
        let started = now();
        let mut startup_check = true;
        loop {
            if app.context().exiting.load(Ordering::Acquire) {
                break;
            }
            let worker = app.clone();
            if let Ok(Ok(state)) = tokio::task::spawn_blocking(move || build_state(&worker)).await {
                let _ = app.emit("state", state);
            }
            if installed && now().saturating_sub(started) >= 60 {
                if let Ok(paths) = ProductPaths::discover() {
                    let mut state = read_state(&paths);
                    if now().saturating_sub(state.cleanup_at) >= 86400
                        && !app.context().operation_busy.load(Ordering::Acquire)
                    {
                        if let Ok(_guard) = begin_operation(app.context()) {
                            let script = paths.script("clean-cache.ps1");
                            let worker = app.clone();
                            let root = paths.app_root.clone();
                            let log = paths.control_log.clone();
                            if script.is_file() {
                                let _ = tokio::task::spawn_blocking(move || {
                                    run_powershell(&worker, &script, &[], &log, Some(&root))
                                })
                                .await;
                            }
                            state.cleanup_at = now();
                            let _ = write_json(&state_path(&paths), &state);
                        }
                    }
                }
            }
            if installed
                && now().saturating_sub(started) >= 60
                && !controller::settings().auto_update_disabled
            {
                if let Ok(paths) = ProductPaths::discover() {
                    let state = read_state(&paths);
                    if state.phase == "ready" {
                        if let Err(error) = try_apply(&app, &paths, &state, false).await {
                            emit_log(&app, &paths.control_log, error);
                        }
                    } else if now() >= state.retry_at
                        && (startup_check || now().saturating_sub(state.last_check) >= 86400)
                    {
                        startup_check = false;
                        if let Err(error) = request_update(&app, false).await {
                            let mut state = read_state(&paths);
                            state.failures += 1;
                            state.retry_at =
                                now() + [900, 3600, 21600][(state.failures - 1).min(2)];
                            state.last_check = 0;
                            state.error = error.clone();
                            let _ = write_json(&state_path(&paths), &state);
                            emit_log(&app, &paths.control_log, error);
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_secs(if app.panel_open() { 3 } else { 30 })).await;
        }
    });
}

pub async fn request_update(app: &AppHandle, manual: bool) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) {
        return Err("请先安装控制中心。".into());
    }
    progress::phase(app, &paths.app_root, "update", "checking", "正在检查稳定版本", "running");
    let check = check_for_update().await?;
    let mut state = read_state(&paths);
    state.last_check = now();
    state.retry_at = 0;
    if !check.available {
        state.phase = "current".into();
        state.error.clear();
        write_json(&state_path(&paths), &state)?;
        let _ = app.emit("status", "已是最新稳定版");
        progress::phase(app, &paths.app_root, "update", "complete", "已是最新稳定版", "completed");
        return Ok(());
    }
    if state.phase == "failed" && state.version == check.latest_version && !manual {
        write_json(&state_path(&paths), &state)?;
        return Ok(());
    }
    if state.phase == "ready" && !manual {
        return Ok(());
    }
    let staged = update::download_and_stage(app, &paths.app_root).await?;
    state.version = staged.version;
    state.directory = staged.directory;
    state.phase = "preparing".into();
    state.error.clear();
    write_json(&state_path(&paths), &state)?;
    let script = state.directory.join("package/install.ps1");
    let gui = state.directory.join("package/zotero-pdf2zh-pro.exe");
    let args = vec![
        "-PrepareOnly".into(),
        "-NonInteractive".into(),
        "-GuiSource".into(),
        gui.to_string_lossy().to_string(),
        "-InstallRoot".into(),
        paths.app_root.to_string_lossy().to_string(),
    ];
    let worker = app.clone();
    let log = paths.control_log.clone();
    let prepared =
        tokio::task::spawn_blocking(move || run_powershell(&worker, &script, &args, &log, None))
            .await
            .map_err(|e| e.to_string())?;
    if let Err(error) = prepared {
        let network_failure = fs::read(paths.app_root.join("updates/preparation-error.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .is_some_and(|failure| failure["category"] == "network");
        state.phase = if network_failure { "network" } else { "failed" }.into();
        state.error = error.clone();
        write_json(&state_path(&paths), &state)?;
        if manual || network_failure {
            return Err(error);
        }
        app.error("更新准备失败，旧服务继续运行；请在设置中重试或查看日志。".into());
        return Ok(());
    }
    state.phase = "ready".into();
    write_json(&state_path(&paths), &state)?;
    let _ = app.emit("status", "更新已准备，等待空闲后自动切换");
    progress::phase(app, &paths.app_root, "update", "waiting", "更新已准备，等待连续空闲 60 秒后切换", "waiting");
    // Preparation only holds the operation lock, application is serialized on the next tick.
    Ok(())
}

async fn health(paths: &ProductPaths) -> Result<Value, String> {
    let response = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{}/health", port()))
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let health: Value = response.json().await.map_err(|e| e.to_string())?;
    if !health["workspace"]["path"]
        .as_str()
        .is_some_and(|path| path_equal(Path::new(path), &paths.data_dir))
    {
        return Err("服务数据目录不匹配，未停止任何进程。".into());
    }
    Ok(health)
}

async fn release(paths: &ProductPaths) -> Result<(), String> {
    let token = read_trimmed(&paths.data_dir.join(".control-token")).ok_or("缺少维护凭据")?;
    reqwest::Client::new()
        .delete(format!("http://127.0.0.1:{}/internal/maintenance", port()))
        .header("X-PDF2ZH-Control-Token", token)
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

async fn acquire(paths: &ProductPaths, idle: u64) -> Result<bool, String> {
    let current = health(paths).await?;
    if current["capabilities"]["updateMaintenance"] != true {
        return Err("当前服务尚不支持安全维护，请完成任务后手动迁移升级。".into());
    }
    let token = read_trimmed(&paths.data_dir.join(".control-token")).ok_or("缺少本地维护凭据")?;
    let response = reqwest::Client::new()
        .post(format!("http://127.0.0.1:{}/internal/maintenance", port()))
        .header("X-PDF2ZH-Control-Token", token)
        .json(&json!({"idleSeconds":idle}))
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if response.status().as_u16() == 409 {
        return Ok(false);
    }
    if !response.status().is_success() {
        return Err("无法进入安全维护，服务继续运行。".into());
    }
    Ok(true)
}

async fn try_apply(
    app: &AppHandle,
    paths: &ProductPaths,
    state: &UpdateState,
    manual: bool,
) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let worker = paths.clone();
    let running = tokio::task::spawn_blocking(move || query_health(&worker))
        .await
        .map_err(|e| e.to_string())?;
    if running.listening && !running.valid {
        return Err("端口由其他程序占用，未进行更新。".into());
    }
    if running.listening && !acquire(paths, if manual { 0 } else { 60 }).await? {
        return Ok(());
    }
    progress::phase(app, &paths.app_root, "update", "switching", "正在切换版本，请勿中断", "running");
    let script = state.directory.join("package/apply-update.ps1");
    let mut command = apply_update_command(&script, std::process::id(), &paths.app_root);
    command.arg("-Prepared");
    if !running.listening {
        command.arg("-KeepStopped");
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    app.context().exiting.store(true, Ordering::Release);
    app.exit(0);
    Ok(())
}

pub async fn stop_safely(app: &AppHandle) -> Result<(), String> {
    let paths = ProductPaths::discover()?;
    if query_health(&paths).listening && !acquire(&paths, 0).await? {
        return Err("还有翻译、划词或下载任务，请等待完成后停止服务。".into());
    }
    let result = stop_server(app.clone(), app.context()).await;
    if result.is_err() {
        let _ = release(&paths).await;
    }
    result?;
    Ok(())
}

pub async fn repair(app: &AppHandle) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let paths = ProductPaths::discover()?;
    let script = paths.script("install.ps1");
    let worker = app.clone();
    let root = paths.app_root.clone();
    let gui = paths.installed_gui.clone();
    let log = paths.control_log.clone();
    let args = vec![
        "-PrepareOnly".into(),
        "-Repair".into(),
        "-NonInteractive".into(),
        "-GuiSource".into(),
        gui.to_string_lossy().to_string(),
        "-InstallRoot".into(),
        root.to_string_lossy().to_string(),
    ];
    tokio::task::spawn_blocking(move || run_powershell(&worker, &script, &args, &log, None))
        .await
        .map_err(|e| e.to_string())??;
    let mut command = apply_update_command(
        &paths.script("apply-update.ps1"),
        std::process::id(),
        &paths.app_root,
    );
    command.arg("-Prepared");
    if !query_health(&paths).listening {
        command.arg("-KeepStopped");
    } else if !acquire(&paths, 0).await? {
        return Err("修复环境已准备，请等待任务结束后重试。".into());
    }
    command.spawn().map_err(|e| e.to_string())?;
    app.exit(0);
    Ok(())
}

pub async fn clean_cache(app: &AppHandle) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let paths = ProductPaths::discover()?;
    let root = paths.app_root.clone();
    let log = paths.control_log.clone();
    let worker = app.clone();
    let script = paths.script("clean-cache.ps1");
    tokio::task::spawn_blocking(move || {
        run_powershell(
            &worker,
            &script,
            &["-ClearDownloads".into()],
            &log,
            Some(&root),
        )
    })
    .await
    .map_err(|e| e.to_string())??;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn scheduler_state_survives_replacement() {
        let path = env::temp_dir().join(format!("pdf2zh-state-{}.json", std::process::id()));
        write_json(
            &path,
            &UpdateState {
                phase: "ready".into(),
                ..Default::default()
            },
        )
        .unwrap();
        write_json(
            &path,
            &UpdateState {
                phase: "failed".into(),
                ..Default::default()
            },
        )
        .unwrap();
        let state: UpdateState = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(state.phase, "failed");
        fs::remove_file(path).unwrap();
    }
}
