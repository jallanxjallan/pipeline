//! Read committed source through the repository's required Git entry point.
use crate::{ServiceError, ServiceResult, selector::Record};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

pub fn git_command() -> Command {
    let wrapper = std::env::var_os("AUTOSCRIBE_GIT_PY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/home/jeremy/Loom/server/service/git.py"));
    let mut command = Command::new("/usr/bin/python3");
    command.arg(wrapper);
    command
}

pub fn git(repo: &Path, args: &[&str]) -> ServiceResult<Vec<u8>> {
    let output = git_command()
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|error| ServiceError::Process(format!("git.py: {error}")))?;
    if !output.status.success() {
        return Err(ServiceError::Process(format!(
            "git.py: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(output.stdout)
}

fn text(bytes: Vec<u8>) -> ServiceResult<String> {
    String::from_utf8(bytes)
        .map_err(|error| ServiceError::InvalidInput(format!("Git output is not UTF-8: {error}")))
}

pub fn root(path: &Path) -> ServiceResult<PathBuf> {
    Ok(PathBuf::from(
        text(git(path, &["rev-parse", "--show-toplevel"])?)?.trim_end(),
    ))
}

fn constrained_pwd_root(cwd: &Path, command_name: &str) -> ServiceResult<PathBuf> {
    let cwd = fs::canonicalize(cwd)
        .map_err(|error| ServiceError::Io(format!("current directory: {error}")))?;
    let output = git_command()
        .env("LC_ALL", "C")
        .arg("-C")
        .arg(&cwd)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .map_err(|error| ServiceError::Process(format!("git.py: {error}")))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        // Keep executable, permissions, and other Git failures distinguishable
        // from an ordinary directory (or a bare repository).
        if stderr.contains("not a git repository") || stderr.contains("must be run in a work tree")
        {
            return Err(ServiceError::InvalidInput(format!(
                "{command_name} requires $PWD to be a Git repository root (working tree)"
            )));
        }
        return Err(ServiceError::Process(format!("git.py: {}", stderr.trim())));
    }
    let repo = fs::canonicalize(PathBuf::from(text(output.stdout)?.trim_end()))
        .map_err(|error| ServiceError::Io(format!("repository root: {error}")))?;
    if repo != cwd {
        return Err(ServiceError::InvalidInput(format!(
            "{command_name} is constrained to $PWD; run it from the repository root {}",
            repo.display()
        )));
    }
    Ok(repo)
}

/// Writeback authority is exactly $PWD. Never widen a nested current directory to
/// the containing Git worktree.
pub fn writeback_root(cwd: &Path) -> ServiceResult<PathBuf> {
    constrained_pwd_root(cwd, "write-responses")
}

/// Discover Git working trees below a configured repository root. Discovery follows
/// directory symlinks, deduplicates canonical paths, and never descends into .git.
pub fn repositories(root: &Path) -> ServiceResult<Vec<Record>> {
    use std::collections::{BTreeSet, VecDeque};
    let root = fs::canonicalize(root).map_err(|error| {
        ServiceError::Io(format!("repository root {}: {error}", root.display()))
    })?;
    let mut queue = VecDeque::from([root.clone()]);
    let mut visited = BTreeSet::new();
    let mut repos = Vec::new();

    while let Some(dir) = queue.pop_front() {
        let canonical = match fs::canonicalize(&dir) {
            Ok(path) => path,
            Err(_) => continue,
        };
        if !visited.insert(canonical.clone()) {
            continue;
        }
        if canonical.join(".git").exists() {
            let label = canonical
                .strip_prefix(&root)
                .ok()
                .filter(|p| !p.as_os_str().is_empty())
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| canonical.display().to_string());
            repos.push(Record {
                value: canonical.display().to_string(),
                display: label,
            });
            continue;
        }
        let entries = match fs::read_dir(&canonical) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            if entry.file_name() == std::ffi::OsStr::new(".git") {
                continue;
            }
            let path = entry.path();
            let is_dir = entry
                .file_type()
                .map(|t| t.is_dir() || t.is_symlink())
                .unwrap_or(false);
            if is_dir {
                queue.push_back(path);
            }
        }
    }
    repos.sort_by_key(|repo| repo.display.to_lowercase());
    if repos.is_empty() {
        return Err(ServiceError::InvalidInput(format!(
            "no Git repositories found below {}",
            root.display()
        )));
    }
    Ok(repos)
}

pub fn default_repositories_root() -> PathBuf {
    std::env::var_os("AUTOSCRIBE_REPOS")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/home/jeremy/Repos"))
}

pub fn full_commit(repo: &Path, value: &str) -> ServiceResult<String> {
    if !matches!(value.len(), 40 | 64) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(ServiceError::InvalidInput(
            "--commit requires a full commit SHA".into(),
        ));
    }
    let resolved = text(git(
        repo,
        &["rev-parse", "--verify", &format!("{value}^{{commit}}")],
    )?)?;
    let resolved = resolved.trim().to_string();
    if !resolved.eq_ignore_ascii_case(value) {
        return Err(ServiceError::InvalidInput(
            "commit SHA did not resolve exactly".into(),
        ));
    }
    Ok(resolved)
}

pub fn commits(repo: &Path) -> ServiceResult<Vec<Record>> {
    // Sort by committer timestamp explicitly, including histories with clock skew.
    // NUL separators keep arbitrary subject punctuation separate from machine fields.
    let output = text(git(
        repo,
        &["log", "--format=%H%x00%ct%x00%cI%x00%s", "HEAD", "--"],
    )?)?;
    let mut rows = Vec::new();
    for line in output.lines() {
        let fields: Vec<_> = line.splitn(4, '\0').collect();
        if fields.len() != 4 {
            return Err(ServiceError::InvalidInput("malformed commit record".into()));
        }
        let timestamp = fields[1]
            .parse::<i64>()
            .map_err(|error| ServiceError::InvalidInput(format!("commit timestamp: {error}")))?;
        rows.push((
            timestamp,
            Record {
                value: fields[0].to_owned(),
                display: format!("{}  {}  {}", &fields[0][..7], fields[2], fields[3]),
            },
        ));
    }
    rows.sort_by_key(|row| std::cmp::Reverse(row.0));
    Ok(rows.into_iter().map(|(_, record)| record).collect())
}

pub fn paths(repo: &Path, commit: &str) -> ServiceResult<Vec<String>> {
    // The first-parent delta defines the selected commit's changed files, including
    // merge commits. Root commits include their complete tree.
    let parents = text(git(repo, &["rev-list", "--parents", "-n", "1", commit])?)?;
    let parent = parents.split_whitespace().nth(1);
    let bytes = match parent {
        Some(parent) => git(
            repo,
            &[
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--diff-filter=AM",
                parent,
                commit,
                "--",
            ],
        ),
        None => git(repo, &["ls-tree", "-r", "--name-only", "-z", commit]),
    }?;
    text(bytes)?
        .split('\0')
        .filter(|path| path.ends_with(".md"))
        .map(|path| Ok(path.to_owned()))
        .collect()
}

pub fn blob(repo: &Path, commit: &str, path: &str) -> ServiceResult<(String, String)> {
    let spec = format!("{commit}:{path}");
    let oid = text(git(repo, &["rev-parse", "--verify", &spec])?)?
        .trim()
        .to_owned();
    let body = text(git(repo, &["cat-file", "blob", &oid])?)?;
    Ok((oid, body))
}
