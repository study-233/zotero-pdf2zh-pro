//! A per-install signal shared with PowerShell and its short-lived uv worker.
use std::{env, fs, path::PathBuf, time::{SystemTime, UNIX_EPOCH}};

pub const ENV: &str = "PDF2ZH_INSTALL_CANCEL_FILE";
pub const EXIT_CODE: i32 = 1223;
pub const ERROR: &str = "安装已取消";

pub struct Signal { path: PathBuf }
impl Signal {
    pub fn new() -> Self {
        let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        Self { path: env::temp_dir().join(format!("pdf2zh-cancel-{}-{stamp}", std::process::id())) }
    }
    pub fn path(&self) -> &std::path::Path { &self.path }
    pub fn request(&self) -> std::io::Result<()> { fs::write(&self.path, b"cancel") }
}
impl Drop for Signal { fn drop(&mut self) { let _ = fs::remove_file(&self.path); } }

pub fn requested() -> bool {
    env::var_os(ENV).is_some_and(|path| PathBuf::from(path).is_file())
}

// uv and any build children belong to this worker only. Closing the job also
// cleans up descendants after cancellation; the installed service is never in it.
pub struct UvJob(windows_sys::Win32::Foundation::HANDLE);
impl UvJob {
    pub fn new() -> Result<Self, String> {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
            let job = Self(handle);
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _, std::mem::size_of_val(&limits) as u32) == 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            Ok(job)
        }
    }
    pub fn assign(&self, process: windows_sys::Win32::Foundation::HANDLE) -> Result<(), String> {
        if unsafe { windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(self.0, process) } == 0 {
            Err(std::io::Error::last_os_error().to_string())
        } else { Ok(()) }
    }
    pub fn cancel(&self) {
        unsafe { windows_sys::Win32::System::JobObjects::TerminateJobObject(self.0, EXIT_CODE as u32); }
    }
}
impl Drop for UvJob {
    fn drop(&mut self) { unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0); } }
}
