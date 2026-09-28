from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path


class ControlError(RuntimeError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise ControlError(f"control database not found: {db_path}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def resolve_plan(db_path: Path, plan_id: str) -> dict | None:
    with closing(connect(db_path)) as db, db:
        row = db.execute(
            "SELECT id, ref, label, description, plan_type FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_plans(db_path: Path) -> list[dict]:
    with closing(connect(db_path)) as db, db:
        rows = db.execute("SELECT id, label FROM plans ORDER BY label COLLATE NOCASE, id").fetchall()
    return [dict(row) for row in rows]


def materialize_plan(db_path: Path, plan_id: str) -> dict:
    with closing(connect(db_path)) as db, db:
        plan_row = db.execute(
            "SELECT id, ref, label, description, plan_type FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if plan_row is None:
            raise ControlError(f"unknown plan id: {plan_id}")
        step_rows = db.execute(
            """
            SELECT ps.position AS step_position, s.id, s.ref, s.label, s.executor, s.entrypoint
            FROM plan_steps ps
            JOIN steps s ON s.id = ps.step_id
            WHERE ps.plan_id = ?
            ORDER BY ps.position, s.id
            """,
            (plan_id,),
        ).fetchall()
        steps = []
        for step in step_rows:
            instruction_rows = db.execute(
                """
                SELECT si.position, i.id, i.ref, i.label, i.kind, i.body
                FROM step_instructions si
                JOIN instructions i ON i.id = si.instruction_id
                WHERE si.step_id = ?
                ORDER BY si.position, i.id
                """,
                (step["id"],),
            ).fetchall()
            steps.append({
                "position": step["step_position"],
                "id": step["id"],
                "ref": step["ref"],
                "label": step["label"],
                "executor": step["executor"],
                "entrypoint": step["entrypoint"],
                "instructions": [{
                    "position": row["position"], "id": row["id"], "ref": row["ref"],
                    "label": row["label"], "kind": row["kind"], "body": row["body"],
                } for row in instruction_rows],
            })
    return {
        "id": plan_row["id"], "ref": plan_row["ref"], "label": plan_row["label"],
        "description": plan_row["description"], "plan_type": plan_row["plan_type"], "steps": steps,
    }
