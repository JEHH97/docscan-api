"""Webhook URL validation and delivery helpers."""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import SOCKET_OPTION

MAX_WEBHOOK_RESPONSE_BYTES = 64 * 1024
WEBHOOK_TIMEOUT_SECONDS = 5.0


def _canonical_host(host: str) -> str:
    """Normalize a network-backend hostname for comparison with the validated URL host."""
    try:
        return ipaddress.ip_address(host.split("%", maxsplit=1)[0]).compressed.lower()
    except ValueError:
        return host.encode("idna").decode("ascii").rstrip(".").lower()


def _validated_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an address and require a globally routable, non-mapped IP literal."""
    address = ipaddress.ip_address(value.split("%", maxsplit=1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        raise ValueError("Callback addresses must be globally routable")
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("Callback addresses must be globally routable")
    return address


class _PinnedAddressBackend(httpcore.AsyncNetworkBackend):
    """Route TCP connects to the already validated address without changing HTTP/TLS hostnames."""

    def __init__(self, hostname: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address):
        self._hostname = _canonical_host(hostname)
        self._address = str(address)
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if _canonical_host(host) != self._hostname:
            raise httpcore.ConnectError("Unexpected hostname in pinned webhook transport")
        return await self._backend.connect_tcp(
            host=self._address,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not supported for webhooks")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


def _pinned_transport(
    hostname: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address
) -> httpx.AsyncHTTPTransport:
    """Create a direct HTTP transport whose socket connect uses the validated address."""
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    # httpx currently exposes no network-backend argument; this httpcore pool hook changes only
    # the TCP destination. The request URL remains the original hostname for Host and TLS SNI.
    transport._pool._network_backend = _PinnedAddressBackend(hostname, address)
    return transport


async def _resolve_public_webhook(
    url: str,
) -> tuple[str, int, ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Validate the URL and all DNS answers, returning one address from the validated set."""
    parsed = urlparse(url)
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("Callback port is invalid") from exc
    port = parsed_port if parsed_port is not None else (443 if parsed.scheme == "https" else 80)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Callback must be an HTTP(S) URL without embedded credentials")
    if not 1 <= port <= 65535:
        raise ValueError("Callback port is invalid")
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(
            parsed.hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("Callback hostname could not be resolved") from exc
    if not records:
        raise ValueError("Callback hostname could not be resolved")

    validated: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for family, _, _, _, sockaddr in records:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("Callback addresses must be globally routable")
        validated.append(_validated_address(sockaddr[0]))
    return parsed.hostname, port, validated[0]


async def validate_public_webhook(url: str) -> None:
    """Reject non-HTTP(S) URLs and any hostname resolving to non-global addresses."""
    await _resolve_public_webhook(url)


async def send_webhook(url: str, payload: dict[str, object]) -> None:
    """Resolve once, pin the socket destination, and deliver a bounded completion event."""
    hostname, _, address = await _resolve_public_webhook(url)
    transport = _pinned_transport(hostname, address)
    async with (
        httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client,
        client.stream("POST", url, json=payload) as response,
    ):
        if 300 <= response.status_code < 400:
            raise httpx.HTTPStatusError(
                "Webhook redirects are not followed",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        response_size = 0
        async for chunk in response.aiter_raw():
            response_size += len(chunk)
            if response_size > MAX_WEBHOOK_RESPONSE_BYTES:
                raise ValueError("Callback response exceeds the 64 KiB limit")
