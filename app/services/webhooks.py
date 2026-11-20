"""Webhook URL validation and delivery helpers."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx


async def validate_public_webhook(url: str) -> None:
    """Reject non-HTTP(S) URLs and any hostname resolving to non-global addresses."""
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Callback must be an HTTP(S) URL without embedded credentials")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Callback port is invalid")
    loop = asyncio.get_running_loop()
    try:
        addresses = await loop.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror as exc:
        raise ValueError("Callback hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Callback addresses must be globally routable")


async def send_webhook(url: str, payload: dict[str, object]) -> None:
    """Revalidate and deliver a short-timeout completion event."""
    await validate_public_webhook(url)
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
