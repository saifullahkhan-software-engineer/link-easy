# Durable campaign scheduling

Campaign step delays are stored in `leads.next_action_at`. The value is calculated from the **next** campaign step's `delay_hours` when the current action succeeds. This makes the database, rather than a Celery worker's in-memory ETA queue, the source of truth for scheduled work.

Celery Beat runs `tasks.dispatch_due_account_sessions` every minute. It finds active campaigns with an initial lead or a lead whose `next_action_at` has passed, then queues an immediate account session. The account-level Redis lock prevents overlapping browser sessions for one LinkedIn account.

## Local Windows commands

Start Redis and the API as usual. Start the worker and Beat in separate PowerShell windows:

```powershell
python -m celery -A worker.celery_app worker --loglevel=info --pool=solo
python -m celery -A worker.celery_app beat --loglevel=info
```

For local testing only, Beat may be embedded in the solo worker:

```powershell
python -m celery -A worker.celery_app worker --loglevel=info --pool=solo -B
```

Do not rely on one-hour `eta`/`countdown` tasks for campaign steps. If the worker is restarted, Beat will redispatch overdue actions from `next_action_at` on its next run.
