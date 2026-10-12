//! Slint owns pages on the main thread; workers only send controller events.
use crate::*;
use slint::{winit_030::WinitWindowAccessor, ComponentHandle, Model};
use std::{cell::RefCell, sync::mpsc::Receiver, time::Instant};
slint::include_modules!();

thread_local! { static UI: RefCell<Option<Ui>> = const { RefCell::new(None) }; }
struct Ui {
    app: AppHandle,
    rx: Receiver<Value>,
    tray: platform::Tray,
    window: Option<AppWindow>,
    timer: slint::Timer,
    state: Option<ControlState>,
    page: i32,
    previous: i32,
    wizard_stage: i32,
    existing: bool,
    keep_stopped: bool,
    selected_root: PathBuf,
    choices: (bool, bool),
    preferences: controller::Settings,
    busy: bool,
    failed: bool,
    cancelled: bool,
    purge_data: bool,
    last_action: String,
    status: String,
    details: String,
    confirmation: String,
    pending_action: String,
    progress: Option<progress::OperationProgress>,
    download_sample: Option<(Instant, u64)>,
    speed: f64,
    copied_until: Option<Instant>,
    notification: String,
    ignored_snapshot: Option<progress::OperationProgress>,
    details_open: bool,
    follow_logs: bool,
    log_offset: f32,
    active_rows: std::rc::Rc<slint::VecModel<DownloadRow>>,
    retired_operations: std::collections::HashSet<String>,
}

fn short_message(value: &str) -> String {
    let line = value.lines().next().unwrap_or(value).trim();
    if line.chars().count() > 95 {
        format!("{}…", line.chars().take(94).collect::<String>())
    } else {
        line.into()
    }
}
fn friendly_error(value: &str) -> String {
    let lower = value.to_lowercase();
    if [
        "timed out",
        "dns",
        "connection",
        "网络",
        "下载失败",
        "failed to download",
    ]
    .iter()
    .any(|s| lower.contains(s))
    {
        "下载未完成，请检查网络后重试。".into()
    } else if ["disk full", "not enough space", "空间不足", "磁盘空间"]
        .iter()
        .any(|s| lower.contains(s))
    {
        "磁盘空间不足，请清理空间后重试。".into()
    } else if ["checksum", "校验", "damaged"]
        .iter()
        .any(|s| lower.contains(s))
    {
        "文件校验未通过，重试会重新准备文件。".into()
    } else if ["access denied", "拒绝访问"]
        .iter()
        .any(|s| lower.contains(s))
    {
        "安装位置无法写入，请查看详情。".into()
    } else {
        short_message(value)
    }
}
fn bytes_label(bytes: u64) -> String {
    if bytes < 1024 * 1024 {
        format!("{:.0} KiB", bytes as f64 / 1024.0)
    } else {
        format!("{:.1} MiB", bytes as f64 / 1048576.0)
    }
}

impl Ui {
    fn active(&self) -> bool {
        self.busy || self.app.context().operation_busy.load(Ordering::Acquire)
    }
    fn can_cancel(&self) -> bool {
        self.active() && self.last_action == "install" && !self.cancelled
            && self.progress.as_ref().is_none_or(|p| p.active() && !matches!(p.phase.as_str(), "waiting" | "switching" | "health" | "rollback"))
    }
    fn confirm_cancel(&mut self) {
        if !self.can_cancel() { return; }
        if self.pending_action != "cancel-install" { self.previous = self.page; }
        self.pending_action = "cancel-install".into();
        self.confirmation = "取消后会停止下载和安装。已下载的文件将保留，方便重试；已有配置和翻译数据会保留。".into();
        self.navigate(4);
    }
    fn open(&mut self) -> Result<(), String> {
        if self.window.is_none() {
            let window = AppWindow::new().map_err(|e| e.to_string())?;
            // Slint 1.18.1 + softbuffer can lose the Windows surface after a
            // restore/DPI change but present only dirty controls (#13568).
            // Skia disables partial rendering when a rendering notifier exists.
            // Use full frames until the pinned backend includes that fix; this
            // does not schedule frames or keep a hidden window rendering.
            window.window().set_rendering_notifier(|_, _| {})
                .map_err(|e| format!("无法启用完整窗口重绘：{e}"))?;
            window.set_active_files(self.active_rows.clone().into());
            let app = self.app.clone();
            window.on_purge_changed(move |purge| { let _ = app.emit("purge-data", purge); });
            let app = self.app.clone();
            window.on_details_state(move |open, follow, offset| { let _ = app.emit("details-state", serde_json::json!([open,follow,offset])); });
            let app = self.app.clone();
            window.on_action(move |action| {
                let _ = app.emit("command", action.as_str());
            });
            let app = self.app.clone();
            window.on_preferences(move |theme, start, update| {
                let _ = app.emit("preferences", serde_json::json!([theme, start, update]));
            });
            let app = self.app.clone();
            window.on_install(move |root, start, update| {
                let _ = app.emit("install", serde_json::json!([root.as_str(), start, update]));
            });
            let app = self.app.clone();
            window.on_path_edited(move |root| {
                let _ = app.emit("path", root.as_str());
            });
            let app = self.app.clone();
            window.on_draft_options(move |start, update| {
                let _ = app.emit("draft-options", serde_json::json!([start, update]));
            });
            let app = self.app.clone();
            window.window().on_close_requested(move || {
                let _ = app.emit("command", "close");
                slint::CloseRequestResponse::KeepWindowShown
            });
            self.window = Some(window);
        }
        self.render();
        let window = self.window.as_ref().unwrap();
        window.show().map_err(|e| e.to_string())?;
        window.window().with_winit_window(|w| {
            platform::sync_window_icons(w);
            w.set_minimized(false);
            w.focus_window();
        });
        self.app.0.panel_open.store(true, Ordering::Release);
        self.timer
            .start(slint::TimerMode::Repeated, Duration::from_millis(250), tick);
        self.app.refresh();
        Ok(())
    }
    fn close(&mut self) {
        if has_argument("--setup") && !self.active() {
            self.app.exit(0);
            return;
        }
        if let Some(window) = self.window.take() {
            let _ = window.hide();
        }
        self.timer.stop();
        self.app.0.panel_open.store(false, Ordering::Release);
        // Dropping the component also drops its renderer/surface. State lives here, not in the page.
    }
    fn navigate(&mut self, page: i32) {
        self.page = page;
        if let Some(window) = &self.window {
            window.set_page(page);
            window.window().set_size(slint::LogicalSize::new(
                if page == 0 { 400.0 } else if page == 2 { 560.0 } else { 520.0 },
                if page == 0 {
                    300.0
                } else if page == 1 {
                    440.0
                } else {
                    480.0
                },
            ));
        }
    }
    fn render(&self) {
        let Some(window) = &self.window else {
            return;
        };
        window
            .window()
            .with_winit_window(platform::sync_window_icons);
        window.set_page(self.page);
        window.set_wizard_stage(self.wizard_stage);
        window.set_busy(self.active());
        window.set_details_open(self.details_open);
        window.set_follow_logs(self.follow_logs);
        window.set_log_offset(self.log_offset);
        window.set_failed(self.failed);
        window.set_cancelled(self.cancelled);
        window.set_cancelling(self.can_cancel() && self.app.context().install_cancel_requested.load(Ordering::Acquire));
        window.set_can_cancel(self.can_cancel());
        window.set_confirm_uninstall(self.pending_action == "uninstall");
        window.set_confirm_cancel(self.pending_action == "cancel-install");
        window.set_purge_data(self.purge_data);
        window.set_ready(self.state.is_some());
        window.set_existing(self.existing);
        window.set_install_path(self.selected_root.to_string_lossy().as_ref().into());
        window.set_autostart(if self.page == 2 {
            self.choices.0
        } else {
            self.state.as_ref().is_some_and(|s| s.autostart_enabled)
        });
        window.set_automatic_updates(if self.page == 2 {
            self.choices.1
        } else {
            !self.preferences.auto_update_disabled
        });
        let hc = platform::high_contrast();
        let appearance = window.global::<Appearance>();
        appearance.set_high_contrast(hc.is_some());
        let theme = if let Some((background, foreground)) = hc {
            appearance.set_system_background(background);
            appearance.set_system_foreground(foreground);
            if (background.red() as u32 + background.green() as u32 + background.blue() as u32)
                < 384
            {
                2
            } else {
                1
            }
        } else {
            self.preferences.appearance_theme.index()
        };
        window.set_theme(self.preferences.appearance_theme.index());
        window.set_effective_theme(theme);
        window.set_animate(platform::animations_enabled());
        window.set_text_scale(platform::text_scale());
        if let Some(state) = &self.state {
            let (heading, action) = match (&state.installation, &state.service) {
                (InstallationStatus::NotInstalled, _) => ("尚未安装翻译服务", "安装翻译服务"),
                (InstallationStatus::UpdateAvailable, _) => ("可以升级翻译服务", "升级翻译服务"),
                (InstallationStatus::DowngradeBlocked, _) => ("已有更新版本", "重新检测"),
                (_, ServiceStatus::Running) => ("翻译服务已就绪", "停止服务"),
                (_, ServiceStatus::PortConflict) => ("端口被占用", "重新检测"),
                _ => ("翻译服务已停止", "启动服务"),
            };
            window.set_heading(heading.into());
            window.set_subtitle(
                if matches!(state.service, ServiceStatus::Running) {
                    "可以开始 PDF 翻译和划词精读"
                } else {
                    "在此管理本地翻译服务"
                }
                .into(),
            );
            window.set_action_label(
                if self.active() {
                    match self.last_action.as_str() {
                        "start" => "正在启动…",
                        "stop" => "正在停止…",
                        "check" => "正在检查…",
                        _ => "正在处理…",
                    }
                } else {
                    action
                }
                .into(),
            );
            window.set_address(state.address.as_str().into());
            window.set_version(
                format!(
                    "控制中心 {} · 服务 {}",
                    state.app_version,
                    state
                        .service_version
                        .as_deref()
                        .or(state.installed_version.as_deref())
                        .unwrap_or("未安装")
                )
                .into(),
            );
            window.set_installed(state.running_from_installed_path);
            window.set_can_relocate(state.can_relocate);
            window.set_downgrade(matches!(
                state.installation,
                InstallationStatus::DowngradeBlocked
            ));
        }
        let progress = self.progress.as_ref();
        let active_rows: Vec<DownloadRow> = progress.into_iter().flat_map(|p| &p.files)
            .filter(|f| !self.cancelled && matches!(f.status.as_str(), "downloading" | "extracting" | "failed")).map(file_row).collect();
        sync_rows(&self.active_rows, &active_rows);
        let active = progress.is_some_and(|p| p.active());
        let message = if self.failed || self.cancelled {
            self.status.as_str()
        } else {
            progress
                .filter(|p| p.active())
                .map(|p| p.message.as_str())
                .unwrap_or(&self.status)
        };
        window.set_operation_message(
            short_message(if message.is_empty() {
                if self.preferences.auto_update_disabled {
                    "自动更新已关闭"
                } else {
                    "空闲时自动检查更新"
                }
            } else {
                message
            })
            .into(),
        );
        let known = progress.filter(|p| p.unit.as_deref() == Some("bytes")).and_then(|p| p.percent());
        window.set_progress_known(known.is_some() && active && !self.cancelled);
        window.set_operation_step(match progress.map(|p| p.phase.as_str()) {
            Some("download" | "python" | "dependencies" | "python-download" | "dependency-download" | "extracting" | "installing") => 1,
            Some("verifying" | "waiting" | "switching" | "health" | "complete") => 2,
            _ => 0,
        });
        window.set_elapsed(progress.filter(|p| p.started_at > 0).map(|p| {
            let elapsed = (if p.active() { progress::now() } else { p.updated_at }).saturating_sub(p.started_at);
            format!("已用时 {}分{:02}秒", elapsed / 60, elapsed % 60)
        }).unwrap_or_default().into());
        window.set_progress(known.unwrap_or(0) as f32 / 100.0);
        window.set_progress_active(active || self.busy);
        let detail = match progress {
            Some(p) if p.unit.as_deref() == Some("items") => format!("已完成 {} / {} · 已用时 {}秒", p.completed.unwrap_or(0), p.total.unwrap_or(0), progress::now().saturating_sub(p.started_at)),
            Some(p) if p.active() && p.unit.as_deref() == Some("bytes") => format!(
                "{} / {}{}{}",
                bytes_label(p.completed.unwrap_or(0)),
                p.total
                    .map(bytes_label)
                    .unwrap_or_else(|| "总量未知".into()),
                known.map(|n| format!(" · {n}%")).unwrap_or_default(),
                if self.speed > 0.0 {
                    format!(" · {}/s", bytes_label(self.speed as u64))
                } else {
                    String::new()
                }
            ),
            Some(p) if p.active() && p.progress_unavailable => "当前步骤正在进行，暂时无法获取下载进度。".into(),
            Some(p) if p.active() && matches!(p.phase.as_str(), "switching" | "health") => "正在完成配置，请稍候。".into(),
            _ => String::new(),
        };
        window.set_operation_detail(detail.into());
        window.set_details(self.log_details().into());
        window.set_confirmation(self.confirmation.as_str().into());
        window.set_copied(
            self.copied_until
                .is_some_and(|until| Instant::now() < until),
        );
    }
    fn full_details(&self) -> String {
        let mut lines = Vec::new();
        if let Some(p) = &self.progress {
            for file in &p.files { let row = file_row(file); lines.push(format!("{}：{}", row.name, row.detail)); }
            lines.extend(p.recent_logs.iter().cloned());
        }
        for line in self.details.lines() { if !lines.iter().any(|s| s == line) { lines.push(line.into()); } }
        format!(
            "{}\n{}\n\n{}",
            self.status,
            self.progress
                .as_ref()
                .map(|p| p.message.as_str())
                .unwrap_or(""),
            lines.join("\n")
        )
    }
    fn log_details(&self) -> String {
        let mut lines = Vec::new();
        for line in self.details.lines().chain(self.progress.iter().flat_map(|p| p.recent_logs.iter().map(String::as_str))) {
            if !line.trim().is_empty() && !lines.contains(&line) { lines.push(line); }
        }
        if lines.is_empty() { "暂无日志。".into() } else { lines.join("\n") }
    }
    fn command(&mut self, command: &str) {
        match command {
            "open" => {
                if let Err(e) = self.open() {
                    self.app.error(e);
                }
                return;
            }
            "close" => {
                if self.can_cancel() && !self.app.context().install_cancel_requested.load(Ordering::Acquire) {
                    self.confirm_cancel();
                    return;
                }
                self.close();
                return;
            }
            "background" => {
                if self.pending_action == "cancel-install" {
                    self.navigate(self.previous);
                    self.pending_action.clear();
                }
                self.close();
                return;
            }
            "cancel-install" => { self.confirm_cancel(); return; }
            "cancel-install-now" => {
                if self.can_cancel() {
                    self.app.context().install_cancel_requested.store(true, Ordering::Release);
                    self.status = "正在取消安装…".into();
                    self.navigate(self.previous);
                    self.pending_action.clear();
                }
                return;
            }
            "home" => {
                self.navigate(0);
                return;
            }
            "settings" => {
                self.navigate(1);
                return;
            }
            "back" => {
                self.navigate(self.previous);
                self.pending_action.clear();
                return;
            }
            "details" => {
                self.details_open = !self.details_open;
                return;
            }
            "license" | "notices" => {
                let file = if command == "license" { "LICENSE.txt" } else { "THIRD_PARTY_NOTICES.md" };
                let beside = current_executable().ok().and_then(|p| p.parent().map(|d| d.join(file)));
                let path = self.selected_root.join("bin").join(file);
                let path = if path.is_file() { path } else { beside.unwrap_or(path) };
                if let Err(e) = open_with("notepad.exe", &path) { self.app.error(e); }
                return;
            }
            "quit" => {
                if !self.active() {
                    self.app.exit(0);
                } else {
                    self.status = "操作仍在进行，可以关闭窗口让它在后台继续。".into();
                }
                return;
            }
            "copy-address" | "copy-details" => {
                let value = if command == "copy-address" {
                    format!("http://127.0.0.1:{}", port())
                } else {
                    self.full_details()
                };
                match platform::copy(&value) {
                    Ok(()) => self.copied_until = Some(Instant::now() + Duration::from_secs(2)),
                    Err(e) => self.app.error(e),
                }
                return;
            }
            "log" => {
                if self.page != 2 && !has_argument("--setup") {
                    if let Err(e) = open_log() {
                        self.app.error(e);
                    }
                    return;
                }
                let log = self.selected_root.join("logs/control-panel.log");
                if let Some(parent) = log.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                if !log.exists() {
                    let _ = fs::write(&log, []);
                }
                if let Err(e) = open_with("notepad.exe", &log) {
                    self.app.error(e);
                }
                return;
            }
            "data" => {
                if let Err(e) = open_data_dir() {
                    self.app.error(e);
                }
                return;
            }
            "location" => {
                if let Err(e) = open_with("explorer.exe", &self.selected_root) {
                    self.app.error(e);
                }
                return;
            }
            _ => {}
        }
        if self.active() {
            return;
        }
        if command == "browse" {
            if !self.existing {
                if let Some(path) = platform::choose_folder() {
                    self.selected_root = path.join(PRODUCT_NAME);
                }
            }
            return;
        }
        if command == "finish" {
            let paths = ProductPaths::from_root(self.selected_root.clone());
            let mut child = Command::new(paths.installed_gui);
            child.args(["--post-install", "--show-panel"]);
            child.arg(format!("--wait-parent={}", std::process::id()));
            if env::var_os("PDF2ZH_WINDOWS_APP_ROOT").is_some() {
                child.env("PDF2ZH_WINDOWS_APP_ROOT", &self.selected_root);
            }
            if self.keep_stopped {
                child.arg("--keep-stopped");
            }
            match child.spawn() {
                Ok(_) => self.app.exit(0),
                Err(e) => self.app.error(format!("无法打开控制中心：{e}")),
            }
            return;
        }
        if command == "primary" {
            let Some(state) = &self.state else {
                return;
            };
            match (&state.installation, &state.service) {
                (InstallationStatus::NotInstalled | InstallationStatus::UpdateAvailable, _) => {
                    self.wizard_stage = 0;
                    self.navigate(2);
                    return;
                }
                (InstallationStatus::DowngradeBlocked, _) | (_, ServiceStatus::PortConflict) => {
                    self.app.refresh();
                    return;
                }
                (_, ServiceStatus::Running) => {
                    self.perform("stop", None);
                    return;
                }
                _ => {
                    self.perform("start", None);
                    return;
                }
            }
        }
        if command == "retry" {
            if self.last_action.is_empty() {
                self.app.refresh();
                return;
            }
            let action = self.last_action.clone();
            if action == "install" {
                self.navigate(2);
                self.wizard_stage = 1;
            }
            self.perform(&action, None);
            return;
        }
        if ["uninstall", "clean", "relocate"].contains(&command) {
            self.confirmation = match command {
                "uninstall" => "将移除控制中心和翻译运行环境。你可以选择是否同时清理个人数据。",
                "clean" => "清理安装缓存后，更新或修复可能需要重新下载依赖。继续吗？",
                _ => "迁移需要暂停服务，将保留配置和数据。接下来选择新位置。",
            }
            .into();
            self.pending_action = command.into();
            self.purge_data = false;
            self.previous = self.page;
            self.navigate(4);
            return;
        }
        if command == "confirm" {
            let action = std::mem::take(&mut self.pending_action);
            self.navigate(self.previous);
            if action == "relocate" {
                if let Some(path) = platform::choose_folder() {
                    self.perform(&action, Some(path.join(PRODUCT_NAME)));
                }
            } else if !action.is_empty() {
                self.perform(&action, None);
            }
            return;
        }
        if ["install", "check", "repair", "start", "stop"].contains(&command) {
            self.perform(command, None);
        }
    }
    fn perform(&mut self, action: &str, destination: Option<PathBuf>) {
        if self.active() {
            return;
        }
        if self.page == 1 {
            self.navigate(0);
        }
        self.ignored_snapshot = progress::read(&self.selected_root);
        self.busy = true;
        self.failed = false;
        self.cancelled = false;
        self.app.context().install_cancel_requested.store(false, Ordering::Release);
        self.last_action = action.into();
        self.details.clear();
        self.details_open = false;
        self.follow_logs = true;
        self.log_offset = 0.0;
        self.progress = None;
        self.status = match action {
            "install" => "正在安装…",
            "start" => "正在启动…",
            "stop" => "正在停止…",
            "check" => "正在检查更新…",
            "repair" => "正在修复…",
            _ => "正在处理…",
        }
        .into();
        let app = self.app.clone();
        let action = action.to_owned();
        let purge_data = self.purge_data;
        let root = self.selected_root.to_string_lossy().to_string();
        let choices = (
            self.choices.0,
            self.choices.1,
            self.preferences.appearance_theme,
        );
        self.app.0.runtime.spawn(async move {
            let result: Result<(), String> = async {
                match action.as_str() {
                    "install" => {
                        install_or_upgrade(app.clone(), app.context(), Some(root), Some(choices))
                            .await?;
                        let _ = app.emit("setup-completed", Value::Null);
                    }
                    "start" => {
                        start_server(app.clone(), app.context()).await?;
                    }
                    "stop" => scheduler::stop_safely(&app).await?,
                    "check" => scheduler::request_update(&app, true).await?,
                    "repair" => scheduler::repair(&app).await?,
                    "clean" => scheduler::clean_cache(&app).await?,
                    "relocate" => {
                        let path = destination.ok_or("尚未选择迁移位置")?;
                        relocate_installation(
                            app.clone(),
                            app.context(),
                            path.to_string_lossy().to_string(),
                        )?;
                    }
                    "uninstall" => uninstall_product(app.clone(), app.context(), purge_data)?,
                    _ => {}
                }
                Ok(())
            }
            .await;
            if let Err(e) = result {
                if e == cancellation::ERROR {
                    let _ = app.emit("cancelled", Value::Null);
                } else { app.error(e); }
            }
            app.refresh();
            let _ = app.emit("done", Value::Null);
        });
    }
    fn accept_progress(&mut self, value: progress::OperationProgress) {
        if self.retired_operations.contains(&value.operation_id) { return; }
        if self.ignored_snapshot.as_ref() == Some(&value) {
            return;
        }
        self.ignored_snapshot = None;
        if self.progress.as_ref().is_some_and(|p| p == &value ||
            (p.operation_id == value.operation_id && value.sequence < p.sequence)) {
            return;
        }
        if value.active()
            && self
                .progress
                .as_ref()
                .is_none_or(|p| p.operation_id != value.operation_id)
        {
            self.failed = false;
            self.details_open = false;
            self.follow_logs = true;
            self.log_offset = 0.0;
        }
        if let Some(previous) = &self.progress {
            if previous.operation_id != value.operation_id {
                self.retired_operations.insert(previous.operation_id.clone());
            }
        }
        let continuing = self.progress.as_ref().is_some_and(|p| {
            p.operation_id == value.operation_id && p.phase == value.phase && p.total == value.total
        });
        if value.unit.as_deref() == Some("bytes") {
            let received = value.completed.unwrap_or(0);
            if continuing {
                if let Some((instant, previous)) = self.download_sample {
                    let seconds = instant.elapsed().as_secs_f64();
                    if seconds >= 0.4 {
                        self.speed = received.saturating_sub(previous) as f64 / seconds;
                        self.download_sample = Some((Instant::now(), received));
                    }
                }
            } else {
                self.download_sample = Some((Instant::now(), received));
                self.speed = 0.0;
            }
        } else {
            self.download_sample = None;
            self.speed = 0.0;
        }
        if matches!(value.status.as_str(), "failed" | "rolledBack") {
            self.failed = true;
            self.details_open = true;
            self.status = friendly_error(&value.message);
        } else if value.status == "cancelled" {
            self.cancelled = true;
            self.failed = false;
            self.status = value.message.clone();
        } else if !value.active() {
            self.status = value.message.clone();
        }
        self.progress = Some(value);
    }
    fn event(&mut self, event: Value) {
        let payload = &event["payload"];
        match event["kind"].as_str().unwrap_or("") {
            "purge-data" => { if self.pending_action == "uninstall" { self.purge_data = payload.as_bool().unwrap_or(false); } }
            "cancelled" => {
                self.cancelled = true;
                self.failed = false;
                self.status = "安装已取消，已下载的文件可在重试时复用。".into();
            }
            "cancel-error" => {
                self.status = payload.as_str().unwrap_or("取消请求未发送，请重试。").into();
                self.details.push_str(&format!("\n{}", self.status));
                self.details_open = true;
            }
            "details-state" => {
                self.details_open = payload[0].as_bool().unwrap_or(false);
                self.follow_logs = payload[1].as_bool().unwrap_or(true);
                self.log_offset = payload[2].as_f64().unwrap_or(0.0) as f32;
            }
            "command" => self.command(payload.as_str().unwrap_or("")),
            "path" => self.selected_root = PathBuf::from(payload.as_str().unwrap_or("")),
            "draft-options" => {
                self.choices = (
                    payload[0].as_bool().unwrap_or(true),
                    payload[1].as_bool().unwrap_or(true),
                )
            }
            "install" => {
                if !self.active() {
                    self.selected_root = PathBuf::from(payload[0].as_str().unwrap_or(""));
                    self.choices = (
                        payload[1].as_bool().unwrap_or(true),
                        payload[2].as_bool().unwrap_or(true),
                    );
                    self.wizard_stage = 1;
                    self.perform("install", None);
                }
            }
            "preferences" => {
                let start = payload[1].as_bool().unwrap_or(false);
                if let Some(state) = &self.state {
                    if state.running_from_installed_path && state.autostart_enabled != start {
                        if let Err(e) = crate::set_autostart(self.app.clone(), start) {
                            self.app.error(e);
                            return;
                        }
                    }
                }
                self.preferences.appearance_theme = controller::AppearanceTheme::from_index(
                    payload[0].as_i64().unwrap_or(0) as i32,
                );
                self.preferences.auto_update_disabled = !payload[2].as_bool().unwrap_or(true);
                // Before installation these are drafts: creating settings.json in an
                // unowned destination would make the installer reject that directory.
                if self
                    .state
                    .as_ref()
                    .is_some_and(|state| state.running_from_installed_path)
                {
                    if let Err(e) = controller::save_settings(&self.preferences) {
                        self.app.error(e);
                    }
                }
                if let Some(state) = &mut self.state {
                    state.autostart_enabled = start;
                }
            }
            "state" => {
                if let Ok(state) = serde_json::from_value::<ControlState>(payload.clone()) {
                    if self.state.is_none() {
                        self.existing =
                            !matches!(state.installation, InstallationStatus::NotInstalled);
                        self.keep_stopped =
                            self.existing && !matches!(state.service, ServiceStatus::Running);
                        if self.existing {
                            self.choices = (
                                state.autostart_enabled,
                                !self.preferences.auto_update_disabled,
                            );
                            self.selected_root = PathBuf::from(&state.install_root);
                        }
                    }
                    self.state = Some(state);
                }
            }
            "refresh" => self.app.refresh(),
            "done" => {
                self.busy = false;
                self.app.context().install_cancel_requested.store(false, Ordering::Release);
                if self.pending_action == "cancel-install" {
                    self.navigate(self.previous);
                    self.pending_action.clear();
                }
                if !self.failed && self.status.starts_with("正在") {
                    self.status = "操作已完成".into();
                }
            }
            "setup-completed" => {
                self.wizard_stage = 2;
            }
            "operation-progress" => {
                if let Ok(p) = serde_json::from_value(payload.clone()) {
                    self.accept_progress(p);
                }
            }
            "operation-log" => {
                if let Some(line) = payload["line"].as_str() {
                    self.details.push_str(line);
                    self.details.push('\n');
                    if self.details.len() > 16000 {
                        self.details = self
                            .details
                            .chars()
                            .rev()
                            .take(8000)
                            .collect::<Vec<_>>()
                            .into_iter()
                            .rev()
                            .collect();
                    }
                }
            }
            "status" => self.status = payload.as_str().unwrap_or("").into(),
            "error" => {
                let message = payload.as_str().unwrap_or("操作失败");
                self.details.push_str(&format!("\n{message}"));
                self.status = friendly_error(message);
                self.failed = true;
                self.details_open = true;
                if !self.app.panel_open() && self.notification != message {
                    self.tray.notify(&self.status);
                    self.notification = message.into();
                }
            }
            _ => {}
        }
    }
}
fn file_row(file: &progress::FileProgress) -> DownloadRow {
    let known = file.status == "downloading" && file.total.is_some_and(|n| n > 0);
    let fraction = file.total.filter(|n| *n > 0).map_or(0.0, |n| file.completed.min(n) as f32 / n as f32);
    let status = match file.status.as_str() {
        "downloaded" => "已下载", "failed" => "下载失败", "cancelled" => "已取消", "extracting" => "正在解压", "extracted" => "已解压",
        _ if file.attempt > 1 => "重新下载", _ => "正在下载",
    };
    let status = if file.attempt > 1 && file.status == "downloading" { format!("重新下载（第 {} 次）", file.attempt) } else { status.into() };
    let detail = if known {
        format!("{status} · {:.0}% · {} / {} · {}/s", fraction * 100.0, bytes_label(file.completed), bytes_label(file.total.unwrap()), bytes_label(file.bytes_per_second))
    } else if file.status == "downloading" { format!("{status} · 实时进度不可用") } else { status };
    let name = if let Some(python) = file.name.strip_prefix("cpython-") {
        format!("Python {}", python.split('-').next().unwrap_or(python))
    } else { file.name.clone() };
    DownloadRow { name: name.into(), detail: detail.into(), known, progress: fraction }
}
fn sync_rows(model: &slint::VecModel<DownloadRow>, rows: &[DownloadRow]) {
    while model.row_count() > rows.len() { model.remove(model.row_count() - 1); }
    for (index, row) in rows.iter().enumerate() {
        if index >= model.row_count() { model.push(row.clone()); }
        else if model.row_data(index).as_ref() != Some(row) { model.set_row_data(index, row.clone()); }
    }
}
pub fn events() {
    UI.with(|slot| {
        if let Some(ui) = slot.borrow_mut().as_mut() {
            let mut immediate = false;
            while let Ok(event) = ui.rx.try_recv() {
                immediate |= !matches!(event["kind"].as_str(), Some("operation-progress" | "operation-log"));
                ui.event(event);
            }
            if immediate { ui.render(); }
        }
    });
}
fn tick() {
    UI.with(|slot| {
        if let Some(ui) = slot.borrow_mut().as_mut() {
            if let Some(p) = progress::read(&ui.selected_root) {
                ui.accept_progress(p);
            }
            ui.render();
        }
    });
}

fn run_inner() -> Result<(), String> {
    if !platform::wait_parent() {
        return Ok(());
    }
    let paths = ProductPaths::discover()?;
    if !running_from_installed_path(&paths)
        && matches!(installation_status(&paths).0, InstallationStatus::Current)
        && !has_argument("--candidate")
        && !has_argument("--setup")
    {
        if Command::new(&paths.installed_gui)
            .args(env::args_os().skip(1))
            .spawn()
            .is_ok()
        {
            return Ok(());
        }
    }
    slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("skia-software".into())
        .select()
        .map_err(|e| e.to_string())?;
    let (app, rx) = controller::channel();
    let tray = platform::Tray::new(app.clone(), &paths.app_root)?;
    if tray.duplicate {
        return Ok(());
    }
    let installed = running_from_installed_path(&paths);
    if installed {
        fs::create_dir_all(&paths.app_root).map_err(|e| e.to_string())?;
        fs::write(&paths.control_pid, std::process::id().to_string()).map_err(|e| e.to_string())?;
        fs::write(
            &paths.control_executable,
            current_executable()?.to_string_lossy().as_bytes(),
        )
        .map_err(|e| e.to_string())?;
    }
    let preferences = controller::settings();
    let choices = (true, !preferences.auto_update_disabled);
    let snapshot = progress::read(&paths.app_root);
    let mut ui = Ui {
        app: app.clone(),
        rx,
        tray,
        window: None,
        timer: slint::Timer::default(),
        state: None,
        page: if has_argument("--setup") { 2 } else { 0 },
        previous: 0,
        wizard_stage: 0,
        existing: false,
        keep_stopped: false,
        selected_root: paths.app_root.clone(),
        choices,
        preferences,
        busy: false,
        failed: false,
        cancelled: false,
        purge_data: false,
        last_action: String::new(),
        status: String::new(),
        details: String::new(),
        confirmation: String::new(),
        pending_action: String::new(),
        progress: None,
        download_sample: None,
        speed: 0.0,
        copied_until: None,
        notification: String::new(),
        ignored_snapshot: None,
        details_open: false,
        follow_logs: true,
        log_offset: 0.0,
        active_rows: std::rc::Rc::new(slint::VecModel::default()),
        retired_operations: std::collections::HashSet::new(),
    };
    if let Some(p) = snapshot {
        ui.last_action = match p.operation.as_str() {
            "update" => "check",
            "repair" => "repair",
            "install" if has_argument("--setup") => "install",
            "install" => "repair",
            _ => "",
        }
        .into();
        ui.accept_progress(p);
    }
    if platform::show_on_start() {
        ui.open()?;
    }
    UI.with(|slot| *slot.borrow_mut() = Some(ui));
    if let Ok(location) = install_root::discover() {
        emit_log(
            &app,
            &paths.control_log,
            format!(
                "安装位置识别：source={}; root={}; {}",
                location.source,
                paths.app_root.display(),
                location.registry_diagnostic
            ),
        );
    }
    emit_log(
        &app,
        &paths.control_log,
        format!(
            "控制中心启动：renderer=skia-software; root={}; current={}; installed={}; recognized={installed}",
            paths.app_root.display(), current_executable()?.display(), paths.installed_gui.display()
        ),
    );
    app.refresh();
    scheduler::start(app.clone(), installed);
    let result = slint::run_event_loop_until_quit().map_err(|e| e.to_string());
    app.context().exiting.store(true, Ordering::Release);
    UI.with(|slot| {
        slot.borrow_mut().take();
    });
    if installed
        && read_trimmed(&paths.control_pid).as_deref() == Some(&std::process::id().to_string())
    {
        let _ = fs::remove_file(&paths.control_pid);
        let _ = fs::remove_file(&paths.control_executable);
    }
    result
}
pub fn run() {
    if let Err(e) = run_inner() {
        platform::message(&e);
    }
}
