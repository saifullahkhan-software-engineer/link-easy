# WhatsApp Per-User Sessions — Rollout Notes

## What changed

WhatsApp connections are now **per user**, mirroring the LinkedIn accounts
model (`docs/persistent_profiles_rollout.md`). Before this rollout the whole
deployment shared ONE WhatsApp number and ONE Chromium profile; now every
platform user connects their own number and gets:

- **`whatsapp_sessions.owner_email`** — FK to `users.email`. The API never
  reads another user's session. Legacy singleton rows stay `NULL`; the first
  authenticated user without a session adopts one on their next WhatsApp
  request (`api/v1/whatsapp_sessions.get_owned_session`).
- **`whatsapp_sessions.profile_dir`** — the session's durable Chromium
  user-data-dir, set at creation to
  `{PROFILE_STORAGE_DIR}/whatsapp/session-{id}`. `NULL` resolves to the old
  shared flat directory, so pre-migration installs keep their working
  connection without moving files.
- **Per-session Redis locks** — `profile_lock:whatsapp:{id}` instead of the
  global `profile_lock:whatsapp`. One user's live chat or scan no longer
  blocks another user's browser.
- **Per-session browser managers** — `get_browser_view(session_id)` and
  `get_live_browser(session_id)` in `services/browser_view.py` /
  `services/whatsapp_live_browser.py` keep one QR-connect view and one
  live-chat browser per session, so ten users can drive ten numbers
  concurrently (RAM permitting: one Chromium process per open session).

## API behavior (paths unchanged, scope changed)

| Endpoint | Before | After |
|---|---|---|
| `POST /api/v1/whatsapp/connect` | creates a global session | creates/adopts the **caller's** session + QR view |
| `GET /api/v1/whatsapp/status` | global status | the **caller's** session status |
| `DELETE /api/v1/whatsapp/connection` | wipes the shared profile | disconnects + removes **only the caller's** profile dir |
| `GET /api/v1/whatsapp/groups`, `/scan/trigger`, `/session/capture` | global | caller-scoped |
| `/api/v1/whatsapp/live/*` | process-wide singleton browser | the **caller's** session browser |
| `/api/v1/live/browser/*` (QR view control/stream) | global view | the **caller's** session view |
| worker `check_whatsapp_messages` | newest connected session | the **filter owner's** connected session |

## Worker resolution

`tasks.check_whatsapp_messages` resolves the session from the filter row:

- filter with `owner_email` → that owner's active/connected session
  (skip the scan if they have none),
- legacy unowned filter → the legacy unowned session, falling back to the
  newest connected session.

The scan then launches `launch_whatsapp_persistent(profile_dir=...)` against
that session's own profile and holds `profile_lock:whatsapp:{id}`.

## Migration

`migrations/versions/e9d2f1a0b3c4_add_whatsapp_per_user_sessions.py`
(revision `e9d2f1a0b3c4`, down to `d7f3a1b9c2e4`) adds `owner_email` +
`profile_dir` and the FK to `users.email`. It is additive: existing rows,
profile dirs, and the old flat `{PROFILE_STORAGE_DIR}/whatsapp` directory all
keep working. Run `alembic upgrade head` (or `python run_migrations.py`) on
deploy.

## Operational notes

- **Device slots**: each connected number consumes one of WhatsApp's
  (~4) linked-device slots, and each open session costs a Chromium process
  (~200–400 MB). Disconnect (or admin delete) removes the profile dir so
  slots/memory are reclaimed.
- **Legacy flat profile**: adopted legacy sessions keep the flat directory
  and it is protected by the same `profile_lock:whatsapp:{id}` key as any
  other session — the old global `profile_lock:whatsapp` key is only used by
  the retired CLI connect task.
- **Admin**: `GET /api/v1/admin/accounts` now reports `owner_email` and a
  per-row `profile_missing` for each WhatsApp session.
