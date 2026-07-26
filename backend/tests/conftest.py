import os
import tempfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.security import _attempts

os.environ.setdefault("SECRET_KEY", "test-secret-32-bytes-random-aaaaa")
os.environ.setdefault("ADMIN_PASSWORD", "changeme123")
os.environ.setdefault("TWILIO_VALIDATE_SIGNATURE", "0")
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.example")
os.environ.setdefault("CALL_ALLOWLIST", "+919876543210")

# Force Twilio/Groq disabled in tests — override real .env values
# so tests don't hit live APIs
for _k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
           "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[_k] = ""

from app.db import SessionLocal, init_engine
from app.main import app


@pytest.fixture()
def _engine():
    """One fresh SQLite per test, shared by `db` and `client` (both depend on it)."""
    d = tempfile.mkdtemp(prefix="abtest_")
    url = f"sqlite:///{d}/app.db"
    init_engine(url)
    return url


@pytest.fixture()
def db(_engine) -> Iterator:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(_engine) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _attempts.clear()
    yield