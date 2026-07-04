# Production database safety

This document defines how TenderScope CLI scripts must connect to Postgres so
that **local development is the default** and **production writes require explicit,
interactive confirmation**.

## Root cause (2026-07-03)

`scripts/run_company_canonical_merge.py --apply` ran against production because
`DATABASE_URL` in `.env` pointed at Railway (`*.proxy.rlwy.net`). The agent
intended a local run; there was no structural gate.

A related hang: read-only probes that called `init_db()` triggered schema DDL
and Class D escalation against production. Probes must use `check_db_connection()`
+ `get_session()` only — **never** `init_db()`.

## Environment layout

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | **Local Postgres** for CLI scripts (set in `.env.local`) |
| `DATABASE_URL_PRODUCTION` | Railway production URL — **never** the default for scripts |
| Railway runtime | Injects `DATABASE_URL` on the deployed API/worker — unchanged |

### Setup

1. Copy `.env.local.example` → `.env.local`
2. Move any Railway URL from `DATABASE_URL` in `.env` to `DATABASE_URL_PRODUCTION`
3. Ensure `.env.local` sets `DATABASE_URL=postgresql://...@localhost:5432/bc_tenders`

Load order (`config/env.py`):

1. Snapshot keys already in the process environment (Railway deploy, test harness)
2. `.env`
3. `.env.local` — overrides `.env` for keys **not** in the startup snapshot
4. CWD `.env` fallback

Explicit shell env (including test `DATABASE_URL=...`) always wins over file-based config.

## Safety classes (A–D)

See `scripts/CLASSIFICATION.md` for the full 93-script registry.

| Class | Name | Writes DB | Calls init_db | Production |
|-------|------|-----------|---------------|------------|
| **A** | No Write | No (read-only SELECT or no DB) | No | Read-only via `--use-production` when script reads DB |
| **B** | Local Write | Yes (data) | No | Local default; `--allow-production` + phrase for prod |
| **C** | Registry Write | Yes (registry data) | Sometimes | Local default; `--allow-production` + valid dry-run artifact |
| **D** | Schema DDL | Yes (schema/data) | Yes | `--allow-production` + confirmation phrase only |

**A vs B:** Class A never mutates the database (includes scripts with no Postgres
connection and read-only probes). Class B performs local data writes (backfill,
cache warm, staging loads) but not registry merge workflows.

### Runtime escalation

The **highest-risk operation actually executed at runtime** determines the
**effective** class, regardless of nominal class.

If a Class A, B, or C script calls `init_db()` or any DDL mid-run, `db_safety.py`
escalates to **Class D** at that point and re-checks authorization (including
production confirmation when applicable).

**Read-only probes (Class A) must not call `init_db()`.** Use
`check_db_connection()` then `get_session()` for SELECT-only access.

## `db/db_safety.py` — mandatory gate

**Every script under `scripts/` that touches the database must call one of:**

| Helper | Class | When |
|--------|-------|------|
| `guard_readonly_db(script_name)` | A | SELECT-only probes, audits, dry-runs |
| `guard_local_write_db(...)` | B | Backfills, cache warm, staging data writes |
| `guard_destructive_db(...)` | C/D | Registry apply, `init_db()`, migrations, imports |

All guards print a banner **before any query executes**:

```
=========================================================
Target Database
Environment: LOCAL | PRODUCTION
Host: {host}
Database: {dbname}
Mode: READ-ONLY | DESTRUCTIVE
Nominal Class: Class A (No Write) | ...
Effective Class: ...
Script: {script_name}
=========================================================
```

### Production detection (non-overridable)

Any host matching:

- `*.proxy.rlwy.net`
- `*.rlwy.net`
- `*.railway.internal`

Optional extra hosts: comma-separated `DB_PRODUCTION_HOSTS`.

There is **no** “this Railway host is really local” override.

### Class A — read-only scripts

- Production connection: **allowed** (banner shows `PRODUCTION`)
- Use `--use-production` to connect via `DATABASE_URL_PRODUCTION` when
  `DATABASE_URL` is local
- **Never** call `init_db()` — opens SELECT-only session via `get_session()`

### Class B — local write scripts

Blocked on production unless **both**:

1. `--allow-production` flag
2. Interactive prompt — type exactly:  
   `I UNDERSTAND THIS WILL MODIFY PRODUCTION`

### Class C/D — registry and schema scripts

Same production gate as Class B. Class C `--apply` additionally requires a
fresh dry-run artifact with matching `git_commit_sha` and `dataset_fingerprint`.

Non-interactive stdin (CI, piped input): **always refused** for production writes.

Authorized production writes append to `logs/destructive_operations.log`:

```
{iso_timestamp}\tscript=...\thost=...\tdatabase=...\tuser=...\tconfirmed=true
```

## Script conventions

Read-only probe (Class A):

```python
from db.connection import check_db_connection, get_session
from db.db_safety import guard_readonly_db

guard_readonly_db(script_name=Path(__file__).name)
if not check_db_connection():
    raise SystemExit(1)
session = get_session()
# SELECT only — never init_db()
```

Local write (Class B):

```python
from db.classification import SafetyClass
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args

add_production_safety_args(parser)
args = parser.parse_args()
guard_destructive_db_from_args(
    args,
    script_name=Path(__file__).name,
    nominal_class=SafetyClass.B,
)
from db.connection import get_session  # after guard
```

Destructive / schema (Class C/D):

```python
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args

parser = argparse.ArgumentParser(...)
add_production_safety_args(parser)
args = parser.parse_args()
guard_destructive_db_from_args(args, script_name=Path(__file__).name, operation="merge apply")
from db.connection import get_session, init_db  # after guard
init_db()
```

## What this does **not** change

- FastAPI / Railway deploy: still uses injected `DATABASE_URL`
- `run_pipeline.py` scheduler on Railway: unchanged
- n8n / GitHub Actions: no merge script automation exists today

## Testing the guard (simulated URL only)

```powershell
$env:DATABASE_URL = "postgresql://test:test@acela.proxy.rlwy.net:47306/railway"
python scripts/run_company_canonical_merge.py --apply
# Expect: [db_safety] Refusing ... (exit 1), no DB connection attempted
```

```powershell
$env:DATABASE_URL = "postgresql://test:test@localhost:5432/bc_tenders"
python scripts/run_company_canonical_merge.py --report exports/test_merge_report.json
# Expect: banner Environment: LOCAL, then dry-run (may fail if Postgres not running)
```

Never use a real production credential for guard testing.
