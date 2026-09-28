from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .redis_runtime import FORENSIC_TTL, RedisClient, load_json


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtensionResult:
    task_key: str
    call_id: str
    executor: str
    entrypoint: str
    content: str


def load_registry(path: Path) -> dict[str, Path]:
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkerError(f"extension registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkerError(f"invalid extension registry: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkerError("extension registry must be a JSON object")
    out: dict[str, Path] = {}
    for name, target in raw.items():
        if not isinstance(name, str) or not name or any(ch.isspace() for ch in name):
            raise WorkerError(f"invalid extension name: {name!r}")
        if not isinstance(target, str) or not target.startswith("/"):
            raise WorkerError(f"extension path must be absolute: {name}")
        out[name] = Path(target)
    return out


def run_extension(executable: Path, content: str, timeout: float = 30.0) -> str:
    if not executable.is_file():
        raise WorkerError(f"extension executable not found: {executable}")
    if not executable.stat().st_mode & 0o111:
        raise WorkerError(f"extension is not executable: {executable}")
    try:
        proc = subprocess.run(
            [str(executable)],
            input=content,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"extension timed out: {executable.name}") from exc
    if proc.returncode != 0:
        err = proc.stderr.strip()
        suffix = f": {err}" if err else ""
        raise WorkerError(f"extension exited {proc.returncode}: {executable.name}{suffix}")
    return proc.stdout


def execute_task(client: RedisClient, task_key: str, registry: dict[str, Path], timeout: float = 30.0) -> ExtensionResult:
    task = client.hgetall(task_key)
    if not task:
        raise WorkerError(f"missing task: {task_key}")
    call_id = task.get("call_id", "")
    content_key = task.get("content_key", "")
    if not call_id or not content_key:
        raise WorkerError(f"incomplete task: {task_key}")
    content = load_json(client, content_key)
    input_record = content.get("input") or {}
    input_content = input_record.get("content")
    if not isinstance(input_content, str):
        raise WorkerError(f"call content has no text input: {content_key}")
    executor = task.get("engine", "")
    entrypoint = task.get("entrypoint", "")
    if executor != "extension":
        raise WorkerError(f"unsupported worker executor: {executor}")
    executable = registry.get(entrypoint)
    if executable is None:
        raise WorkerError(f"unregistered extension: {entrypoint}")
    output = run_extension(executable, input_content, timeout)
    return ExtensionResult(
        task_key=task_key,
        call_id=call_id,
        executor=executor,
        entrypoint=entrypoint,
        content=output,
    )


def persist_redis_response(client: RedisClient, result: ExtensionResult) -> str:
    key = f"response:{result.call_id}:content"
    client.hset(key, {"content": result.content})
    client.expire(key, FORENSIC_TTL)
    return key
