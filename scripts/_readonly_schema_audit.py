#!/usr/bin/env python3
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
print("step1", flush=True)
import config.env
print("step2", flush=True)
from sqlalchemy import create_engine, text
from db.connection import _normalize_database_url, _engine_connect_args
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from config.env import get_env
print("step3", flush=True)
url = _normalize_database_url(get_env("DATABASE_URL"))
engine = create_engine(url, connect_args=_engine_connect_args(url), pool_pre_ping=True)
print("step4 connect", flush=True)
with engine.connect() as conn:
    print("step5 ping", conn.execute(text("SELECT 1")).scalar(), flush=True)
    vt = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND (table_name ILIKE '%alembic%' OR table_name ILIKE '%migration%') "
        "ORDER BY 1"
    )).scalars().all()
    print("version_tables", vt, flush=True)
    for tbl in ("companies", "contract_awards"):
        rows = conn.execute(text(
            "SELECT column_name, udt_name, character_maximum_length, is_nullable "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name=:t "
            "ORDER BY ordinal_position"
        ), {"t": tbl}).all()
        print(f"COLUMNS_{tbl}", len(rows), flush=True)
        for r in rows:
            print(f"  {r[0]}|{r[1]}|{r[2]}|{r[3]}", flush=True)
