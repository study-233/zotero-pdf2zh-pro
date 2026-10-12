//! UI-independent service operations, settings and worker events.
use crate::*;
use serde_json::json;
use std::sync::{
    mpsc::{self, Receiver, Sender},
    Arc,
};
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

pub use crate::platform::wide;
pub struct Hub {
    pub panel_open: AtomicBool,
    tx: Sender<Value>,
    pub context: Arc<AppContext>,
    pub runtime: tokio::runtime::Runtime,
}
#[derive(Clone)]
pub struct AppHandle(pub Arc<Hub>);
pub fn channel() -> (AppHandle, Receiver<Value>) {
    let (tx, rx) = mpsc::channel();
    (
        AppHandle(Arc::new(Hub {
            panel_open: AtomicBool::new(false),
            tx,
            context: Arc::new(AppContext::default()),
            runtime: tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .unwrap(),
        })),
        rx,
    )
}
#[cfg(test)]
pub fn test_handle() -> AppHandle {
    channel().0
}
impl AppHandle {
    pub fn emit<T: Serialize>(&self, kind: &str, payload: T) -> Result<(), String> {
        self.0
            .tx
            .send(json!({"kind":kind,"payload":payload}))
            .map_err(|e| e.to_string())?;
        let _ = slint::invoke_from_event_loop(crate::ui::events);
        Ok(())
    }
    pub fn exit(&self, _code: i32) {
        self.context().exiting.store(true, Ordering::Release);
        let _ = slint::invoke_from_event_loop(|| {
            let _ = slint::quit_event_loop();
        });
    }
    pub fn error(&self, error: String) {
        if let Ok(paths) = ProductPaths::discover() {
            emit_log(self, &paths.control_log, format!("错误：{error}"));
            if let Some(mut value) = progress::read(&paths.app_root).filter(|p| p.active()) {
                value.status = "failed".into();
                value.message = error.clone();
                progress::publish(self, &paths.app_root, value);
            }
        }
        let _ = self.emit("error", error);
    }
    pub fn panel_open(&self) -> bool {
        self.0.panel_open.load(Ordering::Acquire)
    }
    pub fn context(&self) -> &AppContext {
        &self.0.context
    }
    pub fn refresh(&self) {
        let app = self.clone();
        self.0
            .runtime
            .spawn_blocking(move || match build_state(&app) {
                Ok(state) => {
                    let _ = app.emit("state", state);
                }
                Err(e) => app.error(e),
            });
    }
}
#[derive(Clone, Copy, Default, Deserialize, Serialize, PartialEq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum AppearanceTheme {
    #[default]
    System,
    Light,
    Dark,
}
impl AppearanceTheme {
    pub fn index(self) -> i32 {
        match self {
            Self::System => 0,
            Self::Light => 1,
            Self::Dark => 2,
        }
    }
    pub fn from_index(index: i32) -> Self {
        match index {
            1 => Self::Light,
            2 => Self::Dark,
            _ => Self::System,
        }
    }
}
#[derive(serde::Deserialize, Serialize, Default)]
#[serde(default, rename_all = "camelCase")]
pub struct Settings {
    pub auto_update_disabled: bool,
    pub appearance_theme: AppearanceTheme,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, Value>,
}

pub fn settings() -> Settings {
    ProductPaths::discover()
        .ok()
        .map(|paths| settings_at(&paths.app_root))
        .unwrap_or_default()
}
pub fn settings_at(root: &Path) -> Settings {
    fs::read(root.join("settings.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

pub fn save_settings(settings: &Settings) -> Result<(), String> {
    let path = ProductPaths::discover()?.app_root.join("settings.json");
    scheduler::write_json(&path, settings)
}

fn run_key() -> String {
    // Tests never write the user's real startup item.
    env::var("PDF2ZH_WINDOWS_REGISTRY_KEY")
        .map(|key| format!("{}\\Run", key.trim_start_matches("HKCU:\\")))
        .unwrap_or_else(|_| "Software\\Microsoft\\Windows\\CurrentVersion\\Run".into())
}

pub fn autostart_enabled(paths: &ProductPaths) -> bool {
    if env::var_os("PDF2ZH_WINDOWS_REGISTRY_KEY").is_none() {
        if let Ok(key) = RegKey::predef(HKEY_CURRENT_USER).open_subkey(
            "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run",
        ) {
            if key.get_raw_value(PRODUCT_NAME).ok().is_some_and(|value| {
                value
                    .bytes
                    .first()
                    .is_some_and(|value| *value == 3 || *value == 7)
            }) {
                return false;
            }
        }
    }
    RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(run_key())
        .ok()
        .and_then(|key| key.get_value::<String, _>(PRODUCT_NAME).ok())
        .is_some_and(|value| {
            value.contains(&paths.installed_gui.to_string_lossy().to_string())
                && value.contains("--autostart")
        })
}

pub fn set_autostart(paths: &ProductPaths, enabled: bool) -> Result<(), String> {
    let (key, _) = RegKey::predef(HKEY_CURRENT_USER)
        .create_subkey(run_key())
        .map_err(|e| e.to_string())?;
    if enabled {
        if env::var_os("PDF2ZH_WINDOWS_REGISTRY_KEY").is_none() {
            if let Ok(key) = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(
                "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run",
                winreg::enums::KEY_SET_VALUE,
            ) {
                let _ = key.delete_value(PRODUCT_NAME);
            }
        }
        key.set_value(
            PRODUCT_NAME,
            &format!("\"{}\" --autostart", paths.installed_gui.display()),
        )
        .map_err(|e| e.to_string())
    } else {
        match key.delete_value(PRODUCT_NAME) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_settings_default_to_system_theme_without_losing_update_choice() {
        let value: Settings = serde_json::from_str(r#"{"autoUpdateDisabled":true}"#).unwrap();
        assert_eq!(value.appearance_theme, AppearanceTheme::System);
        assert!(value.auto_update_disabled);
    }
    #[test]
    fn changing_theme_preserves_other_settings() {
        let mut value: Settings =
            serde_json::from_str(r#"{"autoUpdateDisabled":true,"futureSetting":{"value":42}}"#)
                .unwrap();
        value.appearance_theme = AppearanceTheme::Dark;
        let saved = serde_json::to_value(&value).unwrap();
        assert_eq!(saved["appearanceTheme"], "dark");
        assert_eq!(saved["autoUpdateDisabled"], true);
        assert_eq!(saved["futureSetting"]["value"], 42);
    }
}
