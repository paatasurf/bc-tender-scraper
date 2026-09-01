# WebsiteContactProvider Phase 3A — runtime/infrastructure decision (pre-smoke-test review)

Status: **research and decision document only**. No production code, schema, API, frontend, or
scheduler change is made by this document. No Docker image, Railway service, or browser-delivery
mechanism was built or deployed. The smoke test planned in
`docs/COMPANY_CONTACT_PROVIDER_PHASE3A_SMOKE_TEST_PLAN.md` was **not run** — nothing in this
document authorizes running it. Nothing here is authorized to be implemented, committed, merged,
or deployed by this document alone.

**Revision note (round 4 — dependency-isolation fix):** a real blocker was found after round 3's
requirements.txt change: `crawl4ai`/`playwright`/`extruct`/`trafilatura` had been added directly
to the **shared** `requirements.txt`, meaning Railway's Nixpacks build for the main, already-live
production API service would install them unconditionally on every deploy — independent of
`ENRICHMENT_ENABLED` or whether the provider is wired in. §3.0 (new) covers the fix: these four
packages now live only in a new, separate `requirements-company-enrichment-worker.txt`, verified
via two genuinely clean virtualenvs (not this session's own long-lived, already-mixed
environment) to prove real isolation, not just reason about it from the files' text. Sections
1, 2, 4, and 5 are unchanged from round 3 and are not re-litigated here.

---

## 1. Playwright on Railway — the three delivery paths, reviewed

### 1.1 Nixpacks (current builder — `railway.toml`'s `builder = "NIXPACKS"`, unchanged)

**What it would take**: a custom `nixpacks.toml` (or `railway.json`) build phase running
`playwright install --with-deps chromium`, layered on top of Nixpacks' normal Python build —
this pattern is community-documented and not exotic.

**The real risk, found directly, not theoretical**: multiple current (2026) Railway Help
Station / Central Station threads report a `GLIBC_2.36`/`GLIBC_2.38` "version not found" error
specifically when deploying **FastAPI + crawl4ai (using Playwright) on Railway with Nixpacks** —
i.e. the exact stack this provider uses, not a generic Playwright issue. The root cause is that
Nixpacks' Nix-store-based build environment doesn't naturally line up with the glibc version
Playwright's downloaded browser binary expects at runtime. Per the same reports, **there is no
single confirmed fix** — the community consensus is "the exact solution may depend on your
specific project configuration," i.e. this is an open, not-reliably-solved compatibility gap for
this specific combination, not a documented one-line workaround.

**Why this matters more than a generic "extra build step" cost**: this project has exactly one
Railway service today, built via Nixpacks, serving the entire production API
(`bc-tender-scraper`, `api/main.py`). If `WebsiteContactProvider` is ever wired into that same
service, a Nixpacks build change to add Playwright support risks **the same build pipeline the
already-working production app depends on** — a failure here doesn't just block the new feature,
it's a live risk to the existing service's own deployability, for a compatibility issue with no
confirmed fix as of this research.

**Verdict: not recommended.** Real, current, project-specific incompatibility risk with no
confirmed resolution, against a build pipeline the existing production service already depends
on.

### 1.2 Docker-based worker (Railway supports switching `builder` to `DOCKERFILE`)

**Railway's own official guidance** (`docs.railway.com/guides/playwright`) recommends exactly
this: use the **official Microsoft Playwright image** (`mcr.microsoft.com/playwright/python`) as
the base, which bundles the Playwright SDK, all three browser engines (Chromium, Firefox,
WebKit), and every required OS-level library (`libnss3`, `libatk-bridge2.0`, `libgbm`, etc.) —
maintained and tested by the Playwright team against each release, sidestepping the Nixpacks
glibc mismatch entirely because the image controls its own OS/glibc environment rather than
relying on Nixpacks' Nix-derived one.

**Size/RAM, from current sources**:
- Image size: **~1.8GB** (Ubuntu 24.04-based, includes all three browser engines' binaries and
  dependencies) — larger than this project's likely-much-smaller current Nixpacks-built image,
  meaning slower cold builds/deploys purely from data volume, independent of anything else.
- RAM: Railway's own guide says allocate **at least 1GB**; a more realistic operating range is
  **1-2GB for normal single-fetch-at-a-time load** (matching this project's own already-decided
  `ENRICHMENT_MAX_CONCURRENT_JOBS=1`/would-need-its-own-lower-limit-for-this-provider policy,
  decision doc §7), rising toward **4GB** only under concurrent multi-page scraping loads this
  project's own design explicitly does not do (single sequential fetch per company, never
  parallel).
- A minimal Chromium-only custom image (installing just `playwright install chromium` without
  Firefox/WebKit) would be meaningfully smaller than the official all-browsers image, at the
  cost of maintaining that trimming yourself rather than relying on Microsoft's maintained image.

**Two sub-variants, meaningfully different in blast radius**:
- **(a) Replace the main API service's own builder** with this Dockerfile — touches the same
  service the production API already runs on; every future deploy of the main app (not just
  Playwright-related changes) now goes through a Docker build instead of Nixpacks, and the
  Dockerfile has to reproduce everything Nixpacks currently handles automatically for the
  existing app's other dependencies (Postgres client, apscheduler, etc.), not just Playwright.
  Higher risk to the existing, already-working deploy pipeline.
- **(b) A genuinely separate Railway service**, its own Dockerfile, its own deploy, invoked by
  the main API over an internal HTTP call when a website fetch is actually needed — the main
  API's own Nixpacks build is completely untouched. Isolates the ~1.8GB image, the RAM
  footprint, and (per `_call_provider_with_timeout()`'s own already-documented finding) the
  abandoned-thread/delayed-process-exit risk of a hung Playwright call away from the main API
  process entirely — a hang in this worker can no longer delay the main service's own graceful
  shutdown/restart during a deploy, which it currently could if wired into the same process.

**Cost**: Railway's per-resource billing (confirmed last round: roughly $10/GB-RAM-month,
$20/vCPU-month, $0.05/GB egress, billed per second of actual use) applies to whichever service
runs this — for variant (b), only to the new, separate service, not doubling the main API's own
bill. For a lightly-trafficked worker (occasional on-demand fetches, not sustained traffic),
realistic monthly cost lands in a similar range to the earlier SearXNG-on-Railway estimate from
`docs/COMPANY_CONTACT_DISCOVERY_INFRASTRUCTURE_DECISION.md` — roughly **$10-20/month**,
contingent on real observed usage once (if) activated. Image size itself (1.8GB) is a
build-time/deploy-time cost (slower cold builds), not confirmed to be a separately metered
storage line item on Railway.

**Operational risk**: a Docker-based service is a genuinely new kind of deploy artifact for this
project (which has run exclusively on Nixpacks so far) — more moving parts to monitor (image
builds, browser version drift as Microsoft updates the base image, Chromium sandbox
considerations inside a container, per Playwright's own docs: run as non-root, never disable the
sandbox casually). This is real added complexity, but it is Railway's own documented, supported
path, and — for variant (b) specifically — the added complexity is isolated to a new service
rather than threatening the existing one.

**Verdict: viable, Railway's own recommended path. Variant (b) — a separate service — is
preferred over (a) for blast-radius reasons, detailed in §2.**

### 1.3 Separate browser service (a remote, CDP-connected browser — e.g. a self-hosted
Browserless-style container)

**The architecture**: instead of launching a local Chromium process per fetch, the application
connects to a remote, already-running browser instance over the Chrome DevTools Protocol (CDP) —
Playwright itself supports this natively (`chromium.connect()`/`connectOverCDP()`), and
Browserless (open-source, self-hostable, wraps headless Chrome in a container with a WebSocket
CDP endpoint) is the most common implementation of this pattern.

**The concrete blocker for this specific project, found directly**: **Crawl4AI does not expose
a way to connect to a remote Browserless-style CDP endpoint** — it manages its own local
Playwright browser internally via its `BrowserConfig`/`AsyncWebCrawler` API (exactly what
`website_contact_provider.py` already uses), with no documented remote-connect configuration
option. Adopting this architecture would require either (a) crawl4ai adding that capability in a
future release (not something to plan around happening on any particular timeline), or (b)
rewriting `_fetch_rendered_page()` to bypass crawl4ai's own browser management and drive raw
Playwright directly against a remote CDP endpoint — a real, non-trivial rewrite of code already
built, tested (78 passing tests), and reviewed across multiple rounds this session.

**Cost, if pursued regardless via a rewrite**: self-hosting Browserless is free software-wise but
still needs a container to run it on (i.e., still needs Docker infrastructure somewhere — this
does not avoid §1.2's Docker requirement, it relocates it to a browser-only container). A
managed/cloud-hosted option (Browserless's own hosted tier, or comparable competitors) runs
roughly **$50-200+/month** depending on tier — meaningfully more expensive than self-hosting a
worker directly, for a project whose founding stack decisions have consistently favored the
lowest-cost self-hosted option (decision doc's own title: "budget stack decision").

**Verdict: not recommended for now.** A real architectural mismatch with the already-built
crawl4ai-based implementation, not just an operational inconvenience — pursuing this would mean
un-shipping already-tested code, not just adding infrastructure.

---

## 2. Recommended delivery mechanism — decision, not implementation

**Recommended: §1.2 variant (b) — a separate Railway service, Docker-built from the official
`mcr.microsoft.com/playwright/python` base image, running `WebsiteContactProvider`'s fetch logic
in isolation, invoked by the main API over an internal HTTP call when (and only when) a website
fetch is actually needed.**

Reasoning, in order of weight:

1. **Avoids the one concrete, currently-unresolved compatibility risk found in this research**
   (§1.1's Nixpacks/glibc issue) entirely, by using Railway's own officially-documented,
   supported path instead of an approach with open community reports of failure for this exact
   stack.
2. **Isolates blast radius from the existing production service.** The main API's Nixpacks build
   — which already works, today, in production — is untouched. A build problem, a crashed
   browser process, or a hung fetch in the new worker cannot take down or block-redeploy the
   existing app the way it could if wired into the same process/service (directly addressing the
   already-documented `_call_provider_with_timeout()` process-exit-delay risk).
3. **Matches the already-verified implementation.** §1.3's remote-CDP approach would require
   rewriting code that is already built and tested; this option requires no changes to
   `website_contact_provider.py`'s own logic — only where and how it runs.
4. **Cost is comparable to, not dramatically worse than, the alternatives** — roughly the same
   ballpark as the already-considered SearXNG-on-Railway option from the discovery-infrastructure
   decision, and meaningfully cheaper than a managed remote-browser service (§1.3).

This recommendation is **not implemented by this document**. Building the Dockerfile, creating
the Railway service, and wiring the main API to call it over HTTP are all separate, future,
explicitly-authorized steps — each with its own review, exactly as this project's canon requires
for every prior step in this feature's history.

---

## 3. Dependency policy

### 3.0 Dependency isolation — a real blocker found post-review, now fixed

**The problem, exactly as flagged**: round 3's requirements.txt change (§3.3 of that round)
added `crawl4ai`/`playwright`/`extruct`/`trafilatura` directly into the **shared**
`requirements.txt` — the same file Railway's Nixpacks build and every CI job install for the
**main, already-in-production API service**. `ENRICHMENT_ENABLED=false` and
`WebsiteContactProvider` not being wired into `_default_providers()` only gate whether the
*feature* runs — they do nothing to stop `pip install -r requirements.txt` from installing these
four heavy, browser-launching packages (and their own large transitive dependency trees —
`crawl4ai` alone pulls in `litellm`, `openai`, `pydantic`, `playwright-stealth`, and more) into
the main service's build **unconditionally**, every single deploy, whether or not this feature
is ever used. That is a real, unnecessary risk surface (build time, image size, and — per §1.1 —
a demonstrated real compatibility risk between this exact package combination and Nixpacks) added
to a service that currently has zero functional need for any of the four packages.

**The fix**: these four packages are removed from `requirements.txt` entirely and now live only
in a new, separate file, **`requirements-company-enrichment-worker.txt`**, installed
*additively* on top of the base file (`pip install -r requirements.txt -r
requirements-company-enrichment-worker.txt`) — a command **not run anywhere in this repository
today**, matching this project's existing `requirements-dev.txt` convention (a separate file,
installed via a separate `pip install -r <file>` command in CI, never `-r`-included from inside
another requirements file). This install command exists for the future, not-yet-built,
not-yet-authorized Docker worker (§2) only.

**Verified empirically, not just reasoned about from the files' text** — via two genuinely clean
virtualenvs (not this session's own long-lived, already-mixed global environment, which still has
all four packages installed from earlier rounds and could not prove isolation on its own):

| Check | Result |
|---|---|
| Clean venv, `pip install -r requirements.txt` only | 67 packages installed; `pip list \| grep -iE "crawl4ai\|playwright\|extruct\|trafilatura"` → **zero matches** |
| Same venv, `pip check` | "No broken requirements found" |
| Same venv, `import api.internal` (the module containing the enrichment route) | **Succeeds**, with zero browser packages installed anywhere in that venv — definitive proof the main API's actual import graph does not need them |
| Same venv, `import pipeline.company_enrichment.orchestrator` | Succeeds |
| Same process, `sys.modules` checked after both imports above | Neither `crawl4ai`, `playwright`, nor `pipeline.company_enrichment.website_contact_provider` ever entered `sys.modules` |
| Separate clean venv, `pip install -r requirements.txt -r requirements-company-enrichment-worker.txt` | All four packages installed (`crawl4ai==0.9.3`, `playwright==1.62.0`, `extruct==0.18.0`, `trafilatura==2.2.0`) alongside the base 67 |
| Same worker venv, `pip check` | "No broken requirements found" |
| Same worker venv, `import pipeline.company_enrichment.website_contact_provider` | Succeeds |
| Same worker venv, `pytest tests/unit/test_website_contact_provider.py` | 78/78 passed |

Also confirmed via `grep -rn "website_contact_provider" --include="*.py"` across the entire
repository (excluding `tests/`): **zero matches outside the test file** — no production module,
including `pipeline/company_enrichment/__init__.py` (which does not re-export anything from this
package), references it at all. Nothing was silently pulling it into the main API's import graph
even before this fix; the risk was specifically and only the shared `requirements.txt` file's
own install-time footprint, now closed.

### 3.1 crawl4ai — constrained to the tested minor version, not left open-ended (now in the
worker file)

Unchanged reasoning from round 3, now living in `requirements-company-enrichment-worker.txt`
instead of `requirements.txt`: `crawl4ai==0.8.9` declares `lxml~=5.3`, which does not overlap at
all with `trafilatura`'s own `lxml>=6.1.1` — a genuine, not installation-order-dependent,
unresolvable conflict. `crawl4ai` 0.9.3 (still the current PyPI latest) declares the relaxed
`lxml<7,>=5.3`, which does overlap. The specifier remains **`crawl4ai~=0.9.0`** (PEP 440
compatible-release — `>=0.9.0, ==0.9.*`): allows 0.9.x patch releases, blocks a silent jump to
0.10.0+ without a deliberate, reviewed decision to raise the ceiling.

### 3.2 extruct / trafilatura / playwright — left as `>=` floors, verified compatible

Unchanged reasoning, now re-verified against the fresh clean-venv install rather than only the
prior round's mixed global environment: `extruct` declares an unconstrained `lxml` requirement,
`playwright` has no `lxml` dependency at all, and `trafilatura`'s own `lxml>=6.1.1` floor is what
`crawl4ai` 0.9.3 now comfortably satisfies. No upper bound added to these three — no history of
conflict, unlike `crawl4ai`.

**Exact version set verified together** (fresh clean-venv install, not carried over from a
previous round's environment): `crawl4ai==0.9.3`, `playwright==1.62.0` (the floor is `>=1.60.0`;
a fresh install picked up a newer compatible patch, expected and harmless), `extruct==0.18.0`,
`trafilatura==2.2.0`, `lxml==6.1.2` — plus `playwright-stealth==2.0.3`, observed as `crawl4ai`'s
own transitive dependency, not something either requirements file pins directly.

### 3.3 Diff — both files

```diff
--- a/requirements.txt
+++ b/requirements.txt
@@ -13,44 +13,17 @@
 PyJWT[crypto]>=2.8.0
 crawlee[beautifulsoup]>=0.6.0
 httpx>=0.27.0
-# Phase 3A -- pipeline/company_enrichment/website_contact_provider.py
-# (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md). Not wired into
-# _default_providers() yet -- these are new resource-footprint-bearing
-# dependencies (Crawl4AI launches a real Chromium process per fetch, see
-# decision doc S9) added ahead of that wiring, not activated by it.
-#
-# crawl4ai is constrained to the 0.9.x line (~=0.9.0), not left open-ended:
-# ... [round 3's full crawl4ai/lxml-conflict comment block] ...
-crawl4ai~=0.9.0
-playwright>=1.60.0
-extruct>=0.18.0
-trafilatura>=2.2.0
-#
-# IMPORTANT -- `pip install playwright` (this file) installs only the
-# ... [round 3's full playwright-browser-binary comment block] ...
+#
+# Phase 3A (pipeline/company_enrichment/website_contact_provider.py,
+# docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md) deliberately does NOT
+# list its dependencies here. crawl4ai/playwright/extruct/trafilatura are
+# heavy, browser-launching dependencies that must never be installed into
+# this project's main Nixpacks-built API service -- see
+# requirements-company-enrichment-worker.txt and
+# docs/COMPANY_CONTACT_PROVIDER_PHASE3A_RUNTIME_DECISION.md S3 for why,
+# and for the exact versions/compatibility verification. This file's own
+# `pip install -r requirements.txt` (what Railway's Nixpacks build and
+# every CI job actually run) never touches those four packages, whether
+# or not ENRICHMENT_ENABLED is set.
```

```diff
--- /dev/null
+++ b/requirements-company-enrichment-worker.txt
@@
+# Company-enrichment Phase 3A worker dependencies ONLY.
+#
+# NOT installed by the main API service (requirements.txt). NOT installed by
+# any CI job today. [... full file, ~55 lines -- see the file itself, sent
+# alongside this report ...]
+#
+#   pip install -r requirements.txt -r requirements-company-enrichment-worker.txt
+#
+crawl4ai~=0.9.0
+playwright>=1.60.0
+extruct>=0.18.0
+trafilatura>=2.2.0
```

**Verified after applying**: both clean-venv checks in §3.0's table; `tests/unit/
test_website_contact_provider.py` → 78/78 passed (run inside the isolated worker venv, not just
this session's own mixed global environment). No other file — no `requirements-dev.txt` entry,
no production code, no `orchestrator.py`, no `_default_providers()`, no schema, no API route, no
`railway.toml` — is touched by this fix.

---

## 4. Confirmed: the test-timeout fix did not change production timeout semantics

Re-verified directly against the source, not assumed from the prior round's own report:

- `pipeline/company_enrichment/orchestrator.py` has **zero diff** for this entire session (`git
  diff --stat pipeline/company_enrichment/orchestrator.py` returns nothing) — this file was never
  touched by the flake fix.
- The real production default, `DEFAULT_PROVIDER_TIMEOUT_S = 90` (orchestrator.py line 60,
  "RFC S10 ENRICHMENT_PROVIDER_TIMEOUT_S, revised per benchmark"), is unchanged and is what
  `run_cascade_for_job()` actually uses in production (`api/internal.py`'s route calls it with no
  `timeout_s` override, so the 90s default applies).
- The values changed (`timeout_s=1.0`, `sleep_s=3.0`) exist **only** as explicit, test-local
  arguments passed directly to `run_cascade_for_job(...)` and `FakeProvider(...)` inside one
  specific test function
  (`test_a_slow_provider_past_the_timeout_is_marked_partial_success_not_hidden_as_success`) in
  `tests/unit/test_company_enrichment_orchestrator.py` — they do not set, override, or influence
  any default, environment variable, or shared constant.
- The three other tight-timeout tests in the same file
  (`test_bugbot_finding_a_genuinely_hung_provider_is_interrupted_not_awaited`,
  `test_bugbot_finding_two_concurrent_hung_lookups_are_each_interrupted_independently`,
  `test_bugbot_finding_repeated_timeouts_accumulate_abandoned_threads_a_documented_limitation`)
  were reviewed and left unmodified — they assert only that a provider **expected** to time out
  does so promptly, a property connection-setup jitter can only reinforce, never break, so they
  were never at risk of the same flake and needed no change.

**Verdict: confirmed — production timeout behavior (90s default, real wall-clock interrupt via
`ThreadPoolExecutor` + `Future.result(timeout=...)`) is byte-for-byte unchanged.** The fix was
scoped entirely to one test's own local parameters.

---

## 5. Smoke test — not run

`docs/COMPANY_CONTACT_PROVIDER_PHASE3A_SMOKE_TEST_PLAN.md` remains a plan only. No request was
made to `example.com`, no ground-truth company site, and no other live target, as part of
producing this document. Running it requires its own separate, explicit authorization, exactly
as that plan document's own §5 already states.

---

## 6. Explicitly out of scope for this document

- Building the recommended Dockerfile or Railway service (§2).
- Wiring `WebsiteContactProvider` into `_default_providers()`, `api/internal.py`, or any HTTP
  call path between a future worker service and the main API.
- Setting `ENRICHMENT_ENABLED=true`.
- Running the smoke test (§5).
- Any schema migration.
- Any change to `railway.toml`, CI workflow files, or any file outside `requirements.txt`.
