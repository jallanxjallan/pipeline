from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .redis_runtime import FORENSIC_TTL, RedisClient, load_json

MODEL_LABELS = {
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
    "best": "gpt-5.6-sol",
    "frontier": "gpt-5.6-sol",
    "pro": "gpt-5.6-sol",
    "standard": "gpt-5.6-terra",
    "cheap": "gpt-5.6-terra",
    "mini": "gpt-5.6-terra",
    "nano": "gpt-5.6-luna",
}


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskResult:
    task_key: str
    call_id: str
    ordinal: int
    engine_kind: str
    engine: str
    entrypoint: str
    content: str


# Retain the old public name for callers that imported it directly.
ExtensionResult = TaskResult


def load_registry(path: Path) -> dict[str, Path]:
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


def _step(content: dict, ordinal: int) -> dict:
    plan = content.get("plan") or {}
    steps = plan.get("steps") or []
    matches = [step for step in steps if int(step.get("position", -1)) == int(ordinal)]
    if len(matches) != 1:
        raise WorkerError(f"call plan has no unique step {ordinal}")
    return matches[0]


def _task_input(client: RedisClient, task: dict[str, str], content: dict) -> str:
    input_key = task.get("input_key")
    if input_key:
        record = client.hgetall(input_key)
        if not record or "content" not in record:
            raise WorkerError(f"missing step input: {input_key}")
        return str(record["content"])
    input_record = content.get("input") or {}
    value = input_record.get("content")
    if not isinstance(value, str):
        raise WorkerError(f"call content has no text input: {task.get('content_key', '')}")
    return value


def _instructions_text(step: dict) -> str:
    values = []
    for instruction in step.get("instructions") or []:
        body = instruction.get("body")
        if isinstance(body, str) and body.strip():
            values.append(body.strip())
    return "\n\n".join(values)


def _resolve_model(label: str) -> str:
    if label.startswith("gpt-"):
        return label
    try:
        return MODEL_LABELS[label]
    except KeyError as exc:
        raise WorkerError(f"unknown ChatGPT model label: {label!r}") from exc


def run_chatgpt(step: dict, content: str, timeout: float = 120.0) -> str:
    if step.get("engine_kind") != "llm" or step.get("engine") != "chatgpt":
        raise WorkerError("run_chatgpt requires llm/chatgpt step")
    model_label = step.get("model")
    if not isinstance(model_label, str) or not model_label:
        raise WorkerError("ChatGPT step requires model")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise WorkerError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise WorkerError("Python package 'openai' is not installed") from exc

    args = step.get("args") or {}
    if not isinstance(args, dict):
        raise WorkerError("step args must be an object")
    reserved = {"model", "input", "instructions"}
    conflicts = reserved.intersection(args)
    if conflicts:
        raise WorkerError(f"step args may not override {', '.join(sorted(conflicts))}")

    request: dict[str, object] = {
        "model": _resolve_model(model_label),
        "input": content,
        **args,
    }
    instructions = _instructions_text(step)
    if instructions:
        request["instructions"] = instructions

    try:
        result = OpenAI(api_key=api_key, timeout=timeout).responses.create(**request)
    except Exception as exc:
        raise WorkerError(f"ChatGPT request failed: {type(exc).__name__}: {exc}") from exc
    output = getattr(result, "output_text", None)
    if not isinstance(output, str):
        raise WorkerError("ChatGPT response has no output_text")
    return output


def execute_task(
    client: RedisClient,
    task_key: str,
    registry: dict[str, Path],
    script_timeout: float = 30.0,
    llm_timeout: float = 120.0,
) -> TaskResult:
    task = client.hgetall(task_key)
    if not task:
        raise WorkerError(f"missing task: {task_key}")
    call_id = task.get("call_id", "")
    content_key = task.get("content_key", "")
    if not call_id or not content_key:
        raise WorkerError(f"incomplete task: {task_key}")
    try:
        ordinal = int(task.get("ordinal", ""))
    except ValueError as exc:
        raise WorkerError(f"invalid task ordinal: {task_key}") from exc
    call_content = load_json(client, content_key)
    step = _step(call_content, ordinal)
    input_content = _task_input(client, task, call_content)
    engine_kind = str(step.get("engine_kind") or task.get("engine_kind") or "")
    engine = str(step.get("engine") or task.get("engine") or "")

    if engine_kind == "llm":
        if engine != "chatgpt":
            raise WorkerError(f"unsupported llm engine: {engine}")
        output = run_chatgpt(step, input_content, llm_timeout)
        entrypoint = str(step.get("model") or "")
    elif engine_kind == "script":
        entrypoint = str(step.get("script") or "")
        executable = registry.get(entrypoint)
        if executable is None:
            raise WorkerError(f"unregistered extension: {entrypoint}")
        output = run_extension(executable, input_content, script_timeout)
    elif engine_kind == "rag":
        entrypoint = str(step.get("rag_profile") or "")
        raise WorkerError(f"rag execution is not implemented: {entrypoint}")
    else:
        raise WorkerError(f"unsupported worker engine_kind: {engine_kind}")

    return TaskResult(
        task_key=task_key,
        call_id=call_id,
        ordinal=ordinal,
        engine_kind=engine_kind,
        engine=engine,
        entrypoint=entrypoint,
        content=output,
    )


def persist_step_result(client: RedisClient, result: TaskResult) -> str:
    key = f"result:{result.call_id}:{result.ordinal}:content"
    client.hset(
        key,
        {
            "content": result.content,
            "ordinal": result.ordinal,
            "engine_kind": result.engine_kind,
            "engine": result.engine,
            "entrypoint": result.entrypoint,
        },
    )
    client.expire(key, FORENSIC_TTL)
    return key


def persist_redis_response(client: RedisClient, result: TaskResult) -> str:
    key = f"response:{result.call_id}:content"
    client.hset(
        key,
        {
            "schema": "autoscribe.response.v1",
            "call_id": result.call_id,
            "content": result.content,
            "ordinal": result.ordinal,
            "engine_kind": result.engine_kind,
            "engine": result.engine,
            "entrypoint": result.entrypoint,
        },
    )
    client.expire(key, FORENSIC_TTL)
    return key
