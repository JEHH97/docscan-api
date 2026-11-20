"""Synthetic image helpers shared by tests."""

import cv2
import numpy as np


def synthetic_page() -> np.ndarray:
    """Draw a rotated white page with fictional printed text on a dark background."""
    image = np.full((520, 720, 3), 48, dtype=np.uint8)
    corners = np.array([[135, 52], [580, 92], [535, 458], [92, 415]], dtype=np.int32)
    cv2.fillConvexPoly(image, corners, (250, 250, 250))
    cv2.putText(image, "SAMPLE PAGE", (180, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
    cv2.putText(image, "HELLO WORLD", (175, 270), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
    cv2.putText(image, "OCR DEMO", (180, 345), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
    return image


def textured_synthetic_page() -> tuple[np.ndarray, np.ndarray]:
    """Create a phone-like page with texture, uneven light, tilt, and shallow creases."""
    rng = np.random.default_rng(42)
    height, width = 800, 900
    noise = rng.normal(0, 11, (height, width, 1))
    fabric = np.clip(34 + noise + 8 * np.sin(np.arange(width)[None, :, None] / 19), 0, 85)
    image = np.repeat(fabric.astype(np.uint8), 3, axis=2)
    corners = np.array([[190, 60], [740, 110], [680, 740], [125, 690]], dtype=np.int32)
    page_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(page_mask, corners, 255)
    light = np.tile(np.linspace(245, 218, width, dtype=np.float32), (height, 1))
    light -= np.tile(np.linspace(0, 13, height, dtype=np.float32)[:, None], (1, width))
    paper_noise = rng.normal(0, 2.5, (height, width))
    paper = np.clip(light + paper_noise, 0, 255).astype(np.uint8)
    image[page_mask > 0] = cv2.cvtColor(paper, cv2.COLOR_GRAY2BGR)[page_mask > 0]
    cv2.line(image, (250, 165), (625, 207), (205, 205, 205), 3)
    cv2.line(image, (180, 530), (650, 575), (210, 210, 210), 4)
    cv2.putText(image, "REALISTIC PAGE", (240, 290), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (25, 25, 25), 3)
    cv2.putText(
        image, "TICKET TOTAL 455", (220, 390), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (25, 25, 25), 3
    )
    cv2.putText(image, "APPROVED", (255, 490), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (25, 25, 25), 3)
    return image, corners.astype(np.float32)


def image_bytes(image: np.ndarray, extension: str = ".png") -> bytes:
    """Encode a synthetic OpenCV image."""
    encoded_ok, encoded = cv2.imencode(extension, image)
    assert encoded_ok
    return encoded.tobytes()
