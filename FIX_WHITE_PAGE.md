# Fix: White Page Issue in LinkedIn Automation

## Problem

LinkedIn automation was encountering white/blank pages when navigating to profiles. This happens because:

1. **`wait_until="domcontentloaded"` only waits for HTML parsing**, not for JavaScript execution and rendering
2. LinkedIn is a heavy React/SPA application that loads most content dynamically via JavaScript
3. LinkedIn sometimes serves blank pages when:
   - Bot detection triggers
   - Session is stale/expired
   - Challenge/captcha needs to be solved
   - Network issues occur

The original fix (single `is_blank_page()` spot-check + one immediate reload)
had two weaknesses:

- It produced **false positives**: the spot check ran a few seconds after
  `domcontentloaded`, while LinkedIn's React app was often still mounting, so
  healthy-but-slow pages were declared blank, the reload raced the same way,
  and the lead failed with "Page failed to load (blank page after reload).
  Session may be stale."
- It never actually verified the session, and the worker failed the lead
  permanently — a transient blank page killed the lead, while a truly stale
  session kept burning every remaining lead in the session one by one.

## Solution

### 1. Shared progressive recovery (`automation/actions/utils.py`)

`recover_blank_page(page, target_url)` is now the single recovery path used
by every action. Steps:

1. **Wait for rendering** — `wait_for_page_render()` polls `is_blank_page()`
   for up to 12 s so a slow-mounting SPA is not mistaken for a blank page.
2. **Reload once** — and wait for rendering again.
3. **Probe session health on a known-good page (the feed)**:
   - redirected to login/checkpoint/authwall → session is **stale**;
   - feed renders fine → retry the original navigation once;
   - feed is blank too → session is unusable (bot detection / restriction).

It returns `(recovered, error, session_stale)`. Actions copy the flags into
their result dicts:

- `page_load_failed: True` — the failure was a page-load failure (retryable
  while the session is healthy).
- `session_stale: True` — the session itself is dead and the account session
  must stop.

All navigations still use `wait_until="domcontentloaded"`: `networkidle`
never fires on LinkedIn (continuous background requests) and causes a 30 s
timeout on every navigation.

### 2. Action modules

- **`automation/actions/connect.py`** — uses `recover_blank_page()`; a
  `goto` timeout is no longer instantly fatal (the recovery path decides).
- **`automation/actions/visit_profile.py`** — both the profile visit and the
  recent-activity navigation use `recover_blank_page()`.
- **`automation/actions/message.py`** — both compose paths (profile Message
  button, direct compose URL) report load failures with the flags instead of
  collapsing into "compose box not found".
- **`automation/actions/feed_scroll.py`** — feed navigation uses the same
  recovery.

### 3. Auth walls and checkpoints are classified by URL, not "blankness"

`is_blank_page()` is text-based only: a page with substantial content has
rendered, even when it is LinkedIn's authwall / login / checkpoint page
(those have no `#app-mount`, and the old app-container requirement
mis-labelled them as blank, hiding the real cause).
`recover_blank_page()` checks the current URL for auth redirects at every
stage and reports `session_stale = True` with the offending URL — this is
the typical failure mode when connection requests are sent with a degraded
session: profile pages still render, but LinkedIn parks sensitive flows on
the authwall/checkpoint.

### 4. Rate limits enforced on the primary execution path

Blank pages cluster on connection requests because invites are the action
LinkedIn throttles hardest.  The account-session path (the normal execution
path) previously bypassed `check_and_increment()` entirely — only the legacy
per-step tasks called it — so a single session could fire a full queue of
invites regardless of the campaign's `daily_connection_limit` and the
warm-up caps (3–7 invites/day for new accounts).  `_process_leads_session`
and the generic step executor now check the limiter before every action;
when a cap is hit the lead is kept on its step and deferred ~1 day
(visible as a SKIPPED job in the activity feed), and the session continues
with other action types.

### 5. Worker policy (`worker/tasks/campaign_tasks.py`)

When a step fails with the new flags:

- **`session_stale`** → the whole account session stops and the account is
  suspended (`SessionFailureException` policy), instead of silently failing
  every remaining lead against a dead session.
- **`page_load_failed` with a healthy session** → the lead is **not** failed
  permanently: it stays on the same step and is retried after a jittered
  2–6 h delay, up to `MAX_PAGE_LOAD_RETRIES` (3) failed attempts total.
  Only after the retry budget is exhausted is the lead marked FAILED.
- Retry state is persisted in `Lead.next_action_at` (survives worker
  restarts) and recorded in the `CampaignJob` row shown in the UI activity
  feed ("... Retrying automatically.").

## Result

✅ **No false positives**: slow renders get up to 12 s to mount before being
   declared blank
✅ **Progressive recovery**: wait → reload → session health probe → retry
✅ **Truthful errors**: the message now says whether the session is actually
   stale (checked against /feed/) or the failure is transient
✅ **Bounded automatic retries**: transient blank pages retry the step later
   instead of permanently failing the lead
✅ **Stale sessions stop fast**: a dead session suspends the account instead
   of burning the whole lead queue
✅ **Auth walls identified**: authwall/checkpoint redirects are reported
   with the real URL instead of a generic "blank page" message
✅ **Daily caps enforced everywhere**: campaign + warm-up + hard caps apply
   on the account-session path too, so invite bursts no longer trigger
   LinkedIn's throttling
✅ **Shared code**: one recovery path for connect, visit, like, message and
   feed-scroll

## Testing

Unit tests in `tests/test_blank_page_recovery.py` cover the recovery state
machine with a scripted fake page:

```
python3 -m unittest tests.test_blank_page_recovery -v
```

To observe in production:
1. Run a campaign with leads
2. Monitor logs for "Blank page detected" warnings and "🔁 ... retrying at"
   reschedules
3. Check debug screenshots (`connect_blank_page_debug.png`, etc.) when
   screenshots are enabled
4. Verify leads that hit transient blank pages are retried automatically,
   and accounts with dead sessions get suspended promptly

## Notes

- The blank page detection is conservative (text < 100 chars + no app container)
- Reloads remain capped at one per navigation; the feed probe + retry is the
  escalation path, so total navigations per action stay bounded
- The 30-second timeout prevents hanging on slow pages
- Screenshots are only taken when `should_take_screenshots()` returns True
