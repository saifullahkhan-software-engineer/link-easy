"""
WhatsApp Job Scanner — OCR logic using pytesseract.
FILE: services/whatsapp_ocr.py

Extracts text from images using Tesseract OCR (free, no API key needed).

Requirements:
    pip install pytesseract pillow
    apt-get install tesseract-ocr
"""
import base64
import io
import re

from core.logging_config import get_logger

logger = get_logger(__name__)

# Minimum character count for OCR to be considered successful.
MIN_OCR_CHARS = 10


def _decode_image(raw_image_bytes: str | None):
    """Decode a raw image (base64 string) into a PIL Image.

    Returns None if decoding fails.
    """
    if not raw_image_bytes:
        return None

    try:
        from PIL import Image

        # Handle base64 data URI format: data:image/png;base64,...
        if isinstance(raw_image_bytes, str) and raw_image_bytes.startswith("data:"):
            # Strip the data URI prefix
            header, encoded = raw_image_bytes.split(",", 1)
            raw_image_bytes = encoded

        # Decode base64
        image_data = base64.b64decode(raw_image_bytes)
        return Image.open(io.BytesIO(image_data))

    except Exception as e:
        logger.error(f"Failed to decode image: {e}")
        return None


def extract_text_from_image(raw_image_bytes: str | None) -> tuple[str, bool]:
    """Run pytesseract OCR on a raw image (base64-encoded).

    Args:
        raw_image_bytes: Base64-encoded image bytes (with or without data URI prefix).

    Returns:
        (extracted_text, ocr_failed):
            extracted_text: The OCR result string (empty on failure).
            ocr_failed: True if OCR produced < MIN_OCR_CHARS or completely failed.
    """
    if not raw_image_bytes:
        return "", True

    image = _decode_image(raw_image_bytes)
    if image is None:
        return "", True

    try:
        import pytesseract

        # Run Tesseract OCR
        text = pytesseract.image_to_string(image)
        text = text.strip()

        # Post-process: remove excessive whitespace
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < MIN_OCR_CHARS:
            logger.info(f"OCR produced only {len(text)} chars (threshold: {MIN_OCR_CHARS})")
            return text, True

        logger.info(f"OCR extracted {len(text)} characters")
        return text, False

    except ImportError:
        logger.error(
            "pytesseract is not installed. "
            "Run: pip install pytesseract pillow && apt-get install tesseract-ocr"
        )
        return "", True
    except Exception as e:
        logger.error(f"OCR failed with error: {e}")
        return "", True
