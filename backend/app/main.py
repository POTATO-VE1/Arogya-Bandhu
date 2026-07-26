import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.audio.gen_audio import AUDIO_DIR
from app.config import settings
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
from app.routers import amr_steward as amr_steward_router
from app.routers import health as health_router
from app.scheduler import reschedule_pending, shutdown_scheduler
from app.security import hash_password
from app.telegram.bot import poll_loop
from app.amr_steward import send_daily_reminders, check_pill_count, check_non_adherence, send_weekly_summary

IS_PROD = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RENDER") or os.getenv("PRODUCTION")


def _seed_admin() -> None:
    s = SessionLocal()
    try:
        if s.query(User).count() == 0:
            if not settings.ADMIN_PASSWORD:
                print("[WARN] ADMIN_PASSWORD not set — skipping admin seed")
                return
            s.add(User(
                hospital_code=settings.HOSPITAL_CODE,
                username="admin",
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                display_name="District Admin",
                role="admin",
            ))
            s.commit()
    finally:
        s.close()


def _register_amr_jobs() -> None:
    """Register daily AMR stewardship jobs on the APScheduler."""
    from app.scheduler import ensure_scheduler
    from apscheduler.triggers.cron import CronTrigger
    sched = ensure_scheduler()
    # daily reminders at 09:00 IST (03:30 UTC)
    sched.add_job(send_daily_reminders, CronTrigger(hour=3, minute=30),
                  id="amr:daily_reminders", replace_existing=True)
    # pill count check at 10:00 IST (04:30 UTC)
    sched.add_job(check_pill_count, CronTrigger(hour=4, minute=30),
                  id="amr:pill_count", replace_existing=True)
    # non-adherence check at 21:00 IST (15:30 UTC)
    sched.add_job(check_non_adherence, CronTrigger(hour=15, minute=30),
                  id="amr:non_adherence", replace_existing=True)
    # weekly summary: Monday 09:00 IST
    sched.add_job(send_weekly_summary, CronTrigger(day_of_week="mon", hour=3, minute=30),
                  id="amr:weekly_summary", replace_existing=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_all()
    _seed_admin()
    engine.register_red_hook(lambda db, esc: telegram_red(esc))
    reschedule_pending()
    import_router.cleanup_old_uploads()
    # start telegram polling in background (only if token configured)
    tg_task = None
    if settings.TELEGRAM_BOT_TOKEN:
        tg_task = asyncio.create_task(poll_loop())
    # register daily AMR stewardship jobs
    _register_amr_jobs()
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
app.include_router(amr_steward_router.router)
app.include_router(health_router.router)

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; media-src 'self'; style-src 'self' 'unsafe-inline'"
    )
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
