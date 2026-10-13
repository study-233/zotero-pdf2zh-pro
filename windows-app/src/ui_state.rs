//! Page and feedback state survives Slint component destruction.
use crate::{progress::OperationProgress, scheduler::UpdateState, update::UpdateCheck};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(i32)]
pub enum Page { #[default] Home = 0, Settings = 1, Setup = 2, Confirm = 4 }

impl Page {
    pub fn size(self) -> (f32, f32) {
        match self { Self::Home => (400., 300.), Self::Settings => (520., 480.), Self::Setup => (560., 480.), Self::Confirm => (520., 480.) }
    }
}

#[derive(Default)]
pub struct SettingsView {
    pub offset: f32,
    pub advanced: bool,
    pub thanks: bool,
    pub update_details: bool,
    pub maintenance_details: bool,
}

#[derive(Default)]
pub struct Feedback {
    pub message: String,
    pub details: String,
    pub failed: bool,
    pub retry: String,
    pub progress: Option<OperationProgress>,
}

impl Feedback {
    pub fn start(&mut self, action: &str, message: &str) {
        *self = Self { message: message.into(), retry: action.into(), ..Default::default() };
    }
    pub fn fail(&mut self, message: &str) {
        self.message = message.lines().next().unwrap_or(message).into();
        self.details.push_str(&format!("\n{message}"));
        self.failed = true;
        if let Some(p) = &mut self.progress { p.status = "failed".into(); }
    }
    pub fn accept(&mut self, p: &OperationProgress) {
        self.message = p.message.clone();
        self.failed = matches!(p.status.as_str(), "failed" | "rolledBack");
        self.progress = Some(p.clone());
    }
    pub fn logs(&self) -> String {
        let mut logs = self.details.clone();
        if let Some(p) = &self.progress {
            for line in &p.recent_logs { if !logs.lines().any(|s| s == line) { logs.push('\n'); logs.push_str(line); } }
        }
        if logs.trim().is_empty() { "暂无详情。".into() } else { logs }
    }
    pub fn detail(&self) -> String {
        let Some(p) = &self.progress else { return String::new(); };
        if !p.active() { return String::new(); }
        match p.unit.as_deref() {
            Some("bytes") => format!("已下载 {:.1} MiB{}", p.completed.unwrap_or(0) as f64 / 1048576.,
                p.total.filter(|n| *n > 0).map(|n| format!(" / {:.1} MiB", n as f64 / 1048576.)).unwrap_or_default()),
            Some("items") => format!("已完成 {} / {}", p.completed.unwrap_or(0), p.total.unwrap_or(0)),
            _ => String::new(),
        }
    }
    pub fn percent(&self) -> Option<f32> {
        self.progress.as_ref().filter(|p| p.active() && p.unit.as_deref() == Some("bytes"))
            .and_then(|p| p.percent()).map(|n| n as f32 / 100.)
    }
}

#[derive(Clone, Copy, Default, PartialEq, Debug)]
pub enum UpdatePhase { #[default] Idle, Checking, Current, Available, Preparing, Waiting, Switching, Failed }

#[derive(Default)]
pub struct UpdateView {
    pub phase: UpdatePhase,
    pub latest: String,
    pub checked_at: u64,
    pub feedback: Feedback,
    pub manual_requested: bool,
    pending_check: Option<u64>,
}

impl UpdateView {
    pub fn checking(&mut self, id: u64) {
        self.pending_check = Some(id);
        self.phase = UpdatePhase::Checking;
        self.feedback.start("check", "正在检查稳定版本…");
    }
    pub fn checked(&mut self, id: u64, result: Result<UpdateCheck, String>, at: u64) -> bool {
        if self.pending_check != Some(id) { return false; }
        self.pending_check = None;
        match result {
            Ok(check) => {
                self.checked_at = at;
                self.latest = check.latest_version;
                self.phase = if check.available { UpdatePhase::Available } else { UpdatePhase::Current };
                self.feedback.message = if check.available { format!("发现新版本 {}", self.latest) } else { "已是最新稳定版".into() };
                self.feedback.failed = false;
            }
            Err(error) => { self.phase = UpdatePhase::Failed; self.feedback.fail(&format!("检查更新失败：{error}")); }
        }
        true
    }
    pub fn state(&mut self, state: &UpdateState) {
        let phase = match state.phase.as_str() {
            "current" => UpdatePhase::Current, "preparing" => UpdatePhase::Preparing,
            "ready" => UpdatePhase::Waiting, "switching" => UpdatePhase::Switching,
            "failed" | "network" => UpdatePhase::Failed, _ => return,
        };
        self.pending_check = None;
        self.phase = phase;
        self.latest = state.version.clone();
        self.manual_requested = state.manual_version.as_deref() == Some(&state.version);
        self.checked_at = self.checked_at.max(state.last_check);
        self.feedback.retry = if state.version.is_empty() || state.error.contains("重新检查") || state.error.contains("检查更新失败") { "check" } else { "update" }.into();
        self.feedback.failed = phase == UpdatePhase::Failed;
        self.feedback.message = match phase {
            UpdatePhase::Current => "已是最新稳定版".into(),
            UpdatePhase::Preparing => "正在下载并准备更新…".into(),
            UpdatePhase::Waiting => "更新已准备，等待连续空闲 60 秒后切换".into(),
            UpdatePhase::Switching => "正在切换版本，控制中心将在后台重新启动".into(),
            UpdatePhase::Failed => state.error.clone(), _ => String::new(),
        };
        if phase == UpdatePhase::Failed { self.feedback.details = state.error.clone(); }
        if matches!(phase, UpdatePhase::Current | UpdatePhase::Waiting | UpdatePhase::Switching | UpdatePhase::Failed) {
            self.feedback.progress = None;
        }
    }
    pub fn progress(&mut self, p: &OperationProgress) {
        self.pending_check = None;
        self.phase = if matches!(p.status.as_str(), "failed" | "rolledBack") { UpdatePhase::Failed }
            else if p.phase == "switching" { UpdatePhase::Switching }
            else if p.phase == "waiting" { UpdatePhase::Waiting }
            else if !p.active() { self.phase } else { UpdatePhase::Preparing };
        self.feedback.retry = if self.latest.is_empty() || p.message.contains("重新检查") || p.message.contains("检查更新失败") { "check" } else { "update" }.into();
        self.feedback.accept(p);
    }
    pub fn pending(&self) -> bool { matches!(self.phase, UpdatePhase::Preparing | UpdatePhase::Waiting | UpdatePhase::Switching) }
    pub fn downloadable(&self) -> bool {
        !self.latest.is_empty() && (self.phase == UpdatePhase::Available || (self.phase == UpdatePhase::Waiting && !self.manual_requested))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn late_checks_cannot_overwrite_new_requests_or_downloads() {
        let mut view = UpdateView::default();
        view.checking(1); view.checking(2);
        assert!(!view.checked(1, Err("old".into()), 1));
        assert_eq!(view.phase, UpdatePhase::Checking);
        view.state(&UpdateState { phase: "preparing".into(), version: "2.0.0".into(), ..Default::default() });
        assert!(!view.checked(2, Err("late".into()), 2));
        assert_eq!(view.phase, UpdatePhase::Preparing);
    }
    #[test]
    fn check_failure_has_its_own_retry_and_does_not_claim_current() {
        let mut view = UpdateView::default(); view.checking(1);
        view.checked(1, Err("断网".into()), 7);
        assert_eq!(view.phase, UpdatePhase::Failed);
        assert_eq!(view.feedback.retry, "check"); assert_eq!(view.checked_at, 0);
        view.checking(2);
        view.checked(2, Ok(UpdateCheck { available: true, current_version: "1.0.0".into(), latest_version: "2.0.0".into() }), 8);
        assert!(view.downloadable()); assert_eq!(view.checked_at, 8);
        assert!(!view.feedback.failed);
    }
    #[test]
    fn unknown_totals_and_completed_downloads_have_no_percentage() {
        let mut feedback = Feedback::default();
        let mut p = OperationProgress { status: "running".into(), unit: Some("bytes".into()), completed: Some(50), ..Default::default() };
        feedback.accept(&p); assert_eq!(feedback.percent(), None);
        p.total = Some(100); feedback.accept(&p); assert_eq!(feedback.percent(), Some(0.5));
        p.status = "failed".into(); feedback.accept(&p); assert_eq!(feedback.percent(), None);
    }
}
