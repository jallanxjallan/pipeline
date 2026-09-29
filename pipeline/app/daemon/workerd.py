#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from autoscribe.executor import next_step_ordinal, prepare_step
from autoscribe.ledger import record_response
from autoscribe.redis_runtime import FORENSIC_TTL, WORKER_QUEUE_KEY, RedisClient, load_json
from autoscribe.worker import execute_task, load_registry, persist_redis_response, persist_step_result

HOME = Path.home()
LEDGER_DB = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(HOME / "Data/ledger.sql")))
REGISTRY = Path(os.environ.get("AUTOSCRIBE_EXTENSION_REGISTRY", "/opt/autoscribe/extensions/registry.json"))
REDIS_HOST = os.environ.get("AUTOSCRIBE_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("AUTOSCRIBE_REDIS_PORT", "6379"))
POLL_SECONDS = float(os.environ.get("AUTOSCRIBE_WORKER_POLL_SECONDS", "1"))
SCRIPT_TIMEOUT = float(os.environ.get("AUTOSCRIBE_EXTENSION_TIMEOUT_SECONDS", "30"))
LLM_TIMEOUT = float(os.environ.get("AUTOSCRIBE_LLM_TIMEOUT_SECONDS", "120"))
running = True


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def stop(*_args) -> None:
    global running
    running = False


def process_ready(client: RedisClient, registry: dict[str, Path]) -> None:
    for task_key in client.zrange(WORKER_QUEUE_KEY, 0, 31):
        client.zrem(WORKER_QUEUE_KEY, task_key)
        call_id = None
        try:
            task = client.hgetall(task_key)
            call_id = task.get("call_id") if task else None
            result = execute_task(
                client,
                task_key,
                registry,
                script_timeout=SCRIPT_TIMEOUT,
                llm_timeout=LLM_TIMEOUT,
            )
            result_key = persist_step_result(client, result)
            content_key = task.get("content_key", "")
            call_content = load_json(client, content_key)
            next_ordinal = next_step_ordinal(call_content, result.ordinal)
            if next_ordinal is not None:
                ready = prepare_step(
                    client,
                    result.call_id,
                    content_key,
                    next_ordinal,
                    input_key=result_key,
                )
                emit(
                    "worker_step_completed",
                    call_id=result.call_id,
                    task_key=task_key,
                    ordinal=result.ordinal,
                    result_key=result_key,
                    next_task_key=ready.task_key,
                    next_ordinal=ready.ordinal,
                    engine=result.engine,
                    entrypoint=result.entrypoint,
                )
                continue

            response_key = persist_redis_response(client, result)
            created = record_response(LEDGER_DB, result.call_id, response_key)
            emit(
                "worker_completed",
                call_id=result.call_id,
                task_key=task_key,
                ordinal=result.ordinal,
                result_key=result_key,
                response_key=response_key,
                engine=result.engine,
                entrypoint=result.entrypoint,
                created=created,
            )
        except Exception as exc:
            if call_id:
                diagnostic_key = f"diagnostic:{call_id}:worker"
                client.hset(diagnostic_key, {"task_key": task_key, "error": str(exc)})
                client.expire(diagnostic_key, FORENSIC_TTL)
            else:
                diagnostic_key = None
            emit(
                "worker_failed",
                call_id=call_id,
                task_key=task_key,
                diagnostic_key=diagnostic_key,
                error=str(exc),
            )


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    registry = load_registry(REGISTRY)
    client = RedisClient(REDIS_HOST, REDIS_PORT)
    client.ping()
    emit(
        "ready",
        registry=str(REGISTRY),
        extensions=sorted(registry),
        redis=f"{REDIS_HOST}:{REDIS_PORT}",
        llm_timeout=LLM_TIMEOUT,
    )
    while running:
        process_ready(client, registry)
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
