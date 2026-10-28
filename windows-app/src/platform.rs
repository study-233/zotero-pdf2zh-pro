//! Windows integration only. All product pages are Slint components.
use crate::*;
use std::{
    ptr,
    sync::{atomic::AtomicIsize, mpsc, Arc},
};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::GetActiveWindow;
use windows_sys::Win32::{
    Foundation::*,
    Graphics::Gdi::*,
    System::{Com::*, DataExchange::*, LibraryLoader::*, Memory::*, Threading::*},
    UI::{
        Accessibility::{HCF_HIGHCONTRASTON, HIGHCONTRASTW},
        Shell::*,
        WindowsAndMessaging::*,
    },
};

const ACTIVATE: u32 = WM_APP + 2; // Kept compatible with the previous controller.
const TRAY_EVENT: u32 = WM_APP + 4;
pub fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

pub fn open_zotero() -> Result<(), String> {
    // Resolve the protocol's executable without passing an incomplete select URI.
    let association = wide("zotero");
    let verb = wide("open");
    let mut executable = vec![0u16; 32768];
    let mut length = executable.len() as u32;
    let resolved = unsafe {
        AssocQueryStringW(ASSOCF_IS_PROTOCOL, ASSOCSTR_EXECUTABLE, association.as_ptr(),
            verb.as_ptr(), executable.as_mut_ptr(), &mut length)
    };
    let fallback = wide("zotero.exe");
    let target = if resolved == 0 && length > 1 && length as usize <= executable.len() {
        &executable[..length as usize]
    } else {
        &fallback
    };
    let result = unsafe {
        ShellExecuteW(ptr::null_mut(), verb.as_ptr(), target.as_ptr(),
            ptr::null(), ptr::null(), SW_SHOWNORMAL)
    };
    if result as isize <= 32 {
        Err("无法打开 Zotero，请确认已安装 Zotero，或从开始菜单打开。".into())
    } else {
        Ok(())
    }
}
struct Owner {
    app: AppHandle,
    taskbar: u32,
}

unsafe fn tray_icon(hwnd: HWND, action: u32, notification: Option<&str>) {
    let mut data = std::mem::zeroed::<NOTIFYICONDATAW>();
    data.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    data.hWnd = hwnd;
    data.uID = 1;
    data.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    data.uCallbackMessage = TRAY_EVENT;
    data.hIcon = LoadIconW(GetModuleHandleW(ptr::null()), 32512usize as _);
    let tip = wide(PRODUCT_NAME);
    data.szTip[..tip.len()].copy_from_slice(&tip);
    if let Some(message) = notification {
        data.uFlags |= NIF_INFO;
        data.dwInfoFlags = NIIF_WARNING | NIIF_RESPECT_QUIET_TIME;
        let info: Vec<_> = message.encode_utf16().take(254).collect();
        data.szInfo[..info.len()].copy_from_slice(&info);
        let title = wide("翻译服务需要处理");
        data.szInfoTitle[..title.len()].copy_from_slice(&title);
    }
    Shell_NotifyIconW(action, &data);
}
unsafe extern "system" fn procedure(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if message == WM_NCCREATE {
        SetWindowLongPtrW(
            hwnd,
            GWLP_USERDATA,
            (*(l as *const CREATESTRUCTW)).lpCreateParams as isize,
        );
    }
    let owner = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const Owner;
    if !owner.is_null() {
        let owner = &*owner;
        if message == owner.taskbar {
            tray_icon(hwnd, NIM_ADD, None);
            return 0;
        }
        if message == ACTIVATE || message == TRAY_EVENT && l as u32 == WM_LBUTTONUP {
            let _ = owner.app.emit("command", "open");
            return 0;
        }
        if message == WM_SETTINGCHANGE || message == WM_SYSCOLORCHANGE {
            let _ = owner.app.emit("system-appearance", Value::Null);
        }
        if message == TRAY_EVENT && l as u32 == WM_RBUTTONUP {
            let menu = CreatePopupMenu();
            for (id, title) in [
                (1, "打开控制中心"),
                (2, "启动服务"),
                (3, "停止服务"),
                (4, "检查更新"),
                (5, "退出控制中心"),
            ] {
                AppendMenuW(menu, MF_STRING, id, wide(title).as_ptr());
            }
            let mut point = std::mem::zeroed();
            GetCursorPos(&mut point);
            SetForegroundWindow(hwnd);
            let id = TrackPopupMenu(
                menu,
                TPM_RETURNCMD | TPM_RIGHTBUTTON,
                point.x,
                point.y,
                0,
                hwnd,
                ptr::null(),
            );
            DestroyMenu(menu);
            PostMessageW(hwnd, WM_NULL, 0, 0);
            if let Some(action) = match id {
                1 => Some("open"),
                2 => Some("start"),
                3 => Some("stop"),
                4 => Some("check"),
                5 => Some("quit"),
                _ => None,
            } {
                let _ = owner.app.emit("command", action);
            }
            return 0;
        }
    }
    if message == WM_CLOSE {
        DestroyWindow(hwnd);
        return 0;
    }
    if message == WM_DESTROY {
        tray_icon(hwnd, NIM_DELETE, None);
        PostQuitMessage(0);
        return 0;
    }
    DefWindowProcW(hwnd, message, w, l)
}

pub struct Tray {
    mutex: HANDLE,
    window: Arc<AtomicIsize>,
    thread: Option<thread::JoinHandle<()>>,
    pub duplicate: bool,
}
impl Tray {
    pub fn new(app: AppHandle, root: &Path) -> Result<Self, String> {
        use sha2::Digest;
        let mut identity = format!(
            "zotero-pdf2zh-pro-{:x}",
            sha2::Sha256::digest(normalize_path(root).as_bytes())
        );
        if has_argument("--setup") {
            identity.push_str("-setup");
        }
        unsafe {
            let mutex = CreateMutexW(ptr::null(), 0, wide(&format!("Local\\{identity}")).as_ptr());
            if mutex.is_null() {
                return Err(std::io::Error::last_os_error().to_string());
            }
            let window = Arc::new(AtomicIsize::new(0));
            if GetLastError() == ERROR_ALREADY_EXISTS {
                if show_on_start() {
                    // Allow the current instance to raise its panel for this foreground launch.
                    let hwnd = FindWindowW(wide(&identity).as_ptr(), ptr::null());
                    let mut pid = 0;
                    GetWindowThreadProcessId(hwnd, &mut pid);
                    AllowSetForegroundWindow(pid);
                    PostMessageW(hwnd, ACTIVATE, 0, 0);
                }
                return Ok(Self {
                    mutex,
                    window,
                    thread: None,
                    duplicate: true,
                });
            }
            let slot = window.clone();
            let (tx, rx) = mpsc::channel();
            let thread = thread::spawn(move || {
                let class = wide(&identity);
                let module = GetModuleHandleW(ptr::null());
                let wc = WNDCLASSW {
                    lpfnWndProc: Some(procedure),
                    hInstance: module,
                    lpszClassName: class.as_ptr(),
                    ..std::mem::zeroed()
                };
                RegisterClassW(&wc);
                let owner = Box::new(Owner {
                    app,
                    taskbar: RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()),
                });
                let hwnd = CreateWindowExW(
                    0,
                    class.as_ptr(),
                    wide("pdf2zh-owner").as_ptr(),
                    0,
                    0,
                    0,
                    0,
                    0,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    module,
                    &*owner as *const _ as _,
                );
                slot.store(hwnd as isize, Ordering::Release);
                let _ = tx.send(!hwnd.is_null());
                if hwnd.is_null() {
                    return;
                }
                tray_icon(hwnd, NIM_ADD, None);
                let mut message = std::mem::zeroed();
                while GetMessageW(&mut message, ptr::null_mut(), 0, 0) > 0 {
                    TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
            });
            if !rx.recv().unwrap_or(false) {
                CloseHandle(mutex);
                let _ = thread.join();
                return Err("无法创建托盘".into());
            }
            Ok(Self {
                mutex,
                window,
                thread: Some(thread),
                duplicate: false,
            })
        }
    }
    pub fn notify(&self, message: &str) {
        unsafe {
            tray_icon(
                self.window.load(Ordering::Acquire) as HWND,
                NIM_MODIFY,
                Some(message),
            );
        }
    }
}
impl Drop for Tray {
    fn drop(&mut self) {
        unsafe {
            let hwnd = self.window.load(Ordering::Acquire) as HWND;
            if !hwnd.is_null() {
                PostMessageW(hwnd, WM_CLOSE, 0, 0);
            }
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
            CloseHandle(self.mutex);
        }
    }
}

pub fn sync_window_icons(window: &slint::winit_030::winit::window::Window) {
    use slint::winit_030::winit::raw_window_handle::{HasWindowHandle, RawWindowHandle};
    use windows_sys::Win32::UI::HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi};
    let Ok(handle) = window.window_handle() else {
        return;
    };
    let RawWindowHandle::Win32(handle) = handle.as_raw() else {
        return;
    };
    unsafe {
        let hwnd = handle.hwnd.get() as HWND;
        let dpi = GetDpiForWindow(hwnd).max(96);
        let property = wide("pdf2zh-icon-dpi");
        if GetPropW(hwnd, property.as_ptr()) as usize == dpi as usize {
            return;
        }
        for (kind, x, y) in [
            (ICON_BIG, SM_CXICON, SM_CYICON),
            (ICON_SMALL, SM_CXSMICON, SM_CYSMICON),
        ] {
            let icon = LoadImageW(
                GetModuleHandleW(ptr::null()),
                32512usize as _,
                IMAGE_ICON,
                GetSystemMetricsForDpi(x, dpi),
                GetSystemMetricsForDpi(y, dpi),
                LR_SHARED,
            );
            SendMessageW(hwnd, WM_SETICON, kind as usize, icon as isize);
        }
        SetPropW(hwnd, property.as_ptr(), dpi as usize as HANDLE);
    }
}
pub fn show_on_start() -> bool {
    !has_argument("--autostart")
        && (!has_argument("--post-install") || has_argument("--show-panel"))
}
pub fn wait_parent() -> bool {
    let Some(pid) = env::args().find_map(|a| {
        a.strip_prefix("--wait-parent=")
            .and_then(|p| p.parse::<u32>().ok())
    }) else {
        return true;
    };
    unsafe {
        let process = OpenProcess(0x0010_0000, 0, pid);
        if process.is_null() {
            return true;
        }
        let result = WaitForSingleObject(process, 20_000);
        CloseHandle(process);
        result != WAIT_TIMEOUT
    }
}
pub fn message(error: &str) {
    unsafe {
        MessageBoxW(
            ptr::null_mut(),
            wide(error).as_ptr(),
            wide(PRODUCT_NAME).as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}
pub fn choose_folder() -> Option<PathBuf> {
    unsafe {
        let result = CoInitializeEx(ptr::null(), COINIT_APARTMENTTHREADED as u32);
        let mut display = [0u16; 260];
        let title = wide("选择父目录，程序将建立 zotero-pdf2zh-pro 子目录");
        let info = BROWSEINFOW {
            pszDisplayName: display.as_mut_ptr(),
            lpszTitle: title.as_ptr(),
            ulFlags: BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE,
            ..std::mem::zeroed()
        };
        let pidl = SHBrowseForFolderW(&info);
        let value = if pidl.is_null() {
            None
        } else {
            let mut path = [0u16; 260];
            let valid = SHGetPathFromIDListW(pidl, path.as_mut_ptr());
            CoTaskMemFree(pidl as _);
            (valid != 0).then(|| {
                PathBuf::from(String::from_utf16_lossy(
                    &path[..path.iter().position(|c| *c == 0).unwrap_or(path.len())],
                ))
            })
        };
        if result >= 0 {
            CoUninitialize();
        }
        value
    }
}
pub fn copy(value: &str) -> Result<(), String> {
    unsafe {
        // Use a real owner rather than NULL: SetClipboardData needs ownership after EmptyClipboard.
        let hwnd = GetActiveWindow();
        if OpenClipboard(hwnd) == 0 {
            return Err("剪贴板暂时不可用，请重试。".into());
        }
        let value = wide(value);
        let memory = GlobalAlloc(GMEM_MOVEABLE, value.len() * 2);
        let result = if memory.is_null() {
            Err("无法分配剪贴板内存".into())
        } else {
            let target = GlobalLock(memory);
            if target.is_null() {
                GlobalFree(memory);
                Err("无法写入剪贴板".into())
            } else {
                ptr::copy_nonoverlapping(value.as_ptr(), target as *mut u16, value.len());
                GlobalUnlock(memory);
                if EmptyClipboard() == 0 || SetClipboardData(13, memory).is_null() {
                    GlobalFree(memory);
                    Err("无法复制，请重试。".into())
                } else {
                    Ok(())
                }
            }
        };
        CloseClipboard();
        result
    }
}
pub fn high_contrast() -> Option<(slint::Color, slint::Color)> {
    unsafe {
        let mut value = HIGHCONTRASTW {
            cbSize: std::mem::size_of::<HIGHCONTRASTW>() as u32,
            ..std::mem::zeroed()
        };
        if SystemParametersInfoW(
            SPI_GETHIGHCONTRAST,
            value.cbSize,
            &mut value as *mut _ as _,
            0,
        ) == 0
            || value.dwFlags & HCF_HIGHCONTRASTON == 0
        {
            return None;
        }
        let color = |id| {
            let c = GetSysColor(id);
            slint::Color::from_rgb_u8(c as u8, (c >> 8) as u8, (c >> 16) as u8)
        };
        Some((color(COLOR_WINDOW), color(COLOR_WINDOWTEXT)))
    }
}
pub fn animations_enabled() -> bool {
    unsafe {
        let mut value = 1i32;
        SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &mut value as *mut _ as _, 0);
        value != 0
    }
}
pub fn text_scale() -> f32 {
    winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER)
        .open_subkey("Software\\Microsoft\\Accessibility")
        .ok()
        .and_then(|key| key.get_value::<u32, _>("TextScaleFactor").ok())
        .unwrap_or(100)
        .clamp(100, 225) as f32
        / 100.0
}
