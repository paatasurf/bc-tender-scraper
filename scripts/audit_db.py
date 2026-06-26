import os, sys
sys.path.insert(0, r"C:\Users\DAVIDSURF\Projects\bc-tender-scraper")
import config.env
from db.connection import get_session
from sqlalchemy import text

s = get_session()
rows = s.execute(text(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"
)).fetchall()
print("=== TABLES ===")
for r in rows:
    print(r[0])

pgv = s.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).fetchall()
print("\n=== PGVECTOR ===")
print("installed:", bool(pgv))
s.close()
