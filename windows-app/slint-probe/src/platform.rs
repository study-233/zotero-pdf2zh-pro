//! An isolated tray and instance signal. Never touches product registration or services.
use crate::ProbeWindow;
use slint::ComponentHandle;
use std::{
    ptr,
    sync::{
        Arc,
        atomic::{AtomicIsize, Ordering},
        mpsc,
    },
    thread,
};
use windows_sys::Win32::{
    Foundation::*,
    System::{LibraryLoader::*, Threading::*},
    UI::{Shell::*, WindowsAndMessaging::*},
};

const ACTIVATE: u32 = WM_APP + 1;
const TRAY: u32 = WM_APP + 2;
const CLASS: &str = "pdf2zh-slint-probe-owner";
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

struct Owner {
    ui: slint::Weak<ProbeWindow>,
    taskbar_created: u32,
}

unsafe fn icon(hwnd: HWND, operation: u32) {
    // The owner thread supplies its live window; all strings remain live for each call.
    unsafe {
        let mut value = std::mem::zeroed::<NOTIFYICONDATAW>();
        value.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        value.hWnd = hwnd;
        value.uID = 1;
        value.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
        value.uCallbackMessage = TRAY;
        value.hIcon = LoadIconW(GetModuleHandleW(ptr::null()), 32512usize as _);
        let label = wide("pdf2zh · Slint 验证版");
        value.szTip[..label.len()].copy_from_slice(&label);
        Shell_NotifyIconW(operation, &value);
    }
}

unsafe extern "system" fn procedure(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    // Windows dispatches on the owner thread. Owner outlives its message loop.
    unsafe {
        if message == WM_NCCREATE {
            let create = &*(l as *const CREATESTRUCTW);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, create.lpCreateParams as isize);
        }
        let owner = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const Owner;
        if !owner.is_null() {
            if message == (*owner).taskbar_created {
                icon(hwnd, NIM_ADD);
                return 0;
            }
            if message == ACTIVATE || message == TRAY && l as u32 == WM_LBUTTONUP {
                let _ = (*owner).ui.upgrade_in_event_loop(|ui| {
                    let _ = ui.show();
                });
                return 0;
            }
            if message == TRAY && l as u32 == WM_RBUTTONUP {
                let menu = CreatePopupMenu();
                AppendMenuW(menu, MF_STRING, 1, wide("打开验证窗口").as_ptr());
                AppendMenuW(menu, MF_STRING, 2, wide("结束验证").as_ptr());
                let mut point = std::mem::zeroed();
                GetCursorPos(&mut point);
                SetForegroundWindow(hwnd);
                let action = TrackPopupMenu(
                    menu,
                    TPM_RETURNCMD | TPM_RIGHTBUTTON,
                    point.x,
                    point.y,
                    0,
                    hwnd,
                    ptr::null(),
                );
                DestroyMenu(menu);
                if action == 1 {
                    PostMessageW(hwnd, ACTIVATE, 0, 0);
                }
                if action == 2 {
                    let _ = slint::invoke_from_event_loop(|| {
                        let _ = slint::quit_event_loop();
                    });
                }
                return 0;
            }
        }
        if message == WM_CLOSE {
            DestroyWindow(hwnd);
            return 0;
        }
        if message == WM_DESTROY {
            icon(hwnd, NIM_DELETE);
            PostQuitMessage(0);
            return 0;
        }
        DefWindowProcW(hwnd, message, w, l)
    }
}

pub struct Tray {
    mutex: HANDLE,
    owner: Arc<AtomicIsize>,
    thread: Option<thread::JoinHandle<()>>,
    duplicate: bool,
}
impl Tray {
    pub fn new(ui: slint::Weak<ProbeWindow>) -> Result<Self, String> {
        unsafe {
            let mutex = CreateMutexW(
                ptr::null(),
                0,
                wide("Local\\pdf2zh-slint-framework-probe").as_ptr(),
            );
            if mutex.is_null() {
                return Err("无法创建验证版单实例锁".into());
            }
            let owner = Arc::new(AtomicIsize::new(0));
            if GetLastError() == ERROR_ALREADY_EXISTS {
                let window = FindWindowW(wide(CLASS).as_ptr(), ptr::null());
                PostMessageW(window, ACTIVATE, 0, 0);
                return Ok(Self {
                    mutex,
                    owner,
                    thread: None,
                    duplicate: true,
                });
            }
            let slot = owner.clone();
            let (tx, rx) = mpsc::channel();
            let thread = thread::spawn(move || {
                let class = wide(CLASS);
                let module = GetModuleHandleW(ptr::null());
                let wc = WNDCLASSW {
                    lpfnWndProc: Some(procedure),
                    hInstance: module,
                    lpszClassName: class.as_ptr(),
                    ..std::mem::zeroed()
                };
                RegisterClassW(&wc);
                let owner = Box::new(Owner {
                    ui,
                    taskbar_created: RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()),
                });
                let window = CreateWindowExW(
                    0,
                    class.as_ptr(),
                    wide(CLASS).as_ptr(),
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
                slot.store(window as isize, Ordering::Release);
                let _ = tx.send(!window.is_null());
                if window.is_null() {
                    return;
                }
                icon(window, NIM_ADD);
                let mut message = std::mem::zeroed();
                while GetMessageW(&mut message, ptr::null_mut(), 0, 0) > 0 {
                    TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
            });
            if !rx.recv().map_err(|e| e.to_string())? {
                CloseHandle(mutex);
                return Err("无法创建验证版托盘".into());
            }
            Ok(Self {
                mutex,
                owner,
                thread: Some(thread),
                duplicate: false,
            })
        }
    }
    pub fn is_duplicate(&self) -> bool {
        self.duplicate
    }
}
impl Drop for Tray {
    fn drop(&mut self) {
        unsafe {
            let window = self.owner.load(Ordering::Acquire) as HWND;
            if !window.is_null() {
                PostMessageW(window, WM_CLOSE, 0, 0);
            }
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
            CloseHandle(self.mutex);
        }
    }
}
