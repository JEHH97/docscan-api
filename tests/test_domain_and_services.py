"""Domain, repository, storage, and callback safety tests."""

import asyncio
import os
import socket
import time
from pathlib import Path
from typing import cast

import fakeredis
import pytest
from app.config import Settings
from app.domain import JobState, Point, ScanResult, WordBox
from app.services.jobs import JobRepository
from app.services.webhooks import validate_public_webhook
from app.storage.local import LocalStorage


def sample_result() -> ScanResult:
    """Return a valid synthetic OCR result."""
    return ScanResult(
        text="HELLO WORLD",
        mean_confidence=92.0,
        word_boxes=[WordBox(text="HELLO", confidence=92, x=1, y=2, width=30, height=10)],
        page_corners=[Point(x=0, y=0), Point(x=99, y=0), Point(x=99, y=199), Point(x=0, y=199)],
        timings_ms={"ocr": 12.5},
    )


def test_domain_validates_box_confidence_and_state() -> None:
    """Public model constraints and enum values are explicit."""
    with pytest.raises(ValueError):
        WordBox(text="X", confidence=101, x=0, y=0, width=1, height=1)
    assert JobState.QUEUED.value == "queued"
    assert len(sample_result().page_corners) == 4


def test_demo_settings_use_small_uploads_and_short_retention() -> None:
    """The public demo defaults keep requests and retained data bounded."""
    settings = Settings()
    assert settings.api_key.get_secret_value() == ""
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.rate_limit_per_minute == 10
    assert settings.result_ttl_seconds == 60 * 60


def test_job_repository_lifecycle_and_expiry() -> None:
    """Redis state round-trips result JSON and refreshes the configured TTL."""
    client = fakeredis.FakeRedis(decode_responses=True)
    repository = JobRepository(client, 600)
    repository.create("sample-id", "sample/source.png", "")
    initial = repository.get("sample-id")
    assert initial is not None and initial["status"] == "queued"
    repository.update_status("sample-id", JobState.PROCESSING)
    repository.store_result("sample-id", sample_result())
    complete = repository.get("sample-id")
    assert complete is not None and complete["status"] == "done"
    assert repository.result(complete["result"]).text == "HELLO WORLD"
    assert cast("int", client.ttl("scan:sample-id")) > 0
    repository.update_status("sample-id", JobState.FAILED, error="safe failure")
    assert repository.get("sample-id")["error"] == "safe failure"  # type: ignore[index]
    assert repository.get("missing") is None


def test_local_storage_roundtrip_and_path_guard(tmp_path: Path) -> None:
    """Local artifact storage round-trips bytes and blocks traversal."""
    storage = LocalStorage(tmp_path / "artifacts")
    storage.save("scan/source.png", b"png", "image/png")
    assert storage.load("scan/source.png") == b"png"
    storage.delete("scan/source.png")
    with pytest.raises(FileNotFoundError):
        storage.load("scan/source.png")
    with pytest.raises(ValueError, match="escapes"):
        storage.save("../outside.txt", b"x", "text/plain")


def test_local_storage_prunes_expired_artifacts(tmp_path: Path) -> None:
    """The periodic cleanup removes expired files but preserves recent outputs."""
    storage = LocalStorage(tmp_path / "artifacts")
    storage.save("old/source.png", b"old", "image/png")
    storage.save("new/corrected.png", b"new", "image/png")
    old_file = tmp_path / "artifacts" / "old" / "source.png"
    old_time = time.time() - 3_600
    os.utime(old_file, (old_time, old_time))

    assert storage.prune_expired(60) == 1
    assert not old_file.exists()
    assert storage.load("new/corrected.png") == b"new"


def test_private_webhook_address_is_rejected() -> None:
    """Literal loopback callback targets are blocked before any network call."""
    with pytest.raises(ValueError, match="globally routable"):
        asyncio.run(validate_public_webhook("http://127.0.0.1/hook"))
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        asyncio.run(validate_public_webhook("file:///tmp/x"))


def test_webhook_dns_private_resolution_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hostnames resolving to private addresses cannot be used as callbacks."""

    async def private_addresses(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", private_addresses)
    with pytest.raises(ValueError, match="globally routable"):
        asyncio.run(validate_public_webhook("https://callback.invalid/hook"))
