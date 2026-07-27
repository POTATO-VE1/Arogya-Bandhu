"""Telegram bot unit tests — pure-function utilities and session helpers.

These don't go through the API (the bot is a long-polling Telegram
client, not an HTTP endpoint), so we test the helpers directly.

Note: tests that need the per-test SQLite DB must declare the `_engine`
fixture (or `db`) so the in-memory SessionLocal is rebound to a fresh
tempfile DB with the new `telegram_sessions` columns. Without that,
`SessionLocal()` falls back to the LIVE engine (`data/app.db`) which
may have an older schema.
"""
import pytest


# ── Phone normalisation (T12 follow-up) ─────────────────────────────────────

class TestNormalisePhone:
    """_normalise_phone accepts all the common Indian phone input shapes
    that nurses paste in: with +, without +, 10-digit, 12-digit, with
    leading trunk 0, with spaces/dashes, with the 91 country code."""

    def test_with_plus_10_digit(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("+919876543210") == "+919876543210"

    def test_bare_10_digit_gets_plus_91(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("9876543210") == "+919876543210"

    def test_bare_12_digit_strips_91(self):
        """If the user typed the country code without '+', strip the 91
        prefix so we don't end up with a 12-digit number (which the DB
        wouldn't match)."""
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("919876543210") == "+919876543210"

    def test_with_leading_trunk_zero(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("09876543210") == "+919876543210"

    def test_with_spaces_and_dashes(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("+91 98765-43210") == "+919876543210"

    def test_with_parens(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("(98765) 43210") == "+919876543210"

    def test_too_short_returns_none(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("12345") is None

    def test_too_long_returns_none(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("1234567890123456789") is None

    def test_non_digit_returns_none(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("abcdefghij") is None

    def test_empty_returns_none(self):
        from app.telegram.bot import _normalise_phone
        assert _normalise_phone("") is None


# ── Symptom detection (T12) ────────────────────────────────────────────────

class TestSymptomDetection:
    def test_critical_english(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("I have chest pain") == "critical"
        assert _detect_symptoms("can't breathe") == "critical"

    def test_high_english(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("high fever for 3 days") == "high"
        assert _detect_symptoms("vomiting blood") == "high"

    def test_medium_english(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("I have a headache") == "medium"

    def test_critical_kannada(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("ಎದೆನೋವು ಆಗುತ್ತಿದೆ") == "critical"

    def test_high_kannada(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("ತೀವ್ರ ಜ್ವರ ಬಂದಿದೆ") == "high"

    def test_no_symptom(self):
        from app.telegram.bot import _detect_symptoms
        assert _detect_symptoms("how is the weather today?") is None


class TestSOSDetection:
    def test_sos_english(self):
        from app.telegram.bot import _detect_sos
        assert _detect_sos("help me please") is True
        assert _detect_sos("this is an emergency") is True

    def test_sos_kannada(self):
        from app.telegram.bot import _detect_sos
        assert _detect_sos("ಸಹಾಯ ಮಾಡಿ") is True
        assert _detect_sos("ತುರ್ತು ಆಸ್ಪತ್ರೆಗೆ ಕರೆಯಿರಿ") is True

    def test_not_sos(self):
        from app.telegram.bot import _detect_sos
        assert _detect_sos("how is the recovery going?") is False
        assert _detect_sos("thank you so much") is False


# ── In-memory cache bounds (T12) ───────────────────────────────────────────

class TestCacheBounds:
    """The in-memory _pending_* dicts should not grow unboundedly."""

    def test_family_selection_bounded(self, monkeypatch):
        from app.telegram import bot
        for i in range(1100):
            bot._pending_family_selection[i] = []
        bot._evict_family_selection_if_full()
        assert len(bot._pending_family_selection) <= 1000

    def test_admin_states_bounded(self, monkeypatch):
        from app.telegram import admin_bot
        for i in range(1100):
            admin_bot._admin_states[i] = {}
        admin_bot._evict_admin_states_if_full()
        assert len(admin_bot._admin_states) <= 1000

    def test_sessions_cache_bounded(self, monkeypatch):
        from app.telegram import sessions
        monkeypatch.setattr(sessions, "_SESSION_CACHE_MAX", 10)
        for i in range(20):
            sessions._sessions[i] = sessions.Session(telegram_id=i)
        sessions._evict_cache_if_full()
        assert len(sessions._sessions) <= 10


# ── Auth attempt lockout + session persistence (T12) ─────────────────────
# Tests in this class need the per-test tempfile SQLite, so they declare
# the `db` fixture (and `client` for the app-load). This forces
# `init_engine` to run, which rebuilds the table with the new
# `is_admin` and `auth_attempts` columns.

class TestAuthSessionPersistence:
    def test_max_auth_attempts_is_5(self):
        from app.telegram.sessions import MAX_AUTH_ATTEMPTS
        assert MAX_AUTH_ATTEMPTS == 5

    def test_reset_session_clears_all_auth_fields(self, db):
        from app.telegram.sessions import (
            Session, get_session, reset_session, save_session,
        )
        from app.models import TelegramSession
        import uuid

        telegram_id = 70000001
        ts = db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if ts:
            db.delete(ts); db.commit()
        ts = TelegramSession(
            id=uuid.uuid4().hex, telegram_id=telegram_id,
            phone="+919999999999", patient_id=None,
            is_verified=1, is_staff=1, is_admin=1,
            auth_attempts=4, preferred_lang="kn",
            diet_info="test", google_fit_consent=1,
            current_step="active", updated_at="2025-01-01T00:00:00",
        )
        db.add(ts); db.commit()

        session = get_session(telegram_id)
        assert session.phone == "+919999999999"
        assert session.patient_id is None
        assert session.auth_attempts == 4
        assert session.verified is True
        assert session.staff is True
        assert session.admin is True
        assert session.current_step == "active"

        reset_session(session)

        assert session.phone is None
        assert session.patient_id is None
        assert session.verified is False
        assert session.staff is False
        assert session.admin is False
        assert session.auth_attempts == 0
        assert session.diet_info is None
        assert session.google_fit_consent is False
        assert session.current_step == "awaiting_phone"
        assert session.preferred_lang == "en"

        # Persisted
        db.expire_all()
        ts = db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        assert ts.phone is None
        assert ts.is_verified == 0
        assert ts.is_staff == 0
        assert ts.is_admin == 0
        assert ts.auth_attempts == 0

    def test_admin_persists_across_get(self, db):
        from app.telegram.sessions import get_session, save_session
        from app.models import TelegramSession

        telegram_id = 70000002
        ts = db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if ts:
            db.delete(ts); db.commit()

        session = get_session(telegram_id)
        assert session.admin is False
        session.admin = True
        save_session(session)

        # Reload from DB
        from app.telegram import sessions
        sessions._sessions.pop(telegram_id, None)

        session2 = get_session(telegram_id)
        assert session2.admin is True

    def test_auth_attempts_persists(self, db):
        from app.telegram.sessions import get_session, save_session
        from app.models import TelegramSession

        telegram_id = 70000003
        ts = db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if ts:
            db.delete(ts); db.commit()

        session = get_session(telegram_id)
        assert session.auth_attempts == 0
        session.auth_attempts = 3
        save_session(session)

        from app.telegram import sessions
        sessions._sessions.pop(telegram_id, None)

        session2 = get_session(telegram_id)
        assert session2.auth_attempts == 3

    def test_google_fit_consent_persists(self, db):
        from app.telegram.sessions import get_session
        from app.telegram.bot import notify_google_fit_linked
        from app.models import TelegramSession

        telegram_id = 70000004
        ts = db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if ts:
            db.delete(ts); db.commit()

        # Without a Telegram token (test env), the function just
        # persists the flag without trying to send.
        from app import config as cfg
        old_token = cfg.settings.TELEGRAM_BOT_TOKEN
        cfg.settings.TELEGRAM_BOT_TOKEN = ""
        try:
            notify_google_fit_linked(telegram_id)
        finally:
            cfg.settings.TELEGRAM_BOT_TOKEN = old_token

        from app.telegram import sessions
        sessions._sessions.pop(telegram_id, None)
        session = get_session(telegram_id)
        assert session.google_fit_consent is True


# ── Patient report lookup ────────────────────────────────────────────────

class TestPatientReport:
    def test_get_patient_report_by_id_returns_shape(self, db):
        from app.telegram.sessions import get_patient_report_by_id
        from app.models import Enrollment, EnrollmentMed, Patient, User
        from app.security import hash_password
        import uuid

        u = User(id=uuid.uuid4().hex, hospital_code="KA-DIST-01",
                 username=f"tg_test_{uuid.uuid4().hex[:6]}", display_name="X",
                 password_hash=hash_password("x"), role="nurse", ward="T-Ward")
        db.add(u); db.commit()
        p = Patient(id=uuid.uuid4().hex, hospital_code="KA-DIST-01",
                    name="Test Patient", age=30, sex="M",
                    caregiver_name="CG", caregiver_phone="+919876500999",
                    consent_at="2025-01-01T00:00:00+00:00", created_by=u.id)
        db.add(p); db.commit()
        e = Enrollment(id=uuid.uuid4().hex, hospital_code="KA-DIST-01",
                       patient_id=p.id, protocol_id="wound_care",
                       condition_label="test", ward="T-Ward",
                       discharge_date="2025-01-01",
                       created_by=u.id)
        db.add(e); db.commit()
        db.add(EnrollmentMed(id=uuid.uuid4().hex, enrollment_id=e.id,
                            med_name="M", med_type="antibiotic",
                            doses_per_day=2))
        db.commit()
        pid = p.id

        report = get_patient_report_by_id(pid)
        assert report is not None
        assert report["name"] == "Test Patient"
        assert report["condition"] == "test"
        assert "M" in report["meds"]
        assert report["ward"] == "T-Ward"

    def test_get_patient_report_by_id_unknown_returns_none(self):
        from app.telegram.sessions import get_patient_report_by_id
        assert get_patient_report_by_id("nonexistent-uuid-xxx") is None
