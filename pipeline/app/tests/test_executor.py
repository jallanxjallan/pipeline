import unittest

from autoscribe.executor import prepare_first_step
from autoscribe.redis_runtime import WORKER_QUEUE_KEY, materialize_call


class FakeRedis:
    def __init__(self): self.hashes = {}; self.expiries = {}; self.zsets = {}
    def ping(self): pass
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score


class ExecutorTests(unittest.TestCase):
    def test_task_references_content_instead_of_copying_input(self):
        content = {
            "schema": "autoscribe.call-content.v1",
            "input": {"content": "Body\n", "directive": None},
            "plan": {"steps": [{"position": 1, "executor": "extension", "entrypoint": "prepend-seen"}]},
        }
        baggage = {"schema": "autoscribe.call-baggage.v1", "source": {}, "output": {}}
        redis = FakeRedis()
        call = materialize_call(redis, "01TEST", content, baggage)
        ready = prepare_first_step(redis, "01TEST", call.content_key)
        task = redis.hashes[ready.task_key]
        self.assertEqual(task["content_key"], call.content_key)
        self.assertNotIn("input", task)
        self.assertNotIn("source_identity", task)
        self.assertIn(ready.task_key, redis.zsets[WORKER_QUEUE_KEY])


if __name__ == "__main__": unittest.main()
