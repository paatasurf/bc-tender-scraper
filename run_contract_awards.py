#!/usr/bin/env python3
"""Import contract awards from CanadaBuys, BC Data Catalogue, and Vancouver Open Data."""

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards


def main() -> None:
    init_db()
    session = get_session()
    try:
        results = import_contract_awards(session)
    finally:
        session.close()

    print("[ContractAwards] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
