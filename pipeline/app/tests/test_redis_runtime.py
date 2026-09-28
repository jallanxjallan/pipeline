import json
import unittest

from autoscribe.redis_runtime import EXECUTOR_QUEUE_KEY, enqueue_call, load_json, materialize_call


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.expiries = {}
        self.zsets = {}
        self.pinged = False

    def ping(self): self.pinged = True
    def hset(self, key, fields): self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in fields.items()})
    def hgetall(self, key): return dict(self.hashes.get(key, {}))
    def expire(self, key, seconds): self.expiries[key] = seconds
    def zadd(self, key, score, member): self.zsets.setdefault(key, {})[member] = score


class RuntimeTests(unittest.TestCase):
    def test_materializes_only_call_content_and_baggage_then_queues_id(self):
        redis = FakeRedis()
        content = {"schema": "autoscribe.call-content.v1", "input": {"content": "Body\n", "directive": None}, "plan": {"steps": []}}
        baggage = {"schema": "autoscribe.call-baggage.v1", "source": {"repo": "/r.git"}, "output": {"adapter": "git"}}
        call = materialize_call(redis, "01TEST", content, baggage)
        enqueue_call(redis, "01TEST")
        self.assertTrue(redis.pinged)
        self.assertEqual(call.content_key, "call:01TEST:content")
        self.assertEqual(call.baggage_key, "call:01TEST:baggage")
        self.assertEqual(load_json(redis, call.content_key), content)
        self.assertEqual(load_json(redis, call.baggage_key), baggage)
        self.assertIn("01TEST", redis.zsets[EXECUTOR_QUEUE_KEY])
        self.assertEqual(set(redis.hashes), {call.content_key, call.baggage_key})


if __name__ == "__main__": unittest.main()
