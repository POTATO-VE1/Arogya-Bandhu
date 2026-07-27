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


# ── Production config validation (T1 in docs/09_PLAN.md) ──────────────────────

class TestProdConfigValidation:
    """When IS_PROD=1, validate_production() must catch insecure config:
    weak SECRET_KEY, short ADMIN_PASSWORD, missing PUBLIC_BASE_URL, weak
    ADMIN_PASSWORD blocklist, and Twilio-without-signature-check."""

    def test_dev_returns_no_errors(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", False)
        assert cfg.settings.validate_production() == []

    def test_prod_missing_secret_key(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="", ADMIN_PASSWORD="goodpass1234",
                         PUBLIC_BASE_URL="https://x.example")
        errs = s.validate_production()
        assert any("SECRET_KEY" in e for e in errs)

    def test_prod_weak_admin_password(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="short",
                         PUBLIC_BASE_URL="https://x.example")
        errs = s.validate_production()
        assert any("ADMIN_PASSWORD" in e and "8 characters" in e for e in errs)

    def test_prod_default_password_blocklist(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="Admin1234",
                         PUBLIC_BASE_URL="https://x.example")
        errs = s.validate_production()
        assert any("demo default" in e for e in errs)

    def test_prod_missing_public_base_url(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="goodpass1234",
                         PUBLIC_BASE_URL="")
        errs = s.validate_production()
        assert any("PUBLIC_BASE_URL" in e for e in errs)

    def test_prod_twilio_partial_without_sig_check(self, monkeypatch):
        """If any Twilio cred is set, signature validation must be on."""
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="goodpass1234",
                         PUBLIC_BASE_URL="https://x.example",
                         TWILIO_ACCOUNT_SID="ACtest",
                         TWILIO_VALIDATE_SIGNATURE=False)
        errs = s.validate_production()
        assert any("TWILIO_VALIDATE_SIGNATURE" in e for e in errs)

    def test_prod_twilio_with_sig_check_ok(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="goodpass1234",
                         PUBLIC_BASE_URL="https://x.example",
                         TWILIO_ACCOUNT_SID="ACtest",
                         TWILIO_VALIDATE_SIGNATURE=True)
        errs = s.validate_production()
        assert not any("TWILIO_VALIDATE_SIGNATURE" in e for e in errs)

    def test_prod_no_twilio_no_sig_required(self, monkeypatch):
        """If no Twilio creds are set, signature validation is irrelevant."""
        from app import config as cfg
        monkeypatch.setattr(cfg, "IS_PROD", True)
        s = cfg.Settings(SECRET_KEY="x" * 64, ADMIN_PASSWORD="goodpass1234",
                         PUBLIC_BASE_URL="https://x.example",
                         TWILIO_VALIDATE_SIGNATURE=False)
        errs = s.validate_production()
        assert not any("TWILIO_VALIDATE_SIGNATURE" in e for e in errs)