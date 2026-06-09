#!/usr/bin/env python3
"""Run all scrapers and import results into PostgreSQL."""

import config.env  # noqa: F401

from pipeline.run import run_pipeline


def main() -> None:
    raise SystemExit(run_pipeline())


if __name__ == "__main__":
    main()
