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

from autoscribe.executor import prepare_first_step
from autoscribe.ledger import load_call_keys
from autoscribe.redis_runtime import EXECUTOR_QUEUE_KEY, FORENSIC_TTL, RedisClient

HOME = Path.home()
LEDGER_DB = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(HOME / "Data/ledger.sql")))
REDIS_HOST = os.environ.get("AUTOSCRIBE_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("AUTOSCRIBE_REDIS_PORT", "6379"))
POLL_SECONDS = float(os.environ.get("AUTOSCRIBE_EXECUTOR_POLL_SECONDS", "2"))
running = True


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def stop(*_args) -> None:
    global running
    running = False


def process_active(client: RedisClient) -> None:
    for call_id in client.zrange(EXECUTOR_QUEUE_KEY, 0, 31):
        try:
            keys = load_call_keys(LEDGER_DB, call_id)
            ready = prepare_first_step(client, call_id, keys["content"])
            client.zrem(EXECUTOR_QUEUE_KEY, call_id)
            emit(
                "executor_ready",
                call_id=call_id,
                task_key=ready.task_key,
                ordinal=ready.ordinal,
                engine=ready.engine,
                entrypoint=ready.entrypoint,
            )
        except Exception as exc:
            client.zrem(EXECUTOR_QUEUE_KEY, call_id)
            diagnostic_key = f"diagnostic:{call_id}:executor"
            client.hset(diagnostic_key, {"error": str(exc)})
            client.expire(diagnostic_key, FORENSIC_TTL)
            emit("executor_failed", call_id=call_id, diagnostic_key=diagnostic_key, error=str(exc))


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = RedisClient(REDIS_HOST, REDIS_PORT)
    client.ping()
    emit("ready", ledger_db=str(LEDGER_DB), redis=f"{REDIS_HOST}:{REDIS_PORT}")
    while running:
        process_active(client)
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
