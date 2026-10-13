//! Presentation snapshots never replace the install/recovery journal.
use crate::*;
use std::time::{SystemTime, UNIX_EPOCH};

pub const PREFIX: &str = "PDF2ZH_PROGRESS ";

#[derive(Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(default, rename_all = "camelCase")]
pub struct FileProgress {
    pub id: String,
    pub name: String,
    pub phase: String,
    pub status: String,
    pub attempt: u32,
    pub sequence: u64,
    pub completed: u64,
    pub total: Option<u64>,
    pub bytes_per_second: u64,
}

#[derive(Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(default, rename_all = "camelCase")]
pub struct OperationProgress {
    pub operation_id: String,
    pub operation: String,
    pub phase: String,
    pub status: String,
    pub message: String,
    pub completed: Option<u64>,
    pub total: Option<u64>,
    pub unit: Option<String>,
    pub started_at: u64,
    pub updated_at: u64,
    pub sequence: u64,
    pub files: Vec<FileProgress>,
    pub recent_logs: Vec<String>,
    pub progress_unavailable: bool,
}

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

pub fn begin(app: &AppHandle, root: &Path, operation: &str, message: &str) {
    publish(app, root, OperationProgress {
        operation_id: format!("{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos()),
        operation: operation.into(), phase: "starting".into(), status: "running".into(),
        message: message.into(), started_at: now(), ..Default::default()
    });
}

impl OperationProgress {
    pub fn active(&self) -> bool {
        matches!(self.status.as_str(), "running" | "waiting")
    }
    pub fn percent(&self) -> Option<u32> {
        self.total.filter(|total| *total > 0).map(|total| {
            ((self.completed.unwrap_or(0).min(total) as u128 * 100) / total as u128) as u32
        })
    }
}

pub fn path(root: &Path) -> PathBuf { root.join("updates/operation-progress.json") }

pub fn read(root: &Path) -> Option<OperationProgress> {
    fs::read(path(root)).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok())
}

pub fn publish(app: &AppHandle, root: &Path, mut progress: OperationProgress) {
    progress.updated_at = now();
    progress.sequence = read(root).filter(|p| p.operation_id == progress.operation_id)
        .map_or(progress.sequence, |p| p.sequence.max(progress.sequence)) + 1;
    let _ = scheduler::write_json(&path(root), &progress);
    let _ = app.emit("operation-progress", progress);
}

pub fn phase(app: &AppHandle, root: &Path, operation: &str, phase: &str, message: &str, status: &str) {
    let previous = read(root).filter(|p| p.operation == operation && p.active());
    let mut value = previous.unwrap_or_else(|| OperationProgress {
        operation_id: format!("{}-{}", std::process::id(), now()),
        operation: operation.into(), started_at: now(), ..Default::default()
    });
    value.phase = phase.into(); value.message = message.into(); value.status = status.into();
    value.completed = None; value.total = None; value.unit = None;
    publish(app, root, value);
}

pub fn parse_line(line: &str) -> Option<OperationProgress> {
    let value: OperationProgress = serde_json::from_str(line.strip_prefix(PREFIX)?).ok()?;
    (!value.operation_id.is_empty() && !value.phase.is_empty() &&
        matches!(value.status.as_str(), "running" | "waiting" | "completed" | "cancelled" | "failed" | "rolledBack"))
        .then_some(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn progress_requires_measurable_total_and_clamps_received_bytes() {
        let mut p = OperationProgress::default();
        assert_eq!(p.percent(), None);
        p.total = Some(0); assert_eq!(p.percent(), None);
        p.total = Some(200); p.completed = Some(53); assert_eq!(p.percent(), Some(26));
        p.completed = Some(u64::MAX); assert_eq!(p.percent(), Some(100));
    }
    #[test]
    fn ordinary_logs_and_broken_progress_do_not_change_ui_state() {
        assert!(parse_line("Downloaded 100 packages").is_none());
        assert!(parse_line("PDF2ZH_PROGRESS {broken").is_none());
        assert!(parse_line("PDF2ZH_PROGRESS {\"phase\":\"download\"}").is_none());
        assert!(parse_line("PDF2ZH_PROGRESS {\"operationId\":\"1\",\"phase\":\"download\",\"status\":\"running\",\"total\":100}").is_some());
    }
}
