# Persistent Per-Account Browser Profiles — Rollout Notes

## What changed

LinkedIn session state no longer lives in the database. Each `LinkedInAccount`
now owns a durable Chromium user-data-dir at
`{PROFILE_STORAGE_DIR}/{account.id}` (default `./profiles/<uuid>`), opened via
Patchright's `launch_persistent_context()`. Cookies, localStorage, IndexedDB
and service-worker caches persist to disk continuously as a side effect of
normal browsing — there is no `save_session_state()` / `load_session_state()`
round-trip through Postgres anymore (`encrypted_storage_state` and
`cookies_updated_at` columns are gone).

Every account also has a **pinned browser fingerprint** (user agent, viewport,
timezone, locale, CPU count, device memory) chosen once at first login and
reused unchanged on every subsequent launch, plus warm-up-aware daily/weekly
action caps in `worker/rate_limit.py`.

## ⚠️ All LinkedIn accounts must be re-linked after this ships

None of the old encrypted sessions carry over to persistent profiles.

### Deployment steps (single pass)

1. **Wipe legacy data** — the migration deletes the remaining rows itself
   (`campaign_jobs` → `campaigns` (leads cascade) → `linkedin_accounts`),
   consistent with the decision to reset all previous account/session data.
2. **Run the Alembic migration** `persistent_profiles`
   (`alembic upgrade head`) together with the code deploy.
3. **Mount the profile storage** — in Railway, attach a persistent volume at
   `/app/profiles` (the Docker image sets `PROFILE_STORAGE_DIR` to that path).
   For local/self-hosted runs the source default remains `./profiles`. If no
   volume is attached, the container creates fresh profiles and can still
   start, but those profiles are ephemeral and accounts must be re-linked after
   a restart or deploy.
4. **Re-link every LinkedIn account** via `POST /api/v1/linkedin/account`.
5. Assign each account a **sticky proxy** (one per account, permanently —
   written to `proxy_*` columns once; never rotated per session). Proxy
   assignment lives outside this repo; whatever tooling writes those columns
   must assign exactly one proxy per account for the account's lifetime.

### Container follow-up (now fixed)

The Dockerfile previously ran `playwright install chromium` /
`playwright install-deps chromium`. Since `playwright` was replaced by
`patchright` in `requirements.txt`, the Dockerfile now runs
`patchright install chromium --with-deps` instead.

## Operational notes

- **Concurrency:** a Chromium user-data-dir can only be open by one process
  at a time. The Redis lock `profile_lock:{account.id}`
  (`worker/profile_lock.py`) guards this: the Celery session task, the
  `verify-session` endpoint and the login flow all acquire it before
  launching and release it after closing the context. A second concurrent
  attempt fails fast with "account is currently in use" — never a corrupted
  profile, never a hang. This is separate from (and additional to) the
  global `playwright_semaphore` cap on total concurrent browsers.
- **Profile dirs contain live session cookies in plaintext on disk** — they
  are created with `0o700` permissions. Treat the profiles volume as secret
  material (no shared mounts, restrict host access).
- **Deleting an account** removes its profile directory as well.
- **Verifying fingerprint stability:** every launch logs the pinned values
  (`ua=... viewport=... tz=... locale=... cpu=... mem=...`); diff the log
  lines across two campaign-session runs for the same account to confirm
  they are identical.
