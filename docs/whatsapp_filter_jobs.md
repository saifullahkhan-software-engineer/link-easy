# WhatsApp filter jobs

WhatsApp job scanning follows the same multi-job workflow as Feed Scroll jobs:

1. **WhatsApp Filters** (`/app/whatsapp-scanner`) lists the authenticated user's
   independent filter jobs.
2. **New Filter** creates a draft with its matching criteria, scan interval, and
   per-group latest-message limit.
3. **Edit Filter** (`/app/whatsapp-scanner/jobs/{id}/edit`) is the only page that
   edits criteria and group selection. A filter may monitor **one to three**
   groups and has one forwarding group.
4. The filter **detail page** is read-only configuration plus operational data:
   lifecycle controls, counters, saved message results, and each monitored
   group's incremental scan checkpoint.
5. A filter can be **started**, **paused/resumed**, or **deleted** from the list
   (and started/paused from its detail page).

Filter jobs are stored in `whatsapp_scan_filters` with an owner, name, status,
`next_scan_at`, pause countdown, and `latest_messages_limit` (1–100, default 20).
Group selections and raw messages carry the filter id, so multiple filters do
not share criteria, counters, forwarding state, or scan checkpoints. The
database-backed WhatsApp dispatcher queues active filters whose `next_scan_at`
is due, which keeps pause/resume behavior intact across worker restarts.

## Incremental message checkpoints

Every `whatsapp_monitored_groups` row stores `last_message_id`,
`last_message_timestamp`, and `last_checked_at`. The scanner returns messages
newest-first and persists the newest observed WhatsApp message id as that
group's high-water mark. Later scans inspect only messages after a visible
checkpoint. Because WhatsApp virtualizes the conversation DOM, the scraper
reads the current window and scrolls upward in bounded overlapping steps until
it has the configured number of messages or can see the checkpoint. If
WhatsApp has moved the checkpoint outside the available history, the scanner
uses only the configured newest bounded window and checks persisted raw-message
ids to discard overlap.

Editing a filter reconciles unchanged monitored-group rows instead of deleting
and recreating them, so their checkpoint ids survive configuration saves. This
prevents a normal edit from resetting scan history and pulling already-scanned
messages again.

The original `/api/v1/whatsapp/filters` and NULL-scoped group rows are retained
for compatibility. Visiting the new list endpoint adopts an existing legacy
singleton filter into the current user's workspace and moves its legacy groups
and messages into that filter.

## Anti-blocking forward pacing

WhatsApp flags accounts that send several messages back-to-back — when a scan
matches multiple jobs at once and forwards them all at the same time, the sends
can fail or trip WhatsApp's spam/blocking filter. To prevent this, the
forwarding pass in `worker/tasks/whatsapp_tasks.py` waits between every
consecutive forward:

- `FORWARD_DELAY_SECONDS` (default **10 seconds**) is applied before forwarding
  each matched message after the first, so forwards in a single scan run are
  never simultaneous.
- The value is configurable via the `WHATSAPP_FORWARD_DELAY_SECONDS` env var
  (see `core/config.py`), which the Celery worker reads at startup.
- Pacing applies per scan run: message 1 forwards immediately, then each
  following match waits 10 seconds, e.g. 3 matches → 2 pauses → ~20s of pacing.

A log line is emitted before each pause
(`⏳ Waiting 10s before forwarding the next message ...`) so the pacing is
visible in the worker logs.

