#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ledger = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(Path.home() / "Data/ledger.sql")))
if len(sys.argv) > 1:
    call_id = sys.argv[1]
else:
    with sqlite3.connect(ledger) as db:
        row = db.execute("SELECT call_id FROM calls ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("no calls in ledger")
    call_id = row[0]

with sqlite3.connect(ledger) as db:
    rows = db.execute("SELECT role, redis_key FROM call_keys WHERE call_id = ? ORDER BY role", (call_id,)).fetchall()
    response = db.execute("SELECT redis_key FROM responses WHERE call_id = ?", (call_id,)).fetchone()

keys = {role: key for role, key in rows}
if response:
    keys["response"] = response[0]
print(json.dumps({"call_id": call_id, "keys": keys}, indent=2, sort_keys=True))
for key in keys.values():
    print(f"\n## {key}")
    subprocess.run(["redis-cli", "--raw", "HGETALL", key], check=False)
print("\n## queue:executor")
subprocess.run(["redis-cli", "--raw", "ZRANGE", "queue:executor", "0", "-1", "WITHSCORES"], check=False)
print("\n## queue:worker")
subprocess.run(["redis-cli", "--raw", "ZRANGE", "queue:worker", "0", "-1", "WITHSCORES"], check=False)
