#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from autoscribe.ledger import ensure_schema

path = Path(os.environ.get("AUTOSCRIBE_LEDGER_DB", str(Path.home() / "Data/ledger.sql")))
ensure_schema(path)
print(path)
