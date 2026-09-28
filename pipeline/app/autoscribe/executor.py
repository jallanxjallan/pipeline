from __future__ import annotations

from dataclasses import dataclass

from .redis_runtime import FORENSIC_TTL, WORKER_QUEUE_KEY, RedisClient, load_json


class ExecutorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedExecution:
    call_id: str
    task_key: str
    ordinal: int
    engine: str
    entrypoint: str


def prepare_first_step(client: RedisClient, call_id: str, content_key: str) -> PreparedExecution:
    content = load_json(client, content_key)
    plan = content.get("plan") or {}
    steps = plan.get("steps") or []
    if not steps:
        raise ExecutorError("call plan has no steps")
    first = min(steps, key=lambda step: int(step["position"]))
    ordinal = int(first["position"])
    engine = str(first["executor"])
    entrypoint = str(first["entrypoint"])
    task_key = f"task:{call_id}:{ordinal}"
    client.hset(
        task_key,
        {
            "call_id": call_id,
            "content_key": content_key,
            "ordinal": ordinal,
            "engine": engine,
            "entrypoint": entrypoint,
        },
    )
    client.expire(task_key, FORENSIC_TTL)
    client.zadd(WORKER_QUEUE_KEY, 0, task_key)
    return PreparedExecution(call_id, task_key, ordinal, engine, entrypoint)
