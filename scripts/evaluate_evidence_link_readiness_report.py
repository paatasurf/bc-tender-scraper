"""Evaluate a saved Stage 2A Evidence Link audit JSON artifact.

Pure local operation: no database imports, connections, or writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.registry_engine.evidence.evaluate import (  # noqa: E402
    AuditEvaluationError,
    evaluate_audit_payload,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Stage 2A audit JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.artifact.read_text(encoding="utf-8-sig"))
        scorecard = evaluate_audit_payload(payload)
    except (OSError, json.JSONDecodeError, AuditEvaluationError) as exc:
        print(json.dumps({"overall_status": "FAIL", "error": str(exc)}, indent=2))
        return 2

    print(json.dumps(scorecard, indent=2, sort_keys=True))
    return {"FAIL": 2, "BLOCKED": 1}.get(scorecard["overall_status"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
