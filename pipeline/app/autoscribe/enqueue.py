from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .control import materialize_plan
from .ids import new_ulid
from .ledger import record_call
from .redis_runtime import RedisClient, enqueue_call, materialize_call

INPUT_SCHEMA = "autoscribe.input.v1"
CALL_SCHEMA = "autoscribe.call-content.v2"


class EnqueueError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnqueueResult:
    record_id: str
    call_id: str
    created: bool
    content_key: str
    baggage_key: str


def _required_text(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise EnqueueError(f"input {key} must be non-empty text")
    return value


def validate_input_record(record: dict) -> tuple[str, str]:
    if not isinstance(record, dict):
        raise EnqueueError("input record must be an object")
    if record.get("schema") != INPUT_SCHEMA:
        raise EnqueueError(f"unsupported input schema: {record.get('schema')!r}")

    record_id = _required_text(record, "record_id")
    content = _required_text(record, "content")
    content_sha256 = _required_text(record, "content_sha256")
    actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if content_sha256 != actual_sha256:
        raise EnqueueError("input content_sha256 does not match content")

    source = record.get("source")
    routing = record.get("routing")
    baggage = record.get("baggage")
    if not isinstance(source, dict):
        raise EnqueueError("input source must be an object")
    if not isinstance(routing, dict):
        raise EnqueueError("input routing must be an object")
    if not isinstance(baggage, dict):
        raise EnqueueError("input baggage must be an object")
    if "outputs" in baggage:
        raise EnqueueError("baggage.outputs is not accepted")
    if "autoscribe_return" not in baggage:
        raise EnqueueError("input baggage missing autoscribe_return")

    plan_id = routing.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.startswith("pln_"):
        raise EnqueueError("routing.plan_id must be a pln_ identity")
    return record_id, plan_id


def build_call_content(record: dict, plan: dict) -> dict:
    return {
        "schema": CALL_SCHEMA,
        "record_id": record["record_id"],
        "source": record["source"],
        "routing": record["routing"],
        "input": {
            "content": record["content"],
            "content_sha256": record["content_sha256"],
        },
        "plan": plan,
    }


def enqueue_record(
    *,
    record: dict,
    control_db: Path,
    ledger_db: Path,
    client: RedisClient,
) -> EnqueueResult:
    record_id, plan_id = validate_input_record(record)
    plan = materialize_plan(control_db, plan_id)
    call_id = new_ulid()
    content = build_call_content(record, plan)
    baggage = dict(record["baggage"])
    materialized = materialize_call(client, call_id, content, baggage)
    created = record_call(
        ledger_db,
        call_id,
        {"content": materialized.content_key, "baggage": materialized.baggage_key},
    )
    enqueue_call(client, call_id)
    return EnqueueResult(
        record_id=record_id,
        call_id=call_id,
        created=created,
        content_key=materialized.content_key,
        baggage_key=materialized.baggage_key,
    )
