"""Redis-backed scan state repository."""

import json
from datetime import UTC, datetime
from typing import Any, cast

from redis import Redis

from app.domain import JobState, ScanResult


class JobRepository:
    """Persist job state and result as expiring Redis hashes."""

    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(scan_id: str) -> str:
        return f"scan:{scan_id}"

    def create(self, scan_id: str, image_key: str, callback_url: str | None = None) -> None:
        values = {
            "id": scan_id,
            "status": JobState.QUEUED.value,
            "created_at": datetime.now(UTC).isoformat(),
            "image_key": image_key,
            "callback_url": callback_url or "",
        }
        self.client.hset(self.key(scan_id), mapping=values)
        self.client.expire(self.key(scan_id), self.ttl_seconds)

    def update_status(self, scan_id: str, status: JobState, **values: str) -> None:
        payload = {"status": status.value, **values}
        self.client.hset(self.key(scan_id), mapping=payload)
        self.client.expire(self.key(scan_id), self.ttl_seconds)

    def get(self, scan_id: str) -> dict[str, str] | None:
        values = cast("dict[str, str]", self.client.hgetall(self.key(scan_id)))
        return values or None

    def store_result(self, scan_id: str, result: ScanResult) -> None:
        self.update_status(scan_id, JobState.DONE, result=result.model_dump_json(), error="")

    def result(self, value: str) -> ScanResult:
        parsed: Any = json.loads(value)
        return ScanResult.model_validate(parsed)
