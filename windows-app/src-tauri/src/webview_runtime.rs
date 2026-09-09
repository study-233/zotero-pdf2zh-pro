//! Prepare WebView2 before any web-backed window is created.
use std::os::windows::process::CommandExt;
use std::{
    env, fs,
    io::Write,
    path::PathBuf,
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};
use winreg::{enums::*, RegKey};

const CLIENT_ID: &str = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
const PREPARATION_SCRIPT: &str = include_str!("../../../scripts/windows/prepare-webview2.ps1");

fn valid_version(value: &str) -> bool {
    let parts: Vec<_> = value.trim().split('.').collect();
    if !(2..=4).contains(&parts.len()) {
        return false;
    }
    let numbers: Option<Vec<u32>> = parts
        .iter()
        .map(|part| {
            if part.is_empty() || !part.bytes().all(|c| c.is_ascii_digit()) {
                None
            } else {
                part.parse::<u32>()
                    .ok()
                    .filter(|number| *number <= i32::MAX as u32)
            }
        })
        .collect();
    numbers.is_some_and(|values| values.iter().any(|value| *value > 0))
}

pub fn available() -> bool {
    [
        (
            HKEY_CURRENT_USER,
            format!(r"Software\Microsoft\EdgeUpdate\Clients\{CLIENT_ID}"),
        ),
        (
            HKEY_LOCAL_MACHINE,
            format!(r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{CLIENT_ID}"),
        ),
        (
            HKEY_LOCAL_MACHINE,
            format!(r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{CLIENT_ID}"),
        ),
    ]
    .iter()
    .any(|(root, path)| {
        RegKey::predef(*root)
            .open_subkey(path)
            .ok()
            .and_then(|key| key.get_value::<String, _>("pv").ok())
            .is_some_and(|value| valid_version(&value))
    })
}

fn log_path() -> PathBuf {
    crate::ProductPaths::discover()
        .map(|paths| paths.logs_dir.join("webview2-setup.log"))
        .unwrap_or_else(|_| env::temp_dir().join("zotero-pdf2zh-pro-webview2-setup.log"))
}

fn log(message: &str) {
    let path = log_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{message}");
    }
}

fn prepare() -> Result<bool, String> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let directory = env::temp_dir().join(format!("pdf2zh-prepare-{}-{nonce}", std::process::id()));
    fs::create_dir(&directory).map_err(|e| e.to_string())?;
    let result = (|| {
        let script = directory.join("prepare-webview2.ps1");
        // Windows PowerShell 5.1 needs the BOM to read Chinese strings as UTF-8.
        let content = format!("\u{feff}{}", PREPARATION_SCRIPT.trim_start_matches('\u{feff}'));
        fs::write(&script, content).map_err(|e| e.to_string())?;
        let system_root = env::var_os("SystemRoot").ok_or("SystemRoot is missing")?;
        let powershell =
            PathBuf::from(system_root).join(r"System32\WindowsPowerShell\v1.0\powershell.exe");
        let status = Command::new(powershell)
            .args(["-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&script)
            .arg("-LogPath")
            .arg(log_path())
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW; the WinForms window remains visible.
            .status()
            .map_err(|e| format!("无法启动环境准备程序：{e}"))?;
        match status.code() {
            Some(0) if available() => Ok(true),
            Some(0) => Err("安装结束后仍未检测到 WebView2 Runtime。".into()),
            Some(10) => Ok(false), // Another launch already owns the preparation window.
            Some(20) => Ok(false), // User closed the failure/retry window.
            other => Err(format!("环境准备程序异常退出：{other:?}")),
        }
    })();
    let _ = fs::remove_dir_all(directory);
    result
}

pub fn ensure_ready(autostart: bool) -> bool {
    if available() {
        return true;
    }
    if autostart {
        log("WebView2 Runtime missing during autostart; open the control center to prepare it.");
        return false;
    }
    match prepare() {
        Ok(ready) => ready,
        Err(error) => {
            log(&error);
            let message = format!(
                "无法准备 Microsoft Edge WebView2 运行环境。\n{error}\n\n日志：{}",
                log_path().display()
            );
            let wide: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
            let title: Vec<u16> = "zotero-pdf2zh-pro".encode_utf16().chain(Some(0)).collect();
            unsafe {
                windows_sys::Win32::UI::WindowsAndMessaging::MessageBoxW(
                    std::ptr::null_mut(),
                    wide.as_ptr(),
                    title.as_ptr(),
                    windows_sys::Win32::UI::WindowsAndMessaging::MB_ICONERROR,
                );
            }
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_missing_zero_and_malformed_registrations() {
        for value in [
            "", " ", "0.0.0.0", "not-a-version", "123", "1..2", "-1.0", "1.2.3.4.5",
            "2147483648.0",
        ] {
            assert!(!valid_version(value), "{value}");
        }
        for value in ["128.0.2739.42", " 128.0.2739.42 ", "1.0"] {
            assert!(valid_version(value), "{value}");
        }
    }
    #[test]
    fn preparation_uses_the_same_runtime_registration() {
        assert!(PREPARATION_SCRIPT.contains(CLIENT_ID));
    }
}
