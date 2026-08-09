# WhatsApp filter jobs

WhatsApp job scanning now follows the same workflow as Feed Scroll jobs:

1. **WhatsApp Filters** (`/app/whatsapp-scanner`) lists the authenticated user's
   filter jobs.
2. **New Filter** creates a draft with its matching criteria and scan interval.
3. The filter detail page configures the three monitored groups and forwarding
   group, shows the complete criteria, counters, and message results.
4. A filter can be **started**, **paused/resumed**, or **deleted** from the list
   (and started/paused from its detail page).

Filter jobs are stored in `whatsapp_scan_filters` with an owner, name, status,
`next_scan_at`, and pause countdown. Group selections and raw messages carry the
filter id, so multiple filters do not share criteria, counters, or forwarding
state. The database-backed WhatsApp dispatcher queues active filters whose
`next_scan_at` is due, which keeps pause/resume behavior intact across worker
restarts.

The original `/api/v1/whatsapp/filters` and NULL-scoped group rows are retained
for compatibility. Visiting the new list endpoint adopts an existing legacy
singleton filter into the current user's workspace and moves its legacy groups
and messages into that filter.
