import asyncio
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.audio.gen_audio import AUDIO_DIR
from app.config import IS_PROD, settings
from app.db import SessionLocal, create_all
from app.ivr import engine
from app.models import User
from app.notify import telegram_red
from app.routers import auth as auth_router
from app.routers import enrollments as enrollments_router
from app.routers import patients as patients_router
from app.routers import protocols as protocols_router
from app.routers import webhooks as webhooks_router
from app.routers import escalations as escalations_router
from app.routers import events as events_router
from app.routers import sim as sim_router
from app.routers import import_ as import_router

from app.routers import health as health_router
from app.routers import reports as reports_router
from app.routers import staff as staff_router
from app.routers import admin as admin_router
from app.routers import analytics as analytics_router
from app.routers import dashboard as dashboard_router
from app.routers import hospitals as hospitals_router
from app.routers import hmis as hmis_router
from app.routers.staff_activity import router as staff_activity_router
from app.routers import abdm as abdm_router
from app.scheduler import reschedule_pending, shutdown_scheduler
from app.security import hash_password
from app.telegram.bot import poll_loop


def _validate_startup():
    """Fail fast if critical config is missing in production."""
    errors = settings.validate_production()
    if errors:
        for e in errors:
            print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)


def _seed_admin() -> None:
    """Seed admin + superadmin + system accounts. Other staff are
    created via the Telegram admin bot or the staff management UI.

    Superadmin is the cross-hospital role — one per deploy, no hospital_code
    filter applied. Uses SUPERADMIN_USERNAME (default `root`) and
    SUPERADMIN_PASSWORD env vars; if unset, the superadmin is not seeded.

    The "system" user is used as the `created_by` foreign key for
    service-driven intakes (HMIS webhook, CSV upload, ABDM push, etc.).
    It can't log in (no real password) but the row exists so the
    FK constraint is satisfied.
    """
    from app.security import reset_rate_limits
    reset_rate_limits()
    s = SessionLocal()
    try:
        # Per-hospital admin (the "admin" user)
        admin = s.query(User).filter(User.username == "admin").first()
        admin_pw = settings.ADMIN_PASSWORD or "admin123"
        if not admin:
            s.add(User(
                hospital_code=settings.HOSPITAL_CODE,
                username="admin",
                password_hash=hash_password(admin_pw),
                display_name="District Admin",
                role="admin",
            ))
        else:
            admin.password_hash = hash_password(admin_pw)
            if admin.role == "admin":
                admin.password_hash = hash_password(admin_pw)

        # Cross-hospital superadmin (the "root" user)
        root_user = settings.SUPERADMIN_USERNAME or "root"
        root_pw = settings.SUPERADMIN_PASSWORD
        if root_pw and len(root_pw) >= 8:
            root = s.query(User).filter(User.username == root_user).first()
            if not root:
                s.add(User(
                    hospital_code="*",  # sentinel: superadmin sees all
                    username=root_user,
                    password_hash=hash_password(root_pw),
                    display_name="System Superadmin",
                    role="superadmin",
                ))
            elif root.role != "superadmin":
                root.role = "superadmin"
                root.hospital_code = "*"
                root.password_hash = hash_password(root_pw)

        # System user — for service-driven FK references (HMIS push,
        # CSV import, ABDM). Username is the sentinel; password is
        # random so it can't log in.
        sys_user = s.query(User).filter(User.username == "_system").first()
        if not sys_user:
            s.add(User(
                hospital_code=settings.HOSPITAL_CODE,
                username="_system",
                password_hash=hash_password(secrets.token_hex(32)),
                display_name="System (service)",
                role="staff",  # role is "staff" but username has underscore prefix
            ))
        s.commit()
    finally:
        s.close()


def _validate_twilio_accounts() -> None:
    """Pre-flight: hit every configured Twilio account's `accounts.fetch()`
    and log the result. Fails fast only if `TWILIO_FAIL_ON_PREFLIGHT=1`;
    otherwise logs warnings and lets the deploy boot (a single bad account
    shouldn't block the whole system — the rotator will skip it).

    Called from lifespan before any other startup so misconfigured
    accounts show up in the deploy logs at the very top.
    """
    log = logging.getLogger("twilio.preflight")
    from app.ivr import twilio_rotator
    rotator = twilio_rotator.get_rotator()
    if rotator is None:
        log.info("twilio preflight: no accounts configured (sim-only mode)")
        return
    errors = rotator.validate_all_on_startup()
    if errors and os.getenv("TWILIO_FAIL_ON_PREFLIGHT", "0") == "1":
        for e in errors:
            log.error("twilio preflight FATAL: %s", e)
        raise RuntimeError(
            f"twilio preflight failed for {len(errors)} account(s); "
            f"refusing to boot (TWILIO_FAIL_ON_PREFLIGHT=1). "
            f"Errors: {'; '.join(errors)}")
    if errors:
        for e in errors:
            log.warning("twilio preflight: %s  (continuing — "
                        "this account will be skipped at runtime)", e)


def _health_check_twilio_accounts() -> None:
    """Periodic health check (every 5 min via APScheduler). Pings each
    Twilio account; if one is down for >10 min it's marked unavailable
    and skipped by the rotator."""
    log = logging.getLogger("twilio.health")
    from app.ivr import twilio_rotator
    rotator = twilio_rotator.get_rotator()
    if rotator is None:
        return
    report = rotator.health_check_all()
    for name, status in report.items():
        if status == "ok":
            log.debug("twilio health: %s ok", name)
        else:
            log.warning("twilio health: %s %s", name, status)


def _daily_checkin() -> None:
    """Periodic daily check-in (every 24h via APScheduler). For each
    verified severe-case patient, DM them 'how are you feeling today?'
    in their preferred language and set current_step=collecting_feeling
    so their reply is captured into feeling_info.

    Skips:
    - patients not yet verified
    - patients whose check-in is already done today
    - any patient whose enrollment isn't 'severe' (wound_care /
      antibiotic_course / post_surgical)
    """
    from datetime import datetime, timezone
    from app.telegram.bot import _send
    from app.telegram.sessions import get_session, save_session
    from app.models import TelegramSession, Enrollment, Patient
    from app.config import settings

    log = logging.getLogger("telegram.checkin")
    if not settings.TELEGRAM_BOT_TOKEN:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    SEVERE = ("wound_care", "antibiotic_course", "post_surgical")
    s = SessionLocal()
    try:
        rows = s.query(TelegramSession).filter(
            TelegramSession.is_verified == 1,
            TelegramSession.telegram_id.isnot(None),
        ).all()
        sent = 0
        for ts in rows:
            if ts.last_checkin_date == today:
                continue
            # Confirm patient is severe-case
            if not ts.patient_id:
                continue
            en = s.query(Enrollment).filter(
                Enrollment.patient_id == ts.patient_id,
                Enrollment.status == "active",
            ).first()
            if not en or en.protocol_id not in SEVERE:
                continue
            lang = ts.preferred_lang or "en"
            if lang == "kn":
                msg = ("[!] ದೈನಂದಿನ ಆರೋಗ್ಯ ಪರಿಶೀಲನೆ\n\n"
                       "[?] ನಿಮ್ಮ ಆರೋಗ್ಯ ಸ್ಥಿತಿ ಹೇಗಿದೆ ಇಂದು?\n"
                       "ಉದಾ: 'ಉತ್ತಮ ಚೇತರಿಕೆ', 'ಸ್ವಲ್ಪ ನೋವು', 'ಜ್ವರ ಕಡಿಮೆಯಾಗಿದೆ'")
            else:
                msg = ("[!] Daily check-in\n\n"
                       "[?] How are you feeling today?\n"
                       "Example: 'Recovering well', 'Some pain', 'Fever has reduced'")
            try:
                _send(settings.TELEGRAM_BOT_TOKEN, int(ts.telegram_id), msg)
                # Mark step so the next message is captured as feeling.
                ts.current_step = "collecting_feeling"
                ts.last_checkin_date = today
                sent += 1
            except Exception as ex:
                log.warning("daily check-in send failed for %s: %s", ts.telegram_id, ex)
        if sent:
            s.commit()
        log.info("daily check-in: %d patients pinged", sent)
    except Exception as ex:
        log.exception("daily check-in failed: %s", ex)
        s.rollback()
    finally:
        s.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_startup()
    create_all()
    from app.db import ensure_default_hospital
    ensure_default_hospital()
    _seed_admin()
    # Twilio pre-flight: hit every account's `accounts.fetch()` so
    # misconfigured accounts show up in the deploy log immediately.
    _validate_twilio_accounts()
    engine.register_red_hook(lambda db, esc: telegram_red(esc))
    reschedule_pending()
    import_router.cleanup_old_uploads()
    # T12: 5-min retry job for failed Telegram sends
    try:
        from app.scheduler import ensure_retry_job
        ensure_retry_job()
    except Exception:
        pass
    # Periodic Twilio health check (every 5 min) — detects dead accounts
    # before a call fails. Skips accounts that are down for >10 min.
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        from app.scheduler import ensure_scheduler
        sched = ensure_scheduler()
        sched.add_job(
            _health_check_twilio_accounts,
            trigger=IntervalTrigger(minutes=5),
            id="twilio_health_check", replace_existing=True,
        )
    except Exception as ex:
        logging.getLogger("twilio.health").warning(
            "could not register periodic Twilio health check: %s", ex)

    # Daily check-in: every 24h, ask each verified severe-case patient
    # "how are you feeling today?" in their preferred language. The
    # patient's reply is captured by the regular handler (it will see
    # current_step=collecting_feeling after the check-in prompt).
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        from app.scheduler import ensure_scheduler
        sched = ensure_scheduler()
        sched.add_job(
            _daily_checkin,
            trigger=IntervalTrigger(hours=24),
            id="telegram_daily_checkin", replace_existing=True,
        )
        logging.getLogger("telegram.checkin").info("daily check-in job registered")
    except Exception as ex:
        logging.getLogger("telegram.checkin").warning(
            "could not register daily check-in: %s", ex)
    # start telegram polling in background (only if token configured)
    tg_task = None
    if settings.TELEGRAM_BOT_TOKEN:
        tg_task = asyncio.create_task(poll_loop())

    yield
    if tg_task:
        tg_task.cancel()
        try:
            await tg_task
        except asyncio.CancelledError:
            pass
    shutdown_scheduler()


app = FastAPI(title="Aarogya Bandhu", lifespan=lifespan)

# ── CORS (permissive in dev, locked in prod) ──
if IS_PROD:
    origins = [settings.PUBLIC_BASE_URL] if settings.PUBLIC_BASE_URL else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Session cookie ──
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.effective_secret_key,
    max_age=43200,
    same_site="lax",
    https_only=IS_PROD,
)

app.include_router(auth_router.router)
app.include_router(protocols_router.router)
app.include_router(enrollments_router.router)
app.include_router(patients_router.router)
app.include_router(webhooks_router.router)
app.include_router(escalations_router.router)
app.include_router(events_router.router)
app.include_router(sim_router.router)
app.include_router(import_router.router)
app.include_router(health_router.router)
app.include_router(reports_router.router)
app.include_router(staff_router.router)
app.include_router(staff_activity_router)
app.include_router(admin_router.router)
app.include_router(analytics_router.router)
app.include_router(dashboard_router.router)
app.include_router(hospitals_router.router)
app.include_router(hmis_router.router)
app.include_router(abdm_router.router)

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; media-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'self'"
    )
    if IS_PROD:
        # 6 months, include subdomains, eligible for preload. Only behind
        # HTTPS — harmless on plain HTTP because browsers ignore it.
        resp.headers["Strict-Transport-Security"] = (
            "max-age=15768000; includeSubDomains"
        )
    resp.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), payment=()"
    )
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return resp


@app.get("/api/healthz")
def healthz() -> dict:
    return {"ok": True, "hospital": settings.HOSPITAL_NAME}


# Serve the built frontend (SPA) LAST so it can never shadow API/webhook/audio/ws routes.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str, request: Request):
        if full_path.startswith(("api/", "webhooks/", "audio/", "assets/", "ws/")):
            raise HTTPException(404)
        if full_path in ("manifest.json", "favicon.ico"):
            return Response(
                content=(STATIC_DIR / full_path).read_bytes(),
                media_type="application/json" if full_path.endswith(".json") else "image/x-icon",
            )
        index = STATIC_DIR / "index.html"
        return Response(content=index.read_text(encoding="utf-8"),
                         media_type="text/html")
