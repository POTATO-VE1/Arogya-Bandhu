"""Tests for the ABDM (Ayushman Bharat Digital Mission) client.

Covers:
- Mock client: request_otp, verify (success + 3 failure modes), push
- Real client: request shape matches the ABDM sandbox spec
- Env-driven singleton
- Status endpoint
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ── Mock client ─────────────────────────────────────────────────────────────

def test_mock_request_otp_returns_txn_id():
    from app.abdm import MockAbdmClient, _MOCK_SEEDS
    client = MockAbdmClient()
    txn = client.request_otp("14-3344-5566-7788")
    assert txn.startswith("mock-txn-")
    # txn is also persisted on the request; it can be re-used
    assert isinstance(txn, str) and len(txn) > 5


def test_mock_verify_with_correct_otp():
    from app.abdm import MockAbdmClient, _MOCK_SEEDS
    client = MockAbdmClient()
    txn = client.request_otp("14-3344-5566-7788")
    result = client.verify_abha("14-3344-5566-7788", "123456", txn)
    assert result.verified is True
    assert result.name == "Lakshmamma Devi"
    assert result.gender == "F"
    assert result.year_of_birth == 1958
    assert result.mobile == "+919876543210"
    assert result.txn_id == txn


def test_mock_verify_with_wrong_otp():
    from app.abdm import MockAbdmClient
    client = MockAbdmClient()
    txn = client.request_otp("14-3344-5566-7788")
    result = client.verify_abha("14-3344-5566-7788", "000000", txn)
    assert result.verified is False
    assert "OTP" in (result.reason or "")


def test_mock_verify_with_unknown_abha():
    from app.abdm import MockAbdmClient
    client = MockAbdmClient()
    txn = client.request_otp("99-9999-9999-9999")
    result = client.verify_abha("99-9999-9999-9999", "123456", txn)
    assert result.verified is False
    assert "not found" in (result.reason or "").lower()


def test_mock_push_writes_to_outbox():
    from app.abdm import MockAbdmClient
    with tempfile.TemporaryDirectory() as d:
        outbox = os.path.join(d, "outbox.jsonl")
        client = MockAbdmClient(outbox_path=outbox)
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {"resourceType": "Patient", "name": [{"text": "Lakshmamma"}]}},
                {"resource": {"resourceType": "MedicationRequest"}},
            ],
        }
        result = client.push_discharge(bundle)
        assert result.accepted is True
        assert result.mock_outbox_path == outbox
        # The outbox file has one line, valid JSON, with our bundle
        with open(outbox) as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["bundle"] == bundle
        assert rec["bundle_summary"]["n_resources"] == 2
        assert rec["bundle_summary"]["n_meds"] == 1
        assert rec["bundle_summary"]["patient_name"] == "Lakshmamma"


# ── Real client (shape conformance to the ABDM spec) ──────────────────────

def test_real_client_request_otp_calls_correct_url():
    """Verify the RealAbdmClient calls the documented ABDM endpoint
    with the documented body shape — no mocks, just inspect the
    httpx call args."""
    from app.abdm import RealAbdmClient
    from httpx import Response, Request
    with patch("app.abdm.httpx.post") as mock_post:
        # Mock Response needs a `request` attribute for raise_for_status
        def _resp(code, body):
            r = Response(code, json=body)
            r._request = Request("POST", "https://dev.abdm.gov.in/")
            return r
        # First call: OAuth token
        mock_post.return_value = _resp(200, {
            "accessToken": "tok-abc", "expiresIn": 3000,
        })
        client = RealAbdmClient("https://dev.abdm.gov.in", "client-id", "client-secret")
        client._token_get()  # prime the cache
        # Second call: request_otp
        mock_post.return_value = _resp(200, {"txnId": "T-123"})
        txn = client.request_otp("14-3344-5566-7788")
        assert txn == "T-123"
        # Assert: 2 calls total; the second one is the request-otp
        assert mock_post.call_count == 2
        # The second call went to the ABDM request-otp URL
        otp_call = mock_post.call_args_list[1]
        url = otp_call.kwargs.get("url") or (otp_call.args[0] if otp_call.args else None)
        assert url == "https://dev.abdm.gov.in/v3/healthid/14-3344-5566-7788/request-otp"
        # With the documented body shape
        body = otp_call.kwargs.get("json")
        assert body == {"scope": ["abha-login"], "purpose": "KYC"}
        # And the OAuth bearer header
        headers = otp_call.kwargs.get("headers")
        assert headers["Authorization"] == "Bearer tok-abc"


def test_real_client_verify_calls_correct_url():
    from app.abdm import RealAbdmClient
    from httpx import Response, Request
    with patch("app.abdm.httpx.post") as mock_post:
        def _resp(code, body):
            r = Response(code, json=body)
            r._request = Request("POST", "https://dev.abdm.gov.in/")
            return r
        # side_effect: 1st call returns token, 2nd returns verify result
        mock_post.side_effect = [
            _resp(200, {"accessToken": "tok"}),
            _resp(200, {
                "verified": True, "name": "Lakshmamma", "gender": "F",
                "yearOfBirth": "1958", "mobile": "+919876543210",
            }),
        ]
        client = RealAbdmClient("https://dev.abdm.gov.in", "id", "secret")
        r = client.verify_abha("14-3344-5566-7788", "123456", "T-1")
        assert r.verified is True
        assert r.name == "Lakshmamma"
        # 2 calls: token + verify
        assert mock_post.call_count == 2
        # The verify URL
        verify_call = mock_post.call_args_list[1]
        url = verify_call.kwargs.get("url") or (verify_call.args[0] if verify_call.args else None)
        assert url == "https://dev.abdm.gov.in/v3/healthid/14-3344-5566-7788/verify"
        # And the documented body
        body = verify_call.kwargs.get("json")
        assert body == {"otp": "123456", "txnId": "T-1"}


def test_real_client_requires_credentials():
    from app.abdm import RealAbdmClient
    with pytest.raises(ValueError, match="ABDM_CLIENT_ID"):
        RealAbdmClient("https://dev.abdm.gov.in", "", "secret")
    with pytest.raises(ValueError, match="ABDM_CLIENT_SECRET"):
        RealAbdmClient("https://dev.abdm.gov.in", "id", "")


# ── Env-driven singleton ──────────────────────────────────────────────────

def test_get_abdm_default_is_mock(monkeypatch):
    monkeypatch.setenv("ABDM_MODE", "mock")
    from app import abdm
    abdm.reset_abdm()
    client = abdm.get_abdm()
    assert client.__class__.__name__ == "MockAbdmClient"


def test_get_abdm_real_requires_creds(monkeypatch):
    import importlib
    import app.config as cfg_mod
    import app.abdm as abdm_mod
    monkeypatch.setenv("ABDM_MODE", "real")
    monkeypatch.setenv("ABDM_CLIENT_ID", "id")
    monkeypatch.setenv("ABDM_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ABDM_BASE_URL", "https://dev.abdm.gov.in")
    importlib.reload(cfg_mod)
    importlib.reload(abdm_mod)
    client = abdm_mod.get_abdm()
    assert client.__class__.__name__ == "RealAbdmClient"


def test_get_abdm_real_falls_back_to_mock_if_no_creds(monkeypatch, caplog):
    import importlib
    import app.config as cfg_mod
    import app.abdm as abdm_mod
    monkeypatch.setenv("ABDM_MODE", "real")
    monkeypatch.delenv("ABDM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ABDM_CLIENT_SECRET", raising=False)
    importlib.reload(cfg_mod)
    importlib.reload(abdm_mod)
    client = abdm_mod.get_abdm()
    # Falls back to mock rather than crashing — the deploy boots
    assert client.__class__.__name__ == "MockAbdmClient"


# ── Status endpoint (via the router) ────────────────────────────────────────

def test_abdm_status_endpoint_reports_mode(monkeypatch):
    """The /api/abdm/status endpoint reports the current mode and
    the list of demo seeds in mock mode."""
    monkeypatch.setenv("ABDM_MODE", "mock")
    monkeypatch.setenv("HMIS_SHARED_SECRET", "demo")
    from app.db import init_engine
    import tempfile, sys
    for m in list(sys.modules):
        if m.startswith("app."): del sys.modules[m]
    d = tempfile.mkdtemp(prefix="abdm_status_")
    init_engine(f"sqlite:///{d}/app.db")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": os.getenv("ADMIN_PASSWORD", "admin123")})
        assert r.status_code == 200
        r = client.get("/api/abdm/status")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "mock"
        assert body["mocked"] is True
        assert "14-3344-5566-7788" in body["demo_seeds"]
