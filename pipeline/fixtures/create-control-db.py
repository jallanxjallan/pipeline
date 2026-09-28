#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE plans (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, description TEXT, plan_type TEXT);
CREATE TABLE steps (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, executor TEXT NOT NULL, entrypoint TEXT);
CREATE TABLE instructions (id TEXT PRIMARY KEY, ref TEXT NOT NULL, label TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE plan_steps (plan_id TEXT NOT NULL REFERENCES plans(id), step_id TEXT NOT NULL REFERENCES steps(id), position INTEGER NOT NULL, PRIMARY KEY(plan_id,step_id));
CREATE TABLE step_instructions (step_id TEXT NOT NULL REFERENCES steps(id), instruction_id TEXT NOT NULL REFERENCES instructions(id), position INTEGER NOT NULL, PRIMARY KEY(step_id,instruction_id));
CREATE UNIQUE INDEX plan_steps_position ON plan_steps(plan_id,position);
CREATE UNIQUE INDEX step_instructions_position ON step_instructions(step_id,position);
"""
def main() -> int:
    path=Path(sys.argv[1] if len(sys.argv)>1 else Path.home()/"Data/control.sql")
    plan=json.loads((Path(__file__).with_name("plan.json")).read_text())
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): path.unlink()
    db=sqlite3.connect(path); db.executescript(SCHEMA)
    db.execute("INSERT INTO plans VALUES (?,?,?,?,?)",(plan['id'],plan['ref'],plan['label'],plan.get('description'),plan.get('plan_type')))
    for step in plan.get('steps',[]):
        db.execute("INSERT INTO steps VALUES (?,?,?,?,?)",(step['id'],step['ref'],step['label'],step['executor'],step.get('entrypoint')))
        db.execute("INSERT INTO plan_steps VALUES (?,?,?)",(plan['id'],step['id'],step['position']))
        for ins in step.get('instructions',[]):
            db.execute("INSERT OR IGNORE INTO instructions VALUES (?,?,?,?,?)",(ins['id'],ins['ref'],ins['label'],ins['kind'],ins['body']))
            db.execute("INSERT INTO step_instructions VALUES (?,?,?)",(step['id'],ins['id'],ins['position']))
    db.commit(); db.close(); print(path); return 0
if __name__=="__main__": raise SystemExit(main())
