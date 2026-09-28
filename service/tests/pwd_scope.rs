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
        "autoscribe-pwd-scope-{label}-{}-{stamp}",
        std::process::id()
    ));
    fs::create_dir_all(&path).unwrap();
    path
}

fn git(repo: &Path, args: &[&str]) -> std::process::Output {
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
    output
}

fn fixture(label: &str) -> (PathBuf, PathBuf) {
    let root = test_dir(label);
    git(&root, &["init"]);
    git(&root, &["config", "user.name", "Scope Test"]);
    git(&root, &["config", "user.email", "scope-test@localhost"]);
    fs::write(root.join("Note.md"), "---\nslug: pss.test\n---\nBody\n").unwrap();
    git(&root, &["add", "Note.md"]);
    git(&root, &["commit", "-m", "fixture"]);
    let nested = root.join("nested");
    fs::create_dir(&nested).unwrap();
    (root, nested)
}

fn writeback_from(cwd: &Path, args: &[&str], asc: Option<&Path>) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_writeback"));
    command
        .current_dir(cwd)
        .env(
            "AUTOSCRIBE_GIT_PY",
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("git.py"),
        )
        .args(args);
    if let Some(asc) = asc {
        command.env("AUTOSCRIBE_ASC", asc);
    }
    command.output().unwrap()
}

#[cfg(unix)]
fn fake_asc(root: &Path) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let asc = root.join("asc-test");
    fs::write(
        &asc,
        "#!/bin/sh\nif [ \"$1 $2\" = \"storage dispatch-state\" ]; then echo '{\"inflight_commits\":[],\"busy_sources\":[]}'; exit 0; fi\nexit 9\n",
    )
    .unwrap();
    fs::set_permissions(&asc, fs::Permissions::from_mode(0o700)).unwrap();
    asc
}

#[test]
fn write_responses_refuses_to_widen_nested_pwd_to_parent_repository() {
    let (root, nested) = fixture("writeback");
    let output = writeback_from(&nested, &[], None);
    assert!(!output.status.success());
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(
        stderr.contains("write-responses is constrained to $PWD"),
        "{stderr}"
    );
    assert!(stderr.contains(&root.display().to_string()), "{stderr}");
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[cfg(unix)]
fn repository_root_pwd_is_the_exact_writeback_authority() {
    let (root, _nested) = fixture("root");
    let asc = fake_asc(&root);
    let writeback = writeback_from(&root, &[], Some(&asc));
    assert!(
        writeback.status.success(),
        "{}",
        String::from_utf8_lossy(&writeback.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&writeback.stdout).unwrap();
    assert_eq!(report["available_results"], 0);
    assert!(report["written"].as_array().unwrap().is_empty());

    fs::remove_dir_all(root).unwrap();
}
