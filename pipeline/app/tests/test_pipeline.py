import json
import os
import tempfile
import unittest
from pathlib import Path

from autoscribe.executor import next_step_ordinal, prepare_first_step, prepare_step
from autoscribe.exporter import iter_pending_ndjson, response_content
from autoscribe.ledger import record_call, record_response
from autoscribe.redis_runtime import materialize_call, load_json
from autoscribe.worker import execute_task, persist_redis_response, persist_step_result


class FakeRedis:
    def __init__(self): self.hashes = {}; self.expiries = {}; self.zsets = {}
    def ping(self): pass
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score
    def zrange(self, key, start, stop):
        values = list(self.zsets.get(key, {}))
        return values[start:] if stop == -1 else values[start:stop + 1]
    def zrem(self, key, member): self.zsets.setdefault(key, {}).pop(member, None)


def call_parts():
    content = {
        "schema": "autoscribe.call-content.v2",
        "input": {"content": "Body\n"},
        "plan": {"id": "pln_TEST", "steps": [
            {"position": 1, "engine_kind": "script", "engine": "local", "script": "one", "args": {}, "instructions": []},
            {"position": 2, "engine_kind": "script", "engine": "local", "script": "two", "args": {}, "instructions": []},
        ]},
    }
    baggage = {"autoscribe_return": {"schema": "autoscribe.return.v1", "record_id": "inp_TEST", "route": {"kind": "repo"}, "signature": "sig"}}
    return content, baggage


class PipelineTests(unittest.TestCase):
    def test_two_step_result_becomes_final_response(self):
        with tempfile.TemporaryDirectory() as td:
            redis = FakeRedis()
            content, baggage = call_parts()
            call = materialize_call(redis, "01TEST", content, baggage)
            one = Path(td) / "one"; one.write_text("#!/bin/sh\nprintf 'ONE:'; cat\n"); one.chmod(0o755)
            two = Path(td) / "two"; two.write_text("#!/bin/sh\nprintf 'TWO:'; cat\n"); two.chmod(0o755)
            registry = {"one": one, "two": two}

            first = prepare_first_step(redis, "01TEST", call.content_key)
            result1 = execute_task(redis, first.task_key, registry)
            result1_key = persist_step_result(redis, result1)
            call_content = load_json(redis, call.content_key)
            second_ordinal = next_step_ordinal(call_content, result1.ordinal)
            second = prepare_step(redis, "01TEST", call.content_key, second_ordinal, input_key=result1_key)
            result2 = execute_task(redis, second.task_key, registry)
            self.assertEqual(result2.content, "TWO:ONE:Body\n")
            self.assertIsNone(next_step_ordinal(call_content, result2.ordinal))

            response_key = persist_redis_response(redis, result2)
            db = Path(td) / "ledger.sqlite"
            record_call(db, "01TEST", {"content": call.content_key, "baggage": call.baggage_key})
            record_response(db, "01TEST", response_key)
            self.assertEqual(response_content(db, redis, "01TEST"), "TWO:ONE:Body\n")
            pending = json.loads(next(iter(iter_pending_ndjson(db, redis))))
            self.assertEqual(pending["call_id"], "01TEST")
            self.assertEqual(pending["baggage"], baggage)


if __name__ == "__main__": unittest.main()
