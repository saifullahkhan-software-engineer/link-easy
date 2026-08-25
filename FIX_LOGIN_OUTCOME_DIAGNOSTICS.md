# FIX: LinkedIn login failure diagnostics & outcome classification

**Observed in production logs (2026-08-23):** every `POST /api/v1/linkedin/account`
attempt ended with `❌ Login failed - still on login page` and
`400 (166,939 ms)`, preceded by
`⚠️ Could not process checkbox N: 'ElementHandle' object has no attribute 'wait_for'`.

## What the logs showed

| Elapsed | Event |
|---|---|
| ~3s | Navigate to `/login` OK (through account proxy) |
| 3–72s | **All 16 hardcoded email selectors missed** → self-healing locates field instantly |
| 80–120s | Same for password (~40s) |
| 126s | Checkbox step **crashed** on `ElementHandle.wait_for` (warning-only) |
| 127–151s | **All 8 submit selectors missed** — even `button[type='submit']` → self-healing clicked |
| 156s | Fixed 2–4s sleep, URL still `/login` → classified EXPIRED → account deleted → `400` |

## Root causes fixed

1. **`ElementHandle.wait_for` doesn't exist** (`automation/session.py`,
   mirrored in the verification endpoint of `api/v1/linkedin.py`).
   Checkbox handling is now a single shared Locator-based helper:
   `uncheck_all_checkboxes(page, context_label)`. The old code also
   de-duplicated with `set()` over wrapper objects, so the same box found by
   two selectors was processed twice.

2. **Racy post-submit classification.** The old code slept a fixed 2–4s after
   clicking Sign In, then judged purely by `page.url`. Behind a slow proxy the
   `/uas/login-submit` POST + redirect chain regularly outlives that window,
   so a still-in-flight navigation was misclassified as "still on login page"
   → "Invalid credentials". Replaced with
   `wait_for_login_outcome(page, timeout_ms=60000)` which polls until the URL
   leaves the login surface **or** a rejection banner renders
   (`extract_login_error`), deadline-bounded.

3. **No diagnosis for genuine bounces.** When LinkedIn rejects the form it
   renders an inline banner (`#error-for-username`, `#error-for-password`,
   `div[role='alert']`, …). That text (LinkedIn chrome, never user input) is
   now scraped, logged at WARNING together with a **query-stripped URL** and
   captcha flag, and surfaced in the API `400` detail — so "why did login
   fail" is answerable from the response alone.

4. **CAPTCHA != verification-code.** A rendered CAPTCHA iframe on the login
   page must NOT be routed into the pending verification-code session (that
   flow can only type a 6-digit code). It now returns UNKNOWN with an explicit
   "bot-detection flag on this IP/browser profile" message.

5. **The client could abort before the API responded.** Production showed a
   complete LinkedIn request taking about 97 seconds on a cold container,
   while the frontend timeout was 90 seconds. The frontend LinkedIn and
   WhatsApp automation timeouts are now 180 seconds, so a slow but healthy
   browser startup is not reported as a network/connection error.

6. **~130s of blind selector probing per attempt.** The resilient helpers
   (`find_and_type_resilient` / `find_and_click_resilient` in
   `automation/human.py`) now probe each candidate with a single fast
   *visible* check (1.5s) instead of 3s "attached" + 5s "visible" nested
   waits, and never interact with hidden duplicate forms. This lands the
   whole request back under typical reverse-proxy timeouts. Additionally,
   `human_click` no longer writes a full-page screenshot on **every** selector
   miss (that was dozens of PNGs per login attempt); it's gated behind
   `should_take_screenshots()` like the others.

Also: after `domcontentloaded`, the form is client-hydrated by JS — one
bounded "wait for any `<input>`" was added so probes don't run against a
half-painted page.

## Files changed

- `automation/session.py` — checkbox helper, `wait_for_login_outcome`,
  `extract_login_error`, `detect_human_challenge`, `sanitized_url_path`,
  enriched classification; `linkedin_login` now returns
  `(status, resources, error_detail)`.
- `automation/human.py` — fast visibility probes in the resilient helpers;
  dev-gated failure screenshots.
- `api/v1/linkedin.py` — both `linkedin_login` call sites use the new tuple;
  error detail flows into the `400`/verify-session messages; verification
  endpoint uses the shared checkbox helper.
- `tests/test_login_outcome_helpers.py` — regression tests for the helpers.

## Note on THIS incident

Every hardcoded selector missed while the ARIA-role fallback found the
fields instantly — LinkedIn was serving a login-page variant this server had
never pattern-matched before. A failure screenshot + HTML dump will now be
written (`login_failure_diagnostics.png/.html`, dev mode or
`ENVIRONMENT=development`) on every bounce so the variant can be catalogued.
If the next attempt still fails, the API response body will contain
LinkedIn's own rejection text — "Wrong email or password" vs. throttling
notice vs. CAPTCHA flag tells you exactly which case it is.
