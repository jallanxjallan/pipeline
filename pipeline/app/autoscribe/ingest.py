from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath


class IngestError(RuntimeError):
    pass


def _git(repo: Path, *args: str, binary: bool = False):
    cp = subprocess.run(["git", "-C", str(repo), *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if cp.returncode != 0:
        raise IngestError(cp.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    return cp.stdout if binary else cp.stdout.decode("utf-8")


def _safe_path(path: str) -> str:
    p = PurePosixPath(path)
    if not path or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise IngestError(f"unsafe source path: {path!r}")
    if any(part.lower() == ".git" for part in p.parts):
        raise IngestError(f"unsafe source path: {path!r}")
    return path


def changed_markdown_paths(repo: Path, commit: str) -> list[str]:
    # The server repo is bare. Select only paths changed by this dispatch commit.
    fields = _git(repo, "rev-list", "--parents", "-n", "1", commit).strip().split()
    if not fields:
        raise IngestError(f"commit not found: {commit}")
    if len(fields) > 2:
        raise IngestError("merge dispatch commits are not supported")
    if len(fields) == 1:
        raw = _git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "--diff-filter=AM", "-z", commit, binary=True)
    else:
        raw = _git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=AM", "-z", fields[1], commit, binary=True)
    paths: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            path = item.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestError(f"source path is not UTF-8: {exc}") from exc
        if path.lower().endswith(".md"):
            paths.append(_safe_path(path))
    return paths


def split_frontmatter(text: str) -> tuple[str | None, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text
    offset = len(lines[0])
    for line in lines[1:]:
        offset += len(line)
        if line.rstrip("\r\n") == "---":
            return text[:offset], text[offset:]
    raise IngestError("unterminated leading YAML frontmatter")


def _decode_slug_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        raise IngestError("blank frontmatter slug")
    if value.startswith('"'):
        decoder = json.JSONDecoder()
        try:
            parsed, end = decoder.raw_decode(value)
        except json.JSONDecodeError as exc:
            raise IngestError("invalid double-quoted frontmatter slug") from exc
        if not isinstance(parsed, str):
            raise IngestError("frontmatter slug must be text")
        remainder = value[end:].strip()
        if remainder and not remainder.startswith("#"):
            raise IngestError("invalid text after frontmatter slug")
        return parsed
    if value.startswith("'"):
        out = []
        i = 1
        while i < len(value):
            if value[i] == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                remainder = value[i + 1:].strip()
                if remainder and not remainder.startswith("#"):
                    raise IngestError("invalid text after frontmatter slug")
                return "".join(out)
            out.append(value[i])
            i += 1
        raise IngestError("unterminated single-quoted frontmatter slug")
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def frontmatter_slug(prefix: str) -> str:
    matches = []
    lines = prefix.splitlines()
    for line in lines[1:-1]:
        m = re.match(r"^slug\s*:\s*(.*)$", line)
        if m:
            matches.append(_decode_slug_scalar(m.group(1)))
    if len(matches) != 1:
        raise IngestError("source requires exactly one frontmatter slug")
    return matches[0]


def extract_directive(body: str) -> tuple[str | None, str]:
    leading = body.lstrip(" \t\r\n")
    lines = leading.splitlines(keepends=True)
    if not lines:
        return None, body
    if lines[0].rstrip("\r\n") not in ("::: directive", "::: {.directive}"):
        return None, body
    start = len(lines[0])
    offset = start
    for line in lines[1:]:
        if line.rstrip("\r\n") == ":::":
            directive = leading[start:offset].strip()
            if not directive:
                raise IngestError("leading directive is blank")
            return directive, leading[offset + len(line):]
        offset += len(line)
    raise IngestError("unterminated leading directive")


def ingest_committed_sources(repo: Path, commit: str) -> list[dict[str, str | None]]:
    # Production boundary: the server transport repo is bare. Read exact source bytes
    # from the selected commit's Git tree; never require a server-side checkout.
    _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    records = []
    seen: set[str] = set()
    for path in changed_markdown_paths(repo, commit):
        commit_blob = _git(repo, "rev-parse", f"{commit}:{path}").strip()
        raw = _git(repo, "show", f"{commit}:{path}", binary=True)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestError(f"{path}: committed file is not UTF-8") from exc
        prefix, body = split_frontmatter(text)
        # Markdown without AutoScribe frontmatter is repository/project metadata.
        if prefix is None:
            continue
        slug = frontmatter_slug(prefix)
        if slug in seen:
            raise IngestError(f"duplicate slug in dispatch: {slug}")
        seen.add(slug)
        directive, body = extract_directive(body)
        if not body.strip():
            raise IngestError(f"{path}: source body is blank")
        records.append({"identity": slug, "path": path, "blob": commit_blob, "directive": directive, "content": body})
    if not records:
        raise IngestError("dispatch commit contains no eligible AutoScribe source files")
    return records
