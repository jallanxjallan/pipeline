from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autoscribe.redis_runtime import materialize_call
from autoscribe.worker import WorkerError, execute_task, persist_redis_response, run_extension


class FakeRedis:
    def __init__(self): self.hashes = {}; self.expiries = {}; self.zsets = {}
    def ping(self): pass
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score


class WorkerTests(unittest.TestCase):
    def test_extension_prepends_seen(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "prepend.py"
            script.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('I have seen this\\n\\n'+sys.stdin.read())\n")
            script.chmod(0o755)
            self.assertEqual(run_extension(script, "Body\n"), "I have seen this\n\nBody\n")

    def test_execute_task_loads_input_from_content_key(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "extension.py"
            script.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('I have seen this\\n\\n'+sys.stdin.read())\n")
            script.chmod(0o755)
            r = FakeRedis()
            content = {"schema": "autoscribe.call-content.v1", "input": {"content": "Hello\n", "directive": None}, "plan": {}}
            baggage = {"schema": "autoscribe.call-baggage.v1", "source": {}, "output": {}}
            call = materialize_call(r, "01", content, baggage)
            r.hset("task:01:1", {"call_id": "01", "content_key": call.content_key, "engine": "extension", "entrypoint": "prepend-seen", "ordinal": 1})
            result = execute_task(r, "task:01:1", {"prepend-seen": script})
            self.assertEqual(result.content, "I have seen this\n\nHello\n")
            response_key = persist_redis_response(r, result)
            self.assertEqual(response_key, "response:01:content")
            self.assertEqual(r.hashes[response_key]["content"], result.content)

    def test_unregistered_extension_rejected(self):
        r = FakeRedis()
        content = {"schema": "autoscribe.call-content.v1", "input": {"content": "x", "directive": None}, "plan": {}}
        baggage = {"schema": "autoscribe.call-baggage.v1", "source": {}, "output": {}}
        call = materialize_call(r, "01", content, baggage)
        r.hset("task:01:1", {"call_id": "01", "content_key": call.content_key, "engine": "extension", "entrypoint": "arbitrary/path", "ordinal": 1})
        with self.assertRaisesRegex(WorkerError, "unregistered extension"):
            execute_task(r, "task:01:1", {})


if __name__ == "__main__": unittest.main()
