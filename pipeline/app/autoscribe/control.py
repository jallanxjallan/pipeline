from __future__ import annotations

from contextlib import closing
import json
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
            "SELECT id, label, description, scope FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_plans(db_path: Path) -> list[dict]:
    with closing(connect(db_path)) as db, db:
        rows = db.execute(
            "SELECT id, label FROM plans ORDER BY label COLLATE NOCASE, id"
        ).fetchall()
    return [dict(row) for row in rows]


def _decode_args(raw: object, *, step_id: str) -> dict:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ControlError(f"step {step_id} has invalid args_json") from exc
    if not isinstance(value, dict):
        raise ControlError(f"step {step_id} args_json is not an object")
    return value


def materialize_plan(db_path: Path, plan_id: str) -> dict:
    with closing(connect(db_path)) as db, db:
        plan_row = db.execute(
            "SELECT id, label, description, scope FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if plan_row is None:
            raise ControlError(f"unknown plan id: {plan_id}")

        step_rows = db.execute(
            """
            SELECT ps.position AS step_position,
                   s.id,
                   s.label,
                   s.engine_kind,
                   s.engine,
                   s.model,
                   s.script,
                   s.rag_profile,
                   s.args_json
            FROM plan_steps ps
            JOIN steps s ON s.id = ps.step_id
            WHERE ps.plan_id = ?
            ORDER BY ps.position, s.id
            """,
            (plan_id,),
        ).fetchall()
        if not step_rows:
            raise ControlError(f"plan {plan_id} has no steps")

        steps = []
        for step in step_rows:
            instruction_rows = db.execute(
                """
                SELECT si.component,
                       si.position,
                       i.id,
                       i.label,
                       i.kind,
                       i.body
                FROM step_instructions si
                JOIN instructions i ON i.id = si.instruction_id
                WHERE si.step_id = ?
                ORDER BY CASE si.component
                           WHEN 'role' THEN 1
                           WHEN 'context' THEN 2
                           WHEN 'task' THEN 3
                           ELSE 4
                         END,
                         si.position,
                         i.id
                """,
                (step["id"],),
            ).fetchall()
            steps.append(
                {
                    "position": int(step["step_position"]),
                    "id": str(step["id"]),
                    "label": str(step["label"]),
                    "engine_kind": str(step["engine_kind"]),
                    "engine": str(step["engine"]),
                    "model": step["model"],
                    "script": step["script"],
                    "rag_profile": step["rag_profile"],
                    "args": _decode_args(step["args_json"], step_id=str(step["id"])),
                    "instructions": [
                        {
                            "component": str(row["component"]),
                            "position": int(row["position"]),
                            "id": str(row["id"]),
                            "label": str(row["label"]),
                            "kind": str(row["kind"]),
                            "body": str(row["body"]),
                        }
                        for row in instruction_rows
                    ],
                }
            )

    return {
        "id": str(plan_row["id"]),
        "label": str(plan_row["label"]),
        "description": str(plan_row["description"] or ""),
        "scope": plan_row["scope"],
        "steps": steps,
    }
