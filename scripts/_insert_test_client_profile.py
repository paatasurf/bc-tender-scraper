"""One-off: insert/update test client profile and verify."""
from __future__ import annotations

from sqlalchemy import text

from db.connection import get_session

EMAIL = "paatasurf@gmail.com"


def main() -> None:
    session = get_session()
    try:
        existing = session.execute(
            text("SELECT id FROM client_profiles WHERE email = :email"),
            {"email": EMAIL},
        ).fetchone()

        if existing:
            session.execute(
                text(
                    """
                    UPDATE client_profiles
                    SET company_id = 1,
                        company_name = 'Test Company',
                        regions = ARRAY['Vancouver', 'Burnaby', 'Surrey'],
                        alerts_enabled = TRUE
                    WHERE email = :email
                    """
                ),
                {"email": EMAIL},
            )
            session.commit()
            print(f"Updated existing profile id={existing[0]}")
        else:
            session.execute(
                text(
                    """
                    INSERT INTO client_profiles
                        (clerk_user_id, company_id, company_name, email, regions, specializations, alerts_enabled)
                    VALUES
                        ('', 1, 'Test Company', :email,
                         ARRAY['Vancouver', 'Burnaby', 'Surrey'], ARRAY[]::varchar[], TRUE)
                    """
                ),
                {"email": EMAIL},
            )
            session.commit()
            print("Inserted new profile")

        row = session.execute(
            text(
                """
                SELECT id, company_id, company_name, email, regions, alerts_enabled
                FROM client_profiles WHERE email = :email
                """
            ),
            {"email": EMAIL},
        ).fetchone()
        print("Row:", dict(row._mapping) if row else "NOT FOUND")
    finally:
        session.close()


if __name__ == "__main__":
    main()
