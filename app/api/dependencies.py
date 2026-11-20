"""Shared HTTP dependencies."""

from collections.abc import Generator
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request
from redis import Redis

from app.config import Settings, get_settings
from app.storage.base import ArtifactStorage
from app.storage.factory import build_storage


def get_redis(request: Request) -> Redis:
    """Return the Redis client stored on application state."""
    return cast("Redis", request.app.state.redis)


def get_storage() -> ArtifactStorage:
    """Construct the configured artifact storage adapter."""
    return build_storage(get_settings())


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Enforce the optional API key if one is configured."""
    expected = settings.api_key.get_secret_value()
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def redis_lifespan_resource(request: Request) -> Generator[Redis, None, None]:
    """Yield the application Redis client for dependency overrides if needed."""
    yield request.app.state.redis
