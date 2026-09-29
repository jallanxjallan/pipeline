from __future__ import annotations

import json
from dataclasses import dataclass

from .redis_runtime import FORENSIC_TTL, WORKER_QUEUE_KEY, RedisClient, load_json


class ExecutorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedExecution:
    call_id: str
    task_key: str
    ordinal: int
    engine_kind: str
    engine: str
    entrypoint: str


def ordered_steps(content: dict) -> list[dict]:
    plan = content.get("plan") or {}
    steps = plan.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ExecutorError("call plan has no steps")
    try:
        return sorted(steps, key=lambda step: int(step["position"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutorError("call plan has invalid step positions") from exc


def _step_entrypoint(step: dict) -> str:
    kind = str(step.get("engine_kind") or "")
    if kind == "llm":
        value = step.get("model")
    elif kind == "script":
        value = step.get("script")
    elif kind == "rag":
        value = step.get("rag_profile")
    else:
        raise ExecutorError(f"unsupported engine_kind: {kind!r}")
    if not isinstance(value, str) or not value:
        raise ExecutorError(f"step {step.get('position')} has no {kind} entrypoint")
    return value


def prepare_step(
    client: RedisClient,
    call_id: str,
    content_key: str,
    ordinal: int,
    *,
    input_key: str | None = None,
) -> PreparedExecution:
    content = load_json(client, content_key)
    steps = ordered_steps(content)
    matches = [step for step in steps if int(step["position"]) == int(ordinal)]
    if len(matches) != 1:
        raise ExecutorError(f"call plan has no unique step {ordinal}")
    step = matches[0]
    engine_kind = str(step.get("engine_kind") or "")
    engine = str(step.get("engine") or "")
    if not engine:
        raise ExecutorError(f"step {ordinal} has no engine")
    entrypoint = _step_entrypoint(step)
    task_key = f"task:{call_id}:{ordinal}"
    fields: dict[str, object] = {
        "call_id": call_id,
        "content_key": content_key,
        "ordinal": ordinal,
        "engine_kind": engine_kind,
        "engine": engine,
        "entrypoint": entrypoint,
        "args_json": json.dumps(step.get("args") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
    if input_key:
        fields["input_key"] = input_key
    client.hset(task_key, fields)
    client.expire(task_key, FORENSIC_TTL)
    client.zadd(WORKER_QUEUE_KEY, 0, task_key)
    return PreparedExecution(call_id, task_key, int(ordinal), engine_kind, engine, entrypoint)


def prepare_first_step(client: RedisClient, call_id: str, content_key: str) -> PreparedExecution:
    content = load_json(client, content_key)
    first = ordered_steps(content)[0]
    return prepare_step(client, call_id, content_key, int(first["position"]))


def next_step_ordinal(content: dict, ordinal: int) -> int | None:
    positions = [int(step["position"]) for step in ordered_steps(content)]
    later = [position for position in positions if position > int(ordinal)]
    return min(later) if later else None
