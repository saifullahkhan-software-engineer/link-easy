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
checkpoint. If WhatsApp has moved the checkpoint outside its rendered window,
the scanner considers only the configured newest bounded window and checks
persisted raw-message ids to discard overlap.

Editing a filter reconciles unchanged monitored-group rows instead of deleting
and recreating them, so their checkpoint ids survive configuration saves. This
prevents a normal edit from resetting scan history and pulling already-scanned
messages again.

The original `/api/v1/whatsapp/filters` and NULL-scoped group rows are retained
for compatibility. Visiting the new list endpoint adopts an existing legacy
singleton filter into the current user's workspace and moves its legacy groups
and messages into that filter.
