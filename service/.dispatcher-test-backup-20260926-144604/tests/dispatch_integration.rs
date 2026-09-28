use autoscribe_service::{
    asc::AscClient,
    operations::dispatch,
    types::{DispatchRequest, PlanId, ResponseAction},
    vault_index::VaultIndexer,
};
use std::{
    fs,
    path::PathBuf,
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
fn executable(path: &std::path::Path) {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
}

#[test]
#[cfg(unix)]
fn dispatch_queries_current_plans_and_enqueues_native_markdown() {
    let root = test_dir("dispatch");
    let vault = root.join("vault");
    let bin = root.join("bin");
    fs::create_dir_all(vault.join("Content")).unwrap();
    fs::create_dir_all(&bin).unwrap();
    fs::write(
        vault.join("Content/Test.md"),
        "---\nslug: pss.test\nstatus: ai-process\nstage: draft\nplan: plan.ignored\n---\n::: directive\nFix spelling.\n:::\n# Body\n\nKeep **Markdown**.\n",
    )
    .unwrap();

    let capture = root.join("enqueue.ndjson");
    let asc = bin.join("asc");
    fs::write(
        &asc,
        format!(
            "#!/bin/sh\nif [ \"$1 $2\" = \"control plans\" ]; then printf '%s\\n' '[{{\"slug\":\"plan.test\",\"label\":\"Test\"}}]'; exit 0; fi\nif [ \"$1\" = \"enqueue\" ]; then cat > '{}'; printf '%s\\n' '{{\"accepted\":true}}'; exit 0; fi\nexit 9\n",
            capture.display()
        ),
    )
    .unwrap();
    executable(&asc);

    let rg = bin.join("rg");
    fs::write(
        &rg,
        "#!/bin/sh\nprintf '%s\n' '{\"type\":\"match\",\"data\":{\"path\":{\"text\":\"./Content/Test.md\"},\"lines\":{\"text\":\"slug: pss.test\\n\"}}}' '{\"type\":\"match\",\"data\":{\"path\":{\"text\":\"./Content/Test.md\"},\"lines\":{\"text\":\"status: ai-process\\n\"}}}'\n",
    )
    .unwrap();
    executable(&rg);

    let response = dispatch::run(
        DispatchRequest {
            vault_root: vault.clone(),
            sources: vec!["Content/Test.md".into()],
            plan: PlanId("plan.test".into()),
            response_action: ResponseAction::Writeback,
        },
        &AscClient::new(&asc),
        &VaultIndexer::new(&rg),
    )
    .unwrap();
    assert_eq!(response, b"{\"accepted\":true}\n");
    let enqueued = fs::read_to_string(&capture).unwrap();
    assert!(enqueued.contains("\"identity\":\"pss.test\""));
    assert!(enqueued.contains("\"action\":\"writeback\""));

    let call: serde_json::Value = serde_json::from_str(&enqueued).unwrap();
    assert_eq!(call["plan"], "plan.test");
    assert_eq!(call["directive"], "Fix spelling.");
    assert_eq!(call["content"], "# Body\n\nKeep **Markdown**.\n");
    assert_eq!(call["extra"]["source"]["path"], "Content/Test.md");
    assert_eq!(call["extra"]["source"]["kind"], "vault");
    assert_eq!(call["extra"]["assets"], serde_json::json!([]));
    for body in [
        "",
        "::: directive\n \n:::\nBody",
        "::: directive\nMissing close",
    ] {
        fs::write(
            vault.join("Content/Test.md"),
            format!("---\nslug: pss.test\nstatus: ai-process\n---\n{body}"),
        )
        .unwrap();
        assert!(
            dispatch::run(
                DispatchRequest {
                    vault_root: vault.clone(),
                    sources: vec!["Content/Test.md".into()],
                    plan: PlanId("plan.test".into()),
                    response_action: ResponseAction::Writeback
                },
                &AscClient::new(&asc),
                &VaultIndexer::new(&rg)
            )
            .is_err()
        );
        assert_eq!(fs::read_to_string(&capture).unwrap(), enqueued);
    }
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[cfg(unix)]
fn dispatch_rejects_source_not_indexed_for_ai_processing() {
    let root = test_dir("dispatch-status");
    let vault = root.join("vault");
    let bin = root.join("bin");
    fs::create_dir_all(vault.join("Content")).unwrap();
    fs::create_dir_all(&bin).unwrap();
    fs::write(
        vault.join("Content/Test.md"),
        "---\nslug: pss.test\nstatus: human-review\n---\nBody\n",
    )
    .unwrap();

    let asc = bin.join("asc");
    fs::write(
        &asc,
        "#!/bin/sh\nif [ \"$1 $2\" = \"control plans\" ]; then printf '%s\\n' '[{\"slug\":\"plan.test\",\"label\":\"Test\"}]'; exit 0; fi\nexit 9\n",
    )
    .unwrap();
    executable(&asc);

    let rg = bin.join("rg");
    fs::write(
        &rg,
        "#!/bin/sh\nprintf '%s\\n' '{\"type\":\"match\",\"data\":{\"path\":{\"text\":\"./Content/Test.md\"},\"lines\":{\"text\":\"slug: pss.test\\n\"}}}' '{\"type\":\"match\",\"data\":{\"path\":{\"text\":\"./Content/Test.md\"},\"lines\":{\"text\":\"status: human-review\\n\"}}}'\n",
    )
    .unwrap();
    executable(&rg);

    let error = dispatch::run(
        DispatchRequest {
            vault_root: vault,
            sources: vec![PathBuf::from("Content/Test.md")],
            plan: PlanId("plan.test".into()),
            response_action: ResponseAction::Writeback,
        },
        &AscClient::new(&asc),
        &VaultIndexer::new(&rg),
    )
    .unwrap_err();

    assert!(
        error
            .to_string()
            .contains("not indexed with status: ai-process")
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[cfg(unix)]
fn tracked_dispatch_retains_clean_file_and_inflight_safety() {
    use autoscribe_service::{inflight, operations::tracked_dispatch};
    let root = test_dir("tracked");
    // Test setup uses the repository-prescribed Git entry point.
    let git = |args: &[&str]| {
        let output = std::process::Command::new("/usr/bin/python3")
            .arg(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("git.py"))
            .arg("-C")
            .arg(&root)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    };
    git(&["init"]);
    git(&["config", "user.name", "Service Test"]);
    git(&["config", "user.email", "service-test@localhost"]);
    let note = root.join("Note.md");
    let original = "---\nslug: pss.test\nstatus: ai-process\n---\nBody\n";
    fs::write(&note, original).unwrap();
    git(&["add", "Note.md"]);
    git(&["commit", "-m", "fixture"]);
    let asc = root.join("asc");
    fs::write(&asc, "#!/bin/sh\nif [ \"$1 $2\" = 'control plans' ]; then echo '[{\"slug\":\"plan.test\",\"label\":\"Test\"}]'; else cat >/dev/null; echo accepted; fi\n").unwrap();
    executable(&asc);
    let request = || DispatchRequest {
        vault_root: root.clone(),
        sources: vec![],
        plan: PlanId("plan.test".into()),
        response_action: ResponseAction::Writeback,
    };
    let client = AscClient::new(&asc);
    let indexer = VaultIndexer::new("/usr/bin/rg");
    fs::write(&note, format!("{original}Dirty\n")).unwrap();
    assert!(
        tracked_dispatch::run(request(), &client, &indexer)
            .unwrap_err()
            .to_string()
            .contains("dirty/untracked")
    );
    assert!(inflight::list(&root).unwrap().is_empty());
    fs::write(&note, original).unwrap();
    assert!(
        tracked_dispatch::run(request(), &client, &indexer)
            .unwrap()
            .starts_with(b"batch=")
    );
    let batches = inflight::list(&root).unwrap();
    assert_eq!(batches.len(), 1);
    assert_eq!(batches[0].state, inflight::BatchState::Dispatched);
    assert_eq!(batches[0].files[0].slug, "pss.test");
    assert_eq!(
        batches[0].files[0].source_blob,
        inflight::head_blob(&root, std::path::Path::new("Note.md"))
            .unwrap()
            .unwrap()
    );
    assert_eq!(
        tracked_dispatch::run(request(), &client, &indexer).unwrap(),
        b"{\"status\":\"nothing-to-dispatch\"}\n"
    );
    inflight::delete(&root, &batches[0]).unwrap();
    fs::write(&asc, "#!/bin/sh\nexit 9\n").unwrap();
    assert!(
        tracked_dispatch::run(request(), &client, &indexer)
            .unwrap_err()
            .to_string()
            .contains("retained as pending")
    );
    assert_eq!(
        inflight::list(&root).unwrap()[0].state,
        inflight::BatchState::Pending
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn dispatch_binary_is_single_purpose() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_dispatch"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(output.status.success());
    let usage = String::from_utf8(output.stdout).unwrap();
    assert!(usage.contains("usage: dispatch"));
    assert!(!usage.contains("usage: writeback"));
    assert!(!usage.contains("usage: writenew"));
}
