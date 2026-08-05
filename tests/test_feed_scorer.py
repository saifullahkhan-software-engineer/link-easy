"""
Unit tests for the regex-based feed post scorer.
FILE: tests/test_feed_scorer.py

Covers:
  * half-word (partial) matching — "Python Developer" must also match a post
    about a "Python Engineer" (word "python") or a "Backend Developer"
    (word "developer"), because the post text may not contain the exact phrase
  * experience range matching — a post mentioning "3 years" counts when the
    job asks for 2-5 years; "7 years" does not; "N years ago/old" never
    counts as experience
  * stop words ("the", "of", ...) never trigger a match on their own
"""
import sys
import types
import unittest

# Stub the logging dependency tree so the scorer can be tested without the
# full worker environment (same approach as test_feed_scroll_extraction.py).
if "core.logging_config" not in sys.modules:
    logging_config = types.ModuleType("core.logging_config")

    class _NullLogger:
        def debug(self, *_args, **_kwargs):
            pass

        info = warning = error = debug

    logging_config.get_logger = lambda _name: _NullLogger()
    logging_config.should_log_debug = lambda: False
    logging_config.should_take_screenshots = lambda: False
    sys.modules["core.logging_config"] = logging_config

from automation.scoring.feed_scorer import _term_words, score_post  # noqa: E402


def job_config(**overrides):
    config = {
        "mode": "job_search",
        "job_titles": ["Python Developer"],
        "skill_set": ["AWS"],
        "experience_min_years": 2,
        "experience_max_years": 5,
        "keywords": [],
    }
    config.update(overrides)
    return config


class TermWordsTests(unittest.TestCase):
    def test_split_multiword_term(self):
        self.assertEqual(_term_words("Python Developer"), ["Python", "Developer"])

    def test_hyphenated_and_dotted_tokens(self):
        self.assertEqual(_term_words("full-stack node.js"), ["full", "stack", "node", "js"])

    def test_plus_tokens(self):
        self.assertEqual(_term_words("c++"), ["c"])


class HalfWordMatchTests(unittest.TestCase):
    def test_python_engineer_matches_python_developer(self):
        """Post says 'Python Engineer' — word 'python' of 'Python Developer' matches."""
        score, matched = score_post(
            "Hiring a Python Engineer for our platform team", job_config()
        )
        self.assertEqual(score, 35.0)
        self.assertIn("Python", matched)

    def test_backend_developer_matches_python_developer(self):
        """Post says 'Backend Developer' — word 'developer' matches."""
        score, matched = score_post("Backend Developer role - remote", job_config())
        self.assertEqual(score, 35.0)
        self.assertIn("Developer", matched)

    def test_full_phrase_still_matches(self):
        score, matched = score_post("We need a Python Developer urgently", job_config())
        self.assertEqual(score, 35.0)
        self.assertIn("Python Developer", matched)

    def test_no_shared_words_means_no_match(self):
        score, matched = score_post("Data Scientist with NLP experience", job_config())
        self.assertEqual(score, 0.0)
        self.assertNotIn("Python", matched)
        self.assertNotIn("developer", matched)

    def test_stop_word_never_triggers_match(self):
        score, matched = score_post("The role requires strong communication skills", job_config())
        self.assertEqual(score, 0.0)

    def test_meaningful_words_only(self):
        """'of' is a stop word — 'VP of Engineering' matches via 'VP' alone."""
        config = job_config(job_titles=["VP of Engineering"], skill_set=[])
        score, matched = score_post("We are hiring a VP", config)
        self.assertEqual(score, 35.0)
        self.assertIn("VP", matched)

    def test_single_word_term_unaffected(self):
        score, matched = score_post("We run everything on AWS", job_config())
        self.assertEqual(score, 30.0)
        self.assertIn("AWS", matched)

    def test_post_search_keywords_half_word(self):
        config = {"mode": "post_search", "keywords": ["machine learning"]}
        score, matched = score_post("Excited about learning new things", config)
        self.assertEqual(score, 100.0)
        self.assertIn("learning", matched)


class ExperienceRangeTests(unittest.TestCase):
    def test_three_years_in_two_to_five_range(self):
        """Post mentions 3 years, job range is 2-5 — must be considered."""
        score, matched = score_post("Looking for someone with 3 years of experience", job_config())
        self.assertEqual(score, 20.0)
        self.assertTrue(any("3" in m and "years" in m for m in matched))

    def test_bare_three_years_counts(self):
        score, matched = score_post("Must have 3 years minimum", job_config())
        self.assertEqual(score, 20.0)

    def test_yrs_abbreviation_counts(self):
        score, matched = score_post("3 yrs experience required", job_config())
        self.assertEqual(score, 20.0)

    def test_yoe_counts(self):
        score, matched = score_post("3 YOE in backend systems", job_config())
        self.assertEqual(score, 20.0)

    def test_out_of_range_not_matched(self):
        score, matched = score_post("We want a principal with 7 years of experience", job_config())
        self.assertEqual(score, 0.0)

    def test_range_with_overlap_matches(self):
        """Post asks 2-4 years, job range 2-5 — overlaps, so it counts."""
        score, matched = score_post("We need 2 to 4 years of experience", job_config())
        self.assertEqual(score, 20.0)
        self.assertTrue(any("2" in m and "4" in m for m in matched))

    def test_range_outside_band_not_matched(self):
        score, matched = score_post("Principal only, 8-10 years experience required", job_config())
        self.assertEqual(score, 0.0)

    def test_years_ago_is_not_experience(self):
        score, matched = score_post("I posted this 3 years ago", job_config())
        self.assertEqual(score, 0.0)

    def test_years_old_is_not_experience(self):
        score, matched = score_post("My laptop is 5 years old", job_config())
        self.assertEqual(score, 0.0)

    def test_only_min_bound(self):
        config = job_config(experience_max_years=None)
        score, matched = score_post("At least 4 years experience", config)
        self.assertEqual(score, 20.0)


class JobSearchKeywordTests(unittest.TestCase):
    def test_keyword_is_scored_in_job_search_mode(self):
        """Optional job-search keywords add their own 15-point relevance signal."""
        score, matched = score_post(
            "This is a remote role with flexible hours",
            job_config(
                job_titles=[],
                skill_set=[],
                experience_min_years=None,
                experience_max_years=None,
                keywords=["remote"],
            ),
        )
        self.assertEqual(score, 15.0)
        self.assertEqual(matched, ["remote"])

    def test_keyword_completes_a_full_job_search_score(self):
        score, matched = score_post(
            "Hiring a Python Developer with AWS and 3 years experience for a remote role",
            job_config(keywords=["remote"]),
        )
        self.assertEqual(score, 100.0)
        self.assertIn("remote", matched)


class IntegratedScoreTests(unittest.TestCase):
    def test_python_engineer_aws_exp_combine(self):
        score, matched = score_post(
            "Hiring Python Engineer with 3+ years experience in AWS", job_config()
        )
        # 35 (half-word "python") + 30 (AWS) + 20 (experience in 2-5) = 85
        self.assertEqual(score, 85.0)
        self.assertIn("Python", matched)
        self.assertIn("AWS", matched)
        self.assertTrue(any("3" in m and "years" in m for m in matched))


if __name__ == "__main__":
    unittest.main()
