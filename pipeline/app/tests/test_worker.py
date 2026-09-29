from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoscribe.redis_runtime import materialize_call
from autoscribe.worker import WorkerError, execute_task, persist_step_result, run_chatgpt, run_extension


class FakeRedis:
    def __init__(self): self.hashes = {}; self.expiries = {}; self.zsets = {}
    def ping(self): pass
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score


class WorkerTests(unittest.TestCase):
    def test_script_step_reads_first_call_input(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "prepend.py"
            script.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('SEEN:'+sys.stdin.read())\n")
            script.chmod(0o755)
            redis = FakeRedis()
            content = {"input": {"content": "Hello"}, "plan": {"steps": [{"position": 1, "engine_kind": "script", "engine": "local", "script": "prepend", "args": {}, "instructions": []}]}}
            call = materialize_call(redis, "01", content, {"autoscribe_return": {}})
            redis.hset("task:01:1", {"call_id": "01", "content_key": call.content_key, "ordinal": 1, "engine_kind": "script", "engine": "local", "entrypoint": "prepend"})
            result = execute_task(redis, "task:01:1", {"prepend": script})
            self.assertEqual(result.content, "SEEN:Hello")
            key = persist_step_result(redis, result)
            self.assertEqual(redis.hashes[key]["content"], "SEEN:Hello")

    def test_missing_openai_key_is_explicit(self):
        step = {"engine_kind": "llm", "engine": "chatgpt", "model": "luna", "args": {}, "instructions": []}
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WorkerError, "OPENAI_API_KEY"):
                run_chatgpt(step, "hello")


if __name__ == "__main__": unittest.main()
