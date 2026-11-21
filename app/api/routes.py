"""Versioned scan and health endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from redis import Redis  # noqa: TC002

from app.api.dependencies import get_redis, get_storage, require_api_key
from app.config import get_settings
from app.domain import JobState, ScanResponse
from app.queue.rq_queue import enqueue_scan
from app.services.jobs import JobRepository
from app.services.webhooks import validate_public_webhook
from app.storage.base import ArtifactStorage  # noqa: TC001

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _image_type(data: bytes) -> str | None:
    """Match supported formats by their file signatures."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.post(
    "/v1/scans",
    response_model=ScanResponse,
    status_code=202,
    tags=["Scans"],
    summary="Submit an image for asynchronous document scanning",
    responses={
        202: {
            "description": "Scan queued",
            "content": {
                "application/json": {
                    "example": {
                        "id": "2f0e2e22-9b80-4db4-b997-f4b78d13c271",
                        "status": "queued",
                        "status_url": "/v1/scans/2f0e2e22-9b80-4db4-b997-f4b78d13c271",
                        "image_url": "/v1/scans/2f0e2e22-9b80-4db4-b997-f4b78d13c271/image",
                    }
                }
            },
        },
        400: {"description": "Callback URLs are disabled by WEBHOOKS_ENABLED"},
        413: {"description": "Upload exceeds configured size limit"},
        415: {"description": "Unsupported or invalid image"},
    },
    dependencies=[Depends(require_api_key)],
)
async def submit_scan(
    file: Annotated[UploadFile, File(description="JPEG, PNG, or WebP document image")],
    redis_client: Annotated[Redis, Depends(get_redis)],
    storage: Annotated[ArtifactStorage, Depends(get_storage)],
    callback_url: Annotated[
        str | None,
        Form(
            description=(
                "Optional public HTTP(S) callback. Callbacks are rejected with HTTP 400 when "
                "WEBHOOKS_ENABLED is false (the public demo default)."
            )
        ),
    ] = None,
) -> ScanResponse:
    """Validate the upload, persist it, and enqueue background work."""
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds maximum allowed size")
    detected_type = _image_type(data)
    if detected_type not in ALLOWED_TYPES or (
        file.content_type and file.content_type != detected_type
    ):
        raise HTTPException(
            status_code=415, detail="Only valid JPEG, PNG, and WebP images are accepted"
        )
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise HTTPException(status_code=415, detail="Image data could not be decoded")
    if callback_url:
        if not settings.webhooks_enabled:
            raise HTTPException(
                status_code=400,
                detail="Callbacks are disabled on this service (WEBHOOKS_ENABLED=false)",
            )
        try:
            await validate_public_webhook(callback_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    scan_id = str(uuid.uuid4())
    image_key = f"{scan_id}/source{ALLOWED_TYPES[detected_type]}"
    repository = JobRepository(redis_client, settings.result_ttl_seconds)
    try:
        storage.save(image_key, data, detected_type)
        repository.create(scan_id, image_key, callback_url)
        enqueue_scan(redis_client, settings, scan_id, image_key, callback_url)
    except Exception as exc:
        storage.delete(image_key)
        redis_client.delete(repository.key(scan_id))
        logger.exception("scan_enqueue_failed", extra={"scan_id": scan_id})
        raise HTTPException(status_code=503, detail="Scan could not be queued") from exc
    logger.info("scan_queued", extra={"scan_id": scan_id, "content_type": detected_type})
    return ScanResponse(
        id=scan_id,
        status=JobState.QUEUED,
        created_at=datetime.now(UTC).isoformat(),
        status_url=f"/v1/scans/{scan_id}",
        image_url=f"/v1/scans/{scan_id}/image",
    )


@router.get(
    "/v1/scans/{scan_id}",
    response_model=ScanResponse,
    tags=["Scans"],
    summary="Read scan state and result",
    dependencies=[Depends(require_api_key)],
)
def get_scan(scan_id: str, redis_client: Annotated[Redis, Depends(get_redis)]) -> ScanResponse:
    """Return current state, OCR output, and timings for a scan."""
    try:
        uuid.UUID(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Scan not found") from exc
    value = JobRepository(redis_client, settings.result_ttl_seconds).get(scan_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    result = (
        JobRepository(redis_client, settings.result_ttl_seconds).result(value["result"])
        if value.get("result")
        else None
    )
    return ScanResponse(
        id=scan_id,
        status=JobState(value["status"]),
        result=result,
        error=value.get("error") or None,
        created_at=value["created_at"],
        status_url=f"/v1/scans/{scan_id}",
        image_url=f"/v1/scans/{scan_id}/image" if value["status"] == JobState.DONE.value else None,
    )


@router.get(
    "/v1/scans/{scan_id}/image",
    tags=["Scans"],
    summary="Download the corrected PNG",
    responses={
        200: {"content": {"image/png": {}}},
        404: {"description": "Scan or image not found"},
    },
    dependencies=[Depends(require_api_key)],
)
def get_scan_image(
    scan_id: str,
    redis_client: Annotated[Redis, Depends(get_redis)],
    storage: Annotated[ArtifactStorage, Depends(get_storage)],
) -> Response:
    """Return the corrected page image after successful processing."""
    value = JobRepository(redis_client, settings.result_ttl_seconds).get(scan_id)
    if value is None or value.get("status") != JobState.DONE.value:
        raise HTTPException(status_code=404, detail="Corrected image not found")
    try:
        image = storage.load(f"{scan_id}/corrected.png")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Corrected image not found") from exc
    return Response(
        content=image, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/healthz", tags=["Health"], summary="Check API and Redis health")
def healthz(request: Request) -> dict[str, str]:
    """Return readiness status, including a Redis ping."""
    try:
        request.app.state.redis.ping()
    except Exception as exc:
        logger.warning("health_check_failed", extra={"exception_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    return {"status": "ok"}
