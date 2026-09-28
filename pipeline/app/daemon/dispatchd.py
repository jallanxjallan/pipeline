#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from autoscribe.call import build_call_parts
from autoscribe.control import materialize_plan, resolve_plan
from autoscribe.ids import new_ulid
from autoscribe.ingest import ingest_committed_sources
from autoscribe.ledger import record_call
from autoscribe.redis_runtime import RedisClient, enqueue_call, materialize_call

HOME = Path.home()
SOCKET_PATH = Path(os.environ.get("AUTOSCRIBE_DISPATCH_SOCKET", str(HOME / ".local/run/autoscribe/dispatch.sock")))
CONTROL_DB = Path(os.environ.get("AUTOSCRIBE_CONTROL_DB", str(HOME / "Data/control.sql")))
LEDGER_DB = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(HOME / "Data/ledger.sql")))
REPO_ROOT = Path(os.environ.get("AUTOSCRIBE_REPO_ROOT", str(HOME / "Repos"))).resolve()
REDIS_HOST = os.environ.get("AUTOSCRIBE_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("AUTOSCRIBE_REDIS_PORT", "6379"))
MAX_DATAGRAM = 64 * 1024
sock = None


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return cp.stdout


def repo_is_allowed(repo: Path) -> bool:
    try:
        repo.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def parse_plan_line(message: str) -> tuple[str, str] | None:
    matches = []
    for raw in message.splitlines():
        line = raw.strip()
        if line.startswith("Plan:"):
            value = line.split(":", 1)[1].strip()
            if not value:
                raise ValueError("blank Plan: line")
            tokens = value.split()
            if not tokens:
                raise ValueError("blank Plan: line")
            matches.append((value, tokens[-1]))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("dispatch commit requires exactly one Plan: line")
    return matches[0]


def process(payload: dict) -> None:
    repo_raw = payload.get("repo")
    commit = payload.get("commit")
    ref = payload.get("ref")
    if not isinstance(repo_raw, str) or not isinstance(commit, str):
        emit("reject", reason="payload requires repo and commit", payload=payload)
        return
    repo = Path(repo_raw).resolve()
    if not repo_is_allowed(repo):
        emit("reject", reason="repo outside allowed root", repo=str(repo), commit=commit)
        return
    if not repo.exists():
        emit("reject", reason="repo not found", repo=str(repo), commit=commit)
        return
    if ref not in (None, "refs/heads/master"):
        emit("ignore", reason="only master is dispatchable", repo=str(repo), commit=commit, ref=ref)
        return
    try:
        message = git(repo, "show", "-s", "--format=%B", commit)
    except subprocess.CalledProcessError as exc:
        emit("reject", reason="cannot read commit", repo=str(repo), commit=commit, stderr=exc.stderr.strip())
        return
    try:
        parsed = parse_plan_line(message)
    except ValueError as exc:
        emit("reject", reason=str(exc), repo=str(repo), commit=commit)
        return
    if parsed is None:
        emit("ignore", reason="commit has no Plan: line", repo=str(repo), commit=commit, ref=ref)
        return
    plan_text, plan_id = parsed
    try:
        plan_stub = resolve_plan(CONTROL_DB, plan_id)
        if plan_stub is None:
            emit("reject", reason="unknown plan id", repo=str(repo), commit=commit, plan_id=plan_id, plan_text=plan_text)
            return
        plan = materialize_plan(CONTROL_DB, plan_id)
        sources = ingest_committed_sources(repo, commit)
    except Exception as exc:
        emit("error", stage="ingest", repo=str(repo), commit=commit, plan_id=plan_id, error=str(exc))
        return

    client = RedisClient(REDIS_HOST, REDIS_PORT)
    for source in sources:
        call_id = new_ulid()
        content, baggage = build_call_parts(
            repo=str(repo),
            commit=commit,
            ref=ref if isinstance(ref, str) else None,
            plan=plan,
            source=source,
        )
        try:
            materialized = materialize_call(client, call_id, content, baggage)
            created = record_call(
                LEDGER_DB,
                call_id,
                {"content": materialized.content_key, "baggage": materialized.baggage_key},
            )
            enqueue_call(client, call_id)
        except Exception as exc:
            emit("error", stage="activate_call", call_id=call_id, error=str(exc))
            continue
        emit(
            "activated_call",
            call_id=call_id,
            created=created,
            content_key=materialized.content_key,
            baggage_key=materialized.baggage_key,
        )


def cleanup(*_args) -> None:
    global sock
    try:
        if sock is not None:
            sock.close()
    finally:
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
    raise SystemExit(0)


def main() -> int:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    global sock
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    emit(
        "ready",
        socket=str(SOCKET_PATH),
        control_db=str(CONTROL_DB),
        ledger_db=str(LEDGER_DB),
        repo_root=str(REPO_ROOT),
        redis=f"{REDIS_HOST}:{REDIS_PORT}",
        mode="content-baggage",
        app_root=str(APP_ROOT),
    )
    while True:
        try:
            data = sock.recv(MAX_DATAGRAM)
        except InterruptedError:
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as exc:
            emit("reject", reason="invalid json datagram", error=str(exc))
            continue
        if not isinstance(payload, dict):
            emit("reject", reason="payload must be a JSON object")
            continue
        try:
            process(payload)
        except Exception as exc:
            emit("error", stage="process", error=repr(exc), payload=payload)


if __name__ == "__main__":
    raise SystemExit(main())
