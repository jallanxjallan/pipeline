use crate::{ServiceError, ServiceResult, types::PlanId};
use std::{
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

#[derive(Debug, Clone)]
pub struct AscClient {
    executable: PathBuf,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct DispatchState {
    #[serde(default)]
    pub queued_commits: Vec<String>,
    #[serde(default)]
    pub inflight_commits: Vec<String>,
    #[serde(default)]
    pub busy_sources: Vec<String>,
}

#[derive(Debug, Default)]
pub struct PendingWritebacks {
    pub results: Vec<WritebackResult>,
    pub rejected: Vec<RejectedWriteback>,
}

#[derive(Debug, serde::Serialize)]
pub struct RejectedWriteback {
    pub call_identity: Option<String>,
    pub reason: String,
}

#[derive(Debug, serde::Deserialize)]
pub struct WritebackResult {
    pub call_identity: String,
    pub source_identity: String,
    pub content: String,
    pub extra: WritebackExtra,
}

#[derive(Debug, serde::Deserialize)]
pub struct WritebackExtra {
    pub source: WritebackSource,
    pub context: WritebackContext,
    pub response: WritebackResponse,
}

#[derive(Debug, serde::Deserialize)]
pub struct WritebackSource {
    pub path: String,
}

#[derive(Debug, serde::Deserialize)]
pub struct WritebackContext {
    pub repository: PathBuf,
    pub source_commit: String,
    pub source_blob: String,
}

#[derive(Debug, serde::Deserialize)]
pub struct WritebackResponse {
    pub action: String,
}

impl AscClient {
    pub fn pending_writebacks(
        &self,
        repo: &Path,
        inflight_commits: &[String],
    ) -> ServiceResult<PendingWritebacks> {
        if inflight_commits.is_empty() {
            return Ok(PendingWritebacks::default());
        }
        let mut command = Command::new(&self.executable);
        command
            .args(["export", "list-pending", "--ndjson", "--repository"])
            .arg(repo);
        for commit in inflight_commits {
            command.arg("--source-commit").arg(commit);
        }
        let output = command
            .output()
            .map_err(|error| ServiceError::Server(format!("asc export list-pending: {error}")))?;
        if !output.status.success() {
            return Err(ServiceError::Server(format!(
                "asc export list-pending: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        let mut pending = PendingWritebacks::default();
        for (index, line) in output
            .stdout
            .split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
            .enumerate()
        {
            // JSON is confined to the transport boundary; a malformed individual
            // record does not prevent independent valid records from being applied.
            let value: serde_json::Value = serde_json::from_slice(line).map_err(|error| {
                ServiceError::Server(format!(
                    "pending writeback row {} is invalid JSON: {error}",
                    index + 1
                ))
            })?;
            let call_identity = value
                .get("call_identity")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned);
            match serde_json::from_value(value) {
                Ok(row) => pending.results.push(row),
                Err(error) => pending.rejected.push(RejectedWriteback {
                    call_identity,
                    reason: format!("invalid writeback record: {error}"),
                }),
            }
        }
        Ok(pending)
    }

    pub fn receipt_writeback(
        &self,
        repo: &Path,
        row: &WritebackResult,
        source_commit: &str,
        writeback_commit: &str,
    ) -> ServiceResult<()> {
        let output = Command::new(&self.executable)
            .args([
                "export",
                "update-exports",
                &row.call_identity,
                "--repository",
            ])
            .arg(repo)
            .args([
                "--source-commit",
                source_commit,
                "--writeback-commit",
                writeback_commit,
                "--target-path",
                &row.extra.source.path,
                "--export-message",
                "autoscribe: write responses",
            ])
            .output()
            .map_err(|error| {
                ServiceError::Server(format!("receipt {}: {error}", row.call_identity))
            })?;
        if !output.status.success() {
            return Err(ServiceError::Server(format!(
                "receipt {}: {}",
                row.call_identity,
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(())
    }

    pub fn dispatch_state(&self, repo: &Path) -> ServiceResult<DispatchState> {
        let output = Command::new(&self.executable)
            .args(["storage", "dispatch-state", "--repository"])
            .arg(repo)
            .output()
            .map_err(|error| ServiceError::Server(format!("dispatch state: {error}")))?;
        if !output.status.success() {
            return Err(ServiceError::Server(format!(
                "dispatch state: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        serde_json::from_slice(&output.stdout)
            .map_err(|error| ServiceError::Server(format!("dispatch state JSON: {error}")))
    }

    pub fn new(executable: impl Into<PathBuf>) -> Self {
        Self {
            executable: executable.into(),
        }
    }

    pub fn enqueue(&self, plan: &PlanId, ndjson: &[u8]) -> ServiceResult<Vec<u8>> {
        if ndjson.is_empty() {
            return Err(ServiceError::InvalidInput(
                "refusing to enqueue an empty NDJSON stream".into(),
            ));
        }

        let mut child = Command::new(&self.executable)
            .args(["enqueue", "--plan", &plan.0])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| ServiceError::Server(format!("asc enqueue: {error}")))?;

        child
            .stdin
            .take()
            .ok_or_else(|| ServiceError::Server("asc enqueue stdin unavailable".into()))?
            .write_all(ndjson)
            .map_err(|error| ServiceError::Server(format!("asc enqueue stdin: {error}")))?;

        let output = child
            .wait_with_output()
            .map_err(|error| ServiceError::Server(format!("asc enqueue: {error}")))?;

        if !output.status.success() {
            return Err(ServiceError::Server(format!(
                "asc enqueue failed: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(output.stdout)
    }
}
