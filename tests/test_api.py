"""FastAPI integration checks with in-memory Redis and synchronous job completion."""

from pathlib import Path

import fakeredis
import pytest
from app.api.dependencies import get_storage
from app.config import Settings
from app.domain import JobState, Point, ScanResult, WordBox
from app.main import create_app
from app.services.jobs import JobRepository
from app.storage.local import LocalStorage
from fastapi.testclient import TestClient
from redis import Redis

from tests.helpers import image_bytes, synthetic_page


def test_upload_poll_and_corrected_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upload returns 202 and its result/image become available after work completes."""
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    storage = LocalStorage(tmp_path / "artifacts")

    def enqueue_synchronously(
        client: Redis,
        settings: Settings,
        scan_id: str,
        image_key: str,
        callback_url: str | None,
    ) -> None:
        assert storage.load(image_key).startswith(b"\x89PNG")
        repository = JobRepository(client, settings.result_ttl_seconds)
        repository.update_status(scan_id, JobState.PROCESSING)
        storage.save(f"{scan_id}/corrected.png", b"corrected-png", "image/png")
        repository.store_result(
            scan_id,
            ScanResult(
                text="HELLO WORLD",
                mean_confidence=91,
                word_boxes=[WordBox(text="HELLO", confidence=91, x=10, y=12, width=40, height=14)],
                page_corners=[
                    Point(x=1, y=1),
                    Point(x=99, y=1),
                    Point(x=99, y=199),
                    Point(x=1, y=199),
                ],
                timings_ms={"ocr": 10.1},
            ),
        )

    monkeypatch.setattr("app.api.routes.enqueue_scan", enqueue_synchronously)
    application = create_app(redis_client)
    application.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(application)
    upload = image_bytes(synthetic_page())
    response = client.post("/v1/scans", files={"file": ("sample.png", upload, "image/png")})
    assert response.status_code == 202
    submitted = response.json()
    assert submitted["status"] == "queued"
    scan_id = submitted["id"]
    assert submitted["status_url"] == f"/v1/scans/{scan_id}"

    status = client.get(f"/v1/scans/{scan_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "done"
    assert status.json()["result"]["text"] == "HELLO WORLD"
    assert status.json()["result"]["mean_confidence"] == 91
    assert len(status.json()["result"]["word_boxes"]) == 1
    assert len(status.json()["result"]["page_corners"]) == 4
    assert status.json()["result"]["timings_ms"]["ocr"] == 10.1

    image_response = client.get(f"/v1/scans/{scan_id}/image")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == b"corrected-png"
    assert client.get("/healthz").json() == {"status": "ok"}


def test_upload_rejects_bad_type_and_unknown_scan() -> None:
    """Invalid file signatures and absent jobs return stable client errors."""
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    client = TestClient(create_app(redis_client))
    bad = client.post("/v1/scans", files={"file": ("bad.png", b"not png", "image/png")})
    assert bad.status_code == 415
    missing = client.get("/v1/scans/00000000-0000-0000-0000-000000000001")
    assert missing.status_code == 404
