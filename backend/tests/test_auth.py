from fastapi.testclient import TestClient

from app.main import app

ADMIN = {"username": "admin", "password": "changeme123"}


def test_healthz_public(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_login_ok_sets_cookie(client):
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    assert "session" in r.cookies


def test_login_bad_credentials(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_board_requires_session(client):
    # /api/board doesn't exist yet, but /api/auth/me does and requires auth
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_session(client):
    client.post("/api/auth/login", json=ADMIN)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["display_name"] == "District Admin"


def test_rate_limit_locks(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    # 6th attempt (even with correct password) → 429
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 429, r.text


def test_security_headers_present(client):
    r = client.get("/api/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"