//! Byte samples come from uv 0.12.10's terminal, never an estimated percentage.
//! Newly committed uv cache archives confirm completion when uv clears a bar.
use crate::{progress::{self, FileProgress, OperationProgress}, scheduler, uv_terminal};
use serde::Deserialize;
use std::{collections::{HashMap, HashSet}, fs, io::{BufRead, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, time::{Duration, Instant}};
use std::os::windows::process::CommandExt;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Request { executable: String, arguments: Vec<String>, root: PathBuf, uv_version: String }

fn quantity(text: &str) -> Option<u64> {
    let text = text.trim();
    let end = text.find(|c: char| !c.is_ascii_digit() && c != '.')?;
    let value: f64 = text[..end].parse().ok()?;
    let unit = text[end..].trim();
    let multiplier = match unit { "B" => 1.0, "KiB" => 1024.0, "MiB" => 1048576.0, "GiB" => 1073741824.0, _ => return None };
    (value.is_finite() && value >= 0.0).then_some((value * multiplier).round() as u64)
}

fn download(line: &str) -> Option<(String, u64, u64)> {
    let (left, total) = line.rsplit_once('/')?;
    let total = quantity(total)?;
    // The reporter pads names and bars; locate the final byte quantity, not the name.
    for (position, _) in left.char_indices().rev() {
        if position > 0 && !left[..position].ends_with(char::is_whitespace) { continue; }
        if let Some(received) = quantity(&left[position..]) {
            let name = left[..position].trim().trim_end_matches(['-', '━', '─', ' ']).trim();
            let bare = name.trim_end_matches(" (download)").trim_end_matches(" (extract)");
            if !bare.is_empty() && bare.chars().all(|c| c.is_alphanumeric() || "-_.".contains(c)) { return Some((name.into(), received, total)); }
        }
    }
    None
}

fn item_count(line: &str) -> Option<(u64, u64)> {
    let (_, value) = line.split_once('[')?;
    let (value, _) = value.split_once(']')?;
    let (done, total) = value.split_once('/')?;
    Some((done.trim().parse().ok()?, total.trim().parse().ok()?))
}

// uv 0.12.10 atomically moves finished wheels from .tmp* into archive-v0.
// Observe only new archives in this installation's private cache: an old cached
// version, a disappearing terminal row, and rounded 100% are not completion.
struct CacheCompletions {
    directory: PathBuf,
    seen: HashSet<PathBuf>,
    last_scan: Instant,
}
impl CacheCompletions {
    fn new(cache: PathBuf) -> Option<Self> {
        let directory = cache.join("archive-v0");
        let seen = match fs::read_dir(&directory) {
            Ok(entries) => entries.filter_map(Result::ok).map(|e| e.path()).collect(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => HashSet::new(),
            Err(_) => return None,
        };
        Some(Self { directory, seen, last_scan: Instant::now() })
    }
    fn poll(&mut self, tracker: &mut Tracker) {
        if self.last_scan.elapsed() < Duration::from_secs(1) { return; }
        self.last_scan = Instant::now();
        let Ok(entries) = fs::read_dir(&self.directory) else { return; };
        for archive in entries.filter_map(Result::ok) {
            if self.seen.contains(&archive.path()) { continue; }
            let Ok(contents) = fs::read_dir(archive.path()) else { continue; };
            for entry in contents.filter_map(Result::ok) {
                if !entry.file_name().to_string_lossy().ends_with(".dist-info") { continue; }
                let Ok(metadata) = fs::File::open(entry.path().join("METADATA")) else { continue; };
                if let Some(name) = std::io::BufReader::new(metadata).lines().map_while(Result::ok)
                    .take_while(|line| !line.is_empty()).find_map(|line| line.strip_prefix("Name: ").map(str::to_owned)) {
                    // Wheel names normalize runs of '.', '_' and '-' to '-'.
                    let name = name.split(['.', '_', '-']).filter(|s| !s.is_empty()).collect::<Vec<_>>().join("-").to_ascii_lowercase();
                    if !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
                        tracker.cached(&name);
                        self.seen.insert(archive.path());
                    }
                }
            }
        }
    }
}

struct Tracker {
    value: OperationProgress,
    python: bool,
    samples: HashMap<String, (Instant, u64)>,
    seen_logs: HashSet<String>,
    observed: HashSet<String>,
    changed: bool,
    error_output: bool,
}
impl Tracker {
    fn new(value: OperationProgress) -> Self {
        let python = value.phase == "python";
        Self { value, python, samples: HashMap::new(), seen_logs: HashSet::new(), observed: HashSet::new(), changed: false, error_output: false }
    }
    fn log(&mut self, line: &str) {
        if self.seen_logs.insert(line.into()) {
            println!("{line}");
            self.value.recent_logs.push(line.into());
            if self.value.recent_logs.len() > 120 { self.value.recent_logs.remove(0); }
            self.changed = true;
        }
    }
    fn frame(&mut self, contents: &str) {
        for line in contents.lines().map(str::trim).filter(|s| !s.is_empty()) {
            if line.starts_with("error:") || line.starts_with('×') { self.error_output = true; }
            if let Some((name, received, total)) = download(line) {
                let extracting = name.ends_with("(extract)");
                let phase = if extracting { "extracting" } else if self.python { "python-download" } else { "dependency-download" };
                let id = format!("{phase}:{name}");
                let first = self.observed.insert(id.clone());
                let index = self.value.files.iter().position(|f| f.id == id).unwrap_or_else(|| {
                    self.value.files.push(FileProgress { id: id.clone(), name: name.clone(), phase: phase.into(), attempt: 1, ..Default::default() });
                    self.value.files.len() - 1
                });
                let file = &mut self.value.files[index];
                if !first && matches!(file.status.as_str(), "downloaded" | "extracted") { continue; }
                if first && !file.status.is_empty() {
                    file.attempt += 1; file.completed = 0; file.bytes_per_second = 0;
                }
                // uv creates a fresh zero-position bar when it restarts a request.
                // Other lower observations can be partial redraws and stay clamped.
                if !first && received == 0 && file.completed > 0 && file.status == "downloading" {
                    file.attempt += 1; file.completed = 0; file.bytes_per_second = 0;
                    self.samples.remove(&id); self.changed = true;
                }
                // A partial frame or redraw may show an older value. Only a new
                // uv invocation or a fresh zero bar is evidence of a new attempt.
                let received = received.min(total).max(file.completed);
                if let Some((instant, previous)) = self.samples.get_mut(&id) {
                    if received > *previous && instant.elapsed() >= Duration::from_secs(1) {
                        file.bytes_per_second = (received.saturating_sub(*previous) as f64 / instant.elapsed().as_secs_f64()) as u64;
                        *instant = Instant::now(); *previous = received;
                    }
                } else { self.samples.insert(id, (Instant::now(), received)); }
                if file.completed != received || file.total != Some(total) || first { self.changed = true; }
                file.completed = received; file.total = Some(total); file.sequence += 1;
                file.status = if extracting { "extracting" } else { "downloading" }.into();
                self.value.phase = phase.into();
                self.value.message = if extracting { "正在解压 Python" } else if self.python { "正在下载 Python" } else { "正在下载依赖" }.into();
                self.value.progress_unavailable = false;
            } else if let Some((done, total)) = item_count(line) {
                if total > 0 {
                    self.value.phase = "installing".into(); self.value.message = "正在安装依赖".into();
                    self.value.completed = Some(done); self.value.total = Some(total); self.value.unit = Some("items".into());
                    self.changed = true;
                }
            } else if line.ends_with(" ....") || line.starts_with("Downloading ") || line.starts_with("Downloaded ") {
                let finished = line.starts_with("Downloaded ");
                let name = line.trim_end_matches(" ....").trim_start_matches("Downloading ").trim_start_matches("Downloaded ");
                let name = name.split(" (").next().unwrap_or(name).trim();
                if !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)) {
                    let phase = if self.python { "python-download" } else { "dependency-download" };
                    let id = format!("{phase}:{name}");
                    let first = self.observed.insert(id.clone());
                    let index = self.value.files.iter().position(|f| f.id == id).unwrap_or_else(|| {
                        self.value.files.push(FileProgress { id, name: name.into(), phase: phase.into(), attempt: 1, ..Default::default() });
                        self.value.files.len() - 1
                    });
                    let file = &mut self.value.files[index];
                    if !first && file.status == "downloaded" { continue; }
                    if first && !file.status.is_empty() { file.attempt += 1; file.completed = 0; }
                    let status = if finished { "downloaded" } else { "downloading" };
                    if file.status != status { self.changed = true; }
                    file.status = status.into();
                    self.value.message = if self.python { "正在下载 Python" } else { "正在下载依赖" }.into();
                }
                self.log(&format!("{name}：{}", if finished { "已下载" } else { "正在下载 · 实时进度不可用" }));
                self.value.progress_unavailable = true;
            } else if line.starts_with("Preparing packages") || line.contains("Preparing packages") {
                self.value.message = "正在准备依赖".into();
            } else if line.starts_with("Prepared ") || line.starts_with("Installed ") || line.starts_with("Resolved ")
                || line.starts_with("Using ") || line.starts_with("Creating ") || line.starts_with("error:")
                || line.starts_with("warning:") || line.starts_with("Caused by:") || line.starts_with('×') || line.starts_with('╰') || line.starts_with('├') || line.starts_with('│')
                || line.starts_with("+ ") || line.starts_with("~ ") || self.error_output {
                self.log(line);
                if line.starts_with("Prepared ") { self.value.message = "正在安装依赖".into(); self.value.phase = "installing".into(); self.finish_files(true); }
                if line.starts_with("Installed ") {
                    if let Some(count) = line.split_whitespace().nth(1).and_then(|s| s.parse().ok()) {
                        self.value.completed = Some(count); self.value.total = Some(count); self.value.unit = Some("items".into());
                    }
                }
            }
        }
    }
    fn cached(&mut self, name: &str) {
        if self.python { return; }
        let id = format!("dependency-download:{name}");
        let first = self.observed.insert(id.clone());
        let index = self.value.files.iter().position(|f| f.id == id).unwrap_or_else(|| {
            self.value.files.push(FileProgress { id: id.clone(), name: name.into(), phase: "dependency-download".into(), attempt: 1, ..Default::default() });
            self.value.files.len() - 1
        });
        let file = &mut self.value.files[index];
        if first && !file.status.is_empty() { file.attempt += 1; file.completed = 0; }
        if file.status == "downloaded" { return; }
        file.status = "downloaded".into(); file.bytes_per_second = 0; file.sequence += 1;
        self.samples.remove(&id);
        self.log(&format!("{name}：已下载"));
        self.changed = true;
    }
    fn expire_speeds(&mut self) {
        for file in &mut self.value.files {
            if file.bytes_per_second > 0 && self.samples.get(&file.id).is_some_and(|(time, _)| time.elapsed() >= Duration::from_secs(3)) {
                file.bytes_per_second = 0; file.sequence += 1; self.changed = true;
            }
        }
    }
    fn finish_files(&mut self, success: bool) {
        for f in &mut self.value.files {
            if self.observed.contains(&f.id) && matches!(f.status.as_str(), "downloading" | "extracting") {
                f.status = if success { if f.phase == "extracting" { "extracted" } else { "downloaded" } } else { "failed" }.into();
                // Completion does not manufacture a last byte sample.
                f.bytes_per_second = 0; f.sequence += 1;
            }
        }
        self.changed = true;
    }
    fn publish(&mut self, root: &Path) {
        if !self.changed { return; }
        self.value.sequence += 1; self.value.updated_at = progress::now();
        let _ = scheduler::write_json(&progress::path(root), &self.value);
        if let Ok(json) = serde_json::to_string(&self.value) { println!("{}{json}", progress::PREFIX); let _ = std::io::stdout().flush(); }
        self.changed = false;
    }
}

fn pipes(request: &Request, tracker: &mut Tracker, cache: &mut Option<CacheCompletions>) -> i32 {
    use std::os::windows::io::AsRawHandle;
    if crate::cancellation::requested() { return crate::cancellation::EXIT_CODE; }
    let job = match crate::cancellation::UvJob::new() { Ok(job) => job, Err(e) => { tracker.log(&e); return 1; } };
    tracker.value.progress_unavailable = true; tracker.changed = true; tracker.publish(&request.root);
    let mut command = Command::new(&request.executable);
    command.args(["--color", "never"]).args(&request.arguments).creation_flags(0x08000000).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = match command.spawn() { Ok(c) => c, Err(e) => { tracker.log(&format!("error: {e}")); return 1; } };
    if let Err(error) = job.assign(child.as_raw_handle()) {
        let _ = child.kill(); let _ = child.wait(); tracker.log(&error); return 1;
    }
    let stdout = child.stdout.take().unwrap(); let stderr = child.stderr.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    let other = tx.clone();
    std::thread::spawn(move || { for line in std::io::BufReader::new(stdout).lines().map_while(Result::ok) { if tx.send(line).is_err() { break; } } });
    std::thread::spawn(move || { for line in std::io::BufReader::new(stderr).lines().map_while(Result::ok) { if other.send(line).is_err() { break; } } });
    let mut last = Instant::now();
    loop {
        if crate::cancellation::requested() { job.cancel(); }
        match rx.recv_timeout(Duration::from_millis(50)) {
            Ok(line) => { tracker.log(&line); tracker.frame(&line); }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
        }
        if let Some(cache) = cache { cache.poll(tracker); }
        if last.elapsed() >= Duration::from_millis(250) { tracker.publish(&request.root); last = Instant::now(); }
    }
    child.wait().ok().and_then(|s| s.code()).unwrap_or(1)
}

pub fn run(path: &Path) -> i32 {
    let request: Request = match fs::read(path).ok().and_then(|b| serde_json::from_slice(&b).ok()) { Some(r) => r, None => { eprintln!("Invalid uv progress request"); return 1; } };
    // Only this short-lived worker changes uv's terminal environment.
    for key in ["UV_NO_PROGRESS", "JPY_SESSION_NAME", "CI", "NO_COLOR", "RUST_LOG"] { std::env::remove_var(key); }
    std::env::set_var("COLUMNS", "220");
    std::env::set_var("TERM", "xterm-256color");
    let mut value = progress::read(&request.root).unwrap_or_else(|| OperationProgress {
        operation_id: format!("uv-{}-{}", std::process::id(), progress::now()), operation: "install".into(), phase: "dependencies".into(),
        status: "running".into(), started_at: progress::now(), ..Default::default()
    });
    value.completed = None; value.total = None; value.unit = None;
    let mut tracker = Tracker::new(value);
    let mut cache = if request.uv_version == "0.12.10" && !tracker.python {
        std::env::var_os("UV_CACHE_DIR").map(PathBuf::from).and_then(CacheCompletions::new)
    } else { None };
    let mut parser = vt100::Parser::new(240, 220, 200);
    let mut last = Instant::now();
    let start = Instant::now();
    let code = if request.uv_version == "0.12.10" {
        match uv_terminal::run(&request.executable, &request.arguments, |bytes| {
            parser.process(bytes);
            // contents() joins wrapped rows; uv deliberately fills the terminal
            // width, so retain physical row boundaries for each independent file.
            if !bytes.is_empty() { tracker.frame(&parser.screen().rows(0, 220).collect::<Vec<_>>().join("\n")); }
            if let Some(cache) = &mut cache { cache.poll(&mut tracker); }
            tracker.expire_speeds();
            if start.elapsed() > Duration::from_secs(3) && tracker.observed.is_empty() && !tracker.value.progress_unavailable {
                tracker.value.progress_unavailable = true; tracker.changed = true;
            }
            if last.elapsed() >= Duration::from_millis(250) { tracker.publish(&request.root); last = Instant::now(); }
        }) {
            Ok(code) => code,
            Err(reason) => { tracker.log(&format!("实时进度不可用：{reason}")); pipes(&request, &mut tracker, &mut cache) }
        }
    } else { tracker.log("实时进度不可用：uv 版本未匹配"); pipes(&request, &mut tracker, &mut cache) };
    tracker.finish_files(code == 0);
    if code == crate::cancellation::EXIT_CODE {
        for file in &mut tracker.value.files {
            if tracker.observed.contains(&file.id) && file.status == "failed" { file.status = "cancelled".into(); }
        }
        tracker.value.message = "正在取消安装".into();
    }
    if code == 0 {
        // No observed download is not proof of a cache hit (capture may be unavailable).
        tracker.value.message = if tracker.python { "Python 已准备" } else { "依赖准备完成" }.into();
    }
    tracker.publish(&request.root);
    code
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn parses_rounded_bytes_and_rejects_unknown_totals() {
        assert_eq!(download("numpy   ---------- 1.5 MiB/11.7 MiB"), Some(("numpy".into(),1572864,12268339)));
        assert!(download("numpy 10 KiB/?").is_none());
        assert!(download("Preparing packages [4/20]").is_none());
    }
    #[test] fn stable_order_monotonic_samples_and_explicit_retry() {
        let mut t = Tracker::new(OperationProgress::default());
        t.frame("numpy ---- 2 MiB/10 MiB\nscipy ---- 1 MiB/20 MiB");
        t.frame("scipy ---- 3 MiB/20 MiB\nnumpy ---- 1 MiB/10 MiB");
        assert_eq!(t.value.files[0].name,"numpy"); assert_eq!(t.value.files[0].completed,2097152);
        t.finish_files(false); assert_eq!(t.value.files[0].status,"failed");
        let mut retry = Tracker::new(t.value); retry.frame("numpy ---- 1 MiB/10 MiB");
        assert_eq!(retry.value.files[0].attempt,2); assert_eq!(retry.value.files[0].completed,1048576);
    }
    #[test] fn terminal_chunks_preserve_utf8_and_remove_escape_sequences() {
        let mut parser = vt100::Parser::new(10, 120, 0);
        for byte in "\x1b[2J\x1b[H中文 ---- 1 MiB/2 MiB\r\x1b[2K中文 ---- 1.5 MiB/2 MiB".as_bytes() { parser.process(&[*byte]); }
        let mut t = Tracker::new(OperationProgress::default()); t.frame(&parser.screen().contents());
        assert_eq!(t.value.files[0].name,"中文"); assert_eq!(t.value.files[0].completed,1572864);
    }
    #[test] fn explicit_zero_bar_starts_a_new_attempt_without_hiding_the_reset() {
        let mut t = Tracker::new(OperationProgress::default());
        t.frame("numpy ---- 2 MiB/10 MiB");
        t.frame("numpy ---- 0 B/10 MiB");
        assert_eq!(t.value.files[0].attempt, 2); assert_eq!(t.value.files[0].completed, 0);
        t.frame("numpy ---- 1 MiB/10 MiB");
        assert_eq!(t.value.files[0].attempt, 2);
    }
    #[test] fn legacy_snapshot_and_success_keep_download_distinct_from_install() {
        let value: OperationProgress = serde_json::from_str(r#"{"operationId":"old","phase":"python","status":"running"}"#).unwrap();
        assert!(value.files.is_empty()); let mut t = Tracker::new(value);
        t.frame("cpython (download) ---- 5 MiB/10 MiB"); t.finish_files(true);
        assert_eq!(t.value.files[0].status,"downloaded"); assert_eq!(t.value.status,"running");
        assert_eq!(t.value.files[0].completed,5242880);
    }
    #[test] fn committed_cache_confirms_completion_without_trusting_old_or_partial_files() {
        let root = std::env::temp_dir().join(format!("pdf2zh-cache-progress-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let write_metadata = |directory: &Path, name: &str| {
            let directory = directory.join(format!("{name}-1.0.dist-info"));
            fs::create_dir_all(&directory).unwrap();
            fs::write(directory.join("METADATA"), format!("Name: {name}\nVersion: 1.0\n")).unwrap();
        };
        write_metadata(&root.join("archive-v0/old"), "small_pkg");
        let mut cache = CacheCompletions::new(root.clone()).unwrap();
        let mut t = Tracker::new(OperationProgress::default());
        t.frame("small-pkg ---- 0 B/10 KiB\nlarge ---- 1 MiB/20 MiB");
        write_metadata(&root.join(".tmp-pending"), "small_pkg");
        cache.last_scan -= Duration::from_secs(2); cache.poll(&mut t);
        assert_eq!(t.value.files[0].status, "downloading");
        fs::rename(root.join(".tmp-pending"), root.join("archive-v0/new")).unwrap();
        cache.last_scan -= Duration::from_secs(2); cache.poll(&mut t);
        assert_eq!(t.value.files[0].status, "downloaded");
        assert_eq!(t.value.files[0].completed, 0, "Completion must not invent byte samples");
        t.frame("small-pkg ---- 0 B/10 KiB\nlarge ---- 2 MiB/20 MiB");
        assert_eq!(t.value.files[0].status, "downloaded");
        assert_eq!(t.value.files[1].status, "downloading");
        t.finish_files(false);
        assert_eq!(t.value.files[0].status, "downloaded");
        assert_eq!(t.value.files[1].status, "failed");
        fs::remove_dir_all(root).unwrap();
    }
    #[test] fn rounded_full_bar_and_disappearing_row_do_not_mean_completed() {
        let mut t = Tracker::new(OperationProgress::default());
        t.frame("small ---- 10 KiB/10 KiB");
        t.frame("large ---- 1 MiB/20 MiB");
        assert_eq!(t.value.files[0].status, "downloading");
        t.finish_files(false);
        assert_eq!(t.value.files[0].status, "failed");
    }
    #[test] fn unchanged_terminal_redraw_keeps_speed_until_samples_expire() {
        let mut t = Tracker::new(OperationProgress::default());
        t.frame("large ---- 1 MiB/20 MiB");
        t.samples.get_mut("dependency-download:large").unwrap().0 -= Duration::from_secs(1);
        t.frame("large ---- 2 MiB/20 MiB");
        let speed = t.value.files[0].bytes_per_second;
        assert!(speed > 0);
        t.samples.get_mut("dependency-download:large").unwrap().0 -= Duration::from_secs(1);
        t.frame("large ---- 2 MiB/20 MiB");
        assert_eq!(t.value.files[0].bytes_per_second, speed);
        t.samples.get_mut("dependency-download:large").unwrap().0 -= Duration::from_secs(3);
        t.expire_speeds();
        assert_eq!(t.value.files[0].bytes_per_second, 0);
    }
}
