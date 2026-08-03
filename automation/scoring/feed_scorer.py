"""
Regex-based feed post scorer.
FILE: automation/scoring/feed_scorer.py

Scores LinkedIn posts against user-defined criteria using regex pattern matching.
Designed with a protocol interface so it can be swapped for an AI scorer later.
"""
import re
from typing import Protocol

from core.logging_config import get_logger

logger = get_logger(__name__)


class ScorerInterface(Protocol):
    """Interface for post scorers — swap regex for AI without touching the flow."""

    def score(self, post_text: str, config: dict) -> tuple[float, list[str]]:
        """
        Score a post against the configuration.

        Args:
            post_text: Raw text of the LinkedIn post
            config: Dict with keys like job_titles, skill_set, experience_min_years,
                    experience_max_years, keywords, mode

        Returns:
            (score 0-100, list_of_matched_terms)
        """
        ...


class RegexScorer:
    """
    Regex-based scorer that matches posts against user criteria.

    Scoring breakdown:
    ┌─────────────────────────────────────────────────────────────┐
    │  Category          │ Weight   │ Match Method               │
    ├────────────────────┼──────────┼────────────────────────────┤
    │  Job Titles        │ 35 pts   │ Case-insensitive regex     │
    │  Skills            │ 30 pts   │ Case-insensitive regex     │
    │  Experience Level  │ 20 pts   │ Regex for year ranges      │
    │  Keywords          │ 15 pts   │ Case-insensitive regex     │
    └─────────────────────────────────────────────────────────────┘
    """

    def score(self, post_text: str, config: dict) -> tuple[float, list[str]]:
        if not post_text or not post_text.strip():
            return 0.0, []

        total_score = 0.0
        matched_terms = []

        mode = config.get("mode", "post_search")

        if mode == "job_search":
            # Job Titles — 35 points
            title_score, title_matches = self._score_category(
                post_text, config.get("job_titles", []), weight=35.0
            )
            total_score += title_score
            matched_terms.extend(title_matches)

            # Skills — 30 points
            skill_score, skill_matches = self._score_category(
                post_text, config.get("skill_set", []), weight=30.0
            )
            total_score += skill_score
            matched_terms.extend(skill_matches)

            # Experience Level — 20 points
            exp_score, exp_matches = self._score_experience(
                post_text,
                config.get("experience_min_years"),
                config.get("experience_max_years"),
                weight=20.0,
            )
            total_score += exp_score
            matched_terms.extend(exp_matches)

            # Keywords (if any provided in job_search mode) — 15 points
            if config.get("keywords"):
                kw_score, kw_matches = self._score_category(
                    post_text, config["keywords"], weight=15.0
                )
                total_score += kw_score
                matched_terms.extend(kw_matches)

        elif mode == "post_search":
            # Keywords only — full 100 points
            kw_score, kw_matches = self._score_category(
                post_text, config.get("keywords", []), weight=100.0
            )
            total_score += kw_score
            matched_terms.extend(kw_matches)

        # Cap at 100
        total_score = min(total_score, 100.0)

        return round(total_score, 1), matched_terms

    def _score_category(
        self, text: str, terms: list[str] | None, weight: float
    ) -> tuple[float, list[str]]:
        """Score a single category. Returns (points_earned, matched_terms)."""
        if not terms:
            return 0.0, []

        matches = []
        for term in terms:
            if not term or not term.strip():
                continue
            # Build a regex that allows flexible whitespace between words
            escaped = re.escape(term.strip())
            # Allow flexible whitespace between words
            pattern = escaped.replace(r"\ ", r"\s+")
            regex = re.compile(rf"(?i)\b{pattern}\b")
            if regex.search(text):
                matches.append(term)

        if not terms:
            return 0.0, []

        ratio = len(matches) / len(terms)
        return ratio * weight, matches

    def _score_experience(
        self,
        text: str,
        min_years: int | None,
        max_years: int | None,
        weight: float,
    ) -> tuple[float, list[str]]:
        """Score experience level mentions in the post text."""
        if min_years is None and max_years is None:
            return 0.0, []

        matches = []

        # Patterns for experience mentions
        exp_patterns = [
            r"(?i)(\d+)\s*[\-–to]+\s*(\d+)\s*years?",
            r"(?i)(\d+)\+?\s*years?\s*(?:of)?\s*(?:experience|exp)",
            r"(?i)(?:experience|exp)\s*(?:of)?\s*(\d+)\+?\s*years?",
            r"(?i)(?:junior|jr)\b",
            r"(?i)(?:mid[\-\s]?level|mid)\b",
            r"(?i)(?:senior|sr)\b",
        ]

        for pattern in exp_patterns:
            found = re.findall(pattern, text)
            if found:
                for match in found:
                    if isinstance(match, tuple):
                        # Range pattern like "2-3 years"
                        try:
                            low = int(match[0])
                            high = int(match[1])
                            if min_years is not None and max_years is not None:
                                if low <= max_years and high >= min_years:
                                    matches.append(f"{low}-{high} years")
                            elif min_years is not None:
                                if high >= min_years:
                                    matches.append(f"{low}-{high} years")
                            elif max_years is not None:
                                if low <= max_years:
                                    matches.append(f"{low}-{high} years")
                        except (ValueError, IndexError):
                            pass
                    else:
                        # Single number pattern
                        try:
                            years = int(match)
                            if min_years is not None and max_years is not None:
                                if min_years <= years <= max_years:
                                    matches.append(f"{years} years")
                            elif min_years is not None:
                                if years >= min_years:
                                    matches.append(f"{years}+ years")
                            elif max_years is not None:
                                if years <= max_years:
                                    matches.append(f"{years} years")
                        except (ValueError, IndexError):
                            pass

        # Also check for level keywords that imply experience range
        level_map = {
            "junior": (0, 2),
            "jr": (0, 2),
            "mid": (2, 5),
            "mid-level": (2, 5),
            "senior": (5, 10),
            "sr": (5, 10),
        }
        for level, (level_min, level_max) in level_map.items():
            regex = re.compile(rf"(?i)\b{re.escape(level)}\b")
            if regex.search(text):
                if min_years is not None and max_years is not None:
                    if level_min <= max_years and level_max >= min_years:
                        matches.append(f"{level} level")

        if not matches:
            return 0.0, []

        # Any experience match in range gets full weight (it's binary: match or not)
        return weight, list(set(matches))


def score_post(post_text: str, config: dict) -> tuple[float, list[str]]:
    """Convenience function — scores a single post using RegexScorer."""
    scorer = RegexScorer()
    return scorer.score(post_text, config)
