use crate::{
    ServiceError, ServiceResult,
    asc::AscClient,
    markdown, ndjson, snapshot,
    types::{PipelineCall, PipelineExtra, PlanId, ResponseAction, ResponseContext, SourceContext},
};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub fn run_commit(
    repo: &Path,
    commit: &str,
    plan: &PlanId,
    action: ResponseAction,
    requested: &[PathBuf],
    asc: &AscClient,
) -> ServiceResult<Vec<u8>> {
    let commit = snapshot::full_commit(repo, commit)?;
    let state = asc.dispatch_state(repo)?;
    if state.inflight_commits.contains(&commit) {
        return Err(ServiceError::InvalidInput(format!(
            "commit {commit} is already in flight"
        )));
    }
    let candidates = snapshot::paths(repo, &commit)?;
    let paths = if requested.is_empty() {
        candidates
    } else {
        let mut paths = Vec::new();
        for requested in requested {
            let path = if requested.is_absolute() {
                requested
                    .strip_prefix(repo)
                    .map_err(|_| ServiceError::InvalidInput("source outside repository".into()))?
            } else {
                requested.as_path()
            };
            let path = path
                .strip_prefix("./")
                .unwrap_or(path)
                .to_str()
                .ok_or_else(|| ServiceError::InvalidInput("source path must be UTF-8".into()))?;
            if !candidates.iter().any(|candidate| candidate == path) {
                return Err(ServiceError::InvalidInput(format!(
                    "{path} is not a Markdown file changed in {commit}"
                )));
            }
            if !paths.iter().any(|candidate| candidate == path) {
                paths.push(path.to_owned());
            }
        }
        paths
    };
    if paths.is_empty() {
        return Err(ServiceError::InvalidInput(
            "selected commit contains no added or modified Markdown files".into(),
        ));
    }
    let mut slugs = BTreeSet::new();
    let mut calls = Vec::new();
    for path in paths {
        let (blob, text) = snapshot::blob(repo, &commit, &path)?;
        let (prefix, body) = markdown::split_frontmatter(&text)?;
        let metadata = markdown::metadata(prefix)?;
        let slug = metadata
            .get("slug")
            .and_then(serde_yaml::Value::as_str)
            .filter(|slug| !slug.trim().is_empty() && *slug == slug.trim())
            .ok_or_else(|| {
                ServiceError::InvalidInput(format!("{path}: missing or invalid frontmatter slug"))
            })?;
        if !slugs.insert(slug.to_owned()) {
            return Err(ServiceError::InvalidInput(format!(
                "duplicate slug in source snapshot: {slug}"
            )));
        }
        if state.busy_sources.iter().any(|source| source == slug) {
            return Err(ServiceError::InvalidInput(format!(
                "{slug} is in flight or awaiting export"
            )));
        }
        let (directive, body) = markdown::extract_directive(body)?;
        let call = PipelineCall {
            record_type: "call".into(),
            identity: slug.to_owned(),
            content: body.to_owned(),
            directive,
            plan: plan.0.clone(),
            extra: PipelineExtra {
                source: SourceContext {
                    kind: "vault".into(),
                    path: Some(path.clone()),
                    status: None,
                    stage: None,
                    name: None,
                    format: None,
                    sha256: None,
                    original_path: None,
                },
                response: ResponseContext { action },
                assets: Vec::new(),
                context: BTreeMap::from([
                    ("repository".into(), json!(repo)),
                    ("source_commit".into(), json!(commit)),
                    ("source_blob".into(), json!(blob)),
                ]),
            },
        };
        ndjson::validate_dispatch_calls(std::slice::from_ref(&call), plan, &path, action)?;
        calls.push(call);
    }
    // No mutation occurs before every source has passed validation.
    asc.enqueue(plan, &ndjson::serialize_calls(&calls)?)
}
