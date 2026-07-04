import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config.env
from sqlalchemy import create_engine, text
from db.connection import _normalize_database_url, _engine_connect_args
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from config.env import get_env
url = _normalize_database_url(get_env("DATABASE_URL"))
with create_engine(url, connect_args=_engine_connect_args(url)).connect() as c:
    addr, db, usr = c.execute(text("SELECT inet_server_addr()::text, current_database(), current_user")).one()
    print("SERVER", addr, db, usr)
    pi = c.execute(text(
        "SELECT column_name, udt_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='parsed_identities' ORDER BY ordinal_position"
    )).all()
    for row in pi:
        print("parsed_identities", row)
