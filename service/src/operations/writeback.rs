use crate::{
    ServiceError, ServiceResult,
    asc::{AscClient, WritebackResult},
    markdown, snapshot,
};
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

#[derive(Default, Serialize)]
struct Report {
    inflight_commits: Vec<String>,
    available_results: usize,
    written: Vec<String>,
    already_committed: Vec<String>,
    blocked: Vec<Value>,
    writeback_commit: Option<String>,
    receipted: Vec<String>,
    receipt_failures: Vec<Value>,
}

struct Prepared {
    row: WritebackResult,
    source_commit: String,
    source: Vec<u8>,
    target: Vec<u8>,
}

pub fn run(repository: &Path, asc: &AscClient) -> ServiceResult<Vec<u8>> {
    let repo = snapshot::writeback_root(repository)?;
    let state = asc.dispatch_state(&repo)?;
    let pending = asc.pending_writebacks(&repo, &state.inflight_commits)?;
    let rows = pending.results;
    let mut report = Report {
        inflight_commits: state.inflight_commits,
        available_results: rows.len() + pending.rejected.len(),
        blocked: pending.rejected.into_iter().map(|row| json!(row)).collect(),
        ..Report::default()
    };
    if rows.is_empty() {
        return report_bytes(&report);
    }
    require_empty_index(&repo)?;

    // Block all ambiguous targets, without preventing independent rows from proceeding.
    let mut paths = BTreeMap::new();
    let mut calls = BTreeMap::new();
    for row in &rows {
        *paths.entry(row.extra.source.path.clone()).or_insert(0usize) += 1;
        *calls.entry(row.call_identity.clone()).or_insert(0usize) += 1;
    }
    let mut materialize = Vec::new();
    let mut receipts = Vec::new();
    for row in rows {
        let call = row.call_identity.clone();
        let prepared = (|| {
            if paths[&row.extra.source.path] > 1 || calls[&call] > 1 {
                return Err(invalid(
                    "multiple pending responses share a target path or call identity",
                ));
            }
            prepare(&repo, row, &report.inflight_commits)
        })();
        match prepared {
            Err(error) => report
                .blocked
                .push(json!({"call_identity": call, "reason": error.to_string()})),
            Ok(item) => match prior_commit(&repo, &item) {
                Ok(Some(commit)) => {
                    report.already_committed.push(call);
                    receipts.push((item, commit));
                }
                Ok(None) => materialize.push(item),
                Err(error) => report
                    .blocked
                    .push(json!({"call_identity": call, "reason": error.to_string()})),
            },
        }
    }

    let mut written = Vec::new();
    // Recheck immediately before the first filesystem mutation.
    require_empty_index(&repo)?;
    for item in materialize {
        match materialize_response(&repo, &item) {
            Ok(()) => {
                report.written.push(item.row.call_identity.clone());
                written.push(item);
            }
            Err(error) => report.blocked.push(
                json!({"call_identity": item.row.call_identity, "reason": error.to_string()}),
            ),
        }
    }
    if !written.is_empty() {
        require_empty_index(&repo)?;
        let mut add = vec!["--literal-pathspecs", "add", "--"];
        add.extend(
            written
                .iter()
                .map(|item| item.row.extra.source.path.as_str()),
        );
        snapshot::git(&repo, &add)?;
        // Filters must not silently transform the raw frontmatter or response body.
        for item in &written {
            verify_target(&repo, "", item)?;
        }
        let mut message = String::from("autoscribe: write responses\n\n");
        for item in &written {
            message.push_str(&format!("Autoscribe-Call: {}\n", item.row.call_identity));
        }
        // --only limits the commit to these paths even if another process stages a file.
        // An identical response still needs a durable trailer acknowledgement.
        let mut commit_args = vec![
            "--literal-pathspecs",
            "commit",
            "--only",
            "--allow-empty",
            "-m",
            &message,
            "--",
        ];
        commit_args.extend(
            written
                .iter()
                .map(|item| item.row.extra.source.path.as_str()),
        );
        snapshot::git(&repo, &commit_args)?;
        let commit = String::from_utf8(snapshot::git(&repo, &["rev-parse", "HEAD"])?)
            .map_err(|error| invalid(error.to_string()))?
            .trim()
            .to_owned();
        report.writeback_commit = Some(commit.clone());
        for item in written {
            // Hooks/filters can change committed bytes: never receipt an unverified tree.
            match verify_target(&repo, &commit, &item) {
                Ok(()) => receipts.push((item, commit.clone())),
                Err(error) => report.receipt_failures.push(json!({
                    "call_identity": item.row.call_identity, "writeback_commit": commit,
                    "reason": error.to_string(),
                })),
            }
        }
    }
    for (item, commit) in receipts {
        match asc.receipt_writeback(&repo, &item.row, &item.source_commit, &commit) {
            Ok(()) => report.receipted.push(item.row.call_identity),
            Err(error) => report.receipt_failures.push(json!({
                "call_identity": item.row.call_identity, "writeback_commit": commit,
                "reason": error.to_string(),
            })),
        }
    }
    report_bytes(&report)
}

fn invalid(message: impl Into<String>) -> ServiceError {
    ServiceError::InvalidInput(message.into())
}

fn report_bytes(report: &Report) -> ServiceResult<Vec<u8>> {
    let mut bytes = serde_json::to_vec(report).map_err(|error| invalid(error.to_string()))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn require_empty_index(repo: &Path) -> ServiceResult<()> {
    if !snapshot::git(repo, &["diff", "--cached", "--name-only", "-z", "--"])?.is_empty() {
        return Err(invalid(
            "write-responses requires an index with no staged changes",
        ));
    }
    Ok(())
}

fn prepare(repo: &Path, row: WritebackResult, inflight: &[String]) -> ServiceResult<Prepared> {
    let path = &row.extra.source.path;
    if path.is_empty() || !Path::new(path).components()
        .all(|part| matches!(part, std::path::Component::Normal(name) if !name.as_encoded_bytes().eq_ignore_ascii_case(b".git"))) {
        return Err(invalid("unsafe writeback path"));
    }
    let absolute = repo.join(path);
    if !absolute.starts_with(repo) {
        return Err(invalid("writeback target escapes $PWD"));
    }
    if row.call_identity.is_empty()
        || row.call_identity.chars().any(char::is_whitespace)
        || row.call_identity.chars().any(char::is_control)
    {
        return Err(invalid("invalid call identity for Git trailer"));
    }
    let context = &row.extra.context;
    if context.repository != repo
        || row.extra.response.action != "writeback"
        || !inflight.contains(&context.source_commit)
    {
        return Err(invalid(
            "writeback dispatch metadata does not match repository/inflight selection",
        ));
    }
    let source_commit = snapshot::full_commit(repo, &context.source_commit)?;
    let (blob, source) = snapshot::blob(repo, &source_commit, path)?;
    let (prefix, _) = markdown::split_frontmatter(&source)?;
    let metadata = markdown::metadata(prefix)?;
    if blob != context.source_blob
        || metadata.get("slug").and_then(serde_yaml::Value::as_str) != Some(&row.source_identity)
    {
        return Err(invalid(format!("source blob/slug mismatch for {path}")));
    }
    let target = render_writeback(source.as_bytes(), &row.content)?;
    Ok(Prepared {
        row,
        source_commit,
        source: source.into_bytes(),
        target,
    })
}

fn materialize_response(repo: &Path, item: &Prepared) -> ServiceResult<()> {
    let absolute = repo.join(&item.row.extra.source.path);
    let canonical = fs::canonicalize(&absolute)
        .map_err(|error| ServiceError::Io(format!("{}: {error}", absolute.display())))?;
    if canonical != absolute
        || fs::symlink_metadata(&absolute)
            .map_err(|error| ServiceError::Io(error.to_string()))?
            .file_type()
            .is_symlink()
    {
        return Err(invalid("writeback refuses symlink paths"));
    }
    let current = fs::read(&absolute)
        .map_err(|error| ServiceError::Io(format!("{}: {error}", absolute.display())))?;
    if current == item.target {
        return Ok(());
    }
    if current != item.source {
        return Err(invalid(format!(
            "{} diverged from selected source snapshot",
            item.row.extra.source.path
        )));
    }
    atomic_write(&absolute, &item.target)
}

fn verify_target(repo: &Path, commit: &str, item: &Prepared) -> ServiceResult<()> {
    let (_, body) = snapshot::blob(repo, commit, &item.row.extra.source.path)?;
    if body.as_bytes() != item.target {
        return Err(invalid(format!(
            "committed/staged response differs at {}",
            item.row.extra.source.path
        )));
    }
    Ok(())
}

fn prior_commit(repo: &Path, item: &Prepared) -> ServiceResult<Option<String>> {
    let trailer = format!("Autoscribe-Call: {}", item.row.call_identity);
    let commits = snapshot::git(
        repo,
        &[
            "log",
            "--all",
            "HEAD",
            "--format=%H",
            "--fixed-strings",
            "--grep",
            &trailer,
            "--",
        ],
    )?;
    for commit in String::from_utf8(commits)
        .map_err(|error| invalid(error.to_string()))?
        .lines()
    {
        let trailers = snapshot::git(
            repo,
            &[
                "show",
                "-s",
                "--format=%(trailers:key=Autoscribe-Call,valueonly)",
                commit,
                "--",
            ],
        )?;
        let exact = String::from_utf8(trailers)
            .map_err(|error| invalid(error.to_string()))?
            .lines()
            .any(|value| value.trim() == item.row.call_identity);
        if exact && verify_target(repo, commit, item).is_ok() {
            return Ok(Some(commit.to_owned()));
        }
    }
    Ok(None)
}

fn render_writeback(current: &[u8], response: &str) -> ServiceResult<Vec<u8>> {
    let text = std::str::from_utf8(current).map_err(|error| {
        ServiceError::InvalidInput(format!("source markdown is not UTF-8: {error}"))
    })?;
    let (prefix, _) = markdown::split_frontmatter(text)?;
    let (response_prefix, response) = markdown::split_frontmatter(response)?;
    if let Some(response_prefix) = response_prefix {
        let response_metadata = markdown::metadata(Some(response_prefix))?;
        if let Some(response_slug) = response_metadata.get("slug") {
            let source_metadata = markdown::metadata(prefix)?;
            if source_metadata.get("slug") != Some(response_slug) {
                return Err(ServiceError::InvalidInput(
                    "response/source slug mismatch".into(),
                ));
            }
        }
    }
    let mut output = Vec::new();
    if let Some(prefix) = prefix {
        output.extend_from_slice(prefix.as_bytes());
        if !prefix.ends_with('\n') {
            output.push(b'\n');
        }
    }
    output.extend_from_slice(response.as_bytes());
    if !response.ends_with('\n') {
        output.push(b'\n');
    }
    Ok(output)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> ServiceResult<()> {
    use std::io::Write;
    let parent = path
        .parent()
        .ok_or_else(|| invalid("target has no parent"))?;
    let name = path
        .file_name()
        .ok_or_else(|| invalid("target has no filename"))?;
    let temp = parent.join(format!(
        ".{}.autoscribe-{}.tmp",
        name.to_string_lossy(),
        std::process::id()
    ));
    // Never follow or truncate a pre-existing temporary path.
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|error| ServiceError::Io(format!("create {}: {error}", temp.display())))?;
    let result = (|| -> std::io::Result<()> {
        file.set_permissions(fs::metadata(path)?.permissions())?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result.map_err(|error| ServiceError::Io(format!("replace {}: {error}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::render_writeback;

    #[test]
    fn preserves_frontmatter_and_replaces_body() {
        let current = b"---\nslug: cnt.one\nstatus: ai-process\n---\nOld body\n";
        let output = render_writeback(current, "New body").unwrap();
        assert_eq!(
            String::from_utf8(output).unwrap(),
            "---\nslug: cnt.one\nstatus: ai-process\n---\nNew body\n"
        );
    }

    #[test]
    fn body_only_file_is_replaced() {
        let output = render_writeback(b"Old\n", "New").unwrap();
        assert_eq!(String::from_utf8(output).unwrap(), "New\n");
    }
    #[test]
    fn response_metadata_cannot_replace_original_metadata() {
        let prefix = "---\r\nslug: 'pss.one' # keep\r\ncustom: [a, b]\r\n---\r\n";
        let current = format!("{prefix}Old\n");
        let response = "---\nslug: pss.one\nstatus: changed\n---\nNew\n---\nBody rule\n";
        assert_eq!(
            render_writeback(current.as_bytes(), response).unwrap(),
            format!("{prefix}New\n---\nBody rule\n").as_bytes()
        );
        assert_eq!(
            render_writeback(b"Old\n", "---\nstatus: changed\n---\nNew\n---\nBody rule\n").unwrap(),
            b"New\n---\nBody rule\n"
        );
        let mismatch =
            render_writeback(current.as_bytes(), "---\nslug: pss.wrong\n---\nNew\n").unwrap_err();
        assert!(
            mismatch
                .to_string()
                .contains("response/source slug mismatch")
        );
        assert!(render_writeback(b"Old\n", response).is_err());
        assert!(render_writeback(b"---\nslug: pss.one", "New").is_err());
        assert!(render_writeback(b"Old", "---\nslug: pss.wrong").is_err());
    }
}
