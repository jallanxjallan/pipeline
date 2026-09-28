import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from autoscribe.executor import prepare_first_step
from autoscribe.exporter import iter_pending_ndjson, mark_exported, poke_responses, response_content
from autoscribe.ledger import pending_exports, record_call, record_response
from autoscribe.redis_runtime import (
    EXECUTOR_QUEUE_KEY,
    WORKER_QUEUE_KEY,
    enqueue_call,
    materialize_call,
)
from autoscribe.worker import execute_task, persist_redis_response


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.expiries = {}
        self.zsets = {}
        self.pinged = False

    def ping(self):
        self.pinged = True

    def hset(self, key, fields):
        self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, seconds):
        self.expiries[key] = seconds

    def zadd(self, key, score, member):
        self.zsets.setdefault(key, {})[member] = score

    def zrange(self, key, start, stop):
        members = list(self.zsets.get(key, {}))
        if stop == -1:
            return members[start:]
        return members[start : stop + 1]

    def zrem(self, key, member):
        self.zsets.setdefault(key, {}).pop(member, None)


def sample_parts():
    content = {
        "schema": "autoscribe.call-content.v1",
        "input": {"content": "Body\n", "directive": None},
        "plan": {
            "id": "plan-id",
            "steps": [
                {
                    "position": 1,
                    "executor": "extension",
                    "entrypoint": "prepend-seen",
                    "instructions": [],
                }
            ],
        },
    }
    baggage = {
        "schema": "autoscribe.call-baggage.v2",
        "source": {
            "repo": "/home/jeremy/Repos/X.git",
            "commit": "abc",
            "ref": "refs/heads/master",
            "path": "Note.md",
            "blob": "blob-id",
        },
        "output": {
            "adapter": "git",
            "repo": "X.git",
            "identity": "psg.note",
            "path_hint": "Note.md",
            "commit_message": "AutoScribe response: psg.note",
        },
    }
    return content, baggage


class PipelineTests(unittest.TestCase):
    def _complete_response(self, tmp: str):
        redis = FakeRedis()
        content, baggage = sample_parts()
        call = materialize_call(redis, "01TEST", content, baggage)
        enqueue_call(redis, "01TEST")
        self.assertIn("01TEST", redis.zsets[EXECUTOR_QUEUE_KEY])

        prepared = prepare_first_step(redis, "01TEST", call.content_key)
        self.assertEqual(prepared.task_key, "task:01TEST:1")
        self.assertIn(prepared.task_key, redis.zsets[WORKER_QUEUE_KEY])
        task = redis.hashes[prepared.task_key]
        self.assertNotIn("input", task)
        self.assertEqual(task["content_key"], call.content_key)

        executable = Path(tmp) / "prepend-seen"
        executable.write_text("#!/bin/sh\nprintf 'I have seen this\\n\\n'; cat\n", encoding="utf-8")
        os.chmod(executable, 0o755)
        result = execute_task(redis, prepared.task_key, {"prepend-seen": executable})
        response_key = persist_redis_response(redis, result)
        db = Path(tmp) / "ledger.sql"
        record_call(db, "01TEST", {"content": call.content_key, "baggage": call.baggage_key})
        record_response(db, "01TEST", response_key)
        return redis, db, baggage

    def test_pending_stream_contains_baggage_but_not_response_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            redis, db, baggage = self._complete_response(tmp)
            lines = list(iter_pending_ndjson(db, redis))
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["schema"], "autoscribe.export-pending.v1")
            self.assertEqual(record["call_id"], "01TEST")
            self.assertEqual(record["baggage"], baggage)
            self.assertNotIn("content", record)
            self.assertEqual(response_content(db, redis, "01TEST"), "I have seen this\n\nBody\n")

    def test_export_notice_contains_only_pending_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            redis, db, _ = self._complete_response(tmp)
            socket_path = Path(tmp) / "responses.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            listener.bind(str(socket_path))
            listener.settimeout(1)
            try:
                self.assertEqual(poke_responses(db, socket_path), 1)
                payload = listener.recv(32)
            finally:
                listener.close()
            self.assertEqual(payload, b"1")
            self.assertEqual(response_content(db, redis, "01TEST"), "I have seen this\n\nBody\n")

    def test_mark_exported_creates_receipt_and_removes_pending_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            redis, db, _ = self._complete_response(tmp)
            key, created = mark_exported(
                db,
                redis,
                call_id="01TEST",
                repo="X.git",
                commit="deadbeef",
                target_path="Note.md",
            )
            self.assertTrue(created)
            self.assertEqual(key, "export:01TEST:receipt")
            self.assertEqual(redis.hashes[key]["commit"], "deadbeef")
            self.assertEqual(pending_exports(db), [])

    def test_response_content_rejects_missing_redis_response(self):
        redis = FakeRedis()
        content, baggage = sample_parts()
        call = materialize_call(redis, "01TEST", content, baggage)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.sql"
            record_call(db, "01TEST", {"content": call.content_key, "baggage": call.baggage_key})
            record_response(db, "01TEST", "response:missing:content")
            with self.assertRaises(Exception):
                response_content(db, redis, "01TEST")

    def test_bad_baggage_does_not_hide_other_pending_records(self):
        redis = FakeRedis()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.sql"
            for call_id in ("01BAD", "01GOOD"):
                content, baggage = sample_parts()
                call = materialize_call(redis, call_id, content, baggage)
                redis.hset(f"response:{call_id}:content", {"content": "Result\n"})
                record_call(db, call_id, {"content": call.content_key, "baggage": call.baggage_key})
                record_response(db, call_id, f"response:{call_id}:content")
            redis.hashes.pop("call:01BAD:baggage")
            rows = [json.loads(line) for line in iter_pending_ndjson(db, redis)]
            by_id = {row["call_id"]: row for row in rows}
            self.assertEqual(by_id["01BAD"]["schema"], "autoscribe.export-pending-error.v1")
            self.assertEqual(by_id["01GOOD"]["schema"], "autoscribe.export-pending.v1")
            self.assertIn("baggage", by_id["01GOOD"])


if __name__ == "__main__":
    unittest.main()
