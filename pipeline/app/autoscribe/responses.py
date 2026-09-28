from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .ingest import IngestError, frontmatter_slug, split_frontmatter


class ResponsesError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and cp.returncode != 0:
        raise ResponsesError(cp.stderr.strip() or f"git {' '.join(args)} failed")
    return cp.stdout


def safe_repo_name(value: str) -> str:
    if not value or value in (".", "..") or Path(value).name != value or "/" in value or "\\" in value:
        raise ResponsesError(f"unsafe repo name: {value!r}")
    return value


def resolve_repo(repo_root: Path, repo_name: str) -> Path:
    name = safe_repo_name(repo_name)
    candidates = [repo_root / name]
    if not name.endswith(".git"):
        candidates.append(repo_root / f"{name}.git")
    matches = [p.resolve() for p in candidates if p.is_dir()]
    if len(matches) != 1:
        if not matches:
            raise ResponsesError(f"output repo not found: {repo_name}")
        raise ResponsesError(f"ambiguous output repo: {repo_name}")
    repo = matches[0]
    try:
        repo.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ResponsesError(f"output repo escapes allowed root: {repo}") from exc
    if _git(repo, "rev-parse", "--is-bare-repository").strip() != "true":
        raise ResponsesError(f"output repo is not bare: {repo}")
    return repo


def _safe_tree_path(path: str) -> str:
    p = PurePosixPath(path)
    if not path or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ResponsesError(f"unsafe repository path: {path!r}")
    if any(part.lower() == ".git" for part in p.parts):
        raise ResponsesError(f"unsafe repository path: {path!r}")
    return path


def find_identity_path(repo: Path, identity: str, ref: str = "master") -> str:
    if not identity:
        raise ResponsesError("output identity is required")
    raw = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--name-only", ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if raw.returncode != 0:
        raise ResponsesError(raw.stderr.decode("utf-8", "replace").strip() or f"cannot read {ref}")
    matches: list[str] = []
    for item in raw.stdout.split(b"\0"):
        if not item:
            continue
        try:
            path = item.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not path.lower().endswith(".md"):
            continue
        path = _safe_tree_path(path)
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if blob.returncode != 0:
            continue
        try:
            text = blob.stdout.decode("utf-8")
            prefix, _ = split_frontmatter(text)
            if prefix is None:
                continue
            slug = frontmatter_slug(prefix)
        except (UnicodeDecodeError, IngestError):
            continue
        if slug == identity:
            matches.append(path)
    if not matches:
        raise ResponsesError(f"identity not found in {repo.name}: {identity}")
    if len(matches) != 1:
        raise ResponsesError(f"identity is not unique in {repo.name}: {identity}")
    return matches[0]


def current_blob(repo: Path, path: str, ref: str = "master") -> str:
    return _git(repo, "rev-parse", f"{ref}:{_safe_tree_path(path)}").strip()


def replace_body(path: Path, content: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ResponsesError(f"target file disappeared: {path}") from exc
    try:
        prefix, _ = split_frontmatter(text)
    except IngestError as exc:
        raise ResponsesError(str(exc)) from exc
    if prefix is None:
        raise ResponsesError(f"target has no AutoScribe frontmatter: {path}")
    path.write_text(prefix + content, encoding="utf-8")


def call_trailer(call_id: str) -> str:
    return f"AutoScribe-Call: {call_id}"


def existing_call_commit(repo: Path, call_id: str) -> str | None:
    cp = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "master",
            "--fixed-strings",
            f"--grep={call_trailer(call_id)}",
            "-n",
            "1",
            "--format=%H",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if cp.returncode != 0:
        return None
    value = cp.stdout.strip()
    return value or None


def add_worktree(repo: Path, directory: Path) -> None:
    cp = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--force", str(directory), "master"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if cp.returncode != 0:
        raise ResponsesError(cp.stderr.strip() or f"could not create worktree for {repo}")


def remove_worktree(repo: Path, directory: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(directory)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def reset_worktree(worktree: Path) -> None:
    subprocess.run(["git", "-C", str(worktree), "reset", "--hard", "HEAD"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(worktree), "clean", "-fd"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def commit_response(
    worktree: Path,
    *,
    path: str,
    content: str,
    commit_message: str,
    call_id: str,
) -> str:
    if not commit_message.strip():
        raise ResponsesError("output commit_message is blank")
    target = worktree.joinpath(*PurePosixPath(_safe_tree_path(path)).parts)
    replace_body(target, content)
    _git(worktree, "add", "--", path)
    message = commit_message.rstrip() + "\n\n" + call_trailer(call_id) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(message)
        message_path = Path(handle.name)
    try:
        cp = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "user.name=AutoScribe",
                "-c",
                "user.email=autoscribe@localhost",
                "commit",
                "--allow-empty",
                "-F",
                str(message_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if cp.returncode != 0:
            raise ResponsesError(cp.stderr.strip() or "git commit failed")
    finally:
        message_path.unlink(missing_ok=True)
    return _git(worktree, "rev-parse", "HEAD").strip()
