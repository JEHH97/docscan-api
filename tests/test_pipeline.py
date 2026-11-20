"""Image processing pipeline unit tests using synthetic input."""

import re
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from app.pipeline.scanner import (
    find_document_corners,
    four_point_transform,
    order_points,
    process_image,
)
from pytesseract import Output

from tests.helpers import image_bytes, synthetic_page, textured_synthetic_page


def fake_ocr_data() -> dict[str, list[object]]:
    """Return deterministic word-level OCR fields."""
    return {
        "text": ["", "HELLO", "WORLD"],
        "conf": ["-1", "94.2", "88"],
        "left": [0, 10, 90],
        "top": [0, 12, 12],
        "width": [0, 55, 60],
        "height": [0, 20, 20],
        "block_num": [1, 1, 1],
        "par_num": [1, 1, 1],
        "line_num": [1, 1, 1],
        "page_num": [1, 1, 1],
    }


def test_rotated_page_corners_cover_page() -> None:
    """Canny/contour detection finds the synthetic page rather than the frame."""
    corners = find_document_corners(synthetic_page())
    assert corners.shape == (4, 2)
    assert corners[:, 0].min() > 50
    assert corners[:, 0].max() < 650
    assert corners[:, 1].min() > 20
    assert corners[:, 1].max() < 500


def test_order_points_and_perspective_transform() -> None:
    """Point ordering and four-corner warp produce a page-shaped image."""
    raw = np.array([[90, 415], [580, 92], [135, 52], [535, 458]], dtype=np.float32)
    ordered = order_points(raw)
    assert ordered[0, 0] < ordered[1, 0]
    corrected = four_point_transform(synthetic_page(), ordered)
    assert corrected.shape[0] > 300
    assert corrected.shape[1] > 400


def test_pipeline_runs_and_returns_ocr_boxes_and_timing() -> None:
    """The pipeline emits a corrected PNG and structured mocked OCR data."""
    with patch(
        "app.pipeline.scanner.pytesseract.image_to_data", return_value=fake_ocr_data()
    ) as ocr:
        output = process_image(image_bytes(synthetic_page()), "eng")
    assert ocr.call_count == 3
    for call in ocr.call_args_list:
        assert call.kwargs == {
            "lang": "eng",
            "config": "--oem 1 --psm 6",
            "output_type": Output.DICT,
        }
    assert output.text == "HELLO WORLD"
    assert output.mean_confidence == pytest.approx((94.2 + 88) / 2)
    assert [box.text for box in output.word_boxes] == ["HELLO", "WORLD"]
    assert len(output.page_corners) == 4
    assert output.corrected_png.startswith(b"\x89PNG")
    assert set(output.timings_ms) == {"decode", "detect_transform", "cleanup", "ocr"}
    assert all(value >= 0 for value in output.timings_ms.values())


def test_invalid_image_is_rejected() -> None:
    """Invalid encoded data fails before processing."""
    with pytest.raises(ValueError, match="decodable"):
        process_image(b"not an image")


def test_full_frame_is_used_when_no_page_contour_exists() -> None:
    """Uniform images use their complete frame as a safe fallback."""
    image = np.zeros((100, 160, 3), dtype=np.uint8)
    corners = find_document_corners(image)
    assert corners[:, 0].min() == 0
    assert corners[:, 1].min() == 0
    assert corners[:, 0].max() == 159
    assert corners[:, 1].max() == 99


def test_ocr_empty_result_has_zero_confidence() -> None:
    """OCR output with no usable words remains a valid empty result."""
    data = fake_ocr_data()
    data["text"] = [" ", ""]
    data["conf"] = ["-1", "bad"]
    with patch("app.pipeline.scanner.pytesseract.image_to_data", return_value=data):
        output = process_image(image_bytes(synthetic_page()), "eng")
    assert output.text == ""
    assert output.mean_confidence == 0
    assert output.word_boxes == []


def test_synthetic_page_ocr_contains_expected_words() -> None:
    """Tesseract recognizes the printed words on the rotated synthetic page."""
    output = process_image(image_bytes(synthetic_page()), "spa+eng")
    recognized = output.text.upper()
    assert "HELLO" in recognized
    assert "WORLD" in recognized


def test_textured_phone_photo_detection_and_ocr() -> None:
    """Color/shape detection and OCR tolerate phone-like texture and paper creases."""
    image, truth = textured_synthetic_page()
    corners = find_document_corners(image)
    ordered_truth = order_points(truth)
    assert np.max(np.linalg.norm(corners - ordered_truth, axis=1)) < 35
    output = process_image(image_bytes(image), "eng")
    recognized = output.text.upper()
    assert "REALISTIC" in recognized
    assert "TICKET" in recognized
    assert "455" in recognized
    assert "APPROVED" in recognized


def test_scan_output_has_white_paper_and_dark_text() -> None:
    """The scanner-style artifact removes shadows while retaining dark print strokes."""
    image, _ = textured_synthetic_page()
    output = process_image(image_bytes(image), "eng")
    scan = cv2.imdecode(np.frombuffer(output.corrected_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert scan is not None
    border = np.concatenate(
        (scan[:12].ravel(), scan[-12:].ravel(), scan[:, :12].ravel(), scan[:, -12:].ravel())
    )
    assert np.median(border) >= 245
    assert np.median(scan) >= 245
    assert np.count_nonzero(scan < 200) > 100
    assert 1600 <= scan.shape[0] <= 2400


def test_photo_output_mode_keeps_the_warped_color_image() -> None:
    """The configurable photo mode still returns the perspective-corrected color page."""
    with (
        patch("app.pipeline.scanner.OUTPUT_MODE", "photo"),
        patch("app.pipeline.scanner.pytesseract.image_to_data", return_value=fake_ocr_data()),
    ):
        output = process_image(image_bytes(synthetic_page()), "eng")
    photo = cv2.imdecode(np.frombuffer(output.corrected_png, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert photo is not None
    assert photo.ndim == 3


def test_redacted_real_receipt_detection_and_ocr() -> None:
    """Regression: detect the receipt inside its frame and read its key lines."""
    fixture = Path(__file__).parent / "fixtures" / "real-receipt-redacted.jpg"
    data = fixture.read_bytes()
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    corners = find_document_corners(image)
    assert corners[:, 0].min() > 65
    assert corners[:, 0].max() < 610
    assert corners[:, 1].min() > 180
    assert corners[:, 1].max() < 940
    assert cv2.contourArea(corners) < image.shape[0] * image.shape[1] * 0.8
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception as exc:
        pytest.skip(f"Tesseract is not installed: {exc}")
    output = process_image(data, "spa+eng")
    recognized = output.text.casefold()
    for word in ("ticket", "vendedor", "total", "455", "aprobado"):
        assert word in recognized
    scan = cv2.imdecode(np.frombuffer(output.corrected_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert scan is not None
    assert 1600 <= scan.shape[0] <= 2400
    paper_regions = (
        scan[
            round(scan.shape[0] * 0.19) : round(scan.shape[0] * 0.22),
            round(scan.shape[1] * 0.08) : round(scan.shape[1] * 0.55),
        ],
        scan[
            round(scan.shape[0] * 0.56) : round(scan.shape[0] * 0.75),
            round(scan.shape[1] * 0.10) : round(scan.shape[1] * 0.90),
        ],
    )
    for paper in paper_regions:
        assert float(np.mean(paper >= 250)) >= 0.97
        assert np.median(paper) == 255
    assert np.median(scan) == 255
    band = max(10, round(min(scan.shape) * 0.02))
    border = np.concatenate(
        (scan[:band].ravel(), scan[-band:].ravel(), scan[:, :band].ravel(), scan[:, -band:].ravel())
    )
    assert float(border.mean()) >= 230

    scan_text = pytesseract.image_to_string(
        scan, lang="spa+eng", config="--oem 1 --psm 6"
    ).casefold()
    footer_region = scan[
        round(scan.shape[0] * 0.81) : round(scan.shape[0] * 0.875),
        round(scan.shape[1] * 0.13) : round(scan.shape[1] * 0.82),
    ]
    footer_text = pytesseract.image_to_string(
        footer_region, lang="spa+eng", config="--oem 1 --psm 6"
    ).casefold()
    assert "autorizado" in footer_text
    assert "electrónica" in footer_text or "electronica" in footer_text
    date_region = scan[
        round(scan.shape[0] * 0.1375) : round(scan.shape[0] * 0.195),
        round(scan.shape[1] * 0.60) : round(scan.shape[1] * 0.85),
    ]
    date_text = pytesseract.image_to_string(date_region, lang="eng", config="--oem 1 --psm 7")
    assert re.search(r"20\s*/\s*10\s*/\s*26", date_text)
    forbidden = (
        "551" + "758",
        "87" + "25",
        "185208" + "152568",
        "A000000003" + "1010",
        "Galama",
        "240" + "10",
        "SMARTPOS",
        "149396" + "8729",
        "mercado",
        "pago",
    )
    assert not [term for term in forbidden if term.casefold() in scan_text]

    with patch("app.pipeline.scanner.OUTPUT_MODE", "photo"):
        photo_output = process_image(data, "spa+eng")
    assert output.mean_confidence >= photo_output.mean_confidence
