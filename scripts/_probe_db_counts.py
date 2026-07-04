from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import json
import config.env
from sqlalchemy import text
from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
s = get_session()
counts = {}
for t in ["contract_awards", "companies", "odbus_reference", "orgbook_reference"]:
    try:
        counts[t] = s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
    except Exception as e:
        counts[t] = str(e)
counts["awards_distinct_winners"] = s.execute(
    text("SELECT COUNT(DISTINCT winner_company) FROM contract_awards WHERE winner_company != ''")
).scalar()
counts["tier_a"] = s.execute(
    text("SELECT COUNT(*) FROM companies WHERE company_tier='tier_a'")
).scalar()
counts["canonical"] = s.execute(
    text("SELECT COUNT(*) FROM companies WHERE entity_role='canonical'")
).scalar()
s.close()
print(json.dumps(counts, indent=2))
