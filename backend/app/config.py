import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./data/app.db"
    SECRET_KEY: str = ""  # REQUIRED in production; auto-generated for dev
    HOSPITAL_CODE: str = "KA-DIST-01"
    HOSPITAL_NAME: str = "District Hospital Demo"
    ADMIN_PASSWORD: str = ""  # REQUIRED; seed fails if empty

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_VALIDATE_SIGNATURE: bool = True
    PUBLIC_BASE_URL: str = ""

    BHASHINI_API_KEY: str = ""
    BHASHINI_USER_ID: str = ""

    GROQ_API_KEY: str = ""
    LLM_MODEL: str = "llama-3.3-70b-versatile"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_STAFF_CODE: str = ""

    CALL_ALLOWLIST: str = ""

    # Google Fit OAuth (optional — unset = health device features disabled)
    GOOGLE_FIT_CLIENT_ID: str = ""
    GOOGLE_FIT_CLIENT_SECRET: str = ""
    HEALTH_ENCRYPT_KEY: str = ""  # Fernet key; auto-generated at startup if empty

    @property
    def effective_secret_key(self) -> str:
        """Return configured SECRET_KEY or a random dev key."""
        return self.SECRET_KEY or secrets.token_hex(32)

    @property
    def call_allowlist_set(self) -> set[str]:
        return {x.strip() for x in self.CALL_ALLOWLIST.split(",") if x.strip()}


settings = Settings()
