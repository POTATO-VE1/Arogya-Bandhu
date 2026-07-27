"""wipe.py — remove every row from every table.

Use this when you want a clean slate without re-seeding any demo data.
Equivalent to `python -m app.scripts.seed_demo --wipe`.

Examples
--------
    # from backend/
    ../venv/bin/python -m app.scripts.wipe
    ../venv/bin/python -m app.scripts.wipe --yes
"""
from __future__ import annotations

import argparse

from app.config import settings
from app.db import SessionLocal, init_engine
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation, FollowupCall,
    Hospital, Patient, PendingNotification, TelegramSession, User,
)

# FK-safe deletion order
_TABLES = [
    CallResponse, Escalation, FollowupCall, EnrollmentMed,
    Enrollment, Patient, User, Hospital, PendingNotification,
    TelegramSession,
]


def wipe(echo: bool = True) -> dict[str, int]:
    """Delete every row from every table. Returns a {table: rows_deleted} map."""
    init_engine(settings.DATABASE_URL)
    s = SessionLocal()
    summary: dict[str, int] = {}
    try:
        for m in _TABLES:
            n = s.query(m).count()
            s.query(m).delete()
            s.commit()
            summary[m.__tablename__] = n
    finally:
        s.close()
    if echo:
        print("wipe complete:")
        for t, n in summary.items():
            print(f"  {t:<24} {n} rows")
        print(f"  {'TOTAL':<24} {sum(summary.values())} rows")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Wipe all rows from all tables.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the interactive confirmation prompt")
    args = ap.parse_args()
    if not args.yes:
        print("This will DELETE every row in every table.")
        print("Tables: " + ", ".join(m.__tablename__ for m in _TABLES))
        ans = input("Type 'wipe' to confirm: ").strip().lower()
        if ans != "wipe":
            print("aborted.")
            return
    wipe()


if __name__ == "__main__":
    main()
