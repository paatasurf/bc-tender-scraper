#!/usr/bin/env python3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("1 import env", flush=True)
import config.env  # noqa: F401

from db.db_safety import guard_readonly_db
from db.connection import get_engine
from sqlalchemy import text

_SCRIPT = Path(__file__).name
guard_readonly_db(_SCRIPT)

print("2 get_engine", flush=True)

t0 = time.perf_counter()
engine = get_engine()
print(f"3 engine ready {time.perf_counter()-t0:.1f}s", flush=True)

t0 = time.perf_counter()
with engine.connect() as conn:
    val = conn.execute(text("SELECT 1")).scalar()
print(f"4 ping={val} {time.perf_counter()-t0:.1f}s", flush=True)
