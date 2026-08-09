"""
Unit tests for WhatsApp OCR module (services/whatsapp_ocr.py) and
image message extraction & scoring pipelines.
"""
import base64
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from services.whatsapp_ocr import (
    MIN_OCR_CHARS,
    _configure_tesseract,
    _decode_image,
    _is_tesseract_available,
    _preprocess_image,
    _resolve_tesseract_cmd,
    extract_text_from_image,
)


def _create_sample_image(text="TEST", size=(200, 100), mode="RGB", bg_color="white") -> bytes:
    """Helper to generate sample PNG bytes."""
    img = Image.new(mode, size, color=bg_color)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), text, fill="black" if bg_color == "white" else "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class WhatsAppOCRDecodeTests(unittest.TestCase):
    def test_decode_none_or_empty(self):
        self.assertIsNone(_decode_image(None))
        self.assertIsNone(_decode_image(""))
        self.assertIsNone(_decode_image("   "))

    def test_decode_raw_png_bytes(self):
        raw_bytes = _create_sample_image("Sample Text")
        img = _decode_image(raw_bytes)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (200, 100))

    def test_decode_standard_base64_string(self):
        raw_bytes = _create_sample_image("Base64 Text")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        img = _decode_image(b64_str)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (200, 100))

    def test_decode_data_uri_with_whitespace_and_newlines(self):
        raw_bytes = _create_sample_image("Data URI")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        data_uri = f"  \n  data:image/png;base64,{b64_str}\n\r  "
        img = _decode_image(data_uri)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (200, 100))

    def test_decode_url_safe_base64(self):
        raw_bytes = _create_sample_image("URL Safe")
        b64_url = base64.urlsafe_b64encode(raw_bytes).decode("ascii")
        img = _decode_image(b64_url)
        self.assertIsNotNone(img)

    def test_decode_missing_padding(self):
        raw_bytes = _create_sample_image("Padding Test")
        b64_str = base64.b64encode(raw_bytes).decode("ascii").rstrip("=")
        img = _decode_image(b64_str)
        self.assertIsNotNone(img)

    def test_decode_invalid_corrupted_data(self):
        self.assertIsNone(_decode_image("not_a_valid_image_base64_string_1234567890"))


class WhatsAppOCRPreprocessTests(unittest.TestCase):
    def test_preprocess_rgb(self):
        raw_bytes = _create_sample_image("RGB Image", mode="RGB")
        img = Image.open(io.BytesIO(raw_bytes))
        preprocessed = _preprocess_image(img)
        self.assertIsNotNone(preprocessed)
        self.assertEqual(preprocessed.mode, "L")

    def test_preprocess_rgba_with_transparency(self):
        raw_bytes = _create_sample_image("RGBA Image", mode="RGBA", bg_color=(255, 255, 255, 128))
        img = Image.open(io.BytesIO(raw_bytes))
        preprocessed = _preprocess_image(img)
        self.assertIsNotNone(preprocessed)
        self.assertEqual(preprocessed.mode, "L")

    def test_preprocess_palette_mode(self):
        raw_bytes = _create_sample_image("Palette Image", mode="P")
        img = Image.open(io.BytesIO(raw_bytes))
        preprocessed = _preprocess_image(img)
        self.assertIsNotNone(preprocessed)
        self.assertEqual(preprocessed.mode, "L")

    def test_preprocess_upscales_small_image(self):
        small_img = Image.new("RGB", (100, 50), color="white")
        preprocessed = _preprocess_image(small_img, upscale=True)
        # Small image (<400px) is scaled by 3.0
        self.assertEqual(preprocessed.size, (300, 150))


class WhatsAppOCRExecutionTests(unittest.TestCase):
    def test_extract_text_empty_input(self):
        text, failed = extract_text_from_image(None)
        self.assertEqual(text, "")
        self.assertTrue(failed)

        text, failed = extract_text_from_image("")
        self.assertEqual(text, "")
        self.assertTrue(failed)

    @patch("services.whatsapp_ocr._configure_tesseract", return_value=None)
    def test_extract_text_missing_tesseract_binary(self, _mock_cfg):
        raw_bytes = _create_sample_image("Sample Flyer")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        text, failed = extract_text_from_image(b64_str)
        self.assertEqual(text, "")
        self.assertTrue(failed)

    @patch("services.whatsapp_ocr._configure_tesseract", return_value="/usr/bin/tesseract")
    @patch("pytesseract.image_to_string")
    def test_extract_text_success_primary(self, mock_img_to_str, _mock_cfg):
        mock_img_to_str.return_value = "Hiring Senior Python Developer at Acme Corp. Apply now!"
        raw_bytes = _create_sample_image("Flyer")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        text, failed = extract_text_from_image(b64_str)
        self.assertFalse(failed)
        self.assertIn("Senior Python Developer", text)

    @patch("services.whatsapp_ocr._configure_tesseract", return_value="/usr/bin/tesseract")
    @patch("pytesseract.image_to_string")
    def test_extract_text_fallback_to_psm3_when_short(self, mock_img_to_str, _mock_cfg):
        # First call (psm 6) returns short, second call (psm 3) returns full text
        mock_img_to_str.side_effect = [
            "Short",
            "Full Flyer: We are looking for a Lead Architect in New York",
        ]
        raw_bytes = _create_sample_image("Flyer")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        text, failed = extract_text_from_image(b64_str)
        self.assertFalse(failed)
        self.assertIn("Lead Architect", text)

    @patch("services.whatsapp_ocr._configure_tesseract", return_value="/usr/bin/tesseract")
    @patch("pytesseract.image_to_string")
    def test_extract_text_fallback_to_original_when_preprocessed_fails(self, mock_img_to_str, _mock_cfg):
        # Preprocessed fails, original succeeds
        def side_effect(img, lang="eng", config=""):
            if "psm 6" in config and img.mode == "L":
                return ""
            if "psm 3" in config and img.mode == "L":
                return ""
            if img.mode == "RGB":
                return "Python Backend Developer urgently needed. 5+ years exp."
            return ""

        mock_img_to_str.side_effect = side_effect
        raw_bytes = _create_sample_image("Flyer")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        text, failed = extract_text_from_image(b64_str)
        self.assertFalse(failed)
        self.assertIn("Python Backend Developer", text)

    @patch("services.whatsapp_ocr._configure_tesseract", return_value="/usr/bin/tesseract")
    @patch("pytesseract.image_to_string")
    def test_extract_text_below_threshold_marks_failed(self, mock_img_to_str, _mock_cfg):
        mock_img_to_str.return_value = "Tiny"
        raw_bytes = _create_sample_image("Flyer")
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        text, failed = extract_text_from_image(b64_str)
        self.assertTrue(failed)
        self.assertEqual(text, "Tiny")


class WhatsAppOCRResolveCmdTests(unittest.TestCase):
    def test_resolve_tesseract_cmd_env_override(self):
        with patch.dict(os.environ, {"TESSERACT_CMD": "/custom/path/tesseract"}):
            with patch("pathlib.Path.is_file", return_value=True):
                cmd = _resolve_tesseract_cmd()
                self.assertEqual(cmd, "/custom/path/tesseract")

    def test_resolve_tesseract_cmd_which(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("shutil.which", return_value="/usr/bin/tesseract"):
                cmd = _resolve_tesseract_cmd()
                self.assertEqual(cmd, "/usr/bin/tesseract")


class WhatsAppOCRTaskScoringTests(unittest.TestCase):
    def test_image_with_caption_scores_even_if_ocr_fails(self):
        """When an image message has caption text and OCR fails, the caption is still scored."""
        from services.whatsapp_matcher import compute_match_score

        # Caption matches 'Python', OCR failed
        caption = "Urgent requirement: Senior Python Engineer needed ASAP"
        ocr_text = ""
        ocr_failed = True

        combined = " ".join(part for part in [caption, ocr_text] if part).strip()
        score = compute_match_score(combined, keywords=["Python", "Engineer"])
        self.assertGreaterEqual(score, 60.0)

    def test_image_without_caption_fails_if_ocr_fails(self):
        """When an image message has no caption and OCR fails, combined is empty."""
        caption = None
        ocr_text = ""
        ocr_failed = True

        combined = " ".join(part for part in [caption or "", ocr_text or ""] if part).strip()
        self.assertEqual(combined, "")

    def test_image_with_ocr_text_scores_successfully(self):
        """When an image has extracted OCR text, it is scored against filters."""
        from services.whatsapp_matcher import compute_match_score

        caption = None
        ocr_text = "Hiring React Frontend Developer with TypeScript and Next.js"

        combined = " ".join(part for part in [caption or "", ocr_text or ""] if part).strip()
        score = compute_match_score(combined, keywords=["React", "Frontend"])
        self.assertGreaterEqual(score, 60.0)


if __name__ == "__main__":
    unittest.main()
