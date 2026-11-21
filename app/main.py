"""FastAPI application factory and cross-cutting middleware."""

import asyncio
import contextlib
import ipaddress
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api.routes import router
from app.config import Settings, get_settings
from app.storage.factory import build_storage


class JsonFormatter(logging.Formatter):
    """Emit one structured JSON record per log event."""

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("scan_id", "word_count", "content_type", "exception_type"):
            if hasattr(record, field):
                event[field] = getattr(record, field)
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a Redis-backed per-client request budget."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if settings.rate_limit_per_minute <= 0 or request.url.path == "/healthz":
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        try:
            address = str(ipaddress.ip_address(client_ip))
        except ValueError:
            address = "unknown"
        minute = int(time.time() // 60)
        key = f"rate:{address}:{minute}"
        count = request.app.state.redis.incr(key)
        if count == 1:
            request.app.state.redis.expire(key, 65)
        if count > settings.rate_limit_per_minute:
            return JSONResponse(status_code=429, content={"detail": "Request rate limit exceeded"})
        return await call_next(request)


async def _artifact_cleanup_loop(settings: Settings) -> None:
    """Remove stale source and corrected images without relying on paid storage."""
    while True:
        try:
            storage = build_storage(settings)
            removed = await asyncio.to_thread(storage.prune_expired, settings.result_ttl_seconds)
            if removed:
                logging.getLogger(__name__).info(
                    "expired_artifacts_removed", extra={"artifact_count": removed}
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "artifact_cleanup_failed", extra={"exception_type": type(exc).__name__}
            )
        await asyncio.sleep(min(300, settings.result_ttl_seconds))


def create_app(redis_client: Redis | None = None) -> FastAPI:
    """Create the API and attach its Redis connection and routes."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        cleanup_task = asyncio.create_task(_artifact_cleanup_loop(settings))
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

    app = FastAPI(
        title="DocScan API",
        description="Asynchronous document cleanup and OCR with a stable, versioned contract.",
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Scans", "description": "Submit and retrieve document scans."},
            {"name": "Health", "description": "Service readiness checks."},
        ],
        servers=[{"url": "http://localhost:8000", "description": "Local development"}],
    )
    app.state.redis = redis_client or Redis.from_url(settings.redis_url, decode_responses=True)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    app.include_router(router)
    return app


app = create_app()
