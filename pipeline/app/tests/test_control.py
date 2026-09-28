import sqlite3,tempfile,unittest,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(APP))
from autoscribe.control import list_plans,materialize_plan,resolve_plan
SCHEMA="""
CREATE TABLE plans (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, description TEXT, plan_type TEXT);
CREATE TABLE steps (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, executor TEXT NOT NULL, entrypoint TEXT);
CREATE TABLE instructions (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE plan_steps (plan_id TEXT NOT NULL, step_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(plan_id,step_id));
CREATE TABLE step_instructions (step_id TEXT NOT NULL, instruction_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(step_id,instruction_id));
"""
class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db_path=Path(self.tmp.name)/"control.sql"; db=sqlite3.connect(self.db_path); db.executescript(SCHEMA)
        db.execute("INSERT INTO plans VALUES (?,?,?,?,?)",("global-plan-id","plan.test","Test Plan","desc","test")); db.execute("INSERT INTO steps VALUES (?,?,?,?,?)",("s1","plan.test#1","Step One","chatgpt","sol"))
        db.execute("INSERT INTO instructions VALUES (?,?,?,?,?)",("i1","rol.test","Role","role","ROLE BODY")); db.execute("INSERT INTO instructions VALUES (?,?,?,?,?)",("i2","ctx.test","Context","context","CONTEXT BODY"))
        db.execute("INSERT INTO plan_steps VALUES (?,?,?)",("global-plan-id","s1",1)); db.execute("INSERT INTO step_instructions VALUES (?,?,?)",("s1","i2",2)); db.execute("INSERT INTO step_instructions VALUES (?,?,?)",("s1","i1",1)); db.commit(); db.close()
    def tearDown(self): self.tmp.cleanup()
    def test_resolve_by_global_id(self): self.assertEqual(resolve_plan(self.db_path,"global-plan-id")["label"],"Test Plan")
    def test_label_is_not_identity(self): self.assertIsNone(resolve_plan(self.db_path,"Test Plan"))
    def test_list_plans_label_and_id(self): self.assertEqual(list_plans(self.db_path),[{"id":"global-plan-id","label":"Test Plan"}])
    def test_materialize_order(self): self.assertEqual([x["id"] for x in materialize_plan(self.db_path,"global-plan-id")["steps"][0]["instructions"]],["i1","i2"])
if __name__=="__main__": unittest.main()
