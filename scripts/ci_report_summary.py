#!/usr/bin/env python3
"""Summarize CI junit/coverage artifacts into a deployment-readiness report."""

from __future__ import annotations

import argparse
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def _parse_junit(path: Path) -> tuple[int, int, int, float]:
    if not path.exists():
        return 0, 0, 0, 0.0
    root = ET.parse(path).getroot()
    # pytest may emit <testsuites> or <testsuite>
    suites = root.findall("testsuite")
    if root.tag == "testsuite":
        suites = [root]
    tests = failures = errors = 0
    elapsed = 0.0
    for suite in suites:
        tests += int(suite.attrib.get("tests", 0))
        failures += int(suite.attrib.get("failures", 0))
        errors += int(suite.attrib.get("errors", 0))
        elapsed += float(suite.attrib.get("time", 0) or 0)
    return tests, failures, errors, elapsed


def _coverage_line_rate(path: Path) -> str:
    if not path.exists():
        return "n/a"
    root = ET.parse(path).getroot()
    rate = root.attrib.get("line-rate")
    if rate is None:
        return "n/a"
    try:
        return f"{float(rate) * 100:.1f}%"
    except ValueError:
        return "n/a"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--soft",
        action="store_true",
        help="Always exit 0 (for always() CI steps); readiness still printed",
    )
    args = parser.parse_args()

    reports = args.reports_dir
    reports.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, int, int, int, float]] = []
    for junit in sorted(reports.glob("*-junit.xml")):
        tests, failures, errors, elapsed = _parse_junit(junit)
        rows.append((junit.name, tests, failures, errors, elapsed))

    total_t = sum(r[1] for r in rows)
    total_f = sum(r[2] for r in rows)
    total_e = sum(r[3] for r in rows)
    total_time = sum(r[4] for r in rows)
    coverage = _coverage_line_rate(reports / "coverage.xml")
    ready = total_f == 0 and total_e == 0 and total_t > 0

    lines = [
        f"# Deployment readiness — `{args.repo}`",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        f"**Ready for deploy:** {'YES' if ready else 'NO'}",
        "",
        "| Suite | Passed* | Failed | Errors | Time (s) |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, tests, failures, errors, elapsed in rows:
        passed = max(tests - failures - errors, 0)
        lines.append(
            f"| `{name}` | {passed}/{tests} | {failures} | {errors} | {elapsed:.1f} |"
        )
    lines.extend(
        [
            "",
            f"- **Total tests (sum of suites):** {total_t}",
            f"- **Total failures:** {total_f}",
            f"- **Total errors:** {total_e}",
            f"- **Approx suite time:** {total_time:.1f}s",
            f"- **Coverage (line-rate):** {coverage}",
            "",
            "_* Passed = tests - failures - errors (skips counted in tests by pytest)._",
            "",
        ]
    )
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        print("\n".join(lines))
    except UnicodeEncodeError:
        print("\n".join(lines).encode("ascii", "replace").decode("ascii"))
    if args.soft:
        return 0
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
