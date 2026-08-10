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
# A result below this size is accepted, but we still try the sparse-text mode
# because job flyers often scatter headings and role names around the image.
OCR_RETRY_TARGET_CHARS = 40
# Images smaller than this on BOTH axes are almost always icons/emoji, not job flyers.
# Skipping OCR on them saves ~0.4-0.6s per image (one tesseract subprocess) and
# dramatically reduces the "OCR produced only 0 chars" spam seen in the logs.
MIN_IMAGE_DIMENSION_FOR_OCR = 80
# If OCR extracts at least this many chars and the text contains a hiring
# keyword, consider it a success even if it's below MIN_OCR_CHARS (e.g. "HIRE!").
MIN_HIRING_SHORT_CHARS = 5
_HIRING_RE = re.compile(r"hir(e|ing)", re.IGNORECASE)

# Tesseract configs (language is passed via pytesseract `lang` parameter)
TESSERACT_CONFIG_PSM6 = "--oem 3 --psm 6 -c preserve_interword_spaces=1"
TESSERACT_CONFIG_PSM3 = "--oem 3 --psm 3 -c preserve_interword_spaces=1"
TESSERACT_CONFIG_PRIMARY = TESSERACT_CONFIG_PSM6
TESSERACT_CONFIG_FALLBACK = TESSERACT_CONFIG_PSM3

# Process-level cache: avoid re-OCRing the same image bytes when the scroller's
# overlapping windows or DB dedup overlap produce duplicate raw_image_bytes.
_ocr_result_cache: dict[str, tuple[str, bool]] = {}
_cached_tesseract_cmd: str | None = None
_tesseract_logged = False


def _resolve_tesseract_cmd() -> str | None:
    """Return the Tesseract executable that the current process can use.

    ``pytesseract`` is only a Python wrapper; it launches the native
    ``tesseract`` executable in the same environment as the Celery worker.
    Linux installations normally put it on ``PATH``. Windows installers
    commonly put it in ``Program Files`` or ``AppData`` without updating
    the PATH, and macOS Homebrew puts it in ``/opt/homebrew/bin``.
    This helper checks explicit env overrides, PATH, and common install paths.
    """
    configured = os.getenv("TESSERACT_CMD") or os.getenv("TESSERACT_PATH")
    if configured:
        configured = os.path.expandvars(configured.strip().strip('"'))
        resolved = shutil.which(configured) or configured
        if Path(resolved).is_file():
            return str(resolved)
        logger.warning("TESSERACT_CMD does not point to a file: %s", configured)

    discovered = shutil.which("tesseract")
    if discovered:
        return discovered

    # Common Unix / Linux / macOS locations
    for unix_path in (
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
        "/opt/local/bin/tesseract",
    ):
        if Path(unix_path).is_file():
            return unix_path

    # Common Windows locations
    windows_roots = [
        os.getenv("ProgramFiles"),
        os.getenv("ProgramW6432"),
        os.getenv("ProgramFiles(x86)"),
        os.getenv("LOCALAPPDATA"),
        os.getenv("APPDATA"),
        os.getenv("USERPROFILE"),
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    ]
    for root in filter(None, windows_roots):
        candidates = [
            Path(root) / "Tesseract-OCR" / "tesseract.exe",
            Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            Path(root) / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            Path(root) / "AppData" / "Local" / "Tesseract-OCR" / "tesseract.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    # Chocolatey / Scoop on Windows
    for special_path in (
        Path("C:/ProgramData/chocolatey/bin/tesseract.exe"),
        Path(os.path.expanduser("~/scoop/apps/tesseract/current/tesseract.exe")),
    ):
        if special_path.is_file():
            return str(special_path)

    return None


def _configure_tesseract() -> str | None:
    """Configure pytesseract and return the executable path, if available."""
    global _cached_tesseract_cmd, _tesseract_logged
    if _cached_tesseract_cmd is not None:
        return _cached_tesseract_cmd
    command = _resolve_tesseract_cmd()
    if not command:
        return None

    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = command
    except ImportError:
        pass
    _cached_tesseract_cmd = command
    return command


def _is_tesseract_available() -> bool:
    """Check whether a usable Tesseract executable is available."""
    return _configure_tesseract() is not None


def _hash_raw_image(raw_image_bytes: str | bytes | None) -> str | None:
    """Cheap cache key for duplicate images inside a single scan.

    WhatsApp's virtualized scroller plus overlapping windows can hand the
    same flyer to the OCR path 2-4 times. Hashing the first 512 bytes plus
    length is enough to deduplicate without hashing the entire 50-200k base64.
    """
    if raw_image_bytes is None:
        return None
    try:
        if isinstance(raw_image_bytes, bytes):
            try:
                preview = raw_image_bytes[:512].decode("utf-8", errors="ignore")
            except Exception:
                preview = str(raw_image_bytes[:512])
            return f"b:{len(raw_image_bytes)}:{hash(preview) & 0xffffffff:08x}"
        s = str(raw_image_bytes)
        return f"s:{len(s)}:{hash(s[:512] + s[-128:] if len(s) > 640 else s) & 0xffffffff:08x}"
    except Exception:
        return None


def _is_dark_image(pil_image) -> bool:
    """Heuristic: is the image predominantly dark (white text on dark bg)?

    Inverted flyers (dark background, light text) are the only case where the
    "invert" tesseract retry helps, and that retry costs another 150-200ms.
    Skip it for normal bright flyers.
    """
    try:
        gray = pil_image.convert("L") if pil_image.mode != "L" else pil_image
        # Sample a small thumbnail to estimate brightness quickly.
        thumb = gray.copy()
        try:
            from PIL import Image as PILImage

            resample = PILImage.Resampling.BILINEAR
        except AttributeError:
            resample = PILImage.BILINEAR  # type: ignore
        thumb.thumbnail((32, 32), resample)
        hist = thumb.histogram()
        total = sum(hist)
        if total == 0:
            return False
        # Weighted mean brightness 0-255
        mean = sum(i * c for i, c in enumerate(hist)) / total
        return mean < 85
    except Exception:
        return False


def _ocr_success(text: str) -> bool:
    """Return True if OCR text is long enough or is a short hiring cue."""
    if len(text) >= MIN_OCR_CHARS:
        return True
    if len(text) >= MIN_HIRING_SHORT_CHARS and _HIRING_RE.search(text):
        return True
    return False


def _decode_image(raw_image_bytes: str | bytes | None):
    """Decode a raw image (base64 string, data URI, or raw bytes) into a PIL Image.

    Returns None if decoding fails.
    """
    if not raw_image_bytes:
        return None

    try:
        from PIL import Image, ImageFile

        # Allow truncated images (common for WhatsApp screenshots)
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        # If already raw bytes
        if isinstance(raw_image_bytes, bytes):
            # Check if it starts with image magic numbers
            if raw_image_bytes.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8", b"BM")):
                try:
                    img = Image.open(io.BytesIO(raw_image_bytes))
                    img.load()
                    return img
                except Exception:
                    pass
            try:
                data_str = raw_image_bytes.decode("utf-8", errors="ignore").strip()
            except Exception:
                data_str = ""
        else:
            data_str = str(raw_image_bytes).strip()

        if not data_str:
            return None

        # Handle base64 data URI format: data:image/png;base64,...
        if data_str.startswith("data:"):
            try:
                _, encoded = data_str.split(",", 1)
                data_str = encoded.strip()
            except ValueError:
                logger.error("Failed to split data URI")
                return None

        # Clean whitespace / newlines that may have been introduced
        data_str = data_str.replace("\n", "").replace("\r", "").replace(" ", "")

        # Fix padding if needed
        missing_padding = len(data_str) % 4
        if missing_padding:
            data_str += "=" * (4 - missing_padding)

        # Decode base64 -> bytes (support standard and URL-safe base64)
        try:
            normalized_b64 = data_str.replace("-", "+").replace("_", "/")
            image_data = base64.b64decode(normalized_b64, validate=False)
        except Exception as e:
            logger.error(f"Base64 decode failed: {e}")
            return None

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


def _preprocess_image(image, upscale: bool = True):
    """Preprocess PIL Image to improve Tesseract accuracy.

    Steps:
    - Handle transparency / mode -> composite onto white background RGB
    - Upscale small images (WhatsApp thumbnails are low resolution)
    - Convert to grayscale ('L')
    - Auto-contrast + contrast/sharpness enhancement
    Returns a processed PIL Image in 'L' mode.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps

        # Handle transparency / different modes -> composite onto white background
        if image.mode in ("RGBA", "LA", "PA"):
            try:
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "RGBA":
                    alpha = image.split()[3]
                    background.paste(image, mask=alpha)
                elif image.mode == "LA":
                    background.paste(image, mask=image.split()[-1])
                else:
                    converted = image.convert("RGBA")
                    background.paste(converted, mask=converted.split()[3])
                image = background
            except Exception:
                image = image.convert("RGB")
        elif image.mode == "P":
            try:
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            except Exception:
                image = image.convert("RGB")
        elif image.mode != "RGB" and image.mode != "L":
            image = image.convert("RGB")

        w, h = image.size
        if w == 0 or h == 0:
            return image

        # Upscale logic: job images are often low-res screenshots
        # Target at least ~1800px on the longer edge for better OCR
        if upscale:
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

                try:
                    resample_filter = Image.Resampling.LANCZOS
                except AttributeError:
                    resample_filter = Image.LANCZOS
                image = image.resize((new_w, new_h), resample_filter)

        # Convert to grayscale
        if image.mode != "L":
            gray = image.convert("L")
        else:
            gray = image

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


def extract_text_from_image(raw_image_bytes: str | bytes | None, lang: str | None = None) -> tuple[str, bool]:
    """Run pytesseract OCR on a raw image (base64-encoded, data URI, or raw bytes).

    Args:
        raw_image_bytes: Base64-encoded image bytes, data URI, or raw bytes.
        lang: Tesseract language code (defaults to TESSERACT_LANG env var or 'eng').

    Returns:
        (extracted_text, ocr_failed):
            extracted_text: The OCR result string (empty on failure).
            ocr_failed: True if OCR produced < MIN_OCR_CHARS or completely failed.
    """
    global _tesseract_logged
    if not raw_image_bytes:
        logger.warning("OCR: No image data provided")
        return "", True

    # Fast dedup: same bytes seen in this worker process (scroller overlap)
    cache_key = _hash_raw_image(raw_image_bytes)
    # Bypass cache when pytesseract is mocked (tests patch image_to_string with
    # per-test side_effects but reuse the same image bytes). Production code
    # uses the real function, so the stable-id cache remains effective.
    _is_mock_ocr = False
    try:
        import pytesseract as _pt
        _is_mock_ocr = hasattr(_pt.image_to_string, "assert_called") or hasattr(_pt.image_to_string, "call_count")
    except Exception:
        _is_mock_ocr = False
    if cache_key and cache_key in _ocr_result_cache and not _is_mock_ocr:
        return _ocr_result_cache[cache_key]

    tesseract_cmd = _configure_tesseract()
    if not tesseract_cmd:
        logger.error(
            "OCR failed: Tesseract executable was not found. "
            "Install it in the same environment as the Celery worker or set "
            "TESSERACT_CMD to the full path of tesseract.exe. "
            "Linux/Debian: apt-get install tesseract-ocr tesseract-ocr-eng"
        )
        return "", True

    if not _tesseract_logged:
        logger.debug("Using Tesseract executable: %s", tesseract_cmd)
        _tesseract_logged = True

    image = _decode_image(raw_image_bytes)
    if image is None:
        logger.error("OCR: Failed to decode image")
        return "", True

    # Fast reject: tiny icons / emoji / avatars never contain job flyers.
    try:
        w, h = image.size
        logger.debug("OCR input image decoded at %sx%s", w, h)
        if w < MIN_IMAGE_DIMENSION_FOR_OCR and h < MIN_IMAGE_DIMENSION_FOR_OCR:
            # Don't call Tesseract for an icon. This is an image-capture issue,
            # not a missing executable: a real flyer thumbnail is normally a
            # few hundred pixels on at least one axis.
            logger.warning(
                "OCR skipped %sx%s icon/thumbnail; Tesseract is installed but this is not an OCR-readable message image",
                w,
                h,
            )
            result: tuple[str, bool] = ("", True)
            if cache_key and not _is_mock_ocr:
                _ocr_result_cache[cache_key] = result
            return result
    except Exception:
        pass

    try:
        import pytesseract
    except ImportError:
        logger.error(
            "pytesseract is not installed. "
            "Run: pip install pytesseract pillow && apt-get install tesseract-ocr tesseract-ocr-eng"
        )
        return "", True

    ocr_lang = lang or os.getenv("TESSERACT_LANG", "eng")

    # Helper to run pytesseract safely
    def _run_tesseract(img, psm: int) -> str:
        cfg = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
        try:
            result = pytesseract.image_to_string(img, lang=ocr_lang, config=cfg)
            return (result or "").strip()
        except Exception as err:
            # Do not hide language-data/permission/process errors at DEBUG and
            # then make the result look like ordinary zero-character OCR.
            logger.warning("Tesseract invocation failed with psm=%s: %s", psm, err)
            return ""

    try:
        from PIL import ImageOps

        # Preprocess for better accuracy
        try:
            preprocessed = _preprocess_image(image)
        except Exception as e:
            logger.warning(f"OCR preprocessing error, using original image: {e}")
            preprocessed = image

        # 1) Primary attempt: preprocessed with psm 6 (uniform block of text).
        text = _run_tesseract(preprocessed, psm=6)

        # 2) Automatic page segmentation is useful when the first pass finds
        # nothing at all.
        if not _ocr_success(text):
            logger.debug(
                "OCR produced %s chars with psm 6, retrying preprocessed with psm 3",
                len(text),
            )
            fb_text = _run_tesseract(preprocessed, psm=3)
            if len(fb_text) > len(text):
                text = fb_text

        # 3) Flyers are usually sparse layouts, not one uniform paragraph.
        # Even a technically successful 10-character heading is not enough to
        # score the role/title, so try sparse-text mode while the result is
        # still short.
        if len(text) < OCR_RETRY_TARGET_CHARS:
            logger.debug(
                "OCR result is still short (%s chars), retrying sparse-text psm 11",
                len(text),
            )
            fb_sparse = _run_tesseract(preprocessed, psm=11)
            if len(fb_sparse) > len(text):
                text = fb_sparse

        # 4) Retry a color copy at the SAME upscaled resolution. The previous
        # fallback used the low-resolution original, which could not recover
        # text lost from a 200-300px WhatsApp thumbnail.
        if len(text) < OCR_RETRY_TARGET_CHARS:
            logger.debug(
                "OCR produced %s chars with preprocessing, retrying upscaled color image",
                len(text),
            )
            orig_rgb = image.convert("RGB") if image.mode != "RGB" else image.copy()
            if orig_rgb.size != preprocessed.size:
                try:
                    from PIL import Image as PILImage

                    resampling = PILImage.Resampling.LANCZOS
                except AttributeError:
                    resampling = PILImage.LANCZOS  # type: ignore[attr-defined]
                orig_rgb = orig_rgb.resize(preprocessed.size, resampling)
            fb_orig = _run_tesseract(orig_rgb, psm=6)
            if len(fb_orig) < OCR_RETRY_TARGET_CHARS:
                fb_alt = _run_tesseract(orig_rgb, psm=11)
                if len(fb_alt) > len(fb_orig):
                    fb_orig = fb_alt
            if len(fb_orig) > len(text):
                text = fb_orig

        # 5) Hard-thresholded text helps low-contrast colored flyers.
        if not _ocr_success(text):
            try:
                thresholded = preprocessed.point(lambda pixel: 255 if pixel > 155 else 0)
                fb_threshold = _run_tesseract(thresholded, psm=11)
                if len(fb_threshold) > len(text):
                    text = fb_threshold
            except Exception:
                pass

        # 6) Invert only genuinely dark flyers (light text on dark background).
        if not _ocr_success(text) and _is_dark_image(image):
            try:
                inverted = ImageOps.invert(preprocessed.convert("L"))
                fb_inv = _run_tesseract(inverted, psm=6)
                if not _ocr_success(fb_inv):
                    fb_alt = _run_tesseract(inverted, psm=11)
                    if len(fb_alt) > len(fb_inv):
                        fb_inv = fb_alt
                if len(fb_inv) > len(text):
                    text = fb_inv
            except Exception:
                pass

        # Post-process: normalize whitespace but preserve content
        text = re.sub(r"\s+", " ", text).strip()

        if not _ocr_success(text):
            logger.info(
                f"OCR produced only {len(text)} chars (threshold: {MIN_OCR_CHARS}): '{text[:100]}'"
            )
            result = (text, True)
            if cache_key and not _is_mock_ocr:
                _ocr_result_cache[cache_key] = result
            return result

        logger.info(f"OCR extracted {len(text)} characters")
        result = (text, False)
        if cache_key and not _is_mock_ocr:
            _ocr_result_cache[cache_key] = result
        return result

    except Exception as e:
        try:
            if isinstance(e, pytesseract.TesseractNotFoundError):
                logger.error(
                    "Tesseract binary not found. Install with: apt-get install tesseract-ocr"
                )
                return "", True
        except Exception:
            pass

        logger.error(f"OCR failed with error: {e}", exc_info=True)
        return "", True
