use crate::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};

pub(crate) const RECOVERY_BUSY_EXIT_CODE: i32 = 1618;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct UpdateState {
    pub phase: String,
    pub version: String,
    pub(crate) directory: PathBuf,
    pub last_check: u64,
    pub(crate) retry_at: u64,
    pub(crate) failures: usize,
    pub(crate) cleanup_at: u64,
    pub error: String,
    pub manual_version: Option<String>,
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
pub fn read_state(paths: &ProductPaths) -> UpdateState {
    fs::read(state_path(paths))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn save_update_state(app: &AppHandle, paths: &ProductPaths, state: &UpdateState) -> Result<(), String> {
    write_json(&state_path(paths), state)?;
    app.emit("update-state", json!({"state":state,"operationId":progress::read(&paths.app_root).map(|p| p.operation_id)}))
}

fn should_apply(state: &UpdateState, automatic: bool) -> bool {
    state.phase == "ready" && (automatic || state.manual_version.as_deref() == Some(&state.version))
}

fn reconcile_update(state: &mut UpdateState, current: &str) {
    let installed = Version::parse(current).ok();
    let target = Version::parse(&state.version).ok();
    if matches!((installed, target), (Some(a), Some(b)) if a >= b) {
        state.phase = "current".into();
        state.manual_version = None;
        state.error.clear();
    } else if matches!(state.phase.as_str(), "switching" | "preparing") {
        state.phase = "failed".into();
        state.manual_version = None;
        state.error = "更新未完成，原版本继续保留。请重试更新或查看日志。".into();
    }
}

fn update_failed(app: &AppHandle, paths: &ProductPaths, error: &str) {
    let mut state = read_state(paths);
    // Network preparation failures keep the existing automatic backoff policy.
    let transient = state.phase == "network" || ["检查更新失败", "下载更新失败", "读取更新数据失败"].iter().any(|text| error.contains(text));
    state.phase = if transient && state.manual_version.is_none() { "network" } else { "failed" }.into();
    state.manual_version = None;
    state.error = error.into();
    let _ = save_update_state(app, paths, &state);
    progress::phase(app, &paths.app_root, "update", "failed", error, "failed");
}

// A manual check is deliberately read-only: no staging, scheduling or progress-file writes.
pub async fn check_latest(app: &AppHandle) -> Result<update::UpdateCheck, String> {
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) { return Err("请先安装控制中心。".into()); }
    check_only(app.context(), check_for_update()).await
}

async fn check_only(context: &AppContext, query: impl std::future::Future<Output = Result<update::UpdateCheck, String>>) -> Result<update::UpdateCheck, String> {
    let _guard = begin_operation(context)?;
    query.await
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

async fn recover_installation(app: &AppHandle, paths: &ProductPaths) -> Result<(), String> {
    let script = paths.script("recover-install.ps1");
    if !script.is_file() { return Ok(()); }
    let mut waiting = false;
    while !app.context().exiting.load(Ordering::Acquire) {
        let worker = app.clone();
        let script = script.clone();
        let root = paths.app_root.clone();
        let log = paths.control_log.clone();
        let result = tokio::task::spawn_blocking(move || {
            run_powershell(&worker, &script, &[], &log, Some(&root))
        }).await.map_err(|error| error.to_string())?;
        if result.as_ref().err().map(String::as_str) != Some(OPERATION_BUSY) {
            if waiting && result.is_ok() {
                emit_log(app, &paths.control_log, "安装锁已释放，继续启动与更新调度。".into());
            }
            return result;
        }
        if !waiting {
            emit_log(app, &paths.control_log, "安装操作仍在进行，等待安装锁释放后自动重试恢复。".into());
            waiting = true;
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
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
                if let Err(error) = recover_installation(&app, &paths).await {
                    app.error(format!("中断恢复失败：{error}"));
                    return;
                }
            }
        }
        if app.context().exiting.load(Ordering::Acquire) { return; }
        if installed
            && (has_argument("--autostart") || has_argument("--post-install"))
            && !has_argument("--keep-stopped")
        {
            if let Err(error) = start_server(app.clone(), app.context()).await {
                app.error(error);
            }
        }
        if installed {
            if let Ok(paths) = ProductPaths::discover() {
                let mut state = read_state(&paths);
                reconcile_update(&mut state, env!("CARGO_PKG_VERSION"));
                let _ = save_update_state(&app, &paths, &state);
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
                                progress::begin(&app, &paths.app_root, "clean", "正在自动清理安装缓存");
                                let result = tokio::task::spawn_blocking(move || {
                                    run_powershell(&worker, &script, &["-SkipSpaceReport".into()], &log, Some(&root))
                                })
                                .await.map_err(|error| error.to_string()).and_then(|result| result);
                                match result {
                                    Ok(()) => progress::phase(&app, &paths.app_root, "clean", "complete", "安装缓存已自动清理", "completed"),
                                    Err(error) => progress::phase(&app, &paths.app_root, "clean", "failed", &error, "failed"),
                                }
                            }
                            state.cleanup_at = now();
                            let _ = write_json(&state_path(&paths), &state);
                        }
                    }
                }
            }
            if installed && !app.context().operation_busy.load(Ordering::Acquire) {
                if let Ok(paths) = ProductPaths::discover() {
                    let state = read_state(&paths);
                    let automatic = !controller::settings().auto_update_disabled;
                    if should_apply(&state, automatic) {
                        if let Err(error) = try_apply(&app, &paths, &state).await {
                            if error != OPERATION_BUSY { update_failed(&app, &paths, &error); }
                            emit_log(&app, &paths.control_log, error);
                        }
                    } else if automatic && now().saturating_sub(started) >= 60 && now() >= state.retry_at
                        && (startup_check || now().saturating_sub(state.last_check) >= 86400)
                    {
                        startup_check = false;
                        if let Err(error) = request_update(&app, None).await {
                            if error == OPERATION_BUSY { continue; }
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

pub async fn request_update(app: &AppHandle, target: Option<&str>) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths) {
        return Err("请先安装控制中心。".into());
    }
    let result = prepare_update(app, &paths, target).await;
    if let Err(error) = &result { update_failed(app, &paths, error); }
    result
}

async fn prepare_update(app: &AppHandle, paths: &ProductPaths, target: Option<&str>) -> Result<(), String> {
    let manual = target.is_some();
    let mut state = read_state(paths);
    // A ready package retains its identity; a repeated request must not download it again.
    if state.phase == "ready" && target.is_none_or(|version| version == state.version) {
        if manual { state.manual_version = target.map(str::to_owned); }
        progress::begin(app, &paths.app_root, "update", "更新已准备");
        save_update_state(app, paths, &state)?;
        progress::phase(app, &paths.app_root, "update", "waiting", "更新已准备，等待连续空闲 60 秒后切换", "waiting");
        return Ok(());
    }
    progress::begin(app, &paths.app_root, "update", "正在检查稳定版本");
    if let Some(version) = target {
        state.version = version.into();
        state.manual_version = Some(version.into());
        state.phase = "preparing".into();
        state.error.clear();
        save_update_state(app, paths, &state)?;
    }
    let check = check_for_update().await?;
    if target.is_some_and(|version| version != check.latest_version) {
        return Err("稳定版本已变更，请重新检查更新后确认下载。".into());
    }
    state.last_check = now();
    state.retry_at = 0;
    if !check.available {
        state.phase = "current".into();
        state.error.clear();
        state.manual_version = None;
        state.version = check.latest_version;
        save_update_state(app, paths, &state)?;
        let _ = app.emit("status", "已是最新稳定版");
        progress::phase(app, &paths.app_root, "update", "complete", "已是最新稳定版", "completed");
        return Ok(());
    }
    if state.phase == "failed" && state.version == check.latest_version && !manual {
        save_update_state(app, paths, &state)?;
        progress::phase(app, &paths.app_root, "update", "failed", &state.error, "failed");
        return Ok(());
    }
    state.version = check.latest_version.clone();
    state.manual_version = target.map(str::to_owned);
    state.phase = "preparing".into();
    state.error.clear();
    save_update_state(app, paths, &state)?;
    let staged = update::download_and_stage(app, &paths.app_root, &check.latest_version).await?;
    state.version = staged.version;
    state.directory = staged.directory;
    state.phase = "preparing".into();
    state.error.clear();
    save_update_state(app, paths, &state)?;
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
        return Err(error);
    }
    state.phase = "ready".into();
    save_update_state(app, paths, &state)?;
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
) -> Result<(), String> {
    let _guard = begin_operation(app.context())?;
    let worker = paths.clone();
    let running = tokio::task::spawn_blocking(move || query_health(&worker))
        .await
        .map_err(|e| e.to_string())?;
    if running.listening && !running.valid {
        return Err("端口由其他程序占用，未进行更新。".into());
    }
    if running.listening && !acquire(paths, 60).await? {
        return Ok(());
    }
    progress::phase(app, &paths.app_root, "update", "switching", "正在切换版本，请勿中断", "running");
    let mut switching = state.clone();
    switching.phase = "switching".into();
    if let Err(error) = save_update_state(app, paths, &switching) {
        if running.listening { let _ = release(paths).await; }
        return Err(error);
    }
    let script = state.directory.join("package/apply-update.ps1");
    let mut command = apply_update_command(&script, std::process::id(), &paths.app_root);
    command.arg("-Prepared");
    if !running.listening {
        command.arg("-KeepStopped");
    }
    let spawned = command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    if let Err(error) = spawned {
        if running.listening { let _ = release(paths).await; }
        return Err(error.to_string());
    }
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
    progress::begin(app, &paths.app_root, "repair", "正在修复环境，完成后控制中心将在后台重新启动");
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
    progress::begin(app, &paths.app_root, "clean", "正在清理安装缓存");
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
    progress::phase(app, &paths.app_root, "clean", "complete", "安装缓存已清理", "completed");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RecoveryFixture(ProductPaths);
    impl RecoveryFixture {
        fn new(name: &str) -> Self {
            let root = env::temp_dir().join(format!("pdf2zh-recovery-{}-{name}", std::process::id()));
            let paths = ProductPaths::from_root(root);
            fs::create_dir_all(&paths.bin_dir).unwrap();
            Self(paths)
        }
        fn script(&self, source: &str) {
            fs::write(self.0.script("recover-install.ps1"), source).unwrap();
        }
    }
    impl Drop for RecoveryFixture {
        fn drop(&mut self) {
            let root = &self.0.app_root;
            assert_eq!(root.parent(), Some(env::temp_dir().as_path()));
            assert!(root.file_name().unwrap().to_string_lossy().starts_with("pdf2zh-recovery-"));
            let _ = fs::remove_dir_all(root);
        }
    }
    #[test]
    fn recovery_retries_busy_exit_and_resumes_the_prepared_update() {
        let fixture = RecoveryFixture::new("retry");
        fixture.script(r#"
$counter = Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'attempts.txt'
$attempt = if (Test-Path -LiteralPath $counter) { [int](Get-Content -Raw -LiteralPath $counter) + 1 } else { 1 }
Set-Content -LiteralPath $counter -Value $attempt
if ($attempt -lt 3) { exit 1618 }
"#);
        let app = controller::test_handle();
        let state = UpdateState { phase: "ready".into(), version: "2.0.0".into(), manual_version: Some("2.0.0".into()), ..Default::default() };
        write_json(&state_path(&fixture.0), &state).unwrap();
        app.0.runtime.block_on(async {
            let _guard = begin_operation(app.context()).unwrap();
            tokio::time::timeout(Duration::from_secs(20), recover_installation(&app, &fixture.0)).await.unwrap().unwrap();
        });
        assert_eq!(read_trimmed(&fixture.0.app_root.join("attempts.txt")).as_deref(), Some("3"));
        assert!(should_apply(&read_state(&fixture.0), false));
        assert!(!app.context().operation_busy.load(Ordering::Acquire));
    }
    #[test]
    fn recovery_preserves_fatal_errors_and_stops_retrying_on_exit() {
        let fixture = RecoveryFixture::new("failure");
        fixture.script("throw 'Injected recovery failure'");
        let app = controller::test_handle();
        let error = app.0.runtime.block_on(recover_installation(&app, &fixture.0)).unwrap_err();
        assert_ne!(error, OPERATION_BUSY);
        assert!(error.contains("Injected recovery failure"));
        fixture.script("exit 1618");
        app.0.runtime.block_on(async {
            let worker = app.clone();
            let stop = app.0.runtime.spawn(async move {
                tokio::time::sleep(Duration::from_millis(100)).await;
                worker.context().exiting.store(true, Ordering::Release);
            });
            tokio::time::timeout(Duration::from_secs(10), recover_installation(&app, &fixture.0)).await.unwrap().unwrap();
            stop.await.unwrap();
        });
    }

    #[test]
    fn manual_request_is_version_bound_and_survives_disabled_automatic_updates() {
        let mut state: UpdateState = serde_json::from_str(r#"{"phase":"ready","version":"2.0.0"}"#).unwrap();
        assert!(!should_apply(&state, false));
        assert!(should_apply(&state, true));
        state.manual_version = Some("2.0.0".into());
        let restored: UpdateState = serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
        assert!(should_apply(&restored, false));
        state.manual_version = Some("3.0.0".into()); assert!(!should_apply(&state, false));
        state.phase = "failed".into(); assert!(!should_apply(&state, true));
    }
    #[test]
    fn successful_handoff_clears_intent_and_failed_handoff_does_not_loop() {
        let mut state = UpdateState { phase: "switching".into(), version: "2.0.0".into(), manual_version: Some("2.0.0".into()), ..Default::default() };
        reconcile_update(&mut state, "1.0.0");
        assert_eq!(state.phase, "failed"); assert!(state.manual_version.is_none());
        assert!(!should_apply(&state, true));
        reconcile_update(&mut state, "2.0.0");
        assert_eq!(state.phase, "current"); assert!(state.error.is_empty());
    }
    #[test]
    fn check_returns_metadata_or_error_and_releases_the_shared_lock() {
        let context = AppContext::default();
        let runtime = tokio::runtime::Runtime::new().unwrap();
        let result = runtime.block_on(check_only(&context, async { Ok(update::UpdateCheck { available: true, current_version: "1.0.0".into(), latest_version: "2.0.0".into() }) })).unwrap();
        assert!(result.available); assert!(!context.operation_busy.load(Ordering::Acquire));
        let result = runtime.block_on(check_only(&context, async { Err("offline".into()) }));
        assert_eq!(result.unwrap_err(), "offline"); assert!(!context.operation_busy.load(Ordering::Acquire));
        let _guard = begin_operation(&context).unwrap();
        assert!(runtime.block_on(check_only(&context, async { panic!("Concurrent query must not execute") })).is_err());
    }
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
