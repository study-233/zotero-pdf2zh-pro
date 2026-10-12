#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
slint::include_modules!();
mod platform;
use slint::ComponentHandle;

fn run() -> Result<(), Box<dyn std::error::Error>> {
    slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("skia-software".into())
        .select()?;
    let _runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    let _client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let ui = ProbeWindow::new()?;
    let tray = platform::Tray::new(ui.as_weak())?;
    if tray.is_duplicate() {
        return Ok(());
    }
    let timer = std::rc::Rc::new(slint::Timer::default());
    let weak = ui.as_weak();
    let start_timer = timer.clone();
    ui.on_start_progress(move || {
        if let Some(ui) = weak.upgrade() {
            ui.set_running(true);
            ui.set_progress(0.0);
            ui.set_message("正在演示可计算的下载进度…".into());
            let weak = ui.as_weak();
            let weak_timer = std::rc::Rc::downgrade(&start_timer);
            start_timer.start(
                slint::TimerMode::Repeated,
                std::time::Duration::from_millis(100),
                move || {
                    if let Some(ui) = weak.upgrade() {
                        if ui.get_running() {
                            let next = (ui.get_progress() + 0.01).min(1.0);
                            ui.set_progress(next);
                            ui.set_message(
                                format!(
                                    "演示下载 · {:.0}% · 中文输入和主题可以继续操作",
                                    next * 100.0
                                )
                                .into(),
                            );
                            if next >= 1.0 {
                                ui.set_running(false);
                                ui.set_message("演示完成 · 可以检查中文输入、开关和主题".into());
                                if let Some(timer) = weak_timer.upgrade() {
                                    timer.stop();
                                }
                            }
                        }
                    }
                },
            );
        }
    });
    ui.on_finish(|| {
        let _ = slint::quit_event_loop();
    });
    let weak = ui.as_weak();
    ui.on_hide_panel(move || {
        if let Some(ui) = weak.upgrade() {
            let _ = ui.hide();
        }
    });
    ui.window()
        .on_close_requested(|| slint::CloseRequestResponse::HideWindow);
    if !std::env::args().any(|a| a == "--start-hidden") {
        ui.show()?;
    }
    slint::run_event_loop_until_quit()?;
    drop(tray);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        let text: Vec<u16> = format!("Slint 验证版无法启动：{error}")
            .encode_utf16()
            .chain(Some(0))
            .collect();
        let title: Vec<u16> = "框架验证失败".encode_utf16().chain(Some(0)).collect();
        unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::MessageBoxW(
                std::ptr::null_mut(),
                text.as_ptr(),
                title.as_ptr(),
                0x10,
            );
        }
        std::process::exit(1);
    }
}
