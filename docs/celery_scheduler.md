# Durable campaign scheduling

Campaign step delays are stored in `leads.next_action_at`. The value is calculated from the **next** campaign step's `delay_hours` when the current action succeeds. This makes the database, rather than a Celery worker's in-memory ETA queue, the source of truth for scheduled work.

Celery Beat runs the three due-work dispatchers every minute. They only publish work for active database rows:

- `tasks.dispatch_due_account_sessions` finds active campaigns with an initial lead or a lead whose `next_action_at` has passed, then queues an account session.
- `tasks.dispatch_due_feed_scans` finds active feed jobs and claims the next dispatch before publishing it.
- `tasks.dispatch_due_whatsapp_scans` does the same for active WhatsApp filters.

The account-level Redis lock and the database status checks prevent overlapping or stale browser sessions. A paused/deleted campaign or filter is re-checked by the worker and never opens a browser. The old per-lead ETA tasks, legacy WhatsApp connect task, and stalled-lead Beat entry are retired.

## Local Windows commands

Start Redis and the API as usual. Start the worker and Beat in separate PowerShell windows:

```powershell
python -m celery -A worker.celery_app worker --loglevel=info --pool=solo
python -m celery -A worker.celery_app beat --loglevel=info --schedule=/tmp/linkeasy-celerybeat-schedule
```

For local testing only, Beat may be embedded in the solo worker:

```powershell
python -m celery -A worker.celery_app worker --loglevel=info --pool=solo -B
```

Do not rely on one-hour `eta`/`countdown` tasks for campaign steps. If the worker is restarted, Beat will redispatch overdue actions from `next_action_at` on its next run. Beat uses an ephemeral schedule file by default so a deleted task name cannot be resurrected by an old checked-in `celerybeat-schedule` database.
