import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import LoggingMiddleware, RateLimitMiddleware, global_exception_handler

# Configure logging first
configure_logging(debug=settings.debug)
logger = structlog.get_logger()

# Initialize Sentry (stub — only activates if DSN is set)
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
    )

app = FastAPI(
    title="CredFluence API",
    description="Trust and review platform for creators, agencies, and brands",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Middleware (order matters: outermost first)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)

# Global exception handler
app.add_exception_handler(Exception, global_exception_handler)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from app.routers import admin, admin_auth, auth, disputes, notifications, oauth, onboarding, profiles, reviews

app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(onboarding.router)
app.include_router(profiles.router)
app.include_router(reviews.router)
app.include_router(disputes.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
app.include_router(notifications.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health_check() -> dict:
    from app.core.database import engine
    from app.core.redis import get_redis

    checks: dict[str, str] = {}

    # Database
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # AWS S3
    try:
        import boto3
        from botocore.config import Config

        client_kwargs: dict = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
            "config": Config(connect_timeout=2, read_timeout=2),
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url

        s3 = boto3.client("s3", **client_kwargs)
        s3.head_bucket(Bucket=settings.s3_bucket_name.strip())
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {
        "success": all_ok,
        "message": "healthy" if all_ok else "degraded",
        "data": checks,
    }


@app.on_event("startup")
async def startup() -> None:
    logger.info("credfluence_api_starting", env=settings.app_env)


@app.on_event("shutdown")
async def shutdown() -> None:
    from app.core.http_client import close_http_client
    from app.core.redis import close_redis
    await close_http_client()
    await close_redis()
    logger.info("credfluence_api_stopped")
