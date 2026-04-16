import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.redis import get_redis

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=elapsed_ms,
            ip=request.client.host if request.client else "unknown",
        )

        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Rate limiting middleware (Redis sliding window)
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        redis = await get_redis()

        key = f"rate_limit:{client_ip}:{int(time.time() // 60)}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 60)

            from app.core.config import settings
            if count > settings.rate_limit_requests_per_minute:
                logger.warning("rate_limit_exceeded", ip=client_ip, count=count)
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "success": False,
                        "message": "Too many requests. Please slow down.",
                        "data": None,
                    },
                )
        except Exception:
            # If Redis is down, don't block requests
            pass

        return await call_next(request)


# ---------------------------------------------------------------------------
# Error handler (consistent 4xx/5xx shape)
# ---------------------------------------------------------------------------

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", error=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "An unexpected error occurred. Our team has been notified.",
            "data": None,
        },
    )
