"""Domain, repository, storage, and callback safety tests."""

import asyncio
import ipaddress
import os
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import fakeredis
import httpcore
import httpx
import pytest
from app.config import Settings
from app.domain import JobState, Point, ScanResult, WordBox
from app.services import webhooks
from app.services.jobs import JobRepository
from app.services.webhooks import send_webhook, validate_public_webhook
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
    assert settings.webhooks_enabled is True
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
    with pytest.raises(ValueError, match="port is invalid"):
        asyncio.run(validate_public_webhook("https://callback.invalid:0/hook"))


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


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.0.0.8",
        "127.0.0.1",
        ipaddress.IPv4Address(0xA9FEA9FE).compressed,
        "172.16.0.8",
        "192.0.2.8",
        "224.0.0.1",
        "100.64.0.1",
        "::",
        "::1",
        "::ffff:8.8.8.8",
        "2001:db8::8",
        "fc00::8",
        "fe80::8",
        "ff02::1",
    ],
)
def test_webhook_rejects_non_global_dns_addresses(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    """All non-public IPv4/IPv6 ranges, including mapped IPv4, are rejected."""

    async def resolve(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        family = (
            socket.AF_INET6
            if ipaddress.ip_address(address.split("%")[0]).version == 6
            else socket.AF_INET
        )
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="globally routable"):
        asyncio.run(validate_public_webhook("https://callback.invalid/hook"))


def test_webhook_checks_every_dns_answer_before_accepting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public DNS answer does not mask a private answer in the same resolution."""

    async def mixed_answers(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", mixed_answers)
    with pytest.raises(ValueError, match="globally routable"):
        asyncio.run(validate_public_webhook("https://callback.invalid/hook"))


def test_pinned_network_backend_connects_to_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP network backend dials the pinned IP, never the hostname."""
    calls: list[dict[str, object]] = []

    class FakeBackend:
        async def connect_tcp(self, **kwargs: object) -> httpcore.AsyncNetworkStream:
            calls.append(kwargs)
            raise httpcore.ConnectError("expected test stop")

        async def sleep(self, seconds: float) -> None:
            return None

    monkeypatch.setattr(webhooks, "AutoBackend", FakeBackend)
    backend = webhooks._PinnedAddressBackend("callback.invalid", ipaddress.ip_address("8.8.8.8"))
    with pytest.raises(httpcore.ConnectError, match="expected test stop"):
        asyncio.run(backend.connect_tcp("callback.invalid", 443))
    assert calls[0]["host"] == "8.8.8.8"
    assert calls[0]["port"] == 443


def test_webhook_rebinding_uses_only_the_first_validated_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A simulated later private DNS answer is never queried or used for the connection."""
    resolutions = 0
    dialed: list[str] = []
    requests: list[httpx.Request] = []

    async def rebinding_resolution(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        nonlocal resolutions
        resolutions += 1
        answer = "8.8.8.8" if resolutions == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, 443))]

    async def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield b"ok"

            async def aclose(self) -> None:
                return None

        return httpx.Response(200, stream=Body(), request=request)

    def mock_pinned_transport(hostname: str, address: object) -> httpx.AsyncBaseTransport:
        dialed.append(str(address))
        assert hostname == "callback.invalid"
        return httpx.MockTransport(response)

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", rebinding_resolution)
    monkeypatch.setattr(webhooks, "_pinned_transport", mock_pinned_transport)
    asyncio.run(send_webhook("https://callback.invalid/hook", {"id": "synthetic"}))
    assert resolutions == 1
    assert dialed == ["8.8.8.8"]
    assert len(requests) == 1
    assert requests[0].url.host == "callback.invalid"
    assert requests[0].headers["host"] == "callback.invalid"


def test_webhook_redirect_to_private_ip_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect responses are rejected without issuing a second request to an internal URL."""
    requests: list[httpx.Request] = []

    async def public_resolution(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    async def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/admin"},
            request=request,
        )

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", public_resolution)
    monkeypatch.setattr(
        webhooks,
        "_pinned_transport",
        lambda hostname, address: httpx.MockTransport(redirect),
    )
    with pytest.raises(httpx.HTTPStatusError, match="redirects are not followed"):
        asyncio.run(send_webhook("https://callback.invalid/hook", {"id": "synthetic"}))
    assert len(requests) == 1
    assert requests[0].url.host == "callback.invalid"


def test_webhook_response_size_is_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Webhook success responses are streamed with a strict 64 KiB cap."""

    async def public_resolution(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    async def large_response(request: httpx.Request) -> httpx.Response:
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield b"x" * (webhooks.MAX_WEBHOOK_RESPONSE_BYTES + 1)

            async def aclose(self) -> None:
                return None

        return httpx.Response(200, stream=Body(), request=request)

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", public_resolution)
    monkeypatch.setattr(
        webhooks,
        "_pinned_transport",
        lambda hostname, address: httpx.MockTransport(large_response),
    )
    with pytest.raises(ValueError, match="64 KiB limit"):
        asyncio.run(send_webhook("https://callback.invalid/hook", {"id": "synthetic"}))
