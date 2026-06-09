#!/usr/bin/env python3
"""Build the Architecture Company Intelligence dataset: aggregate permits by
architect into arch_companies, enrich with Google Places data, and generate
AI reliability profiles."""

import config.env  # noqa: F401

from db.connection import get_session, init_db
from pipeline.arch_company_intelligence import run_arch_company_intelligence


def main() -> None:
    init_db()
    session = get_session()
    try:
        results = run_arch_company_intelligence(session)
    finally:
        session.close()

    print("[ArchCompanies] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
