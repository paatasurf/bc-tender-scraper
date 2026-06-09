#!/usr/bin/env python3
"""Run all scrapers and import results into PostgreSQL."""

import config.env  # noqa: F401

from pipeline.lock import acquire_lock, release_lock
from pipeline.run import run_pipeline


def main() -> None:
    acquire_lock()
    try:
        raise SystemExit(run_pipeline())
    finally:
        release_lock()


if __name__ == "__main__":
    main()
