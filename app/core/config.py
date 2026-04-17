from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    app_secret_key: str = "change-me"
    debug: bool = True

    # Database
    database_url: str = "postgresql+asyncpg://credfluence:credfluence@localhost:5432/credfluence"
    database_url_sync: str = "postgresql://credfluence:credfluence@localhost:5432/credfluence"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    # Local dev: set file paths. Production (Dokploy): set key content directly as env vars.
    jwt_private_key_path: str = "./private.pem"
    jwt_public_key_path: str = "./public.pem"
    jwt_private_key_content: str = ""   # JWT_PRIVATE_KEY_CONTENT in Dokploy env vars
    jwt_public_key_content: str = ""    # JWT_PUBLIC_KEY_CONTENT in Dokploy env vars
    jwt_algorithm: str = "RS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Phone encryption
    phone_encryption_key: str = "00" * 32

    # Storage (S3 / MinIO)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_name: str = "credfluence-proofs"
    s3_region: str = "auto"

    # Email
    sendgrid_api_key: str = "SG.placeholder"
    email_from: str = "noreply@credfluence.com"
    email_from_name: str = "CredFluence"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_tls: bool = False

    # Twilio
    twilio_account_sid: str = "ACplaceholder"
    twilio_auth_token: str = "placeholder"
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # OAuth — Google
    google_client_id: str = "placeholder.apps.googleusercontent.com"
    google_client_secret: str = "placeholder"
    google_redirect_uri: str = "http://localhost:8000/auth/oauth/google/callback"

    # OAuth — LinkedIn
    linkedin_client_id: str = "placeholder"
    linkedin_client_secret: str = "placeholder"
    linkedin_redirect_uri: str = "http://localhost:8000/auth/oauth/linkedin/callback"

    # OAuth — Instagram
    instagram_client_id: str = "placeholder"
    instagram_client_secret: str = "placeholder"
    instagram_redirect_uri: str = "http://localhost:8000/auth/oauth/instagram/callback"

    # AI
    anthropic_api_key: str = "sk-ant-placeholder"
    openai_api_key: str = "sk-placeholder"

    # Sentry
    sentry_dsn: str = ""

    # CORS — comma-separated string in .env: http://localhost:5173,http://localhost:3000
    allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    def get_allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # Rate limiting
    rate_limit_requests_per_minute: int = 60
    rate_limit_reviews_per_week: int = 5

    @property
    def jwt_private_key(self) -> str:
        # Production: content injected directly via Dokploy env var
        if self.jwt_private_key_content:
            return self.jwt_private_key_content.replace("\\n", "\n")
        # Local dev: read from .pem file
        path = Path(self.jwt_private_key_path)
        if path.exists():
            return path.read_text()
        return ""

    @property
    def jwt_public_key(self) -> str:
        # Production: content injected directly via Dokploy env var
        if self.jwt_public_key_content:
            return self.jwt_public_key_content.replace("\\n", "\n")
        # Local dev: read from .pem file
        path = Path(self.jwt_public_key_path)
        if path.exists():
            return path.read_text()
        return ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
