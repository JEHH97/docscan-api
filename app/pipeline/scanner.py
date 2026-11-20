"""OpenCV document cleanup and OCR pipeline."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from app.domain import Point, WordBox


def _float_env(name: str, default: float) -> float:
    """Read a bounded detection threshold from the environment."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if 0 < value < 1 else default


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer pipeline setting without making startup depend on valid input."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


MIN_DOCUMENT_AREA = _float_env("DOCSCAN_MIN_DOCUMENT_AREA_RATIO", 0.10)
MAX_DOCUMENT_AREA = _float_env("DOCSCAN_MAX_DOCUMENT_AREA_RATIO", 0.95)
if MAX_DOCUMENT_AREA <= MIN_DOCUMENT_AREA:
    MAX_DOCUMENT_AREA = 0.95
OCR_MIN_HEIGHT = _int_env("DOCSCAN_OCR_MIN_HEIGHT", 1200, 600, 4000)
OCR_PSM = str(_int_env("DOCSCAN_OCR_PSM", 6, 3, 13))
SCAN_MIN_HEIGHT = _int_env("DOCSCAN_SCAN_MIN_HEIGHT", 1600, 800, 2400)
SCAN_MAX_HEIGHT = 2400
OUTPUT_MODE = os.getenv("OUTPUT_MODE", "scan").strip().casefold()
if OUTPUT_MODE not in {"scan", "photo"}:
    OUTPUT_MODE = "scan"
SCAN_TRIM_RATIO = min(_float_env("DOCSCAN_SCAN_TRIM_RATIO", 0.015), 0.08)


@dataclass(frozen=True)
class PipelineOutput:
    """Immutable output from a single document-processing pass."""

    text: str
    mean_confidence: float
    word_boxes: list[WordBox]
    page_corners: list[Point]
    corrected_png: bytes
    timings_ms: dict[str, float]


def order_points(points: np.ndarray) -> np.ndarray:
    """Order four points as top-left, top-right, bottom-right, bottom-left."""
    points = np.asarray(points, dtype=np.float32)
    center = points.mean(axis=0)
    around = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    top_left = int(np.argmin(around.sum(axis=1)))
    ordered = np.roll(around, -top_left, axis=0)
    return ordered.astype(np.float32)


def _contours(mask: np.ndarray) -> list[np.ndarray]:
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return sorted(found, key=cv2.contourArea, reverse=True)


def _quad_candidates(contour: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Fit stable quadrilaterals, including a rectangle fallback for ragged edges."""
    area = float(cv2.contourArea(contour))
    if area <= 0:
        return []
    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    if hull_area <= 0:
        return []
    solidity = min(area / hull_area, 1.0)
    candidates: list[tuple[np.ndarray, float]] = []
    perimeter = cv2.arcLength(hull, True)
    for epsilon in (0.012, 0.018, 0.025, 0.035, 0.05):
        polygon = cv2.approxPolyDP(hull, epsilon * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            quad = order_points(polygon.reshape(4, 2))
            candidates.append((quad, solidity))
    rectangle = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
    candidates.append((order_points(rectangle), solidity * 0.82))
    return candidates


def _candidate_score(corners: np.ndarray, area_ratio: float, solidity: float) -> float:
    """Rank plausible page candidates by size and shape without favoring the frame."""
    if not MIN_DOCUMENT_AREA <= area_ratio <= MAX_DOCUMENT_AREA:
        return -1.0
    ordered = order_points(corners)
    contour_area = abs(float(cv2.contourArea(ordered)))
    if contour_area <= 0:
        return -1.0
    edges = np.roll(ordered, -1, axis=0) - ordered
    lengths = np.linalg.norm(edges, axis=1)
    if np.min(lengths) < 8:
        return -1.0
    # Penalize extreme slivers and quads whose area is mostly empty space.
    aspect = max(lengths[0], lengths[1]) / max(min(lengths[0], lengths[1]), 1)
    aspect_penalty = 0.85 if aspect > 8 else 1.0
    frame_penalty = 0.92 if area_ratio > 0.88 else 1.0
    return solidity * aspect_penalty * frame_penalty * (0.75 + 0.25 * area_ratio)


def find_document_corners(image: np.ndarray) -> np.ndarray:
    """Detect the best page outline from both paper-color and edge evidence."""
    height, width = image.shape[:2]
    ratio = min(1.0, 1200.0 / max(height, width))
    resized = cv2.resize(image, None, fx=ratio, fy=ratio) if ratio < 1.0 else image
    height_small, width_small = resized.shape[:2]
    frame_area = float(height_small * width_small)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    lab_l = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)[:, :, 0]
    masks = [
        cv2.inRange(hsv, np.array([0, 0, 120]), np.array([180, 125, 255])),
        cv2.threshold(
            cv2.GaussianBlur(lab_l, (9, 9), 0),
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
    ]
    # Morphological closing joins creases and torn/ragged page edges before contour fitting.
    kernel_size = max(7, int(min(height_small, width_small) * 0.025) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    candidates: list[tuple[float, np.ndarray]] = []
    for mask in masks:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        for contour in _contours(mask)[:8]:
            area_ratio = cv2.contourArea(contour) / frame_area
            for quad, solidity in _quad_candidates(contour):
                score = _candidate_score(quad, area_ratio, solidity)
                if score > 0:
                    candidates.append((score, quad))

    # Auto-Canny adds evidence for pages whose color is close to the background.
    median = float(np.median(blurred))
    lower = int(max(0, 0.66 * median))
    upper = int(min(255, max(lower + 1, 1.33 * median)))
    edges = cv2.Canny(blurred, lower, upper)
    edges = cv2.dilate(edges, np.ones((5, 5), dtype=np.uint8), iterations=1)
    for contour in _contours(edges)[:12]:
        area_ratio = cv2.contourArea(contour) / frame_area
        for quad, solidity in _quad_candidates(contour):
            score = _candidate_score(quad, area_ratio, solidity * 0.9)
            if score > 0:
                candidates.append((score, quad))

    corners = (
        max(candidates, key=lambda candidate: candidate[0])[1]
        if candidates
        else np.array(
            [
                [0, 0],
                [width_small - 1, 0],
                [width_small - 1, height_small - 1],
                [0, height_small - 1],
            ],
            dtype=np.float32,
        )
    )
    if ratio < 1.0:
        corners /= ratio
    return order_points(corners)


def four_point_transform(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Perspective-correct the image using the supplied ordered corners."""
    tl, tr, br, bl = corners.astype(np.float32)
    width = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)), 1)
    height = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)), 1)
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    transform = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype=np.float32), target)
    return cv2.warpPerspective(image, transform, (width, height))


def _scan_enhance(
    image: np.ndarray, word_boxes: list[WordBox], ocr_shape: tuple[int, int]
) -> np.ndarray:
    """Upscale, flatten paper illumination, and retain anti-aliased scan-like grayscale."""
    height, width = image.shape[:2]
    margin_x = min(max(1, round(width * SCAN_TRIM_RATIO)), max(1, (width - 8) // 2))
    margin_y = min(max(1, round(height * SCAN_TRIM_RATIO)), max(1, (height - 8) // 2))
    cropped = image[margin_y : height - margin_y, margin_x : width - margin_x]
    cropped_height, cropped_width = cropped.shape[:2]
    target_height = min(max(cropped.shape[0], SCAN_MIN_HEIGHT), SCAN_MAX_HEIGHT)
    scale = target_height / cropped.shape[0]
    if scale != 1:
        cropped = cv2.resize(
            cropped,
            (round(cropped.shape[1] * scale), target_height),
            interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)

    # A broad Gaussian estimates local paper tone; a restrained blend levels illumination without
    # changing low-contrast contours in small thermal text and table lines.
    sigma = max(10.0, min(gray.shape[:2]) * 0.08)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    leveled = cv2.divide(gray, np.maximum(background, 1), scale=255)
    mixed = cv2.addWeighted(gray, 0.9, leveled, 0.1, 0)
    # Use the paper-dominant image percentile as a soft white point; the gentle gamma lifts paper
    # while maintaining continuous antialiased edges instead of hard clipping thin strokes.
    white_point = float(np.clip(np.percentile(mixed, 70), 180, 240))
    normalized = np.clip(mixed.astype(np.float32) * (255.0 / white_point), 0, 255) / 255.0
    scan: np.ndarray = np.asarray(
        np.clip(np.power(normalized, 0.85) * 255.0, 0, 255), dtype=np.uint8
    )
    soft_scan = scan.copy()

    # Protect Tesseract word regions when cleaning their surrounding paper to flat white.
    text_mask = np.zeros(scan.shape, dtype=np.uint8)
    ocr_scale_x = ocr_shape[1] / width
    ocr_scale_y = ocr_shape[0] / height
    scan_scale_x = scan.shape[1] / cropped_width
    scan_scale_y = scan.shape[0] / cropped_height
    padding = max(3, round(scan.shape[0] * 0.002))
    for word in word_boxes:
        left = round((word.x / ocr_scale_x - margin_x) * scan_scale_x) - padding
        top = round((word.y / ocr_scale_y - margin_y) * scan_scale_y) - padding
        right = round(((word.x + word.width) / ocr_scale_x - margin_x) * scan_scale_x) + padding
        bottom = round(((word.y + word.height) / ocr_scale_y - margin_y) * scan_scale_y) + padding
        cv2.rectangle(
            text_mask,
            (max(0, left), max(0, top)),
            (min(scan.shape[1] - 1, right), min(scan.shape[0] - 1, bottom)),
            255,
            thickness=cv2.FILLED,
        )
    text_mask = np.asarray(
        cv2.morphologyEx(
            text_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)),
        ),
        dtype=np.uint8,
    )
    adaptive_ink = cv2.adaptiveThreshold(
        soft_scan,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        51,
        9,
    )
    adaptive_ink = np.asarray(
        cv2.dilate(adaptive_ink, np.ones((5, 5), dtype=np.uint8)), dtype=np.uint8
    )
    text_mask = np.asarray(cv2.bitwise_and(text_mask, adaptive_ink), dtype=np.uint8)

    # Preserve only long, nearly straight table rules; short/textured creases are rejected.
    lines = np.zeros_like(scan)
    detected_lines = cv2.HoughLinesP(
        cv2.Canny(mixed, 20, 60),
        1,
        np.pi / 180,
        threshold=max(40, scan.shape[1] // 16),
        minLineLength=max(50, scan.shape[1] // 5),
        maxLineGap=max(10, scan.shape[1] // 40),
    )
    if detected_lines is not None:
        for line in detected_lines[:, 0]:
            x1, y1, x2, y2 = (int(value) for value in line)
            delta_x, delta_y = abs(x2 - x1), abs(y2 - y1)
            long_horizontal = delta_x >= scan.shape[1] * 0.50 and delta_y / delta_x <= 0.05
            long_vertical = delta_y >= scan.shape[0] * 0.12 and delta_x / delta_y <= 0.04
            if long_horizontal or long_vertical:
                cv2.line(lines, (x1, y1), (x2, y2), 255, thickness=3)
    lines = cv2.dilate(lines, np.ones((3, 3), dtype=np.uint8))
    text_mask = np.asarray(cv2.bitwise_or(text_mask, lines), dtype=np.uint8)

    # Keep antialiased ink and rule pixels while snapping paper inside the padded boxes to white.
    scan = np.where((text_mask > 0) & (soft_scan < 230), soft_scan, 255).astype(np.uint8)

    # Remove only tiny isolated dark components that sit outside detected text-line bands.
    foreground = (scan < 120).astype(np.uint8)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    row_density = np.count_nonzero(foreground, axis=1)
    active_rows = (row_density >= max(6, int(scan.shape[1] * 0.003))).astype(np.uint8)
    text_rows = cv2.dilate(active_rows.reshape(-1, 1), np.ones((41, 1), dtype=np.uint8)).ravel()
    tiny_labels = np.flatnonzero(stats[1:component_count, cv2.CC_STAT_AREA] <= 24) + 1
    for label in tiny_labels:
        x, y, component_width, component_height, _ = stats[label]
        if not np.any(text_rows[y : y + component_height]):
            region = scan[y : y + component_height, x : x + component_width]
            region[labels[y : y + component_height, x : x + component_width] == label] = 255

    # Warp interpolation and irregular paper edges can leave a narrow dark rim.
    border = max(4, round(min(scan.shape[:2]) * 0.015))
    scan[:border, :] = 255
    scan[-border:, :] = 255
    scan[:, :border] = 255
    scan[:, -border:] = 255
    return scan


def _deskew(image: np.ndarray) -> np.ndarray:
    """Correct small residual text-line rotations after perspective correction."""
    foreground = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coordinates = cv2.findNonZero(foreground)
    if coordinates is None or len(coordinates) < 20:
        return image
    angle = cv2.minAreaRect(coordinates)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.2 or abs(angle) > 15:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _ocr(image: np.ndarray, languages: str) -> tuple[str, float, list[WordBox]]:
    """Run Tesseract and return text, mean confidence, and word-level boxes."""
    config = f"--oem 1 --psm {OCR_PSM}"
    data: dict[str, list[Any]] = pytesseract.image_to_data(
        image, lang=languages, config=config, output_type=Output.DICT
    )
    words: list[WordBox] = []
    lines: dict[tuple[str, str, str, str], list[str]] = {}
    confidences: list[float] = []
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError, IndexError):
            continue
        if not text or confidence < 0:
            continue
        words.append(
            WordBox(
                text=text,
                confidence=min(confidence, 100.0),
                x=max(0, int(data["left"][index])),
                y=max(0, int(data["top"][index])),
                width=max(0, int(data["width"][index])),
                height=max(0, int(data["height"][index])),
            )
        )
        confidences.append(confidence)
        key = (
            str(data["block_num"][index]),
            str(data["par_num"][index]),
            str(data["line_num"][index]),
            str(data["page_num"][index]),
        )
        lines.setdefault(key, []).append(text)
    text = "\n".join(" ".join(parts) for parts in lines.values())
    mean_confidence = float(np.mean(confidences)) if confidences else 0.0
    return text, mean_confidence, words


def process_image(image_bytes: bytes, languages: str = "spa+eng") -> PipelineOutput:
    """Run validation, page detection, perspective correction, cleanup, and OCR."""
    started = time.perf_counter()
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Uploaded data is not a decodable image")
    decode_ms = (time.perf_counter() - started) * 1000

    detect_started = time.perf_counter()
    corners = find_document_corners(image)
    corrected = four_point_transform(image, corners)
    detect_ms = (time.perf_counter() - detect_started) * 1000

    cleanup_started = time.perf_counter()
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)
    # Keep a grayscale OCR view: preserving subtle thermal print outperforms hard thresholding.
    # Perspective rectification already removes the large rotation; deskew remains available for
    # genuinely small residual angles but is deliberately not applied to paper texture/creases.
    normalized = gray
    if normalized.shape[0] < OCR_MIN_HEIGHT:
        scale = OCR_MIN_HEIGHT / normalized.shape[0]
        normalized = cv2.resize(normalized, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    thresholded = cv2.adaptiveThreshold(
        normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 13
    )
    contrast_normalized = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(normalized)
    cleaned = normalized
    cleanup_ms = (time.perf_counter() - cleanup_started) * 1000

    ocr_started = time.perf_counter()
    variants = [
        _ocr(cleaned, languages),
        _ocr(contrast_normalized, languages),
        _ocr(thresholded, languages),
    ]
    text, mean_confidence, words = max(variants, key=lambda result: result[1])
    ocr_ms = (time.perf_counter() - ocr_started) * 1000
    output_image = corrected
    if OUTPUT_MODE == "scan":
        scan_started = time.perf_counter()
        output_image = _scan_enhance(corrected, words, normalized.shape[:2])
        cleanup_ms += (time.perf_counter() - scan_started) * 1000
    ok, encoded = cv2.imencode(".png", output_image)
    if not ok:
        raise RuntimeError("Could not encode corrected image")
    return PipelineOutput(
        text=text,
        mean_confidence=mean_confidence,
        word_boxes=words,
        page_corners=[Point(x=float(point[0]), y=float(point[1])) for point in corners],
        corrected_png=encoded.tobytes(),
        timings_ms={
            "decode": decode_ms,
            "detect_transform": detect_ms,
            "cleanup": cleanup_ms,
            "ocr": ocr_ms,
        },
    )
