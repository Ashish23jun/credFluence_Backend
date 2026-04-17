import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)

# Global exception handler
app.add_exception_handler(Exception, global_exception_handler)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from app.routers import admin, auth, disputes, notifications, oauth, profiles, reviews

app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(profiles.router)
app.include_router(reviews.router)
app.include_router(disputes.router)
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

    # S3 / MinIO
    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(connect_timeout=2, read_timeout=2),
        )
        s3.list_buckets()
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
    from app.core.redis import close_redis
    await close_redis()
    logger.info("credfluence_api_stopped")
