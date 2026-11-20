"""Redis Queue adapter for asynchronous processing."""

from redis import Redis
from rq import Queue

from app.config import Settings
from app.queue.tasks import process_scan_task


def enqueue_scan(
    redis_client: Redis, settings: Settings, scan_id: str, image_key: str, callback_url: str | None
) -> None:
    """Push a scan job with a bounded lifetime to the worker queue."""
    queue = Queue("scans", connection=redis_client, default_timeout=600)
    queue.enqueue(
        process_scan_task,
        scan_id,
        image_key,
        callback_url,
        settings.model_dump(),
        job_timeout=600,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.result_ttl_seconds,
    )
