use autoscribe_service::{
    asc::AscClient,
    operations::dispatch,
    snapshot,
    types::{PlanId, ResponseAction},
};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

fn test_dir(label: &str) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "autoscribe-service-{label}-{}-{stamp}",
        std::process::id()
    ));
    fs::create_dir_all(&path).unwrap();
    path
}

#[cfg(unix)]
fn executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
}

fn git(repo: &Path, args: &[&str]) -> String {
    let output = Command::new("/usr/bin/python3")
        .arg(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("git.py"))
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {:?}: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

fn init_repo(root: &Path, label: &str) -> String {
    git(root, &["init"]);
    git(root, &["config", "user.name", "Service Test"]);
    git(root, &["config", "user.email", "service-test@localhost"]);
    fs::write(
        root.join("Note.md"),
        "---\nslug: psg.test\n---\n::: directive\nFix spelling.\n:::\n# Body\n\nKeep **Markdown**.\n",
    )
    .unwrap();
    git(root, &["add", "Note.md"]);
    let message = format!("dispatcher fixture {label}");
    git(root, &["commit", "-m", &message]);
    git(root, &["rev-parse", "HEAD"])
}

#[test]
#[cfg(unix)]
fn dispatch_commit_enqueues_selected_git_snapshot() {
    let root = test_dir("dispatch-commit");
    let commit = init_repo(&root, "selected");
    let capture = root.join("enqueue.ndjson");
    let asc = root.join("asc");
    fs::write(
        &asc,
        format!(
            "#!/bin/sh\nif [ \"$1 $2\" = \"storage dispatch-state\" ]; then printf '%s\\n' '{{\"queued_commits\":[],\"inflight_commits\":[],\"busy_sources\":[]}}'; exit 0; fi\nif [ \"$1\" = \"enqueue\" ]; then cat > '{}'; printf '%s\\n' '{{\"accepted\":true}}'; exit 0; fi\nexit 9\n",
            capture.display()
        ),
    )
    .unwrap();
    executable(&asc);

    let response = dispatch::run_commit(
        &root,
        &commit,
        &PlanId("plan.test".into()),
        ResponseAction::Writeback,
        &[],
        &AscClient::new(&asc),
    )
    .unwrap();

    assert_eq!(response, b"{\"accepted\":true}\n");
    let enqueued = fs::read_to_string(&capture).unwrap();
    let call: serde_json::Value = serde_json::from_str(enqueued.trim()).unwrap();
    assert_eq!(call["identity"], "psg.test");
    assert_eq!(call["directive"], "Fix spelling.");
    assert_eq!(call["content"], "# Body\n\nKeep **Markdown**.\n");
    assert_eq!(call["plan"], "plan.test");
    assert_eq!(call["extra"]["source"]["kind"], "vault");
    assert_eq!(call["extra"]["source"]["path"], "Note.md");
    assert!(call["extra"]["source"].get("status").is_none());
    assert_eq!(call["extra"]["response"]["action"], "writeback");
    assert_eq!(call["extra"]["context"]["source_commit"], commit);
    assert_eq!(
        call["extra"]["context"]["repository"],
        serde_json::json!(root)
    );
    assert!(
        call["extra"]["context"]["source_blob"]
            .as_str()
            .unwrap()
            .len()
            >= 40
    );

    fs::remove_dir_all(root).unwrap();
}

#[test]
#[cfg(unix)]
fn dispatch_rejects_commit_already_inflight() {
    let root = test_dir("dispatch-inflight");
    let commit = init_repo(&root, "selected");
    let asc = root.join("asc");
    fs::write(
        &asc,
        format!(
            "#!/bin/sh\nif [ \"$1 $2\" = \"storage dispatch-state\" ]; then printf '%s\\n' '{{\"queued_commits\":[],\"inflight_commits\":[\"{}\"],\"busy_sources\":[]}}'; exit 0; fi\nexit 9\n",
            commit
        ),
    )
    .unwrap();
    executable(&asc);

    let error = dispatch::run_commit(
        &root,
        &commit,
        &PlanId("plan.test".into()),
        ResponseAction::Writeback,
        &[],
        &AscClient::new(&asc),
    )
    .unwrap_err();

    assert!(error.to_string().contains("already in flight"));
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[cfg(unix)]
fn repository_and_commit_discovery_are_independent_of_pwd() {
    let root = test_dir("repo-discovery");
    let first = root.join("first");
    let nested = root.join("group").join("second");
    fs::create_dir_all(&first).unwrap();
    fs::create_dir_all(&nested).unwrap();
    let first_commit = init_repo(&first, "first");
    let second_commit = init_repo(&nested, "second");
    assert_ne!(first_commit, second_commit);

    let repos = snapshot::repositories(&root).unwrap();
    let values: Vec<_> = repos.into_iter().map(|record| record.value).collect();
    assert!(values.contains(&fs::canonicalize(&first).unwrap().display().to_string()));
    assert!(values.contains(&fs::canonicalize(&nested).unwrap().display().to_string()));

    let commits = snapshot::commits(&nested).unwrap();
    assert!(commits.iter().any(|record| record.value == second_commit));
    assert!(!commits.iter().any(|record| record.value == first_commit));

    fs::remove_dir_all(root).unwrap();
}

#[test]
fn dispatch_binary_is_single_purpose() {
    let output = Command::new(env!("CARGO_BIN_EXE_dispatch"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(output.status.success());
    let usage = String::from_utf8(output.stdout).unwrap();
    assert!(usage.starts_with("usage: dispatch "));
    assert!(usage.contains("--repo"));
    assert!(usage.contains("--commit"));
    assert!(usage.contains("--plan"));
    assert!(!usage.contains("usage: svc"));
    assert!(!usage.contains("dispatch <command>"));
}
