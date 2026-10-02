from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS call_keys (
    call_id TEXT NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    redis_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (call_id, role)
);

CREATE TABLE IF NOT EXISTS responses (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    redis_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS exports (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    redis_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_call_keys_role ON call_keys(role, created_at);
CREATE INDEX IF NOT EXISTS idx_responses_created ON responses(created_at);
CREATE INDEX IF NOT EXISTS idx_exports_created ON exports(created_at);
"""


class LedgerError(RuntimeError):
    pass


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def ensure_schema(path: Path) -> None:
    with closing(connect(path)) as db, db:
        db.executescript(_SCHEMA)


def record_call(path: Path, call_id: str, keys: dict[str, str]) -> bool:
    """Record only call identity plus Redis key references.

    Content and baggage remain exclusively in Redis. Repeating the same call ID is
    idempotent only when it resolves to exactly the same key references.
    """
    if not call_id:
        raise LedgerError("call_id is required")
    if set(keys) != {"content", "baggage"} or any(not value for value in keys.values()):
        raise LedgerError("call requires exactly content and baggage Redis keys")
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        cur = db.execute("INSERT OR IGNORE INTO calls(call_id) VALUES (?)", (call_id,))
        created = cur.rowcount == 1
        for role, redis_key in keys.items():
            db.execute(
                "INSERT OR IGNORE INTO call_keys(call_id, role, redis_key) VALUES (?, ?, ?)",
                (call_id, role, redis_key),
            )
        rows = db.execute(
            "SELECT role, redis_key FROM call_keys WHERE call_id = ? ORDER BY role",
            (call_id,),
        ).fetchall()
        actual = {str(row["role"]): str(row["redis_key"]) for row in rows}
        if actual != keys:
            raise LedgerError(f"call {call_id} already resolves to different Redis keys")
        return created


def load_call_keys(path: Path, call_id: str) -> dict[str, str]:
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        rows = db.execute(
            "SELECT role, redis_key FROM call_keys WHERE call_id = ?",
            (call_id,),
        ).fetchall()
    if not rows:
        raise LedgerError(f"unknown call_id: {call_id}")
    keys = {str(row["role"]): str(row["redis_key"]) for row in rows}
    if set(keys) != {"content", "baggage"}:
        raise LedgerError(f"call {call_id} has incomplete Redis key references")
    return keys


def _record_single_key_fact(path: Path, table: str, call_id: str, redis_key: str) -> bool:
    if table not in {"responses", "exports"}:
        raise ValueError(f"unsupported ledger table: {table}")
    if not redis_key:
        raise LedgerError(f"{table} Redis key is required")
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        cur = db.execute(
            f"INSERT OR IGNORE INTO {table}(call_id, redis_key) VALUES (?, ?)",
            (call_id, redis_key),
        )
        if cur.rowcount == 1:
            return True
        row = db.execute(
            f"SELECT redis_key FROM {table} WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"could not record {table[:-1]} for call {call_id}")
        if str(row["redis_key"]) != redis_key:
            raise LedgerError(f"call {call_id} already has a different {table[:-1]} Redis key")
        return False


def record_response(path: Path, call_id: str, redis_key: str) -> bool:
    return _record_single_key_fact(path, "responses", call_id, redis_key)


def record_export(path: Path, call_id: str, redis_key: str) -> bool:
    return _record_single_key_fact(path, "exports", call_id, redis_key)


def load_response(path: Path, call_id: str) -> dict:
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        row = db.execute("SELECT * FROM responses WHERE call_id = ?", (call_id,)).fetchone()
    if row is None:
        raise LedgerError(f"no response for call_id: {call_id}")
    return dict(row)


def latest_response_id(path: Path) -> str:
    """Return the newest recorded response identity."""
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        row = db.execute(
            """
            SELECT call_id
            FROM responses
            ORDER BY created_at DESC, call_id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise LedgerError("no responses recorded")
    return str(row["call_id"])


def pending_exports(path: Path) -> list[dict]:
    """Return responses that exist as facts but have no export fact yet."""
    ensure_schema(path)
    with closing(connect(path)) as db, db:
        rows = db.execute(
            """
            SELECT r.call_id,
                   r.redis_key AS response_key,
                   b.redis_key AS baggage_key,
                   r.created_at AS response_created_at
            FROM responses r
            JOIN call_keys b
              ON b.call_id = r.call_id AND b.role = 'baggage'
            LEFT JOIN exports e ON e.call_id = r.call_id
            WHERE e.call_id IS NULL
            ORDER BY r.created_at, r.call_id
            """
        ).fetchall()
    return [dict(row) for row in rows]
