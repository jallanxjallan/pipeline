import unittest

from autoscribe.executor import next_step_ordinal, prepare_first_step, prepare_step
from autoscribe.redis_runtime import WORKER_QUEUE_KEY, materialize_call, load_json


class FakeRedis:
    def __init__(self): self.hashes = {}; self.expiries = {}; self.zsets = {}
    def ping(self): pass
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score


def content():
    return {
        "schema": "autoscribe.call-content.v2",
        "input": {"content": "Body\n"},
        "plan": {"steps": [
            {"position": 1, "engine_kind": "llm", "engine": "chatgpt", "model": "luna", "args": {}, "instructions": []},
            {"position": 2, "engine_kind": "script", "engine": "local", "script": "cleanup", "args": {}, "instructions": []},
        ]},
    }


class ExecutorTests(unittest.TestCase):
    def test_first_and_next_step_are_queued_by_ordinal(self):
        redis = FakeRedis()
        call = materialize_call(redis, "01TEST", content(), {"autoscribe_return": {}})
        ready = prepare_first_step(redis, "01TEST", call.content_key)
        self.assertEqual((ready.ordinal, ready.engine_kind, ready.engine, ready.entrypoint), (1, "llm", "chatgpt", "luna"))
        self.assertNotIn("input_key", redis.hashes[ready.task_key])
        self.assertIn(ready.task_key, redis.zsets[WORKER_QUEUE_KEY])
        self.assertEqual(next_step_ordinal(load_json(redis, call.content_key), 1), 2)
        ready2 = prepare_step(redis, "01TEST", call.content_key, 2, input_key="result:01TEST:1:content")
        self.assertEqual(redis.hashes[ready2.task_key]["input_key"], "result:01TEST:1:content")
        self.assertIsNone(next_step_ordinal(load_json(redis, call.content_key), 2))


if __name__ == "__main__": unittest.main()
