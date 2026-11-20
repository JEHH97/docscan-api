"""Stable domain models shared by the API, worker, and storage adapters."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobState(StrEnum):
    """Lifecycle values exposed by the API."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Point(BaseModel):
    """Image coordinate in pixels."""

    x: float
    y: float


class WordBox(BaseModel):
    """Recognized word and its rectangular bounds in corrected-image pixels."""

    text: str
    confidence: float = Field(ge=0, le=100)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class ScanResult(BaseModel):
    """OCR result and pipeline metadata."""

    text: str
    mean_confidence: float = Field(ge=0, le=100)
    word_boxes: list[WordBox]
    page_corners: list[Point] = Field(min_length=4, max_length=4)
    timings_ms: dict[str, float]


class ScanResponse(BaseModel):
    """Public scan state. Result is populated after successful completion."""

    model_config = ConfigDict(use_enum_values=True)

    id: str
    status: JobState
    result: ScanResult | None = None
    error: str | None = None
    created_at: str
    status_url: str
    image_url: str | None = None


class ScanSubmission(BaseModel):
    """202 response returned from the upload endpoint."""

    id: str
    status: Literal["queued"]
    status_url: str
    image_url: str
