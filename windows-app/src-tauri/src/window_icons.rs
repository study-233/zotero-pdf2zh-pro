use std::{io, path::PathBuf, ptr, sync::Mutex};
use tauri::{Manager, WebviewWindow, WindowEvent};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND},
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi},
        WindowsAndMessaging::{
            DestroyIcon, LoadImageW, SendMessageW, HICON, ICON_BIG, ICON_SMALL, IMAGE_ICON,
            SM_CXICON, SM_CXSMICON, SM_CYICON, SM_CYSMICON, WM_SETICON,
        },
    },
};

// tauri-build embeds icons/icon.ico with this resource identifier.
const APPLICATION_ICON: usize = 32512;

struct OwnedIcon(usize);

impl OwnedIcon {
    fn load(module: HINSTANCE, width: i32, height: i32) -> Result<Self, String> {
        // Omit LR_SHARED: each DPI-specific icon belongs to this window and can be
        // released after replacement, without touching Tauri's own icon handles.
        let icon = unsafe {
            LoadImageW(
                module,
                APPLICATION_ICON as *const u16,
                IMAGE_ICON,
                width,
                height,
                0,
            )
        };
        if icon.is_null() {
            Err(format!(
                "Cannot load the application icon: {}",
                io::Error::last_os_error()
            ))
        } else {
            Ok(Self(icon as usize))
        }
    }
}

impl Drop for OwnedIcon {
    fn drop(&mut self) {
        unsafe { DestroyIcon(self.0 as HICON) };
    }
}

struct WindowIcons {
    small: OwnedIcon,
    big: OwnedIcon,
}

impl WindowIcons {
    fn load(dpi: u32) -> Result<Self, String> {
        let module = unsafe { GetModuleHandleW(ptr::null()) };
        if module.is_null() {
            return Err(format!(
                "Cannot find the application module: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(Self {
            small: OwnedIcon::load(
                module,
                unsafe { GetSystemMetricsForDpi(SM_CXSMICON, dpi) },
                unsafe { GetSystemMetricsForDpi(SM_CYSMICON, dpi) },
            )?,
            big: OwnedIcon::load(
                module,
                unsafe { GetSystemMetricsForDpi(SM_CXICON, dpi) },
                unsafe { GetSystemMetricsForDpi(SM_CYICON, dpi) },
            )?,
        })
    }

    fn bind(&self, window: HWND) {
        unsafe {
            SendMessageW(
                window,
                WM_SETICON,
                ICON_SMALL as usize,
                self.small.0 as isize,
            );
            SendMessageW(window, WM_SETICON, ICON_BIG as usize, self.big.0 as isize);
        }
    }
}

pub fn setup(window: &WebviewWindow, log_path: PathBuf) -> Result<(), String> {
    let hwnd = window.hwnd().map_err(|error| error.to_string())?.0 as usize;
    let dpi = unsafe { GetDpiForWindow(hwnd as HWND) };
    let icons = WindowIcons::load(dpi.max(96))?;
    icons.bind(hwnd as HWND);
    let owned_icons = Mutex::new(Some(icons));
    let app = window.app_handle().clone();

    // Tauri's set_icon only sets ICON_SMALL. Keep the taskbar/Alt-Tab ICON_BIG
    // explicit as well, and retain both handles until replacement or destruction.
    window.on_window_event(move |event| match event {
        WindowEvent::ScaleFactorChanged { scale_factor, .. } => {
            let dpi = (scale_factor * 96.0).round().max(96.0) as u32;
            match WindowIcons::load(dpi) {
                Ok(next) => {
                    if let Ok(mut current) = owned_icons.lock() {
                        if current.is_some() {
                            next.bind(hwnd as HWND);
                            *current = Some(next);
                        }
                    }
                }
                Err(error) => crate::emit_log(&app, &log_path, error),
            }
        }
        WindowEvent::Destroyed => {
            if let Ok(mut current) = owned_icons.lock() {
                current.take();
            }
        }
        _ => {}
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::WindowIcons;

    #[test]
    fn executable_contains_icons_for_common_display_scales() {
        for dpi in [96, 144, 192] {
            let icons = WindowIcons::load(dpi).expect("embedded application icon must load");
            assert_ne!(icons.small.0, icons.big.0);
        }
    }
}
