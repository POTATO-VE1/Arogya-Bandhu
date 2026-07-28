import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite") and ":memory:" not in url:
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.replace("sqlite:///", "", 1)
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
    eng = create_engine(url, future=True, **kwargs)

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _):  # WAL for concurrency (docs/02 §2 notes)
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


engine = _make_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_engine(url: str) -> None:
    """Rebind engine/SessionLocal (used by tests and create_all)."""
    global engine, SessionLocal
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)
    from app import models  # noqa: F401 – register tables on metadata

    Base.metadata.create_all(bind=engine)


def create_all() -> None:
    from app import models  # noqa
    from app.health_fit import PatientHealthToken, PatientHealthData, PatientReport  # noqa

    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()


def _migrate_add_columns() -> None:
    """Add new columns to existing SQLite tables if missing."""
    with engine.connect() as conn:
        for table, column, typedef in [
            ("users", "ward", "TEXT"),
            ("users", "telegram_id", "INTEGER"),
            ("users", "supervisor", "TEXT"),
            ("enrollments", "created_by", "TEXT REFERENCES users(id)"),
            ("followup_calls", "triggered_by", "TEXT REFERENCES users(id)"),
            ("followup_calls", "account_name", "TEXT"),
            ("telegram_sessions", "diet_info", "TEXT"),
            ("telegram_sessions", "medication_info", "TEXT"),
            ("telegram_sessions", "feeling_info", "TEXT"),
            ("telegram_sessions", "last_checkin_date", "TEXT"),
            ("telegram_sessions", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
            ("telegram_sessions", "auth_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("patients", "abha_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("patients", "abha_verified_at", "TEXT"),
        ]:
            try:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
                )
                conn.commit()
            except Exception:
                pass  # column already exists


def ensure_default_hospital() -> None:
    """Backfill the hospitals table from HOSPITAL_CODE / HOSPITAL_NAME env
    vars. Idempotent: re-runs are no-ops. Called from lifespan after
    create_all so the table exists."""
    from app.config import settings
    from app.models import Hospital
    s = SessionLocal()
    try:
        h = s.query(Hospital).filter(Hospital.code == settings.HOSPITAL_CODE).first()
        if not h:
            s.add(Hospital(code=settings.HOSPITAL_CODE,
                           name=settings.HOSPITAL_NAME,
                           active=1))
            s.commit()
    finally:
        s.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()