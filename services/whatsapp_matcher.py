"""
WhatsApp Job Scanner — Matching / scoring engine.
FILE: services/whatsapp_matcher.py

Scores a combined text (message + OCR) against user-defined filters.

Scoring rules:
  - Each keyword match  → +2 points
  - Role match          → +3 points
  - Job title match     → +3 points
  - Experience level    → +2 points
  - Score is normalized to 0–100 range.
"""
import re

from core.logging_config import get_logger

logger = get_logger(__name__)


# ── Simple keyword scoring ────────────────────────────────────────────────────

def compute_match_score(
    combined_text: str,
    keywords: list[str] | None = None,
    role: str | None = None,
    job_title: str | None = None,
    experience_level: str | None = None,
) -> float:
    """Score a combined text against the given filters.

    Args:
        combined_text: The text to score (message_text + ocr_text).
        keywords: List of keyword strings to match against.
        role: Role string to match.
        job_title: Job title string to match.
        experience_level: "entry", "mid", or "senior".

    Returns:
        Score normalized to 0–100.
    """
    if not combined_text or not combined_text.strip():
        return 0.0

    text_lower = combined_text.lower()
    max_possible = 0
    earned = 0

    # ── Keywords (+2 each) ──
    if keywords:
        max_possible += len(keywords) * 2
        for kw in keywords:
            if not kw or not kw.strip():
                continue
            if _term_match(text_lower, kw.strip()):
                earned += 2

    # ── Role (+3) ──
    if role and role.strip():
        max_possible += 3
        if _term_match(text_lower, role.strip()):
            earned += 3

    # ── Job Title (+3) ──
    if job_title and job_title.strip():
        max_possible += 3
        if _term_match(text_lower, job_title.strip()):
            earned += 3

    # ── Experience Level (+2) ──
    if experience_level and experience_level.strip():
        max_possible += 2
        level = experience_level.strip().lower()
        if level == "entry":
            if _match_entry_level(text_lower):
                earned += 2
        elif level == "mid":
            if _match_mid_level(text_lower):
                earned += 2
        elif level == "senior":
            if _match_senior_level(text_lower):
                earned += 2

    if max_possible == 0:
        return 0.0

    score = (earned / max_possible) * 100.0
    return round(min(score, 100.0), 1)


def _term_match(text_lower: str, term: str) -> bool:
    """Check if a term (single word or phrase) appears in the text.

    Handles multi-word phrases with flexible whitespace.
    """
    term_lower = term.strip().lower()
    if not term_lower:
        return False

    # Escape and allow flexible whitespace between words
    escaped = re.escape(term_lower)
    pattern = escaped.replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?i)\b{pattern}\b", text_lower))


def _match_entry_level(text_lower: str) -> bool:
    """Check if text mentions entry-level / junior positions."""
    patterns = [
        r"\bentry[-\s]?level\b",
        r"\bjunior\b",
        r"\bjr\b",
        r"\bfresher\b",
        r"\b0[-\s]?2\s*years?\b",
        r"\bintern\b",
    ]
    return any(re.search(p, text_lower) for p in patterns)


def _match_mid_level(text_lower: str) -> bool:
    """Check if text mentions mid-level positions."""
    patterns = [
        r"\bmid[-\s]?level\b",
        r"\bmid[-\s]?senior\b",
        r"\b2[-\s]?5\s*years?\b",
        r"\b3[-\s]?5\s*years?\b",
    ]
    return any(re.search(p, text_lower) for p in patterns)


def _match_senior_level(text_lower: str) -> bool:
    """Check if text mentions senior-level positions."""
    patterns = [
        r"\bsenior\b",
        r"\bsr\b",
        r"\blead\b",
        r"\bprincipal\b",
        r"\bstaff\b",
        r"\b5\+\s*years?\b",
        r"\b5[-\s]?10\s*years?\b",
        r"\b8\+\s*years?\b",
    ]
    return any(re.search(p, text_lower) for p in patterns)
