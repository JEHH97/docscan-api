"""Worker entry point executed by RQ."""

import asyncio
import logging
from typing import Any

from redis import Redis

from app.config import Settings
from app.domain import JobState, ScanResult
from app.pipeline.scanner import process_image
from app.services.jobs import JobRepository
from app.services.webhooks import send_webhook
from app.storage.factory import build_storage

logger = logging.getLogger(__name__)


def process_scan_task(
    scan_id: str, image_key: str, callback_url: str | None, raw_settings: dict[str, Any]
) -> None:
    """Load source, process OCR, and persist the result and corrected image."""
    settings = Settings.model_validate(raw_settings)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    repository = JobRepository(redis_client, settings.result_ttl_seconds)
    storage = build_storage(settings)
    repository.update_status(scan_id, JobState.PROCESSING)
    logger.info("scan_started", extra={"scan_id": scan_id})
    try:
        output = process_image(storage.load(image_key), settings.ocr_languages)
        result = ScanResult(
            text=output.text,
            mean_confidence=output.mean_confidence,
            word_boxes=output.word_boxes,
            page_corners=output.page_corners,
            timings_ms=output.timings_ms,
        )
        storage.save(f"{scan_id}/corrected.png", output.corrected_png, "image/png")
        repository.store_result(scan_id, result)
        logger.info(
            "scan_completed", extra={"scan_id": scan_id, "word_count": len(output.word_boxes)}
        )
        if callback_url:
            try:
                asyncio.run(
                    send_webhook(callback_url, {"id": scan_id, "status": JobState.DONE.value})
                )
            except Exception:
                logger.exception("scan_webhook_failed", extra={"scan_id": scan_id})
    except Exception as exc:
        repository.update_status(scan_id, JobState.FAILED, error="Document processing failed")
        logger.exception(
            "scan_failed", extra={"scan_id": scan_id, "exception_type": type(exc).__name__}
        )
        raise
