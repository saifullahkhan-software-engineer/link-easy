"""Tests for LinkedIn session-cookie parsing (automation/cookie_import.py).

Cookie import is the datacenter-IP workaround for LinkedIn: the user signs in
from their own browser and pastes the resulting session cookie, so the server
never drives the CAPTCHA-prone sign-in form. These tests pin the accepted
input formats and — importantly — that a live credential is never echoed back
in an error message.
"""
import os
import sys
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.cookie_import import (  # noqa: E402
    CookieImportError,
    cookie_names,
    parse_cookie_input,
)

# Representative shape of a real token — long, opaque, base64url-ish.
LI_AT = "AQEDATExampleToken_-0123456789abcdefghijklmnopqrstuvwxyzABCDEF=="


def _by_name(cookies):
    return {c["name"]: c for c in cookies}


class ParseRawValueTests(unittest.TestCase):
    def test_bare_value(self):
        cookies = parse_cookie_input(LI_AT)
        self.assertEqual(cookie_names(cookies), ["li_at"])
        self.assertEqual(cookies[0]["value"], LI_AT)

    def test_surrounding_whitespace_and_quotes_stripped(self):
        cookies = parse_cookie_input(f'  "{LI_AT}"  ')
        self.assertEqual(cookies[0]["value"], LI_AT)

    def test_name_equals_value_form(self):
        cookies = parse_cookie_input(f"li_at={LI_AT}")
        self.assertEqual(cookies[0]["value"], LI_AT)

    def test_full_cookie_header_keeps_only_relevant_cookies(self):
        raw = (
            f'li_at={LI_AT}; JSESSIONID="ajax:1234567890"; '
            "lang=v=2&lang=en-us; _ga=GA1.2.999; some_tracker=nope"
        )
        cookies = parse_cookie_input(raw)
        names = cookie_names(cookies)
        self.assertIn("li_at", names)
        self.assertIn("JSESSIONID", names)
        self.assertIn("lang", names)
        # Unrelated analytics cookies must not be injected into the profile.
        self.assertNotIn("_ga", names)
        self.assertNotIn("some_tracker", names)

    def test_jsessionid_quotes_are_stripped(self):
        raw = f'li_at={LI_AT}; JSESSIONID="ajax:1234567890"'
        cookies = _by_name(parse_cookie_input(raw))
        self.assertEqual(cookies["JSESSIONID"]["value"], "ajax:1234567890")


class ParseJsonExportTests(unittest.TestCase):
    def test_extension_array_export(self):
        payload = (
            '[{"name":"li_at","value":"%s","domain":".linkedin.com"},'
            '{"name":"JSESSIONID","value":"ajax:999","domain":".linkedin.com"}]'
            % LI_AT
        )
        cookies = _by_name(parse_cookie_input(payload))
        self.assertEqual(cookies["li_at"]["value"], LI_AT)
        self.assertEqual(cookies["JSESSIONID"]["value"], "ajax:999")

    def test_object_with_cookies_key(self):
        payload = '{"cookies":[{"name":"li_at","value":"%s"}]}' % LI_AT
        cookies = parse_cookie_input(payload)
        self.assertEqual(cookies[0]["value"], LI_AT)

    def test_flat_mapping(self):
        payload = '{"li_at":"%s","JSESSIONID":"ajax:1"}' % LI_AT
        cookies = _by_name(parse_cookie_input(payload))
        self.assertEqual(cookies["li_at"]["value"], LI_AT)

    def test_non_linkedin_domains_are_ignored(self):
        payload = (
            '[{"name":"li_at","value":"%s","domain":".linkedin.com"},'
            '{"name":"sid","value":"secret","domain":".facebook.com"}]' % LI_AT
        )
        names = cookie_names(parse_cookie_input(payload))
        self.assertEqual(names, ["li_at"])

    def test_malformed_json_is_rejected_clearly(self):
        with self.assertRaises(CookieImportError) as ctx:
            parse_cookie_input('[{"name":"li_at",')
        self.assertIn("could not be parsed", str(ctx.exception))


class CookieAttributeTests(unittest.TestCase):
    def test_cookies_are_scoped_to_linkedin(self):
        for cookie in parse_cookie_input(f'li_at={LI_AT}; JSESSIONID="ajax:1"'):
            self.assertEqual(cookie["domain"], ".linkedin.com")
            self.assertEqual(cookie["path"], "/")
            self.assertTrue(cookie["secure"])

    def test_li_at_is_httponly_but_jsessionid_is_not(self):
        # LinkedIn's own JS reads JSESSIONID to build the CSRF header, so
        # marking it httpOnly would break the internal XHR API.
        cookies = _by_name(parse_cookie_input(f'li_at={LI_AT}; JSESSIONID="ajax:1"'))
        self.assertTrue(cookies["li_at"]["httpOnly"])
        self.assertFalse(cookies["JSESSIONID"]["httpOnly"])

    def test_li_at_is_always_first(self):
        raw = f'JSESSIONID="ajax:1"; lang=v=2; li_at={LI_AT}'
        self.assertEqual(parse_cookie_input(raw)[0]["name"], "li_at")


class RejectionTests(unittest.TestCase):
    def test_empty_input(self):
        for value in ("", "   ", None):
            with self.assertRaises(CookieImportError):
                parse_cookie_input(value)

    def test_missing_li_at(self):
        with self.assertRaises(CookieImportError) as ctx:
            parse_cookie_input('[{"name":"JSESSIONID","value":"ajax:1"}]')
        self.assertIn("No li_at cookie found", str(ctx.exception))

    def test_url_pasted_instead_of_cookie(self):
        with self.assertRaises(CookieImportError) as ctx:
            parse_cookie_input("https://www.linkedin.com/feed/")
        self.assertIn("looks like a URL", str(ctx.exception))

    def test_value_with_spaces_rejected(self):
        with self.assertRaises(CookieImportError):
            parse_cookie_input("not a real cookie value")

    def test_too_short_value_rejected(self):
        with self.assertRaises(CookieImportError):
            parse_cookie_input("abc123")

    def test_error_never_leaks_the_cookie_value(self):
        """A rejection message must not echo back live credential material."""
        secret = "AQEDATE" + "x" * 40 + " has a space"
        with self.assertRaises(CookieImportError) as ctx:
            parse_cookie_input(secret)
        self.assertNotIn("AQEDATE", str(ctx.exception))
        self.assertNotIn("x" * 40, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
