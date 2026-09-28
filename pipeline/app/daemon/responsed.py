#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from autoscribe.exporter import mark_exported
from autoscribe.redis_runtime import RedisClient
from autoscribe.responses import (
    ResponsesError,
    add_worktree,
    commit_response,
    current_blob,
    existing_call_commit,
    find_identity_path,
    remove_worktree,
    reset_worktree,
    resolve_repo,
)

HOME = Path.home()
ASC = Path(os.environ.get("AUTOSCRIBE_ASC", str(HOME / ".local/bin/asc")))
LEDGER_DB = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(HOME / "Data/ledger.sql")))
REPO_ROOT = Path(os.environ.get("AUTOSCRIBE_REPO_ROOT", str(HOME / "Repos"))).resolve()
SOCKET_PATH = Path(
    os.environ.get("AUTOSCRIBE_RESPONSE_SOCKET", str(HOME / ".local/run/autoscribe/response.sock"))
)
REDIS_HOST = os.environ.get("AUTOSCRIBE_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("AUTOSCRIBE_REDIS_PORT", "6379"))
sock: socket.socket | None = None
running = True


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def run_asc(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ASC), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def load_pending() -> list[dict]:
    cp = run_asc("export", "pending")
    if cp.returncode != 0:
        raise ResponsesError(cp.stderr.strip() or "asc export pending failed")
    records = []
    for number, raw in enumerate(cp.stdout.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResponsesError(f"invalid pending NDJSON line {number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ResponsesError(f"pending NDJSON line {number} is not an object")
        records.append(value)
    return records


def requested_content(call_id: str) -> str:
    cp = run_asc("export", "content", call_id)
    if cp.returncode != 0:
        raise ResponsesError(cp.stderr.strip() or f"could not load response content for {call_id}")
    return cp.stdout


def parse_git_record(record: dict) -> tuple[str, dict, dict, dict]:
    call_id = record.get("call_id")
    baggage = record.get("baggage")
    if not isinstance(call_id, str) or not call_id:
        raise ResponsesError("pending export has no call_id")
    if not isinstance(baggage, dict):
        raise ResponsesError(f"pending export {call_id} has no baggage object")
    output = baggage.get("output")
    source = baggage.get("source")
    if not isinstance(output, dict):
        raise ResponsesError(f"pending export {call_id} has no output baggage")
    if output.get("adapter") != "git":
        raise ResponsesError(f"unsupported output adapter for {call_id}: {output.get('adapter')!r}")
    for field in ("repo", "identity", "commit_message"):
        if not isinstance(output.get(field), str) or not output[field]:
            raise ResponsesError(f"pending export {call_id} missing output.{field}")
    if source is None:
        source = {}
    if not isinstance(source, dict):
        raise ResponsesError(f"pending export {call_id} has invalid source baggage")
    return call_id, baggage, source, output


def process_record(
    redis: RedisClient,
    repo: Path,
    worktree: Path,
    record: dict,
) -> None:
    call_id, _baggage, source, output = parse_git_record(record)

    already = existing_call_commit(repo, call_id)
    if already:
        path = find_identity_path(repo, output["identity"])
        receipt_key, created = mark_exported(
            LEDGER_DB,
            redis,
            call_id=call_id,
            repo=repo.name,
            commit=already,
            target_path=path,
        )
        emit(
            "response_recovered",
            call_id=call_id,
            repo=repo.name,
            path=path,
            commit=already,
            receipt_key=receipt_key,
            created=created,
        )
        return

    path = find_identity_path(repo, output["identity"])
    expected_blob = source.get("blob")
    if isinstance(expected_blob, str) and expected_blob:
        actual_blob = current_blob(repo, path)
        if actual_blob != expected_blob:
            raise ResponsesError(
                f"target changed since dispatch for {call_id}: {repo.name}:{path} "
                f"expected {expected_blob}, found {actual_blob}"
            )

    # Content is deliberately requested only after the destination repo and identity
    # have been resolved and any source-blob guard has passed.
    content = requested_content(call_id)
    commit = commit_response(
        worktree,
        path=path,
        content=content,
        commit_message=output["commit_message"],
        call_id=call_id,
    )
    receipt_key, created = mark_exported(
        LEDGER_DB,
        redis,
        call_id=call_id,
        repo=repo.name,
        commit=commit,
        target_path=path,
    )
    emit(
        "response_committed",
        call_id=call_id,
        repo=repo.name,
        path=path,
        commit=commit,
        receipt_key=receipt_key,
        created=created,
    )


def process_pending() -> None:
    records = load_pending()
    if not records:
        emit("responses_idle", pending=0)
        return

    grouped: dict[str, list[dict]] = defaultdict(list)
    rejected: list[tuple[dict, Exception]] = []
    for record in records:
        try:
            _call_id, _baggage, _source, output = parse_git_record(record)
            grouped[output["repo"]].append(record)
        except Exception as exc:
            rejected.append((record, exc))

    for record, exc in rejected:
        emit("response_failed", call_id=record.get("call_id"), stage="route", error=str(exc))

    redis = RedisClient(REDIS_HOST, REDIS_PORT)
    redis.ping()
    for repo_name, repo_records in grouped.items():
        try:
            repo = resolve_repo(REPO_ROOT, repo_name)
        except Exception as exc:
            for record in repo_records:
                emit("response_failed", call_id=record.get("call_id"), repo=repo_name, stage="repo", error=str(exc))
            continue

        with tempfile.TemporaryDirectory(prefix="autoscribe-responses-") as tmp:
            worktree = Path(tmp) / "worktree"
            try:
                add_worktree(repo, worktree)
            except Exception as exc:
                for record in repo_records:
                    emit("response_failed", call_id=record.get("call_id"), repo=repo.name, stage="worktree", error=str(exc))
                continue
            try:
                for record in repo_records:
                    try:
                        process_record(redis, repo, worktree, record)
                    except Exception as exc:
                        reset_worktree(worktree)
                        emit(
                            "response_failed",
                            call_id=record.get("call_id"),
                            repo=repo.name,
                            stage="record",
                            error=str(exc),
                        )
            finally:
                remove_worktree(repo, worktree)


def stop(*_args) -> None:
    global running, sock
    running = False
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def cleanup() -> None:
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    global sock
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleanup()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    emit(
        "ready",
        socket=str(SOCKET_PATH),
        ledger_db=str(LEDGER_DB),
        repo_root=str(REPO_ROOT),
        redis=f"{REDIS_HOST}:{REDIS_PORT}",
        asc=str(ASC),
    )

    # Recover rows that may have accumulated while Responses was stopped.
    try:
        process_pending()
    except Exception as exc:
        emit("responses_failed", stage="startup", error=str(exc))

    try:
        while running:
            try:
                data = sock.recv(128)
            except (InterruptedError, OSError):
                if running:
                    continue
                break
            try:
                notice = int(data.decode("ascii").strip())
            except Exception:
                emit("notice_rejected", raw=data.decode("utf-8", "replace"))
                continue
            emit("notice", pending=notice)
            try:
                process_pending()
            except Exception as exc:
                emit("responses_failed", stage="query", error=str(exc))
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
