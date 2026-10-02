import sqlite3
import tempfile
import unittest
from pathlib import Path

from autoscribe.ledger import (
    LedgerError,
    ensure_schema,
    latest_response_id,
    load_call_keys,
    pending_exports,
    record_call,
    record_export,
    record_response,
)


class LedgerTests(unittest.TestCase):
    def test_ledger_contains_keys_and_timestamps_not_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sql"
            ensure_schema(path)
            with sqlite3.connect(path) as db:
                calls_cols = [row[1] for row in db.execute("PRAGMA table_info(calls)")]
                key_cols = [row[1] for row in db.execute("PRAGMA table_info(call_keys)")]
            self.assertEqual(calls_cols, ["call_id", "created_at"])
            self.assertEqual(key_cols, ["call_id", "role", "redis_key", "created_at"])
            for forbidden in ("content", "canonical_json", "source_repo", "plan_id", "state"):
                self.assertNotIn(forbidden, calls_cols)
                self.assertNotIn(forbidden, key_cols)

    def test_call_keys_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sql"
            keys = {"content": "call:01TEST:content", "baggage": "call:01TEST:baggage"}
            self.assertTrue(record_call(path, "01TEST", keys))
            self.assertFalse(record_call(path, "01TEST", keys))
            self.assertEqual(load_call_keys(path, "01TEST"), keys)
            with self.assertRaises(LedgerError):
                record_call(path, "01TEST", {"content": "other", "baggage": keys["baggage"]})

    def test_latest_response_id_returns_newest_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sql"
            record_call(path, "01FIRST", {"content": "c1", "baggage": "b1"})
            record_response(path, "01FIRST", "r1")
            record_call(path, "01SECOND", {"content": "c2", "baggage": "b2"})
            record_response(path, "01SECOND", "r2")
            self.assertEqual(latest_response_id(path), "01SECOND")

    def test_response_and_export_are_relational_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sql"
            record_call(path, "01TEST", {"content": "c", "baggage": "b"})
            self.assertTrue(record_response(path, "01TEST", "r"))
            self.assertFalse(record_response(path, "01TEST", "r"))
            pending = pending_exports(path)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["response_key"], "r")
            self.assertEqual(pending[0]["baggage_key"], "b")
            self.assertTrue(record_export(path, "01TEST", "e"))
            self.assertEqual(pending_exports(path), [])


if __name__ == "__main__":
    unittest.main()
