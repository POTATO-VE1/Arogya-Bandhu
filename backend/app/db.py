import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()