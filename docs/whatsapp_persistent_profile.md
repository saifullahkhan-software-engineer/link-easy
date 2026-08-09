# WhatsApp Persistent Profile — Rollout Notes

## What changed

The WhatsApp connection no longer round-trips through `storage_state` in the
database. All WhatsApp browsers now share ONE durable Chromium user-data-dir
at `{PROFILE_STORAGE_DIR}/whatsapp` (default `./profiles/whatsapp`), opened
via Patchright's `launch_persistent_context()`:

- the QR login browser view (`services/browser_view.py`),
- the group-list fetch (`GET /api/v1/whatsapp/groups`),
- the periodic scan task (`tasks.check_whatsapp_messages`),
- the legacy CLI connect task (`tasks.connect_whatsapp`).

### Why

WhatsApp Web stores its device/session keys in **IndexedDB**, which
Playwright's `context.storage_state()` does not capture (cookies +
localStorage only). Restoring a "session" from storage_state alone opened a
half-broken device; launching a second browser from that state — which
happened automatically the moment the UI moved from the QR screen to group
selection, and whenever "Start" was pressed in the Live Browser View — killed
the freshly scanned connection. That was the reported "connection broke when
selecting a WhatsApp group" bug.

With the shared persistent profile:

- Cookies, localStorage, IndexedDB and service workers persist to disk
  continuously as a side effect of normal browsing — the profile directory
  itself is the session (same pattern as the LinkedIn accounts rollout in
  `docs/persistent_profiles_rollout.md`).
- `GET /groups` REUSES the live browser view when it is running instead of
  launching a second browser, so a post-connect group fetch can never race
  the connect flow.
- The redis lock `profile_lock:whatsapp` (`worker/profile_lock.py`)
  serializes access across the API process and the Celery worker; Chromium
  only allows one process per user-data-dir.
- A slow-loading WhatsApp Web page is never treated as an expired session:
  `wait_for_login()` polls up to 30s and the session is only marked
  `disconnected` when the QR landing screen is actually confirmed
  (`is_showing_qr()`). Busy/skipped checks leave the status untouched.

`whatsapp_sessions.storage_state_json` is still written on connect as a
best-effort snapshot, but it is no longer the source of truth.

## ⚠️ WhatsApp must be re-connected once after this ships

Old storage_state-only sessions cannot seed the new profile. After deploying,
open **Account → WhatsApp → Connect WhatsApp** and scan the QR code again.
From then on the connection survives restarts and scanner operations.

## UI changes shipped with this fix

- The sidebar tab is now **Account** (was "LinkedIn Account") and shows a hub
  with two cards: LinkedIn connectivity and WhatsApp connectivity.
- The WhatsApp QR flow lives at `/app/account/whatsapp`; the scanner page
  (`/app/whatsapp-scanner`) only shows the connected account's status and the
  monitoring configuration — it can no longer start a competing browser.

## Operational notes

- The profile dir contains live session material on disk and is created with
  `0o700` permissions. Treat the profiles volume as secret material.
- If an operation reports "The WhatsApp browser is busy with another
  operation", another browser currently holds the profile (scan in progress,
  group fetch, live view) — it frees itself within seconds to a couple of
  minutes; the lock also self-expires after 30 minutes as a crash guard.
