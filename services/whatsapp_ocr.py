"""
WhatsApp Job Scanner — OCR logic using pytesseract.
FILE: services/whatsapp_ocr.py

Extracts text from images using Tesseract OCR (free, no API key needed).

Requirements:
    pip install pytesseract pillow
    apt-get install tesseract-ocr tesseract-ocr-eng
"""
import base64
import io
import os
import re
import shutil
from pathlib import Path

from core.logging_config import get_logger

logger = get_logger(__name__)

# Minimum character count for OCR to be considered successful.
MIN_OCR_CHARS = 10

# Tesseract configs — psm 6 = uniform block of text (typical for job flyers)
TESSERACT_CONFIG_PRIMARY = "--oem 3 --psm 6 -l eng -c preserve_interword_spaces=1"
TESSERACT_CONFIG_FALLBACK = "--oem 3 --psm 3 -l eng"


def _resolve_tesseract_cmd() -> str | None:
    """Return the Tesseract executable that the current process can use.

    ``pytesseract`` is only a Python wrapper; it launches the native
    ``tesseract`` executable in the same environment as the Celery worker.
    Linux installations normally put it on ``PATH``.  Windows installers
    commonly put it at ``C:\\Program Files\\Tesseract-OCR\\tesseract.exe``
    without updating the PATH, so also support the usual install locations and
    an explicit ``TESSERACT_CMD`` override.
    """
    configured = os.getenv("TESSERACT_CMD") or os.getenv("TESSERACT_PATH")
    if configured:
        configured = os.path.expandvars(configured.strip().strip('"'))
        # Accept either an absolute path or a command name in PATH.
        resolved = shutil.which(configured) or configured
        if Path(resolved).is_file():
            return resolved
        logger.warning("TESSERACT_CMD does not point to a file: %s", configured)

    discovered = shutil.which("tesseract")
    if discovered:
        return discovered

    if os.name == "nt":
        program_files = [
            os.getenv("ProgramFiles"),
            os.getenv("ProgramW6432"),
            os.getenv("ProgramFiles(x86)"),
        ]
        for root in filter(None, program_files):
            candidate = Path(root) / "Tesseract-OCR" / "tesseract.exe"
            if candidate.is_file():
                return str(candidate)

    return None


def _configure_tesseract() -> str | None:
    """Configure pytesseract and return the executable path, if available."""
    command = _resolve_tesseract_cmd()
    if not command:
        return None

    try:
        import pytesseract

        # Setting this is important when Tesseract was installed on Windows
        # but its directory was not added to the worker's PATH.
        pytesseract.pytesseract.tesseract_cmd = command
    except ImportError:
        # The caller will produce the more specific dependency error below.
        pass
    return command


def _is_tesseract_available() -> bool:
    """Check whether a usable Tesseract executable is available."""
    return _configure_tesseract() is not None


def _decode_image(raw_image_bytes: str | None):
    """Decode a raw image (base64 string) into a PIL Image.

    Returns None if decoding fails.
    """
    if not raw_image_bytes:
        return None

    try:
        from PIL import Image, ImageFile

        # Allow truncated images (common for WhatsApp screenshots)
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        data_str = raw_image_bytes

        # Handle base64 data URI format: data:image/png;base64,...
        if isinstance(data_str, str) and data_str.startswith("data:"):
            try:
                _, encoded = data_str.split(",", 1)
                data_str = encoded
            except ValueError:
                logger.error("Failed to split data URI")
                return None

        # Clean whitespace / newlines that may have been introduced
        if isinstance(data_str, str):
            data_str = data_str.strip().replace("\n", "").replace("\r", "").replace(" ", "")

        # Fix padding if needed
        if isinstance(data_str, str):
            missing_padding = len(data_str) % 4
            if missing_padding:
                data_str += "=" * (4 - missing_padding)

        # Decode base64 -> bytes
        if isinstance(data_str, str):
            try:
                image_data = base64.b64decode(data_str, validate=False)
            except Exception as e:
                logger.error(f"Base64 decode failed: {e}")
                return None
        else:
            # If somehow bytes were passed directly
            image_data = data_str

        if len(image_data) < 100:
            logger.error(f"Decoded image too small: {len(image_data)} bytes")
            return None

        image = Image.open(io.BytesIO(image_data))
        # Force load to catch truncated / corrupt files early
        try:
            image.load()
        except Exception as e:
            logger.warning(f"PIL load warning (may still be usable): {e}")

        return image

    except Exception as e:
        logger.error(f"Failed to decode image: {e}", exc_info=True)
        return None


def _preprocess_image(image):
    """Preprocess PIL Image to improve Tesseract accuracy.

    Steps:
    - Handle alpha channel by compositing onto white background
    - Upscale small images (WhatsApp thumbnails are tiny)
    - Convert to grayscale
    - Auto-contrast + contrast/sharpness enhancement
    Returns a processed PIL Image in 'L' mode.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps

        # Handle transparency -> white background
        if image.mode in ("RGBA", "LA", "PA"):
            try:
                # Create white background
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "RGBA":
                    alpha = image.split()[3]
                    background.paste(image, mask=alpha)
                else:
                    # For LA, use alpha channel
                    background.paste(image, mask=image.split()[-1])
                image = background
            except Exception:
                # Fallback: just convert
                image = image.convert("RGB")
        elif image.mode == "P":
            # Palette images (common for screenshots) -> RGB
            image = image.convert("RGB")
        elif image.mode != "RGB":
            image = image.convert("RGB")

        w, h = image.size
        if w == 0 or h == 0:
            return image

        # Upscale logic: job images are often low-res screenshots
        # Target at least ~1800px on the longer edge for better OCR
        min_dimension = min(w, h)
        scale = 1.0
        if min_dimension < 400:
            scale = 3.0
        elif min_dimension < 800:
            scale = 2.5
        elif min_dimension < 1200:
            scale = 2.0

        if scale > 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            # Cap to avoid OOM on huge images
            max_dim = 4000
            if new_w > max_dim or new_h > max_dim:
                ratio = min(max_dim / new_w, max_dim / new_h)
                new_w = int(new_w * ratio)
                new_h = int(new_h * ratio)

            # Pillow >=10 uses Image.Resampling.LANCZOS; older uses Image.LANCZOS
            try:
                resample_filter = Image.Resampling.LANCZOS
            except AttributeError:
                resample_filter = Image.LANCZOS
            image = image.resize((new_w, new_h), resample_filter)

        # Convert to grayscale
        gray = image.convert("L")

        # Auto-contrast to normalize lighting
        try:
            gray = ImageOps.autocontrast(gray, cutoff=1)
        except Exception:
            pass

        # Enhance contrast
        try:
            enhancer = ImageEnhance.Contrast(gray)
            gray = enhancer.enhance(1.8)
        except Exception:
            pass

        # Enhance sharpness (helps with blurry screenshots)
        try:
            enhancer = ImageEnhance.Sharpness(gray)
            gray = enhancer.enhance(2.0)
        except Exception:
            pass

        return gray

    except Exception as e:
        logger.warning(f"Image preprocessing failed, using original: {e}")
        try:
            return image.convert("L")
        except Exception:
            return image


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
        logger.warning("OCR: No image data provided")
        return "", True

    tesseract_cmd = _configure_tesseract()
    if not tesseract_cmd:
        logger.error(
            "OCR failed: Tesseract executable was not found. "
            "Install it in the same environment as the Celery worker or set "
            "TESSERACT_CMD to the full path of tesseract.exe. "
            "Linux/Debian: apt-get install tesseract-ocr tesseract-ocr-eng"
        )
        return "", True

    logger.debug("Using Tesseract executable: %s", tesseract_cmd)

    image = _decode_image(raw_image_bytes)
    if image is None:
        logger.error("OCR: Failed to decode image")
        return "", True

    # Preprocess for better accuracy
    try:
        preprocessed = _preprocess_image(image)
    except Exception as e:
        logger.warning(f"OCR preprocessing error, using original image: {e}")
        preprocessed = image

    try:
        import pytesseract

        # Log available languages for debugging
        try:
            langs = pytesseract.get_languages()
            logger.debug(f"Tesseract languages available: {langs}")
        except Exception:
            pass

        # Primary attempt: psm 6 (uniform text block)
        text = ""
        try:
            text = pytesseract.image_to_string(preprocessed, config=TESSERACT_CONFIG_PRIMARY)
        except Exception as e:
            # Fallback to original image if preprocessed fails
            logger.warning(f"OCR with preprocessed image failed: {e}, retrying original")
            try:
                text = pytesseract.image_to_string(image, config=TESSERACT_CONFIG_PRIMARY)
            except Exception as e2:
                logger.error(f"OCR retry with original image also failed: {e2}")
                raise

        text = text.strip()

        # If result is too short, try fallback PSM 3 (fully automatic)
        if len(text) < MIN_OCR_CHARS:
            logger.info(
                f"OCR produced only {len(text)} chars with psm6 (threshold: {MIN_OCR_CHARS}), retrying psm3"
            )
            try:
                text_fallback = pytesseract.image_to_string(
                    preprocessed, config=TESSERACT_CONFIG_FALLBACK
                )
                text_fallback = text_fallback.strip()
                if len(text_fallback) > len(text):
                    logger.info(f"Fallback psm3 produced {len(text_fallback)} chars, using it")
                    text = text_fallback
            except Exception as e:
                logger.debug(f"Fallback OCR (psm3) failed: {e}")

        # Post-process: normalize whitespace but preserve some structure
        # Original behavior collapsed to single spaces — keep that for scoring
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < MIN_OCR_CHARS:
            logger.info(f"OCR produced only {len(text)} chars (threshold: {MIN_OCR_CHARS}): '{text[:100]}'")
            return text, True

        logger.info(f"OCR extracted {len(text)} characters")
        return text, False

    except ImportError:
        logger.error(
            "pytesseract is not installed. "
            "Run: pip install pytesseract pillow && apt-get install tesseract-ocr tesseract-ocr-eng"
        )
        return "", True
    except Exception as e:
        # Detect TesseractNotFoundError specifically if pytesseract is available
        try:
            import pytesseract

            if isinstance(e, pytesseract.TesseractNotFoundError):
                logger.error(
                    "Tesseract binary not found. Install with: apt-get install tesseract-ocr"
                )
                return "", True
        except Exception:
            pass

        logger.error(f"OCR failed with error: {e}", exc_info=True)
        return "", True
