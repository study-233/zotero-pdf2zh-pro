//! Optional ConPTY transport. Failure before spawning may fall back to pipes;
//! after spawning the command is never started a second time.
use std::{ffi::OsStr, os::windows::ffi::OsStrExt, ptr, sync::mpsc, time::Duration};
use windows_sys::Win32::{Foundation::*, Storage::FileSystem::ReadFile, System::{Console::*, LibraryLoader::*, Pipes::CreatePipe, Threading::*}};

struct Handle(HANDLE);
impl Drop for Handle { fn drop(&mut self) { unsafe { CloseHandle(self.0); } } }
type Create = unsafe extern "system" fn(COORD, HANDLE, HANDLE, u32, *mut HPCON) -> i32;
type Close = unsafe extern "system" fn(HPCON);
struct Console(HPCON, Close);
impl Drop for Console { fn drop(&mut self) { unsafe { (self.1)(self.0); } } }
struct Attributes(Vec<usize>);
impl Drop for Attributes { fn drop(&mut self) { unsafe { DeleteProcThreadAttributeList(self.0.as_mut_ptr().cast()); } } }
fn wide(s: &OsStr) -> Vec<u16> { s.encode_wide().chain(Some(0)).collect() }

pub fn quote(s: &str) -> String {
    let mut out = String::from("\"");
    let mut slashes = 0;
    for c in s.chars() {
        if c == '\\' { slashes += 1; continue; }
        out.extend(std::iter::repeat_n('\\', if c == '"' { slashes * 2 + 1 } else { slashes }));
        slashes = 0; out.push(c);
    }
    out.extend(std::iter::repeat_n('\\', slashes * 2)); out.push('"'); out
}

pub fn run(executable: &str, args: &[String], mut consume: impl FnMut(&[u8])) -> Result<i32, String> {
    if crate::cancellation::requested() { return Ok(crate::cancellation::EXIT_CODE); }
    let job = crate::cancellation::UvJob::new()?;
    unsafe {
        let module = GetModuleHandleW(wide(OsStr::new("kernel32.dll")).as_ptr());
        let create: Create = std::mem::transmute(GetProcAddress(module, c"CreatePseudoConsole".as_ptr().cast()).ok_or("ConPTY unavailable")?);
        let close: Close = std::mem::transmute(GetProcAddress(module, c"ClosePseudoConsole".as_ptr().cast()).ok_or("ConPTY unavailable")?);
        let pipe = || -> Result<(Handle, Handle), String> {
            let (mut read, mut write) = (ptr::null_mut(), ptr::null_mut());
            if CreatePipe(&mut read, &mut write, ptr::null(), 0) == 0 { return Err(std::io::Error::last_os_error().to_string()); }
            Ok((Handle(read), Handle(write)))
        };
        let (input_read, _input_write) = pipe()?;
        let (output_read, output_write) = pipe()?;
        let mut hpc = 0;
        // Enough rows for uv's concurrent downloads; no cursor-position handshake.
        if create(COORD { X: 220, Y: 240 }, input_read.0, output_write.0, 0, &mut hpc) < 0 { return Err("CreatePseudoConsole failed".into()); }
        let console = Console(hpc, close);
        let mut size = 0;
        InitializeProcThreadAttributeList(ptr::null_mut(), 1, 0, &mut size);
        let mut buffer = vec![0usize; size.div_ceil(std::mem::size_of::<usize>())];
        if InitializeProcThreadAttributeList(buffer.as_mut_ptr().cast(), 1, 0, &mut size) == 0 { return Err("InitializeProcThreadAttributeList failed".into()); }
        let mut attributes = Attributes(buffer);
        if UpdateProcThreadAttribute(attributes.0.as_mut_ptr().cast(), 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE as usize,
            hpc as *const _, std::mem::size_of::<HPCON>(), ptr::null_mut(), ptr::null()) == 0 { return Err("UpdateProcThreadAttribute failed".into()); }
        let mut startup: STARTUPINFOEXW = std::mem::zeroed();
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        // Explicit null standard handles prevent uv from inheriting this helper's
        // redirected pipes instead of attaching to ConPTY (Microsoft Terminal uses this too).
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.lpAttributeList = attributes.0.as_mut_ptr().cast();
        let command = std::iter::once(executable).chain(args.iter().map(String::as_str)).map(quote).collect::<Vec<_>>().join(" ");
        let mut command = wide(OsStr::new(&command));
        let executable = wide(OsStr::new(executable));
        let mut info: PROCESS_INFORMATION = std::mem::zeroed();
        if CreateProcessW(executable.as_ptr(), command.as_mut_ptr(), ptr::null(), ptr::null(), 0,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED, ptr::null(), ptr::null(), &startup.StartupInfo, &mut info) == 0 { return Err(std::io::Error::last_os_error().to_string()); }
        let process = Handle(info.hProcess); let _thread = Handle(info.hThread);
        if job.assign(process.0).is_err() || ResumeThread(info.hThread) == u32::MAX {
            TerminateProcess(process.0, 1);
            // A process was already created: do not retry the command through pipes.
            return Ok(1);
        }
        drop(input_read); drop(output_write);
        let (tx, rx) = mpsc::channel();
        let read_value = output_read.0 as usize;
        // Drain while ClosePseudoConsole runs: closing can write a final terminal frame.
        let reader = std::thread::spawn(move || {
            let _owner = Handle(read_value as HANDLE);
            loop {
                let mut bytes = vec![0u8; 16384]; let mut count = 0;
                if ReadFile(_owner.0, bytes.as_mut_ptr(), bytes.len() as u32, &mut count, ptr::null_mut()) == 0 || count == 0 { break; }
                bytes.truncate(count as usize); if tx.send(bytes).is_err() { break; }
            }
        });
        std::mem::forget(output_read);
        let mut code = 259;
        loop {
            match rx.recv_timeout(Duration::from_millis(50)) { Ok(bytes) => consume(&bytes), Err(_) => consume(&[]) }
            if crate::cancellation::requested() { job.cancel(); }
            if WaitForSingleObject(process.0, 0) == WAIT_OBJECT_0 { GetExitCodeProcess(process.0, &mut code); break; }
        }
        drop(console);
        for bytes in rx { consume(&bytes); }
        let _ = reader.join();
        Ok(code as i32)
    }
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn quotes_windows_paths_and_trailing_slashes() {
        assert_eq!(quote("D:\\中文 空格\\"), "\"D:\\中文 空格\\\\\"");
        assert_eq!(quote("a\"b"), "\"a\\\"b\"");
    }
}
