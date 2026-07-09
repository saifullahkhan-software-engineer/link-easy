# LinkeFlow Backend Production Readiness Review and QA Package

Date: 2026-07-08

Scope: FastAPI backend, SQLAlchemy models, LinkedIn Playwright automation, Celery campaign worker, session/cookie handling, and the currently implemented API layer. Authentication and authorization dependencies that are intentionally commented out for testing are noted but not treated as accidental implementation bugs.

Important security note: A real LinkedIn credential was shared during review. Rotate that password before any wider testing, remove it from chat/exported logs, and do not store Postman collections or screenshots with plaintext credentials.

## Executive Summary

The backend is not production-ready yet. Most critical runtime blockers have been fixed, including adding `REDIS_URL` to settings, adding campaign step fields with proper imports, adding lead completion fields with migration, correcting the worker's Playwright unpacking/session-status checks, removing plaintext password from verification sessions, and fixing browser resource leaks. The remaining blockers are startup configuration mismatch, incomplete CSV validation, Docker/env mismatch, and incomplete action separation for LinkedIn visit vs like behavior.

The `POST /api/v1/test/like-first-post` flow can work in Postman because it uses an already stored `LinkedInAccount` and reloads encrypted cookies from the database. That confirms the local cookie reuse path can work, but it should remain an internal-only endpoint and must be removed or admin-gated before production.

## Fix Verification Update

Checked on: 2026-07-08

Verification commands run:

- `python -m py_compile schemas\campaign.py api\v1\campaigns.py schemas\lead.py worker\tasks\campaign_tasks.py` passed with system Python syntax checking.
- `.\.venv\Scripts\python.exe -c "import schemas.campaign; import api.v1.campaigns; print('imports ok')"` failed because `.env` does not define the newly required `REDIS_URL`.
- Retrying with temporary `REDIS_URL=redis://localhost:6379/0` allowed imports to pass.

| Area | Current Status | Evidence | Remaining Action |
|---|---|---|---|
| Redis config | Fixed | `core/config.py::Settings` now includes `REDIS_URL`. | Add `REDIS_URL` to `.env`; fix Docker env names for `JWT_SECRET` and `CREDENTIAL_ENCRYPTION_KEY`. |
| Campaign steps | Fixed | `schemas/campaign.py::CampaignCreate` now has `steps`; `api/v1/campaigns.py::create_campaign` imports `CampaignStep` and creates step rows. | Add tests for create-with-steps. |
| Lead completion | Fixed at model level | `models/lead.py` now has `LeadStatus.COMPLETE` and `completed_at`; migration created. | Run migration in production. |
| Worker browser unpacking | Fixed in generic worker wrappers | `_run_visit`, `_run_like`, `_run_connect`, `_run_message` now unpack five values from `launch_browser()`. | Run Celery worker smoke tests. |
| Worker session verification | Fixed in generic worker wrappers | Worker now checks `verification.status != LinkedInSessionStatus.VALID`. | Confirm legacy tasks and all wrappers follow the same rule. |
| Verification session plaintext password | Fixed | `automation/session_manager.py::PendingLoginSession` no longer stores `linkedin_password`; `api/v1/linkedin.py::add_linkedin_account` no longer passes it. | None. |
| Session cleanup | Partially fixed | Cleanup now uses `run_until_complete` / `asyncio.run` from sync code; this can fail inside a running FastAPI event loop. | Convert cleanup to async and await it from async endpoints. |
| LinkedIn visit vs like action split | Partially fixed | Worker now has `_run_like`, but both `_run_visit` and `_run_like` still call `visit_profile_and_like_post()`. | Implement separate `visit_profile()` and `like_recent_post()` actions. |
| Lead URL validation | Partially fixed | `LeadCreate.linkedin_url` validates `https://www.linkedin.com/in/`. | CSV upload bypasses `LeadCreate` and still stores raw `linkedin_url`. Apply the same validation in upload. |
| Docker/env readiness | Still open | `docker-compose.yml` still uses `JWT_SECRET_KEY` and `ENCRYPTION_KEY`; settings expect `JWT_SECRET` and `CREDENTIAL_ENCRYPTION_KEY`. | Rename compose variables and provide all required settings. |

## High-Priority Findings

| Severity | File / Function | Issue | Why It Matters | Recommended Fix |
|---|---|---|---|---|
| Critical | `.env`, `core/config.py::Settings`, worker modules | `REDIS_URL` is now required but missing from `.env`. | Local app/worker imports fail settings validation until the env var is provided. | Add `REDIS_URL=redis://localhost:6379/0` locally and production Redis URL in deployment secrets. |
| High | `worker/tasks/campaign_tasks.py::_run_visit`, `_run_like` | `_run_like` was added, but both visit and like still call `visit_profile_and_like_post()`. | A visit-only step can still like a post, and the like step repeats profile visit behavior. | Split automation actions into a pure visit function and a pure like-recent-post function. |
| High | `api/v1/test_automation.py::LikeTestRequest`, `test_like_first_post` | Internal test endpoint accepts plaintext LinkedIn password and has no auth gate in the active code. The password is not used in the cookie path. | Anyone with network access can submit credentials and trigger automation if the endpoint is exposed. | Remove `linkedin_password` from this endpoint, require admin/internal auth, and disable the router in production. |
| High | `automation/session_manager.py::cleanup_session` | Cleanup still tries to close async resources from sync code and swallows failures. | Browser processes can leak, especially when called inside an already running event loop. | Make cleanup async and await context/browser/playwright close. Add periodic cleanup on lifespan. |
| High | `api/v1/leads.py::upload_leads_csv` | CSV upload still accepts arbitrary `linkedin_url` strings because it bypasses `LeadCreate` validation. | Invalid URLs, non-LinkedIn URLs, or malicious payload strings can enter automation and cause unintended navigation. | Reuse the same LinkedIn URL validator during CSV parsing. |
| High | `api/v1/leads.py::upload_leads_csv` | CSV upload silently skips invalid rows and still returns success. | Operators may think all leads were imported when bad rows were ignored. | Return row-level validation errors or a `207`-style summary with accepted/rejected counts. |
| High | `database.py::init_db` | Production startup calls `Base.metadata.create_all`. | Auto-creating schema can mask missing migrations and create drift from Alembic. | Use Alembic migrations in deployment; restrict `create_all` to local/dev only. |
| High | `docker-compose.yml` | Environment variables use `JWT_SECRET_KEY` and `ENCRYPTION_KEY`, but settings expect `JWT_SECRET` and `CREDENTIAL_ENCRYPTION_KEY`; email/CORS/password-reset variables are missing. | Containers will fail settings validation or run with incomplete config. | Rename env vars to match `Settings` and include all required values. |

## Medium and Low Findings

| Severity | File / Function | Issue | Why It Matters | Recommended Fix |
|---|---|---|---|---|
| Medium | `api/v1/campaigns.py::start_campaign` | Campaign status is committed after tasks are enqueued; no job rows are created during enqueue. | If enqueue partially fails, DB status can disagree with the actual queue. | Use a transaction boundary: create `CampaignJob` queued rows first, commit, then enqueue idempotently with job IDs. |
| Medium | `api/v1/campaigns.py::start_campaign` | Two concurrent start requests can both see non-active status and enqueue duplicate lead tasks. | Leads can be processed twice. | Use row-level locking (`SELECT ... FOR UPDATE`) or an atomic conditional update from draft/paused to active. |
| Medium | `worker/rate_limit.py::check_and_increment` | Uses `GET` then `INCR`, allowing a small race beyond the cap. | Multiple workers can exceed LinkedIn action limits. | Use a Redis Lua script or transaction that checks and increments atomically. |
| Medium | `worker/rate_limit.py::_seconds_until_midnight` | `midnight.replace(day=midnight.day + 1)` breaks at month end. | Rate limiting can crash on the last day of a month. | Use `midnight + timedelta(days=1)`. |
| Medium | `worker/playwright_semaphore.py::acquire_playwright_session` | Redis semaphore can go negative if errors occur between increment/decrement paths; no token ownership. | The global browser concurrency limiter can become inaccurate. | Use Redis locks/tokens or a sorted-set semaphore with owner IDs and expiry. |
| Medium | `api/v1/linkedin.py::add_linkedin_account` | Browser cleanup after successful login is not inside `try/finally`. | A DB error or cookie save error can leak Playwright resources. | Wrap session resources in `try/finally`; persist after cleanup or handle both failure modes. |
| Medium | `automation/session.py::save_session_cookies` | Stores all LinkedIn cookies, not just required session cookies. | More sensitive data is retained than necessary. | Store the minimum cookie set needed for reuse and track expiry explicitly. |
| Medium | `automation/session.py::load_session_cookies` | Cookie expiry is logged but expired cookies are still loaded. | Expired cookie sets cause avoidable automation failures. | Reject expired critical cookies before launching workflows and force relogin. |
| Medium | `api/v1/linkedin.py::submit_verification_code` | Verification input discovery is broad and may fill the wrong field. | LinkedIn page variants can make verification fail or submit unintended fields. | Prefer named/labelled OTP selectors and validate page state before filling. |
| Medium | `automation/actions/message.py::send_message` | Uses `human_mouse_move` but does not import it. | Message sending can crash when compose box exists. | Import `human_mouse_move` or remove the movement block. |
| Medium | `automation/actions/connect.py::send_connection_request` | Calls `human_click(page, send_btn)` with an element handle, while other calls pass selectors. | Depending on helper implementation, sending can fail. | Standardize helper signature or click element handles directly. |
| Medium | `api/v1/leads.py::upload_leads_csv` | No file type, size, header, duplicate, or max-row validation. | Large/malformed files can consume memory or create duplicate automation work. | Enforce content type/extension, max bytes, required headers, max rows, and uniqueness per campaign. |
| Medium | `schemas/campaign.py::CampaignCreate` | Daily limits have no `ge`/`le` validation. | Negative or excessive values can enter the database and create nonsensical worker behavior. | Add bounds matching hard caps: visits 1-80, likes 1-30, connections 1-15, messages 1-20. |
| Medium | `api/v1/auth.py::refresh_token` | Refresh tokens are stateless and rotated without revocation storage. | Stolen refresh tokens remain valid until expiry. | Store refresh token IDs, rotate and revoke on use, and support logout invalidation. |
| Medium | `main.py::CORSMiddleware` | Allows credentials with configured origins; config must not include `*`. | Browser credential leakage risk if wildcard origins are used in env. | Validate CORS origins on startup and fail production if wildcard is present. |
| Low | Multiple files | Mojibake characters appear in comments/log strings. | Logs and generated docs are harder to read. | Normalize files to UTF-8 and clean logging strings. |
| Low | `api/v1/test_automation.py::test_like_first_post` | Timeout comment says 2 minutes while actual timeout is 300 seconds. | Operational confusion during debugging. | Correct the message/comment or reduce timeout. |

## Like-First-Post Flow Validation

Endpoint: `POST /api/v1/test/like-first-post`

Active implemented flow:

1. `test_like_first_post()` receives `linkedin_email`, `linkedin_password`, and `profile_url`.
2. `_run_like_test()` looks up `LinkedInAccount` by `linkedin_email`.
3. `launch_browser(user_agent=account.user_agent)` starts Chromium with the saved user-agent.
4. `load_session_cookies(context, account)` decrypts `account.encrypted_cookies`, normalizes `sameSite`, and calls `context.add_cookies()`.
5. `verify_session(page)` opens `https://www.linkedin.com/feed/` and returns `VALID` only if LinkedIn accepts the session.
6. `visit_profile_and_like_post(page, profile_url)` opens the profile, then opens `/recent-activity/all/`, finds the first unliked Like/React button, clicks it, and verifies `aria-pressed` or label state.
7. `finally` closes context, browser, and Playwright.

Validation result:

| Check | Result |
|---|---|
| Session stored correctly | Yes, when the account was created through `add_linkedin_account()` and login reached `VALID`, cookies are encrypted into `LinkedInAccount.encrypted_cookies`, and `cookies_updated_at` is set. |
| Cookies persisted correctly | Mostly yes for local testing. Cookies are encrypted with AES-GCM and stored as JSON. Weakness: all LinkedIn cookies are retained, expiry is not enforced, and key rotation is not supported. |
| Session reuse working | Yes in the current test path if `user_agent` matches and LinkedIn has not expired/checkpointed the session. |
| Like action executed correctly | It attempts the correct behavior and verifies button state. It can fail if LinkedIn changes selectors, if no recent posts exist, if the post is already liked, or if activity visibility is restricted. |
| Hidden edge cases | Expired `li_at`, checkpoint, profile unavailable, activity page has no posts, already-liked post, localized LinkedIn UI, slow network, stale element after click, account rate limits, and LinkedIn automation detection. |

Production action: keep this endpoint out of public production. It should not accept passwords, and it should require admin/internal authorization if retained for diagnostics.

## Architecture Observations

- The data model includes `CampaignStep`, and the campaign API now accepts `steps`, but the current implementation still has a runtime import bug and needs tests before it can be considered reliable.
- The API layer mixes async SQLAlchemy sessions with Celery sync sessions. That is acceptable, but config and model parity must be tested because the worker imports settings independently.
- The automation layer currently combines visit and like behavior in multiple places. Production needs action isolation for rate limits, auditability, and LinkedIn safety.
- Cookie reuse is centralized and encrypted, which is a good base, but expiry, minimum-cookie retention, key rotation, and distributed verification sessions are still missing.
- Campaign execution has no idempotency key per lead/step. Retried or duplicated tasks can repeat LinkedIn actions.

## Test Data: CSV Lead Upload

File created: `testdata/leads_upload_valid.csv`

Expected upload CSV structure:

```csv
first_name,last_name,linkedin_url,headline
Saifullah,Khan,https://www.linkedin.com/in/saifullah-khan-64145b21a/,
Arslan,Khalid,https://www.linkedin.com/in/arslan-khalid-a33645330/,
Syed Dawood,Shah,https://www.linkedin.com/in/syed-dawood-shah-49a14a223/,
```

Current API requires `first_name`, `last_name`, and `linkedin_url`. `headline` is optional.

Upload request:

```http
POST /api/v1/leads/upload?campaign_id={campaign_id}&owner_email={owner_email}
Content-Type: multipart/form-data

file=@testdata/leads_upload_valid.csv
```

Expected success response:

```json
[
  {
    "id": "generated-uuid",
    "campaign_id": "{campaign_id}",
    "linkedin_url": "https://www.linkedin.com/in/saifullah-khan-64145b21a/",
    "first_name": "Saifullah",
    "last_name": "Khan",
    "headline": null,
    "status": "pending",
    "current_step": 0,
    "connection_sent_at": null,
    "accepted_at": null,
    "last_action_at": null,
    "next_action_at": null,
    "notes": null,
    "created_at": "server timestamp"
  }
]
```

## Campaign Creation Request

Current intended API shape after fixes:

```http
POST /api/v1/campaigns?owner_email={owner_email}
Content-Type: application/json
```

```json
{
  "account_email": "linkedin-account@example.com",
  "name": "Two-step profile visit and like campaign",
  "description": "Visit each lead profile, then like the most recent post.",
  "search_filters": {
    "source": "csv_upload",
    "target_region": "Pakistan",
    "notes": "Testing phase campaign"
  },
  "daily_connection_limit": 15,
  "daily_message_limit": 20,
  "daily_visit_limit": 80,
  "connection_note_template": null,
  "message_templates": [],
  "steps": [
    {
      "step_order": 1,
      "step_type": "visit_profile",
      "delay_hours": 0,
      "condition": null
    },
    {
      "step_order": 2,
      "step_type": "like_post",
      "delay_hours": 24,
      "condition": null
    }
  ]
}
```

Current implementation: The endpoint creates the campaign and its two `campaign_steps` rows in one transaction. Step creation imports `CampaignStep` from `models.campaign`.

## Start Campaign Request

```http
POST /api/v1/campaigns/{campaign_id}/start?owner_email={owner_email}
Content-Type: application/json
```

Current request body: none.

Expected success response:

```json
{
  "message": "Campaign started. 3 leads queued.",
  "leads_queued": 3
}
```

Expected database state changes:

- `campaigns.status` changes from `draft` or `paused` to `active`.
- `campaigns.started_at` is set to current UTC time.
- Celery receives one `tasks.execute_campaign_step` task per pending lead for step order 1.
- Current implementation does not create `campaign_jobs` at enqueue time; jobs are created only when the worker starts executing.

## API Execution Flow Mapping

### `POST /api/v1/leads/upload`

1. Route: `api/v1/leads.py::upload_leads_csv`
2. `get_db()` yields async session.
3. Query `Campaign` joined to `LinkedInAccount` by `campaign_id` and `owner_email`.
4. `await file.read()`.
5. `csv.DictReader(io.StringIO(content.decode("utf-8")))`.
6. For each row: read `first_name`, `last_name`, `linkedin_url`, optional `headline`.
7. Skip invalid rows silently.
8. Create `Lead` with `pending` status and `current_step=0`.
9. `db.add()` each lead.
10. `db.commit()`.
11. `db.refresh()` each created lead.
12. Return list of `LeadResponse`.

### `POST /api/v1/campaigns`

1. Route: `api/v1/campaigns.py::create_campaign`
2. `owner_email` query parameter is used for testing-phase ownership validation.
3. `get_db()` yields async session.
4. Query `LinkedInAccount` by `payload.account_email` and `owner_email`.
5. Create `Campaign` with generated UUID and draft status.
6. `db.add(campaign)`.
7. `db.flush()` to assign campaign ID.
8. If `payload.steps` exists, validate unique `step_order` values.
9. Create one `CampaignStep` per submitted step.
10. `db.commit()`.
11. `db.refresh(campaign)`.
12. Return `CampaignResponse`.

### `POST /api/v1/campaigns/{campaign_id}/start`

1. Route: `api/v1/campaigns.py::start_campaign`
2. `get_db()` yields async session.
3. Query `Campaign` joined to `LinkedInAccount` by `campaign_id` and `owner_email`.
4. Reject if not found or already active.
5. Query pending `Lead` rows for campaign.
6. Query `CampaignStep` where `step_order == 1`.
7. Reject with `400` if missing.
8. For each lead, compute random countdown.
9. `celery_app.send_task("tasks.execute_campaign_step", args=[lead.id, campaign_id, first_step.step_order], countdown=delay_seconds)`.
10. Set campaign active and `started_at`.
11. `db.commit()`.
12. Return queued count.

### `POST /api/v1/linkedin/account/verify-session`

1. Route: `api/v1/linkedin.py::verify_linkedin_session`
2. `get_current_user()` validates bearer token.
3. Query `LinkedInAccount` by `current_user.email`.
4. Return failed response if no account.
5. Return pending response if account already pending verification.
6. `launch_browser(user_agent=account.user_agent)`.
7. `load_session_cookies(context, account)`.
8. If cookies loaded, `verify_session(page)`.
9. If valid, return `ACTIVE`.
10. If invalid or no cookies, decrypt stored password.
11. `linkedin_login(email=account.linkedin_email, password=password, keep_alive=False)`.
12. If valid, `save_session_cookies()`, mark account active, commit, return `REFRESHED`.
13. If verification required, mark account pending, send email, return `PENDING_VERIFICATION`.
14. Otherwise mark failed and return `FAILED`.
15. `finally` closes Playwright resources.

### `POST /api/v1/test/like-first-post`

1. Route: `api/v1/test_automation.py::test_like_first_post`
2. `get_db()` yields async session.
3. `asyncio.wait_for(_run_like_test(...), timeout=300)`.
4. `_run_like_test()` queries `LinkedInAccount` by LinkedIn email.
5. `launch_browser(user_agent=account.user_agent)`.
6. `load_session_cookies(context, account)`.
7. `verify_session(page)`.
8. `visit_profile_and_like_post(page, profile_url)`.
9. Build response with `visited`, `liked_post`, `profile_name`, `post_url`, and `error`.
10. `finally` closes context, browser, and Playwright.

## Integration Test Matrix

| Test Case ID | Objective | Preconditions | Request | Expected Result | Failure Cases |
|---|---|---|---|---|---|
| CAM-API-001 | Create campaign for owned LinkedIn account | Active LinkedInAccount owned by owner exists | `POST /api/v1/campaigns?owner_email=owner@example.com` with valid payload and steps | `201`; campaign row created with `draft`; step rows created | Current code with `steps` can fail with `NameError` until `CampaignStep` is imported; missing account -> `400`; invalid limits -> should be `422` after validation is added |
| CAM-API-002 | List campaigns by owner during testing | Campaign rows exist | `GET /api/v1/campaigns?owner_email=owner@example.com` | `200`; only campaigns joined to that owner | Missing owner_email -> `422`; unknown owner -> `[]` |
| CAM-API-003 | Start campaign with configured step and pending leads | Campaign has step order 1 and pending leads; Redis/Celery reachable | `POST /api/v1/campaigns/{id}/start?owner_email=owner@example.com` | `200`; campaign active; `started_at` set; tasks enqueued | No campaign -> `404`; already active -> `409`; no steps -> `400`; Celery unavailable -> should return controlled error |
| CAM-API-004 | Prevent duplicate campaign starts | Same campaign receives two start requests concurrently | Two parallel `POST /start` calls | Exactly one succeeds; no duplicate task enqueue | Current code can enqueue duplicates; fix with row lock |
| LEAD-API-001 | Create a single lead | Campaign owned by owner exists | `POST /api/v1/leads` with campaign, owner, LinkedIn URL | `201`; lead pending/current_step 0 | Unknown campaign -> `400`; invalid URL -> should be `422` after validation |
| LEAD-API-002 | Upload valid CSV | Campaign exists; CSV fixture available | `POST /api/v1/leads/upload?campaign_id=...&owner_email=...` multipart | `201`; 3 leads created | Wrong headers -> should return validation errors; current code may return empty list |
| LEAD-API-003 | Reject malformed CSV | Campaign exists | Upload file missing `linkedin_url` | Should return `422` with row errors | Current code silently skips all rows and returns `201 []` |
| LEAD-API-004 | Prevent duplicate leads per campaign | Same lead URL already exists | Upload CSV containing duplicate URL | Should reject or de-dupe with summary | Current model has no uniqueness constraint |
| EXEC-API-001 | Worker executes first campaign step | Celery worker running; valid cookies; step order 1 exists | Enqueued `tasks.execute_campaign_step` | Job row `done`; lead current_step 1; lead status updated | Current worker crashes due `launch_browser` unpack bug |
| EXEC-API-002 | Expired LinkedIn session blocks execution | Account cookies expired | Enqueue step | Job failed/skipped; account marked pending/failed; no LinkedIn action | Current worker treats verifier object as truthy |
| LINKEDIN-001 | Verify active LinkedIn session | Account has valid encrypted cookies and matching user-agent | `POST /api/v1/linkedin/account/verify-session` with bearer token | `ACTIVE`; no password login | No cookies -> relogin attempted |
| LINKEDIN-002 | Refresh expired session | Account has encrypted password and expired cookies | `POST /verify-session` | `REFRESHED`; new encrypted cookies saved | Verification required -> pending status and email |
| LINKEDIN-003 | Verification-required login | LinkedIn requires OTP/checkpoint | `POST /api/v1/linkedin/account`, then `/account/verify` | Pending session created; successful OTP persists account/cookies | Expired in-memory session -> `404`; multi-process deployment loses session |
| TEST-LIKE-001 | Internal like-first-post smoke test | DB has LinkedInAccount with cookies | `POST /api/v1/test/like-first-post` | `200`; `visited=true`; `liked_post=true` when a likeable post exists | No cookies -> error; no posts -> `liked_post=false`; checkpoint -> session verification failure |

## Pytest-Style Unit Test Skeletons

```python
import pytest
from unittest.mock import AsyncMock, Mock, patch

from automation.session import LinkedInSessionStatus, SessionVerificationResult
from worker.tasks import campaign_tasks


@pytest.mark.asyncio
async def test_load_session_cookies_returns_false_without_cookie_blob(fake_context, linkedin_account):
    linkedin_account.encrypted_cookies = None
    assert await load_session_cookies(fake_context, linkedin_account) is False


@pytest.mark.asyncio
async def test_verify_session_returns_expired_on_login_redirect(fake_page):
    fake_page.url = "https://www.linkedin.com/login"
    result = await verify_session(fake_page)
    assert result.status == LinkedInSessionStatus.EXPIRED


def test_rate_limit_does_not_exceed_hard_cap(redis_client):
    for _ in range(15):
        assert check_and_increment("owner@example.com", "send_connection", 15) is True
    assert check_and_increment("owner@example.com", "send_connection", 15) is False


def test_schedule_next_step_handles_missing_next_step(db_session, lead):
    # Expected after fix: mark lead complete using a real model field.
    campaign_tasks._schedule_next_step(lead.id, lead.campaign_id, current_step_order=2)
    db_session.refresh(lead)
    assert lead.status.value in {"complete", "visited", "messaged"}


@pytest.mark.asyncio
async def test_run_visit_rejects_expired_session(linkedin_account, lead):
    with patch("worker.tasks.campaign_tasks.launch_browser", new=AsyncMock()) as launch, \
         patch("worker.tasks.campaign_tasks.load_session_cookies", new=AsyncMock(return_value=True)), \
         patch("worker.tasks.campaign_tasks.verify_session", new=AsyncMock(return_value=SessionVerificationResult(
             LinkedInSessionStatus.EXPIRED,
             "https://www.linkedin.com/login",
             "expired",
         ))):
        launch.return_value = (AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), "ua")
        with pytest.raises(Exception, match="session"):
            await campaign_tasks._run_visit(linkedin_account, lead)


@pytest.mark.asyncio
async def test_upload_csv_reports_invalid_rows(async_client, campaign):
    files = {"file": ("bad.csv", b"first_name,last_name\nA,B\n", "text/csv")}
    response = await async_client.post(
        f"/api/v1/leads/upload?campaign_id={campaign.id}&owner_email=owner@example.com",
        files=files,
    )
    assert response.status_code in {400, 422}


@pytest.mark.asyncio
async def test_create_campaign_persists_steps_after_fix(async_client, auth_headers, linkedin_account):
    payload = {
        "account_email": linkedin_account.linkedin_email,
        "name": "Visit and like",
        "daily_connection_limit": 15,
        "daily_message_limit": 20,
        "daily_visit_limit": 80,
        "steps": [
            {"step_order": 1, "step_type": "visit_profile", "delay_hours": 0, "condition": None},
            {"step_order": 2, "step_type": "like_post", "delay_hours": 24, "condition": None},
        ],
    }
    response = await async_client.post("/api/v1/campaigns", json=payload, headers=auth_headers)
    assert response.status_code == 201
```

## Production Readiness Recommendations

1. Add `REDIS_URL` to `.env` and deployment secrets; align Docker env names with `Settings`.
2. Run the Alembic migration for `LeadStatus.COMPLETE` and `leads.completed_at` in production.
3. Remove or strictly protect `/api/v1/test/*` endpoints in production.
4. Validate all LinkedIn URLs, including CSV upload rows, plus CSV headers, CSV size, row count, and duplicate leads.
5. Add idempotency for campaign lead/step execution and prevent concurrent duplicate starts.
6. Separate visit, like, connect, and message automation functions and rate-limit them independently.
7. Replace in-memory pending verification sessions with a distributed short-lived store, or pin the verification flow to one process.
8. Convert session cleanup to async and await it from async endpoints.
9. Use Alembic migrations only in production; stop calling `create_all` during production startup.
10. Add automated tests before broader testing: route tests, worker unit tests, CSV validation tests, cookie encryption/decryption tests, and session-state tests.
