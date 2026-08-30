"""Diff the live FastAPI OpenAPI schema against the checked-in contract
baseline (contracts/openapi-baseline.json).

CI quality infrastructure only -- no product logic. Read-only: never
imports anything that touches a database connection at import time (the
app's own lifespan defers DB init to a background thread), so this can
run without a live database.

Phase 1: reporting only. Prints added/removed paths and methods so a
human can review whether a change is an intentional, coordinated API
change (update the baseline in the same PR) or an accidental break for a
consumer such as voice-n8n-agent. Exits 0 unless --strict is passed and a
removal was found, so this does not block merges yet -- see the CI
workflow comment for the promotion plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "openapi-baseline.json"
)


def _endpoints(schema: dict) -> dict[str, set[str]]:
    paths = schema.get("paths", {})
    return {
        path: {method.upper() for method in methods if method.lower() != "parameters"}
        for path, methods in paths.items()
    }


def diff_schemas(baseline: dict, current: dict) -> dict[str, list[str]]:
    base_endpoints = _endpoints(baseline)
    cur_endpoints = _endpoints(current)

    removed_paths = sorted(set(base_endpoints) - set(cur_endpoints))
    added_paths = sorted(set(cur_endpoints) - set(base_endpoints))

    removed_methods: list[str] = []
    added_methods: list[str] = []
    for path in sorted(set(base_endpoints) & set(cur_endpoints)):
        for method in sorted(base_endpoints[path] - cur_endpoints[path]):
            removed_methods.append(f"{method} {path}")
        for method in sorted(cur_endpoints[path] - base_endpoints[path]):
            added_methods.append(f"{method} {path}")

    return {
        "removed_paths": removed_paths,
        "added_paths": added_paths,
        "removed_methods": removed_methods,
        "added_methods": added_methods,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Path to the checked-in baseline OpenAPI schema.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any endpoint/method was removed (not yet used by CI -- Phase 1 is reporting-only).",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api.main import app  # noqa: E402  -- import after sys.path setup

    current = app.openapi()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))

    result = diff_schemas(baseline, current)

    print(f"Baseline: {args.baseline}")
    print(
        f"Baseline paths: {len(_endpoints(baseline))}  Current paths: {len(_endpoints(current))}"
    )
    print()
    for label, key in (
        ("Removed paths", "removed_paths"),
        ("Removed methods (path still exists)", "removed_methods"),
        ("Added paths", "added_paths"),
        ("Added methods (path already existed)", "added_methods"),
    ):
        items = result[key]
        print(f"{label}: {len(items)}")
        for item in items:
            print(f"  - {item}")
    print()

    breaking = result["removed_paths"] or result["removed_methods"]
    if breaking:
        print(
            "REVIEW REQUIRED: endpoints/methods were removed since the last "
            "baseline snapshot. If intentional, regenerate contracts/"
            "openapi-baseline.json in this PR. If not, this may break a "
            "consumer (e.g. voice-n8n-agent's TenderScope client)."
        )
        if args.strict:
            return 1
    else:
        print("No removed endpoints/methods -- contract is backward compatible.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
