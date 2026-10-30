//! A small native launcher. The build script appends a verified installation ZIP.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use sha2::{Digest, Sha256};
use std::{collections::HashSet, env, fs::{self, File}, io::{Cursor, Read, Seek, SeekFrom},
    os::windows::{fs::MetadataExt, process::CommandExt}, path::{Path, PathBuf}, process::{Command, Stdio}, ptr,
    time::{SystemTime, UNIX_EPOCH}};
use windows_sys::Win32::{Foundation::*, Graphics::Gdi::*, System::LibraryLoader::*, UI::{Controls::*, HiDpi::*, WindowsAndMessaging::*}};

const MAGIC: &[u8; 16] = b"PDF2ZH_SETUP_V1\0";
const FOOTER_SIZE: u64 = 56;
const MAX_PAYLOAD: u64 = 256 * 1024 * 1024;
const PRODUCT: &str = "zotero-pdf2zh-pro";
fn wide(value: &str) -> Vec<u16> { value.encode_utf16().chain(Some(0)).collect() }

fn load_payload(path: &Path) -> Result<Vec<u8>, String> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let size = file.metadata().map_err(|e| e.to_string())?.len();
    if size < FOOTER_SIZE { return Err("安装包不完整，请重新下载。".into()); }
    file.seek(SeekFrom::End(-(FOOTER_SIZE as i64))).map_err(|e| e.to_string())?;
    let mut footer = [0u8; FOOTER_SIZE as usize]; file.read_exact(&mut footer).map_err(|e| e.to_string())?;
    let length = u64::from_le_bytes(footer[..8].try_into().unwrap());
    if &footer[40..] != MAGIC || length > MAX_PAYLOAD || length > size - FOOTER_SIZE {
        return Err("安装包缺少有效的内置文件，请重新下载。".into());
    }
    file.seek(SeekFrom::Start(size - FOOTER_SIZE - length)).map_err(|e| e.to_string())?;
    let mut payload = vec![0; length as usize]; file.read_exact(&mut payload).map_err(|e| e.to_string())?;
    if Sha256::digest(&payload)[..] != footer[8..40] { return Err("安装包校验失败，请重新下载。".into()); }
    Ok(payload)
}

fn extract(payload: Vec<u8>, directory: &Path, mut tick: impl FnMut(u64, u64)) -> Result<(), String> {
    let mut zip = zip::ZipArchive::new(Cursor::new(payload)).map_err(|e| e.to_string())?;
    zip.validate_header_consistency().map_err(|e| e.to_string())?;
    let mut names = HashSet::new(); let mut total = 0u64;
    for i in 0..zip.len() {
        let file = zip.by_index(i).map_err(|e| e.to_string())?;
        let name = file.name().map_err(|e| e.to_string())?;
        if name.is_empty() || !name.is_ascii() || name.contains(['/', '\\', ':']) ||
            name.starts_with('.') || name.ends_with(['.', ' ']) || file.is_dir() ||
            file.unix_mode().is_some_and(|mode| mode & 0o170000 == 0o120000) ||
            !names.insert(name.to_ascii_lowercase()) {
            return Err("安装包包含无效或重复的文件名。".into());
        }
        total = total.checked_add(file.size()).ok_or("安装包大小无效")?;
        if total > 512 * 1024 * 1024 { return Err("安装文件大小超过限制。".into()); }
    }
    for required in ["zotero-pdf2zh-pro.exe", "install.ps1", "windows-dependencies.json", "windows-requirements.txt"] {
        if !names.contains(required) { return Err(format!("安装包缺少 {required}")); }
    }
    let mut done = 0u64;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
        let name = entry.name().map_err(|e| e.to_string())?;
        let mut output = fs::OpenOptions::new().write(true).create_new(true).open(directory.join(name.as_ref())).map_err(|e| e.to_string())?;
        let bytes = std::io::copy(&mut entry, &mut output).map_err(|e| e.to_string())?;
        if bytes != entry.size() { return Err("安装文件解包不完整。".into()); }
        done += bytes; tick(done, total);
    }
    Ok(())
}

// Only direct files created by this process are removed. Never recurse through a link.
fn cleanup(directory: &Path, parent: &Path) -> Result<(), String> {
    if directory.parent() != Some(parent) || !directory.file_name().unwrap_or_default().to_string_lossy().starts_with("setup-") {
        return Err("临时目录不属于本次安装。".into());
    }
    if fs::symlink_metadata(directory).map_err(|e| e.to_string())?.file_attributes() & 0x400 != 0 {
        return Err("临时目录被替换为链接。".into());
    }
    for entry in fs::read_dir(directory).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|e| e.to_string())?;
        if !metadata.is_file() || metadata.file_attributes() & 0x400 != 0 { return Err("临时目录中存在非安装文件。".into()); }
        fs::remove_file(entry.path()).map_err(|e| e.to_string())?;
    }
    fs::remove_dir(directory).map_err(|e| e.to_string())
}

unsafe fn pump() { let mut msg = std::mem::zeroed(); while PeekMessageW(&mut msg, ptr::null_mut(), 0, 0, PM_REMOVE) != 0 { TranslateMessage(&msg); DispatchMessageW(&msg); } }
unsafe extern "system" fn procedure(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if message == WM_CLOSE { return 0; } // Extraction lasts a few seconds; the wizard supplies cancellation.
    DefWindowProcW(hwnd, message, w, l)
}

unsafe fn run() -> Result<(), String> {
    let executable = env::current_exe().map_err(|e| e.to_string())?;
    let controls = INITCOMMONCONTROLSEX { dwSize: std::mem::size_of::<INITCOMMONCONTROLSEX>() as u32, dwICC: ICC_PROGRESS_CLASS };
    InitCommonControlsEx(&controls);
    let class = wide("pdf2zh-setup-loader");
    let module = GetModuleHandleW(ptr::null());
    let wc = WNDCLASSW { lpfnWndProc: Some(procedure), hInstance: module, lpszClassName: class.as_ptr(),
        hCursor: LoadCursorW(ptr::null_mut(), IDC_ARROW), hbrBackground: (COLOR_WINDOW + 1) as _, ..std::mem::zeroed() };
    RegisterClassW(&wc);
    let dpi = GetDpiForSystem().max(96) as i32; let scale = |n| n * dpi / 96;
    let window = CreateWindowExW(0, class.as_ptr(), wide("zotero-pdf2zh-pro 安装").as_ptr(), WS_CAPTION | WS_SYSMENU,
        CW_USEDEFAULT, CW_USEDEFAULT, scale(390), scale(148), ptr::null_mut(), ptr::null_mut(), module, ptr::null());
    SendMessageW(window, WM_SETICON, ICON_BIG as usize, LoadIconW(module, 32512usize as _) as isize);
    let label = CreateWindowExW(0, wide("STATIC").as_ptr(), wide("正在校验并准备安装文件…").as_ptr(), WS_CHILD | WS_VISIBLE,
        scale(20), scale(24), scale(344), scale(25), window, ptr::null_mut(), module, ptr::null());
    let font = windows_sys::Win32::Graphics::Gdi::GetStockObject(windows_sys::Win32::Graphics::Gdi::DEFAULT_GUI_FONT);
    SendMessageW(label, WM_SETFONT, font as usize, 1);
    let bar = CreateWindowExW(0, wide("msctls_progress32").as_ptr(), ptr::null(), WS_CHILD | WS_VISIBLE,
        scale(20), scale(64), scale(344), scale(12), window, ptr::null_mut(), module, ptr::null());
    ShowWindow(window, SW_SHOW); UpdateWindow(window); pump();
    let result = (|| {
        let payload = load_payload(&executable)?;
        let parent = env::var_os("PDF2ZH_SETUP_STAGING_ROOT").map(PathBuf::from).unwrap_or_else(|| env::temp_dir().join(PRODUCT).join("installers"));
        fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
        let parent = fs::canonicalize(parent).map_err(|e| e.to_string())?;
        let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
        let directory = parent.join(format!("setup-{}-{stamp}", std::process::id()));
        fs::create_dir(&directory).map_err(|e| e.to_string())?;
        let work: Result<(), String> = (|| {
            extract(payload, &directory, |done, total| { SendMessageW(bar, PBM_SETPOS, (done * 100 / total.max(1)) as usize, 0); pump(); })?;
            let mut command = Command::new(directory.join("zotero-pdf2zh-pro.exe"));
            let profile = directory.join("setup-profile.json");
            if profile.is_file() {
                let value: serde_json::Value = serde_json::from_slice(&fs::read(&profile).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
                if value["candidate"] != true {return Err("候选安装配置无效。".into());}
                let local = PathBuf::from(env::var_os("LOCALAPPDATA").ok_or("无法确定当前用户目录")?);
                command.env("PDF2ZH_WINDOWS_APP_ROOT", local.join("zotero-pdf2zh-pro-native-candidate"))
                    .env("PDF2ZH_ISOLATED_CANDIDATE", "1")
                    .env("PDF2ZH_WINDOWS_REGISTRY_KEY", "HKCU:\\Software\\pdf2zh-native-candidate")
                    .env("PDF2ZH_WINDOWS_PORT", "19892")
                    .env("PDF2ZH_WINDOWS_START_MENU_DIR", local.join("zotero-pdf2zh-pro-native-candidate-shortcuts"));
            }
            command.arg("--setup").args(env::args_os().skip(1)).creation_flags(0x0800_0000)
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
            let mut child = command.spawn().map_err(|e| format!("无法打开安装向导：{e}"))?;
            ShowWindow(window, SW_HIDE);
            let status = child.wait().map_err(|e| e.to_string())?;
            if !status.success() { return Err("安装向导意外退出，请查看控制中心日志后重试。".into()); }
            Ok(())
        })();
        let removed = cleanup(&directory, &parent);
        work?; removed?; Ok(())
    })();
    DestroyWindow(window); result
}

fn main() { unsafe { if let Err(error) = run() { MessageBoxW(ptr::null_mut(), wide(&error).as_ptr(), wide("安装未完成").as_ptr(), MB_ICONERROR); std::process::exit(1); } } }

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[test]
    fn damaged_payload_is_rejected_before_extraction() {
        let path = env::temp_dir().join(format!("pdf2zh-loader-test-{}.exe", std::process::id()));
        let data = b"payload"; let mut file = File::create(&path).unwrap();
        file.write_all(b"MZstub").unwrap(); file.write_all(data).unwrap();
        file.write_all(&(data.len() as u64).to_le_bytes()).unwrap(); file.write_all(&Sha256::digest(data)).unwrap(); file.write_all(MAGIC).unwrap(); drop(file);
        assert_eq!(load_payload(&path).unwrap(), data);
        let mut corrupted = fs::read(&path).unwrap(); corrupted[6] ^= 1; fs::write(&path, corrupted).unwrap();
        assert!(load_payload(&path).is_err()); fs::remove_file(path).unwrap();
    }
    #[test]
    fn unsafe_names_are_rejected_before_writing_any_file() {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        zip.start_file("../outside", zip::write::SimpleFileOptions::default()).unwrap();
        zip.write_all(b"bad").unwrap();
        let data = zip.finish().unwrap().into_inner();
        assert!(extract(data, Path::new("nonexistent-test-directory"), |_, _| {}).is_err());
    }

    #[test]
    fn inconsistent_headers_are_rejected_before_writing_any_file() {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for name in ["install.ps1", "zotero-pdf2zh-pro.exe", "windows-dependencies.json", "windows-requirements.txt"] {
            zip.start_file(name, zip::write::SimpleFileOptions::default()).unwrap();
            zip.write_all(b"fixture").unwrap();
        }
        let mut data = zip.finish().unwrap().into_inner();
        data[30..41].copy_from_slice(b"outside.ps1");
        let directory = env::temp_dir().join(format!("pdf2zh-loader-headers-{}", std::process::id()));
        fs::create_dir(&directory).unwrap();
        let result = extract(data, &directory, |_, _| {});
        let files = fs::read_dir(&directory).unwrap().count();
        fs::remove_dir_all(directory).unwrap();
        assert!(result.is_err());
        assert_eq!(files, 0);
    }
}
