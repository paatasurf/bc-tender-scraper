"""Deployment-artifact tests for the enrichment worker (Phase 3D).
(docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.2, S4, S8 stage 5.)

Covers exactly the four categories this phase's own task asked for:
1. worker/Dockerfile contains the right command/base-image/user shape.
2. The health endpoint responds (unauthenticated).
3. /lookup requires auth.
4. The worker never imports db.connection in a way that touches
   DATABASE_URL, and never reads DATABASE_URL itself.

These are deliberately STATIC + TestClient-based, not a real `docker
build`: this sandboxed environment has no `docker` binary (confirmed
before writing this file), and Phase 3D is explicitly deployment-artifact
PREPARATION only -- no build, no push, no deploy is authorized or
performed by this phase. Parsing worker/Dockerfile's own text is a
faithful, fast, dependency-free way to assert its shape without actually
invoking Docker. Categories 2-3 reuse the exact TestClient pattern
already established in test_enrichment_worker.py (kept self-contained
here rather than importing its fixtures, matching this repo's existing
per-file test-isolation convention).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pipeline.company_enrichment.provider import ProviderResult
from worker.app import app
from worker.auth import WORKER_AUTH_ENV_VAR, WORKER_AUTH_HEADER

WORKER_SECRET = "test-worker-secret-do-not-use-in-production"
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = REPO_ROOT / "worker" / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
WORKER_SOURCE_FILES = [
    REPO_ROOT / "worker" / "app.py",
    REPO_ROOT / "worker" / "auth.py",
    REPO_ROOT / "worker" / "models.py",
]


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _worker_secret_env():
    with patch.dict(os.environ, {WORKER_AUTH_ENV_VAR: WORKER_SECRET}):
        yield


def _auth_headers() -> dict[str, str]:
    return {WORKER_AUTH_HEADER: WORKER_SECRET}


def _valid_body(**overrides) -> dict:
    body = {
        "company_id": 1,
        "company_name": "Acme Construction Ltd",
        "website": "example.com",
        "correlation_id": "11111111-1111-1111-1111-111111111111",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 1. worker/Dockerfile — static shape checks (no real `docker build`;
#    `docker` is not installed in this environment, and this phase does
#    not build/push/deploy anything).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    assert DOCKERFILE_PATH.is_file(), f"expected {DOCKERFILE_PATH} to exist"
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def test_dockerfile_uses_official_playwright_base_image(dockerfile_text: str) -> None:
    match = re.search(r"^FROM\s+(\S+)", dockerfile_text, re.MULTILINE)
    assert match is not None, "no FROM instruction found"
    assert match.group(1).startswith(
        "mcr.microsoft.com/playwright/python:"
    ), f"base image {match.group(1)!r} is not the official Playwright Python image"


def test_dockerfile_installs_worker_requirements(dockerfile_text: str) -> None:
    assert re.search(
        r"pip install .*-r\s+worker/requirements\.txt", dockerfile_text
    ), "Dockerfile does not install worker/requirements.txt"


def test_dockerfile_runs_uvicorn_worker_app(dockerfile_text: str) -> None:
    cmd_match = re.search(r"^CMD\s+(.+)$", dockerfile_text, re.MULTILINE)
    assert cmd_match is not None, "no CMD instruction found"
    cmd = cmd_match.group(1)
    assert "uvicorn" in cmd, f"CMD does not invoke uvicorn: {cmd!r}"
    assert "worker.app:app" in cmd, f"CMD does not target worker.app:app: {cmd!r}"
    # Shell form (not a JSON array) is required for $PORT to expand at
    # container-start time -- a JSON-array CMD does not go through a
    # shell and would pass the literal string "$PORT" to uvicorn.
    assert not cmd.strip().startswith("["), (
        "CMD is JSON-array (exec) form -- $PORT would not expand; "
        "shell form is required"
    )


def test_dockerfile_runs_as_a_non_root_user(dockerfile_text: str) -> None:
    user_matches = re.findall(r"^USER\s+(\S+)", dockerfile_text, re.MULTILINE)
    assert user_matches, "no USER instruction found -- image would run as root"
    assert user_matches[-1] not in {
        "root",
        "0",
    }, f"final USER is {user_matches[-1]!r} -- image runs as root"


def test_dockerfile_never_sets_database_url_or_db_credentials(
    dockerfile_text: str,
) -> None:
    """Checks for an actual `ENV`/`ARG` instruction setting DATABASE_URL,
    not a bare substring match -- the Dockerfile's own comments legitimately
    discuss DATABASE_URL by name (explaining why it's absent), which a
    plain substring check would wrongly flag."""
    assert not re.search(
        r"^\s*(ENV|ARG)\s+DATABASE_URL", dockerfile_text, re.MULTILINE
    ), "Dockerfile sets DATABASE_URL via ENV/ARG"


def test_dockerignore_exists_and_excludes_env_files() -> None:
    assert DOCKERIGNORE_PATH.is_file(), f"expected {DOCKERIGNORE_PATH} to exist"
    text = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    assert "**/.env" in text or ".env" in text, (
        ".dockerignore does not exclude .env -- a real .env file exists at "
        "this repo's root and config/env.py auto-loads one into the "
        "process environment at import time if it ever reaches the image"
    )


def test_dockerignore_is_an_allow_list_not_a_bare_deny_list() -> None:
    """The very first non-comment line must be a bare `*` (deny everything),
    so anything not explicitly re-included later is excluded by
    construction -- protects against future clutter, not just today's."""
    text = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines, ".dockerignore has no active rules"
    assert lines[0] == "*", (
        f"expected the first active .dockerignore rule to be a bare '*' "
        f"(default-deny), got {lines[0]!r}"
    )


# Sample paths this test's simulation must resolve correctly. Each entry
# is a real or representative path, not exhaustive -- this is a targeted
# regression check for two specific risks this file's history found, not
# a full filesystem audit:
# 1. A secret-shaped filename dropped INSIDE an allowed directory
#    previously would NOT have been excluded (safety review finding).
# 2. `.nixpacks/nixpkgs-*.nix` previously WAS excluded, which broke a
#    real production deploy: Nixpacks (railway.toml's `builder =
#    "NIXPACKS"`) does NOT bypass Docker/.dockerignore the way an earlier
#    version of this repo's own .dockerignore comment assumed -- it
#    generates its own build plan through a standard Docker build
#    context rooted at this repo, and that build plan COPYs a `.nix` file
#    it writes into the context at build time. Confirmed directly from
#    the failed deploy's Railway build logs before this fix.
_DOCKERIGNORE_MUST_INCLUDE = (
    "worker/app.py",
    "worker/Dockerfile",
    "worker/requirements.txt",
    "worker/auth.py",
    "worker/models.py",
    "worker/__init__.py",
    "pipeline/company_enrichment/website_contact_provider.py",
    "pipeline/__init__.py",
    "pipeline/company_matching.py",
    "db/models.py",
    "db/connection.py",
    "db/__init__.py",
    "config/env.py",
    "config/__init__.py",
    # The main API's Nixpacks build needs these -- see point 2 above.
    ".nixpacks/nixpkgs-bc8f8d1be58e8c8383e683a06e1e1e57893fff87.nix",
    ".nixpacks/some-other-generated-file.nix",
)
_DOCKERIGNORE_MUST_EXCLUDE = (
    ".env",
    ".env.local",
    ".env.example",
    "docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md",
    "exports/company_contact_discovery_pilot.json",
    "api/main.py",
    "building_permits.csv",
    ".github/workflows/quality-gate.yml",
    "railway.toml",
    "requirements.txt",
    "worker/__pycache__/app.cpython-313.pyc",
    # Secret-shaped files dropped INSIDE an allowed directory -- the real
    # gap this review found: the allow-list re-includes worker/, db/,
    # pipeline/, config/ wholesale, which does nothing on its own to
    # exclude a secret-shaped file placed inside one of them.
    "worker/secrets.json",
    "worker/.secrets",
    "db/secrets.py",
    "pipeline/company_enrichment/secrets.json",
    "worker/.env",
    "config/.env.local",
    "db/credentials.json",
    "worker/id_rsa.pem",
    "config/api.key",
)


def test_dockerignore_pattern_simulation_matches_needed_and_dangerous_paths() -> None:
    """Empirically simulates .dockerignore's own pattern rules against a
    fixed set of paths -- not a real `docker build` (docker is
    unavailable in this environment, confirmed before writing this file;
    see the module docstring), but a faithful approximation: Docker's own
    .dockerignore syntax is documented as following (a subset of)
    .gitignore syntax, and this repo's file uses only the basic
    constructs (`*`, `!`, `**`) both formats agree on.

    Uses `pathspec` (gitwildmatch), an existing TRANSITIVE dependency of
    black/mypy (both already in requirements-dev.txt, installed in every
    CI job that runs this file) -- not a new declared dependency. Skips
    gracefully, rather than failing, if it's ever unavailable, since this
    is a defense-in-depth check on top of the two more basic dockerignore
    tests above, not the only thing standing between a secret and a
    built image.
    """
    pathspec = pytest.importorskip("pathspec")
    text = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    spec = pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())

    failures = []
    for path in _DOCKERIGNORE_MUST_INCLUDE:
        if spec.match_file(path):
            failures.append(f"wrongly IGNORED (needed for the build): {path}")
    for path in _DOCKERIGNORE_MUST_EXCLUDE:
        if not spec.match_file(path):
            failures.append(f"wrongly INCLUDED (must never reach the image): {path}")

    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 2. Health endpoint
# ---------------------------------------------------------------------------


def test_health_endpoint_responds_ok_without_auth(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 3. /lookup requires auth
# ---------------------------------------------------------------------------


def test_lookup_without_auth_header_is_rejected(client: TestClient) -> None:
    response = client.post("/lookup", json=_valid_body())
    assert response.status_code == 403


def test_lookup_with_correct_auth_header_is_accepted(client: TestClient) -> None:
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(provider="website_contact", matched=False),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 4. No DATABASE_URL — static source check + a runtime proof that request
#    handling never depends on it being set.
# ---------------------------------------------------------------------------


_DATABASE_URL_CODE_PATTERN = re.compile(
    r'os\.(environ\[|environ\.get\(|getenv\()\s*["\']DATABASE_URL'
)


def test_worker_source_files_never_read_database_url_in_code() -> None:
    """Checks for an actual environment-variable READ of DATABASE_URL
    (os.environ[...]/os.environ.get(...)/os.getenv(...)), not a bare
    substring match -- worker/app.py's own module docstring and inline
    comments legitimately discuss DATABASE_URL by name (explaining why
    the worker never needs it, per S4.8), which a plain substring check
    would wrongly flag."""
    for path in WORKER_SOURCE_FILES:
        text = path.read_text(encoding="utf-8")
        assert not _DATABASE_URL_CODE_PATTERN.search(
            text
        ), f"{path} reads DATABASE_URL from the environment"


def test_health_and_lookup_work_with_database_url_absent_from_environ(
    client: TestClient,
) -> None:
    """Stronger than a static import check: proves at runtime that neither
    route's actual request handling depends on DATABASE_URL being set,
    by deleting it from the environment (if present) before making both
    calls. This does not disprove that db.connection is imported as a
    module (it is, transitively, via db/__init__.py -- see
    worker/README.md S7) -- it proves the worker never needs
    DATABASE_URL's value to serve a request, which is the property that
    actually matters for S4.8."""
    env_without_database_url = {
        k: v for k, v in os.environ.items() if k != "DATABASE_URL"
    }
    with patch.dict(os.environ, env_without_database_url, clear=True):
        with patch.dict(os.environ, {WORKER_AUTH_ENV_VAR: WORKER_SECRET}):
            assert "DATABASE_URL" not in os.environ
            health_response = client.get("/health")
            with patch(
                "worker.app._provider.lookup",
                return_value=ProviderResult(provider="website_contact", matched=False),
            ):
                lookup_response = client.post(
                    "/lookup", json=_valid_body(), headers=_auth_headers()
                )
    assert health_response.status_code == 200
    assert lookup_response.status_code == 200


def test_worker_ignores_database_url_if_accidentally_set(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The scenario this safety review was specifically asked to cover: a
    human accidentally sets DATABASE_URL on the worker's own deployment
    (e.g. copying the main API's variable set "for consistency"). Proves
    three things at once, with the value actually present in os.environ
    (the complement of the "absent" test above, not a duplicate of it):
    1. No DB connection function is ever called (get_engine/get_session/
       init_db, imported directly from db.connection here so the spy
       target is exactly what worker/app.py's own transitive import graph
       resolves to -- not a re-implementation of that graph).
    2. /health and /lookup both still succeed exactly as when the
       variable is absent -- its presence changes nothing observable.
    3. The sentinel value never appears in any log record captured
       across both requests.
    """
    import db.connection as db_connection_module

    sentinel = "postgresql://sentinel-should-never-be-read:hunter2@example.invalid/db"

    with (
        patch.object(
            db_connection_module,
            "get_engine",
            side_effect=AssertionError("get_engine() must never be called"),
        ),
        patch.object(
            db_connection_module,
            "get_session",
            side_effect=AssertionError("get_session() must never be called"),
        ),
        patch.object(
            db_connection_module,
            "init_db",
            side_effect=AssertionError("init_db() must never be called"),
        ),
        patch.dict(
            os.environ,
            {WORKER_AUTH_ENV_VAR: WORKER_SECRET, "DATABASE_URL": sentinel},
        ),
        caplog.at_level("DEBUG"),
    ):
        health_response = client.get("/health")
        with patch(
            "worker.app._provider.lookup",
            return_value=ProviderResult(provider="website_contact", matched=False),
        ):
            lookup_response = client.post(
                "/lookup", json=_valid_body(), headers=_auth_headers()
            )

    assert health_response.status_code == 200
    assert lookup_response.status_code == 200
    for record in caplog.records:
        assert sentinel not in record.getMessage(), (
            "DATABASE_URL's value leaked into a log record: " f"{record.getMessage()!r}"
        )
