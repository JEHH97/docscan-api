"""Storage adapter contract."""

from typing import Protocol


class ArtifactStorage(Protocol):
    """Small interface used by the API and worker for scan artifacts."""

    def save(self, key: str, data: bytes, content_type: str) -> None: ...

    def load(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...
