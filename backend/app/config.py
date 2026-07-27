import os
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

IS_PROD = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RENDER") or os.getenv("PRODUCTION"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./data/app.db"
    SECRET_KEY: str = ""  # REQUIRED in production; auto-generated for dev
    HOSPITAL_CODE: str = "KA-DIST-01"
    HOSPITAL_NAME: str = "District Hospital Demo"
    ADMIN_PASSWORD: str = ""  # REQUIRED; seed fails if empty
    # Cross-hospital superadmin (one per deploy, sees all hospitals).
    # Optional — leave SUPERADMIN_PASSWORD empty to skip the seed.
    SUPERADMIN_USERNAME: str = "root"
    SUPERADMIN_PASSWORD: str = ""

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_VALIDATE_SIGNATURE: bool = True
    PUBLIC_BASE_URL: str = ""

    # Multi-account rotation. JSON list of {"name","sid","token","from",
    # "allowlist":[...]}. When set, OVERRIDES the legacy single-account
    # vars above. Lets you bypass the per-account 5-verified-numbers cap
    # by using multiple free Twilio accounts. See app/ivr/twilio_rotator.py.
    TWILIO_ACCOUNTS: str = ""

    BHASHINI_API_KEY: str = ""
    BHASHINI_USER_ID: str = ""

    GROQ_API_KEY: str = ""
    # Additional Groq API keys for free-tier rate-limit rotation.
    # Comma-separated. The legacy GROQ_API_KEY above is always key #0.
    GROQ_API_KEYS: str = ""
    LLM_MODEL: str = "llama-3.3-70b-versatile"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_STAFF_CODE: str = ""
    # Admin's phone number (E.164 format) — only this user can create staff via Telegram bot
    ADMIN_PHONE_NUMBER: str = ""

    CALL_ALLOWLIST: str = ""

    # Google Fit OAuth (optional — unset = health device features disabled)
    GOOGLE_FIT_CLIENT_ID: str = ""
    GOOGLE_FIT_CLIENT_SECRET: str = ""
    HEALTH_ENCRYPT_KEY: str = ""  # Fernet key; auto-generated at startup if empty

    # L2: Escalation webhook (optional — unset = no webhook fired).
    # Hospital IT sets this to a WhatsApp-bot / PagerDuty / Slack-incoming
    # webhook URL. The body is HMAC-SHA256 signed with the secret.
    ESCALATION_WEBHOOK_URL: str = ""
    ESCALATION_WEBHOOK_SECRET: str = ""

    # ── HMIS (hospital EMR) integration ──
    # Shared secret the hospital's IT uses to compute the X-HMIS-Signature
    # header on every webhook push. HMAC-SHA256(secret, raw_body) as
    # hex. The same secret works for the CSV upload (passed as a form
    # field, also HMAC-signed).
    HMIS_SHARED_SECRET: str = ""
    # Directory for SFTP-style file drops (future). The poller will
    # scan this directory for new files and feed them through the
    # same DischargeEvent pipeline.
    HMIS_INBOX_DIR: str = ""

    # ── ABDM (Ayushman Bharat Digital Mission) integration ──
    # Mode switch. "real" hits dev.abdm.gov.in (sandbox, free) or
    # abdm.gov.in (production, MoHFW-approved). "mock" returns the
    # same JSON shape from an in-process dict. Default mock so a
    # fresh deploy is safe.
    ABDM_MODE: str = "mock"
    ABDM_BASE_URL: str = "https://dev.abdm.gov.in"
    ABDM_CLIENT_ID: str = ""
    ABDM_CLIENT_SECRET: str = ""

    @property
    def effective_secret_key(self) -> str:
        """Return configured SECRET_KEY or a random dev key."""
        return self.SECRET_KEY or secrets.token_hex(32)

    @property
    def call_allowlist_set(self) -> set[str]:
        return {x.strip() for x in self.CALL_ALLOWLIST.split(",") if x.strip()}

    def validate_production(self) -> list[str]:
        """Validate config for production deployment. Returns list of errors (empty = ok)."""
        errors = []
        if IS_PROD:
            if not self.SECRET_KEY or self.SECRET_KEY in (
                "", "dev-secret-key-change-in-production", "changeme",
            ):
                errors.append(
                    "SECRET_KEY must be set to a strong random value in production. "
                    "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not self.ADMIN_PASSWORD or len(self.ADMIN_PASSWORD) < 8:
                errors.append(
                    "ADMIN_PASSWORD must be at least 8 characters in production."
                )
            if not self.PUBLIC_BASE_URL:
                errors.append("PUBLIC_BASE_URL is required for Twilio webhooks in production.")
            # Weak-password blocklist (defense in depth — the length check above
            # already rejects passwords < 8 chars, but the literal "admin123" /
            # "changeme" etc. slip through length checks).
            weak = {self.ADMIN_PASSWORD.lower()}
            if any(s in self.ADMIN_PASSWORD.lower() for s in
                   ("admin123", "changeme", "password", "letmein", "test1234")):
                errors.append(
                    "ADMIN_PASSWORD looks like a demo default. Set a real password in production."
                )
            if self.SUPERADMIN_PASSWORD and len(self.SUPERADMIN_PASSWORD) < 12:
                errors.append(
                    "SUPERADMIN_PASSWORD must be at least 12 characters in production "
                    "(cross-hospital access warrants a longer secret)."
                )
            # Twilio: if any cred is set, require signature validation to be on
            # (otherwise /webhooks/twilio/* is open — anyone can drive the IVR
            # engine and trigger escalations).
            twilio_partial = bool(
                self.TWILIO_ACCOUNT_SID or self.TWILIO_AUTH_TOKEN or self.TWILIO_FROM_NUMBER
            )
            if twilio_partial and not self.TWILIO_VALIDATE_SIGNATURE:
                errors.append(
                    "TWILIO_VALIDATE_SIGNATURE must be true in production when any Twilio "
                    "credential is set; otherwise the webhook is open. "
                    "Set TWILIO_VALIDATE_SIGNATURE=1 in the environment."
                )
        return errors


settings = Settings()
