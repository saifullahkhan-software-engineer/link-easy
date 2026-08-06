"""
Unit tests for feed scroll job schemas.

Covers the update semantics added for job editing:

  * ``normalize_tags`` distinguishes "field not provided" (``None``) from an
    explicit clear (empty list) — so PATCH can empty a keyword/title/skill
    field instead of silently keeping the old tags
  * ``FeedScrollJobUpdate`` keeps empty lists in ``model_dump(exclude_unset=True)``
    so the API handler applies them verbatim
  * create-time validation (at least one criterion per mode, sane experience
    bounds) still rejects invalid payloads after the ``normalize_tags`` change
"""
import os
import sys
import types
import unittest

# The schemas import models -> core.config, which builds pydantic Settings
# from the environment.  Provide minimal placeholders so these unit tests run
# in a lightweight source checkout without a .env file.
_required_env = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "JWT_SECRET": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
    "PASSWORD_RESET_URL": "http://localhost/reset",
    "BACKEND_CORS_ORIGINS": "http://localhost:5173",
    "RESEND_API_KEY": "test",
    "FROM_EMAIL": "test@example.com",
    "REDIS_URL": "redis://localhost:6379/0",
}
for _key, _value in _required_env.items():
    os.environ.setdefault(_key, _value)

# The API module imports automation.actions.feed_scroll, which imports
# patchright (browser automation).  Unit tests only touch the schemas, so stub
# the browser dependency the same way test_feed_scroll_extraction.py does.
try:
    import patchright.async_api  # noqa: F401
except ModuleNotFoundError:
    patchright = types.ModuleType("patchright")
    async_api = types.ModuleType("patchright.async_api")
    async_api.Page = type("Page", (), {})
    async_api.Locator = type("Locator", (), {})
    async_api.ElementHandle = type("ElementHandle", (), {})
    patchright.async_api = async_api
    sys.modules["patchright"] = patchright
    sys.modules["patchright.async_api"] = async_api

from schemas.feed_scroll import FeedScrollJobCreate, FeedScrollJobUpdate, normalize_tags  # noqa: E402


class NormalizeTagsTest(unittest.TestCase):
    def test_none_means_field_not_provided(self):
        self.assertIsNone(normalize_tags(None))

    def test_empty_list_means_clear_field(self):
        self.assertEqual(normalize_tags([]), [])

    def test_empty_string_means_clear_field(self):
        self.assertEqual(normalize_tags(""), [])

    def test_parses_comma_semicolon_newline(self):
        self.assertEqual(normalize_tags("python, django\nreact;sql"), ["python", "django", "react", "sql"])

    def test_dedupes_exact_duplicates(self):
        self.assertEqual(normalize_tags(["Python", "Python"]), ["Python"])

    def test_whitespace_only_input_is_empty(self):
        self.assertEqual(normalize_tags("   \n  "), [])


class FeedScrollJobUpdateTest(unittest.TestCase):
    def test_empty_list_survives_exclude_unset_dump(self):
        """PATCH must be able to clear a tag field."""
        payload = FeedScrollJobUpdate(job_titles=[])
        self.assertEqual(payload.model_dump(exclude_unset=True), {"job_titles": []})

    def test_none_fields_are_omitted_by_default(self):
        payload = FeedScrollJobUpdate(keywords=["remote"])
        dumped = payload.model_dump(exclude_unset=True)
        self.assertEqual(dumped, {"keywords": ["remote"]})

    def test_comma_string_is_normalized_to_list(self):
        payload = FeedScrollJobUpdate(keywords="remote, SaaS")
        self.assertEqual(payload.keywords, ["remote", "SaaS"])

    def test_experience_bounds_can_be_cleared_with_null(self):
        payload = FeedScrollJobUpdate(experience_min_years=None, experience_max_years=None)
        dumped = payload.model_dump(exclude_unset=True)
        self.assertEqual(dumped, {"experience_min_years": None, "experience_max_years": None})


class FeedScrollJobCreateValidationTest(unittest.TestCase):
    def test_post_search_requires_keyword(self):
        with self.assertRaises(Exception) as ctx:
            FeedScrollJobCreate(
                account_email="a@example.com",
                owner_email="o@example.com",
                name="x",
                mode="post_search",
                keywords=[],
            )
        self.assertIn("at least one keyword", str(ctx.exception))

    def test_job_search_requires_criterion(self):
        with self.assertRaises(Exception) as ctx:
            FeedScrollJobCreate(
                account_email="a@example.com",
                owner_email="o@example.com",
                name="x",
                mode="job_search",
            )
        self.assertIn("at least one", str(ctx.exception))

    def test_job_search_accepts_titles_without_keywords(self):
        job = FeedScrollJobCreate(
            account_email="a@example.com",
            owner_email="o@example.com",
            name="x",
            mode="job_search",
            job_titles=["Software Engineer"],
        )
        self.assertEqual(job.job_titles, ["Software Engineer"])
        self.assertIsNone(job.keywords)

    def test_min_experience_cannot_exceed_max(self):
        with self.assertRaises(Exception) as ctx:
            FeedScrollJobCreate(
                account_email="a@example.com",
                owner_email="o@example.com",
                name="x",
                mode="job_search",
                job_titles=["Engineer"],
                experience_min_years=10,
                experience_max_years=2,
            )
        self.assertIn("Minimum experience", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
