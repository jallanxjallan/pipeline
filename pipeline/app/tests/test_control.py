import sqlite3
import tempfile
import unittest
from pathlib import Path

from autoscribe.control import list_plans, materialize_plan, resolve_plan

SCHEMA = """
CREATE TABLE plans (id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT, scope TEXT);
CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    engine_kind TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT,
    script TEXT,
    rag_profile TEXT,
    args_json TEXT NOT NULL
);
CREATE TABLE instructions (id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE plan_steps (plan_id TEXT NOT NULL, step_id TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE step_instructions (
    step_id TEXT NOT NULL,
    instruction_id TEXT NOT NULL,
    component TEXT NOT NULL,
    position INTEGER NOT NULL
);
"""


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "control.sqlite"
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO plans VALUES (?,?,?,?)", ("pln_TEST", "Test Plan", "desc", None))
        db.execute(
            "INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
            ("stp_1", "Step One", "llm", "chatgpt", "luna", None, None, '{"temperature":0.2}'),
        )
        db.execute("INSERT INTO instructions VALUES (?,?,?,?)", ("rol_1", "Role", "role", "ROLE BODY"))
        db.execute("INSERT INTO instructions VALUES (?,?,?,?)", ("ctx_1", "Context", "context", "CONTEXT BODY"))
        db.execute("INSERT INTO plan_steps VALUES (?,?,?)", ("pln_TEST", "stp_1", 1))
        db.execute("INSERT INTO step_instructions VALUES (?,?,?,?)", ("stp_1", "ctx_1", "context", 1))
        db.execute("INSERT INTO step_instructions VALUES (?,?,?,?)", ("stp_1", "rol_1", "role", 1))
        db.commit()
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_by_identity(self):
        self.assertEqual(resolve_plan(self.db_path, "pln_TEST")["label"], "Test Plan")

    def test_label_is_not_identity(self):
        self.assertIsNone(resolve_plan(self.db_path, "Test Plan"))

    def test_list_plans_label_and_id(self):
        self.assertEqual(list_plans(self.db_path), [{"id": "pln_TEST", "label": "Test Plan"}])

    def test_materialize_schema3_step_and_instruction_order(self):
        plan = materialize_plan(self.db_path, "pln_TEST")
        step = plan["steps"][0]
        self.assertEqual(step["engine_kind"], "llm")
        self.assertEqual(step["engine"], "chatgpt")
        self.assertEqual(step["model"], "luna")
        self.assertEqual(step["args"], {"temperature": 0.2})
        self.assertEqual([x["id"] for x in step["instructions"]], ["rol_1", "ctx_1"])


if __name__ == "__main__":
    unittest.main()
