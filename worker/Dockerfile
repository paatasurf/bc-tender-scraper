# Company-enrichment worker service — deployment artifact only.
# (docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.2 "New
# component: the worker service itself", S8 stage 5, S13 file table.)
#
# NOT built, NOT pushed, NOT deployed by anything in this repository
# today -- no CI job and no Railway service references this file. S8
# stage 5 describes the worker service as "buildable and deployable
# independent of stages 1-4" -- this file is what makes it *buildable*;
# actually building, pushing, and deploying it are each their own,
# later, separately-authorized steps (S8 stage 5's own verification
# checklist, S16's abort conditions).
#
# Build context MUST be the repository root, not worker/, since this
# image needs pipeline/, db/, and config/ alongside worker/ itself:
#
#   docker build -f worker/Dockerfile -t enrichment-worker .
#
# The repo root's .dockerignore is an ALLOW-LIST (see that file's own
# comment) covering exactly the four directories this image needs --
# worker/, pipeline/, db/, config/ -- nothing else ever reaches the
# build context, including this repo's own real, present, root-level
# .env file.

# Official Playwright Python image (per
# docs/COMPANY_CONTACT_PROVIDER_PHASE3A_RUNTIME_DECISION.md S2's own
# recommendation, and execution-plan S3.2) -- bundles Chromium plus
# every OS-level library Playwright needs, avoiding the Nixpacks +
# crawl4ai/Playwright glibc incompatibility that rules out building this
# into the main API's own Nixpacks image. Tag pinned to v1.62.0, the
# exact Playwright version requirements-company-enrichment-worker.txt's
# own comment recorded from a verified clean install against this
# worker's `playwright>=1.60.0` floor (confirmed present on
# mcr.microsoft.com before writing this file). Re-verify this tag still
# matches the resolved `playwright` version (`pip show playwright`
# inside the built image) before any real build; a drifted match still
# self-heals via the explicit `playwright install` step below, but a
# matching tag avoids that step re-downloading ~150-300MB of browser
# binary on every build.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Layer-cache-friendly: requirements before source, so a source-only
# change doesn't invalidate the (slow) dependency-install layer.
COPY worker/requirements.txt worker/requirements.txt
RUN pip install --no-cache-dir -r worker/requirements.txt

# Explicit browser-binary install, even though the base image already
# bundles Chromium at (nominally) the same version pinned above: this is
# a self-healing step, not a redundant one. worker/requirements.txt pins
# `playwright>=1.60.0` -- an open floor, not an exact version -- so the
# `pip install` above can legitimately resolve a newer patch than
# whatever the base image's tag baked in, which would otherwise silently
# desync the Python driver from the installed browser binary and fail at
# request time, not build time. Idempotent and fast when versions
# already match.
RUN python -m playwright install --with-deps chromium

# Exactly the four source directories worker/app.py's import graph
# reaches (traced by hand: worker/ -> pipeline.company_enrichment ->
# db.models / pipeline.company_matching / pipeline.registry_verification
# -> db.*, pipeline.company_resolution, config.env) -- nothing else. No
# main requirements.txt, no docs/, no tests/, no .env, no exports/, no
# CSV/JSON data dumps that live at this repo's root today -- the
# repo-root .dockerignore is an allow-list that structurally cannot
# include any of those even if new ones appear later.
COPY pipeline/ pipeline/
COPY db/ db/
COPY config/ config/
COPY worker/ worker/

# Non-root user. Safe here specifically because crawl4ai's own
# BrowserManager (crawl4ai/browser_manager.py, build_browser_flags())
# already launches Chromium with `--no-sandbox` unconditionally, as part
# of its standard flag set (confirmed by reading that file directly
# before writing this Dockerfile, not assumed) -- so this worker never
# depends on the setuid-sandbox / user-namespace capabilities that
# usually make a non-root headless-Chromium launch fail inside a
# container. No code change was made to reach this property; it was
# already true of the existing, unmodified WebsiteContactProvider.
RUN groupadd --system worker \
    && useradd --system --gid worker --home-dir /app --no-create-home worker \
    && chown -R worker:worker /app
USER worker

# No DATABASE_URL, no DB credential of any kind, anywhere in this file --
# execution-plan S4.8's hard requirement. The worker process CAN still
# import db.connection as a side effect of `from db.models import
# Company` (db/__init__.py unconditionally does `from db.connection
# import get_engine, get_session, init_db`) -- but db.connection never
# reads DATABASE_URL or creates an engine at import time (verified by
# reading db/connection.py directly: its only module-level statement
# outside a function/class body is a plain tuple constant), and
# worker/app.py never calls get_engine()/get_session()/init_db() --  so
# no DB connection is ever attempted, imported module or not. See
# worker/README.md's "No DB credentials" section for the full trace,
# including the one real risk this Dockerfile's .dockerignore closes:
# config/env.py calls load_app_env() at ITS OWN import time, which would
# silently load a baked-in .env file's keys into the process environment
# if one ever reached this image -- the repo-root .dockerignore's
# allow-list structurally prevents that.
EXPOSE 8080

# Docker-level liveness probe for local `docker run` testing (Railway's
# own healthcheck, once a worker service exists, is configured via that
# service's own dashboard/railway.toml -- a separate, future, S13-listed
# artifact this change does not create). Uses Python (always present),
# not curl (not guaranteed in this base image), against the same
# unauthenticated /health route the FastAPI app already exposes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=3)" || exit 1

# Shell form (not exec-form JSON array) specifically so $PORT expands --
# Railway injects PORT at runtime; a JSON-array CMD does not go through a
# shell and would pass the literal string "$PORT" to uvicorn. Matches
# this repo's own railway.toml startCommand convention for the main API
# (`uvicorn api.main:app --host 0.0.0.0 --port $PORT`) -- this worker's
# own future railway.toml (S13, not created by this change) is expected
# to follow the same pattern. PORT is quoted ("${PORT:-8080}", not a bare
# ${PORT:-8080}) so a malformed/unexpected value (whitespace, an extra
# token) is passed to uvicorn as a single argument instead of being
# word-split by the shell -- defense-in-depth for a value this worker
# does not control (it comes from whoever runs the container), not a
# response to any concrete exploit.
CMD uvicorn worker.app:app --host 0.0.0.0 --port "${PORT:-8080}"
