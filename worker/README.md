# Company-enrichment worker service

Deployment-preparation artifact only (Phase 3D). This service is **built
but not deployed, not wired into anything, and inert** — see
[`docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md`](../docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md)
§3.2, §4 (the binding security contract this file follows), §8 stage 5,
and §16 (risks/abort conditions) for the full architecture and rationale.
Nothing in this document authorizes creating a Railway service, deploying,
setting production variables, applying migration 034/035, or setting
`ENRICHMENT_ENABLED` — each remains its own separate, explicit,
human-authorized step.

A single authenticated route (`POST /lookup`) that calls the existing,
already-tested `WebsiteContactProvider.lookup()` and returns its
`ProviderResult` unchanged over HTTP, plus an unauthenticated `GET /health`
liveness route. See [`worker/app.py`](app.py)'s own module docstring for
the code-level contract.

---

## 1. Local run

Two ways to run the worker locally, for development/inspection only —
neither is how it would run in production (that's Railway, §5 below).

### 1a. Directly with Python (fastest, no Docker required)

```bash
pip install -r worker/requirements.txt
python -m playwright install chromium   # one-time, ~150-300MB download
export ENRICHMENT_WORKER_API_KEY=some-local-dev-secret   # required, see §3
uvicorn worker.app:app --host 127.0.0.1 --port 8080
```

Run from the **repository root** (not `worker/`), so `worker.app:app`
resolves as a package path and the app's own imports of `pipeline.*` /
`db.*` / `config.*` succeed.

Do **not** set `DATABASE_URL` for this — the worker never needs it (§7
below). If a `.env` file happens to exist in your local repo root, be
aware `config/env.py` auto-loads it into the process environment at
import time; a local dev `.env` reaching the worker this way is harmless
for local dev but is exactly the property [`worker/Dockerfile`](Dockerfile)'s
`.dockerignore` allow-list exists to prevent inside the built image.

### 1b. Via Docker (matches the eventual deployed artifact)

```bash
# from the repository root — build context must include pipeline/, db/,
# config/ alongside worker/ itself
docker build -f worker/Dockerfile -t enrichment-worker .

docker run --rm -p 8080:8080 \
  -e ENRICHMENT_WORKER_API_KEY=some-local-dev-secret \
  -e PORT=8080 \
  enrichment-worker
```

---

## 2. Healthcheck

`GET /health` — deliberately unauthenticated (§4.3 of the security
contract: Railway's own healthcheck prober cannot supply a secret
header), returns `{"status": "ok"}` and nothing else. No DB access, no
fetch capability, no company data reachable from this route.

```bash
curl -s http://127.0.0.1:8080/health
# {"status":"ok"}
```

[`worker/Dockerfile`](Dockerfile) also declares a Docker-level
`HEALTHCHECK` (Python's own `urllib`, not `curl`, since the latter isn't
guaranteed present in the base image) against this same route, for local
`docker run`/`docker ps` visibility. This is **not** the same thing as a
Railway service healthcheck — once a real Railway worker service exists
(§5, not created by this change), that service's own dashboard config or
a future `worker/railway.toml` governs Railway's healthcheck path/timeout,
mirroring the main API's `healthcheckPath = "/api/health"` convention in
the repo-root `railway.toml` (which this change does not touch).

## 3. Required: `ENRICHMENT_WORKER_API_KEY`

`POST /lookup` requires a shared-secret header, checked **before**
parsing the request body, before touching `WebsiteContactProvider`,
before any outbound fetch (§4.2):

```
X-Enrichment-Worker-Key: <the same value as ENRICHMENT_WORKER_API_KEY>
```

- Compared with `hmac.compare_digest()` (constant-time), mirroring
  `api/internal.py`'s existing `_require_internal_key()` pattern exactly.
- **Fail-closed**: if `ENRICHMENT_WORKER_API_KEY` is unset on the server,
  every request is rejected (403) — never a default/empty-string bypass.
- **Deliberately a separate secret from `INTERNAL_API_KEY`** — compromise
  of one must never imply compromise of the other (§4.2).
- Without this header (or with a wrong value), `/lookup` returns `403`.
  `/health` never requires it.

```bash
curl -s -X POST http://127.0.0.1:8080/lookup \
  -H "X-Enrichment-Worker-Key: some-local-dev-secret" \
  -H "Content-Type: application/json" \
  -d '{"company_id": 1, "company_name": "Example Co", "website": "example.com"}'
```

## 4. Railway internal networking

Once actually deployed (a separate, later, explicitly-authorized step —
§5), the worker service **must** be reachable only over Railway's private
internal network between services in the same project (§4.1):

- The main API reaches it via an env var (e.g. `ENRICHMENT_WORKER_URL`)
  resolving to an internal `*.railway.internal` hostname — never a public
  Railway domain. `db/db_safety.py`'s existing `_PRODUCTION_HOST_MARKERS`
  already recognizes this hostname convention, confirming it's an
  established pattern in this codebase, not a new concept.
- **No public DNS entry should be created for the worker service** unless
  Railway's private-networking feature is confirmed unavailable for this
  project's plan tier at implementation time — if so, that is an explicit,
  separately-flagged deviation requiring its own sign-off before deploy,
  never a silent fallback to a public domain.
- Network isolation (this section) and the auth header (§3 above) are
  **defense-in-depth for each other, not alternatives** — neither
  substitutes for the other (§4.3).

## 5. No public unauthenticated `/lookup` — ever

Restated as a hard rule, not just a consequence of §3/§4: **the worker
must never be reachable from the public internet without the auth header
check in front of it, even temporarily "for testing"** (§16's explicit
"categorically do not" list). The only route that is ever deliberately
unauthenticated is `/health` (§2), and that route does nothing beyond
reporting liveness — no DB access, no fetch capability, no company data.

If a future deploy step accidentally exposes a public Railway domain for
this service (§4.1's stated risk), that is a release-blocking
misconfiguration, not a matter of degree.

## 6. Order of a future deploy (not performed by this change)

This document does not create a Railway service, build an image, push
it, or deploy it. When that is separately authorized, the execution
plan's own ordering (§8) applies. The worker service corresponds to
**stage 5** of 7 and is independently deployable (no dependency on
migrations 034/035, no dependency on the remote adapter existing yet):

1. Build the image (`docker build -f worker/Dockerfile .`) and confirm it
   starts locally (§1) with `/health` responding and `/lookup` rejecting
   an unauthenticated request.
2. Create the Railway service itself (project dashboard or CLI) — **not
   done by this document**. Configure it for **private networking only**
   (§4).
3. Set `ENRICHMENT_WORKER_API_KEY` on the new service (a fresh, unique
   secret — never reused from `INTERNAL_API_KEY` or any other service) —
   **not done by this document**.
4. Deploy. Verify `/health` over the private network, verify `/lookup`
   still 403s without the header, verify **no** public DNS entry exists
   for the service.
5. Re-review the full §4 security contract against the actually-running
   service (not just the code) before treating stage 5 as complete — per
   §8 stage 5's own verification requirement, "reviewed as its own PR."
6. Only after stage 5 is independently reviewed and complete does stage 4
   (`WebsiteContactRemoteProvider`, main-API side) get deployed as
   *reachable* code, and only after that does shadow mode (stage 6) or
   the feature flag (stage 7) become relevant. Building the worker and
   wiring it into `_default_providers()` in the same change is explicitly
   forbidden (§16) — each is its own reviewable checkpoint.

## 7. No DB credentials — the worker never holds `DATABASE_URL`

Execution-plan §4.8, a hard requirement: this worker never receives
`DATABASE_URL` or any credential capable of reaching the production
database. Traced precisely, not just asserted:

- `worker/app.py` never calls `db.connection.get_engine()`,
  `get_session()`, or `init_db()` — the only three places in this
  codebase that ever read `DATABASE_URL` or create a SQLAlchemy engine.
  `_NoDatabaseSession` (in `worker/app.py`) stands in for the `Session`
  argument `WebsiteContactProvider.lookup()` requires; its `.get()`
  always returns `None`, which that function already handles gracefully
  by falling through to the caller-supplied `website` field.
- Importing `WebsiteContactProvider` **does** transitively import
  `db.connection` as a module (via `db/__init__.py`'s own `from
  db.connection import get_engine, get_session, init_db`) — but importing
  a module only executes its top-level statements, and `db/connection.py`
  has none that read an environment variable or open a connection (its
  only module-level statement outside a function/class body is a plain
  tuple constant, `TRANSIENT_DB_ERROR_MARKERS`). No connection is ever
  attempted unless one of those three functions is actually *called*,
  which nothing in the worker's code path does.
- The one real risk in this whole chain: `config/env.py` (imported
  transitively via `db.connection`) calls `load_app_env()` **at its own
  module import time** — this auto-loads a `.env` file's keys into the
  process environment if one exists on disk, via `python-dotenv`, with no
  further code involved. This is why [`worker/Dockerfile`](Dockerfile)'s
  `.dockerignore` (repo root) is an **allow-list**, not a deny-list —
  it structurally guarantees no `.env` file (this repo's root genuinely
  has one today) ever reaches the built image, regardless of what else
  changes at the repo root over time.

### If `DATABASE_URL` is accidentally set on the deployed service anyway

This is a real, plausible operational mistake — e.g. a human copies the
main API's Railway variable set onto the worker service's own dashboard
"for consistency." The worker is **structurally safe** if that happens,
by the trace above, not by a runtime check that rejects the value:

- No engine, no session, no connection: `get_engine()`/`get_session()`/
  `init_db()` are simply never called anywhere in `worker/app.py`,
  `worker/auth.py`, or `worker/models.py` — the presence of the variable
  changes nothing, because nothing in the worker's code path ever reads
  `os.environ["DATABASE_URL"]` or `os.environ.get("DATABASE_URL")` in the
  first place (verified by
  `tests/unit/test_worker_deployment.py::test_worker_source_files_never_read_database_url_in_code`,
  a static check for that exact pattern, not a substring match that
  would also flag this file's own prose).
- No DB query is ever issued as a result — `WebsiteContactProvider.
  lookup()` is called with `_NoDatabaseSession()` regardless of what
  `DATABASE_URL` is set to; that stub's `.get()` always returns `None`
  and never touches a real database.
- No log line, error message, or HTTP response body in `worker/app.py`
  ever includes an environment variable's value — only `correlation_id`,
  `company_id`, `matched`, `error`, and `elapsed_s` are logged (see
  `worker/app.py`'s own `/lookup` handler), none of which is
  environment-derived.
- Regression-tested directly:
  `tests/unit/test_worker_deployment.py::test_worker_ignores_database_url_if_accidentally_set`
  sets a sentinel `DATABASE_URL` value, asserts `/health` and `/lookup`
  both still succeed, asserts `db.connection.get_engine`/`get_session`/
  `init_db` are never called, and asserts the sentinel value never
  appears in any captured log record across the request.

This satisfies the "document and test that the variable is never used by
the worker" property directly — there is deliberately no code path added
that detects and rejects a present `DATABASE_URL` at startup (that would
be a `worker/app.py` behavior change, out of this deployment-artifact
review's scope); the safety property instead holds because the variable
is never read at all, which is the stronger guarantee of the two.

---

## Rollback / abort conditions

Since nothing here is deployed, "rollback" means: conditions under which
this artifact should not proceed to an actual build/deploy, or should be
reverted if it already has.

**Abort building or deploying this image if any of the following is true**
(execution-plan §16, restated for this artifact specifically):

- The `.dockerignore` allow-list at the repo root does not exist, is
  misconfigured, or a build actually includes a `.env` file — verify with
  `docker build -f worker/Dockerfile . 2>&1` and inspect the build
  context, or `docker run --rm enrichment-worker find / -maxdepth 2 -iname '.env*'`
  before ever running the image with real secrets nearby.
- `ENRICHMENT_WORKER_API_KEY` cannot be set as a genuinely separate
  secret from `INTERNAL_API_KEY` on whatever platform ends up hosting
  this service.
- Railway private networking is unavailable for this project's plan tier
  — treat a public-domain fallback as its own separately-flagged decision
  requiring sign-off, not a silent default (§4.1).
- `crawl4ai`'s `BrowserManager` ever stops unconditionally launching
  Chromium with `--no-sandbox` (a future `crawl4ai` upgrade could change
  this) — re-verify before relying on the non-root `USER worker` in
  [`worker/Dockerfile`](Dockerfile) continuing to work; if it no longer
  holds, running as root (with its own, separately-reviewed risk
  tradeoff) may become necessary until resolved.
- Real-world worker latency (once smoke-tested, a future step) is found
  to approach or exceed `DEFAULT_PROVIDER_TIMEOUT_S = 90`s regularly —
  `_call_provider_with_timeout()`'s lease-interaction assumptions need
  deliberate re-tuning before any wiring step, not after.
- Any change bundles this worker's deployment together with wiring
  `WebsiteContactRemoteProvider` into `_default_providers()` in the same
  PR/deploy — these must always be separable, reviewable checkpoints
  (§16's explicit rule).

**Rolling back an already-deployed worker service** (once one exists —
not created by this change): delete the Railway service, or scale it to
zero / disable its deploy. The main API has no dependency on it existing
while the remote adapter is unwired; even once wired, a missing/down
worker degrades to "this one provider errors, cascade continues" (the
adapter's `worker_unreachable:<detail>` error path), never a hard failure
of the whole enrichment route.
