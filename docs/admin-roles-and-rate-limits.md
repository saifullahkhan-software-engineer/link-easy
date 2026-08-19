# Admin roles, settings, and database rate limits

Everything here is self-serve: you are the only operator, so the goal is that
you can grant yourself admin with one SQL statement and do the rest from the
dashboard.

---

## 1. Grant yourself both roles (the SQL you asked for)

Roles live in a proper join table (`user_roles`), so one person can hold
**both** `admin` and `customer` at the same time — that is what makes the two
buttons appear on the landing page.

Replace `you@example.com` and run it against your production database.

```sql
-- Grant BOTH roles to yourself (admin + customer).
INSERT INTO user_roles (user_email, role_id)
SELECT 'you@example.com', r.id
FROM roles r
WHERE r.name IN ('admin', 'customer')
ON CONFLICT (user_email, role_id) DO NOTHING;

-- Keep the denormalised users.role column in sync (legacy checks read it).
UPDATE users SET role = 'admin' WHERE email = 'you@example.com';
```

### Make somebody an admin

```sql
INSERT INTO user_roles (user_email, role_id)
SELECT 'teammate@example.com', id FROM roles WHERE name = 'admin'
ON CONFLICT (user_email, role_id) DO NOTHING;

UPDATE users SET role = 'admin' WHERE email = 'teammate@example.com';
```

### Revoke admin (leaving normal app access intact)

```sql
DELETE FROM user_roles
WHERE user_email = 'teammate@example.com'
  AND role_id = (SELECT id FROM roles WHERE name = 'admin');

-- Ensure they still have the customer role, then downgrade the cached column.
INSERT INTO user_roles (user_email, role_id)
SELECT 'teammate@example.com', id FROM roles WHERE name = 'customer'
ON CONFLICT (user_email, role_id) DO NOTHING;

UPDATE users SET role = 'customer' WHERE email = 'teammate@example.com';
```

### Check who has what

```sql
SELECT u.email,
       u.role AS cached_role,
       COALESCE(string_agg(r.name, ', ' ORDER BY r.name), '(none)') AS roles
FROM users u
LEFT JOIN user_roles ur ON ur.user_email = u.email
LEFT JOIN roles r       ON r.id = ur.role_id
GROUP BY u.email, u.role
ORDER BY u.email;
```

> After changing roles the user must **log in again** (or let the app refresh
> the token) — roles are baked into the JWT as a `roles` claim and re-read on
> every refresh.

Once your roles are set you never need SQL again: the Admin Dashboard has
role toggles per user.

---

## 2. Turning on enforcement (important)

The backend ships with `ADMIN_API_ENFORCED=false`. In that **bootstrap mode**
every signed-in user can reach the admin screens — otherwise nobody could
assign the very first admin.

After you have granted yourself `admin`, set:

```bash
ADMIN_API_ENFORCED=true
```

From then on `/api/v1/admin/*` returns **403** for non-admins and the Admin
Dashboard button disappears for customers.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ADMIN_API_ENFORCED` | `false` | Enforce admin-only access to `/api/v1/admin/*` |
| `RATE_LIMIT_ENABLED` | `true`  | Master switch for database rate limiting |

---

## 3. Rate limiting (PostgreSQL, not Redis)

Redis is busy with the job queues, so limits are counted in Postgres in the
`rate_limit_counters` table using a fixed window and a single atomic
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`. One statement per check means
concurrent requests cannot both read a stale count — verified with 50
simultaneous requests against a limit of 10, where exactly 10 were allowed.

The limiter **fails open**: if the table or connection is unavailable the
request is allowed and a warning is logged. A broken limiter must never lock
users out of the product.

### Buckets enforced today

| Bucket | Default | Endpoint |
| --- | --- | --- |
| `auth:register` | 5 / hour | `POST /api/v1/auth/register` |
| `auth:verify-email` | 10 / 15 min | `POST /api/v1/auth/verify-email` |
| `auth:resend-verification` | 5 / hour | `POST /api/v1/auth/resend-verification` |
| `auth:login` | 10 / 5 min | `POST /api/v1/auth/login` |
| `auth:forgot-password` | 5 / hour | `POST /api/v1/auth/forgot-password` |
| `auth:reset-password` | 10 / hour | `POST /api/v1/auth/reset-password` |
| `profile:scan` | 20 / hour | `POST /api/v1/linkedin/profile/scan` |
| `live:start` | 10 / hour | `POST /api/v1/whatsapp/live/start` |

A throttled request gets **429** plus `Retry-After`, `X-RateLimit-Limit`, and
`X-RateLimit-Remaining` headers.

Callers are identified as `user:<email>` when authenticated, otherwise
`ip:<address>` (honouring `X-Forwarded-For`, so put the real client IP there at
your proxy).

### Inspecting and clearing counters by hand

```sql
-- Who is currently being throttled?
SELECT identity, bucket, request_count, window_started_at
FROM rate_limit_counters
WHERE window_started_at > now() - interval '1 hour'
ORDER BY request_count DESC;

-- Unblock one person immediately.
DELETE FROM rate_limit_counters WHERE identity = 'user:someone@example.com';
```

Old rows are pruned automatically, so the table does not grow forever.

---

## 4. Campaign parameters, jobs, and limits

Editable from the Admin Dashboard and stored in `app_settings`. Values are
validated server-side and **clamped to safe maximums** — the daily action caps
mirror the worker's hard caps, because exceeding them is what gets LinkedIn
accounts flagged.

| Key | Default | Max |
| --- | --- | --- |
| `campaign.daily_connection_limit` | 15 | 15 |
| `campaign.daily_message_limit` | 20 | 20 |
| `campaign.daily_visit_limit` | 80 | 80 |
| `campaign.daily_like_limit` | 30 | 30 |
| `campaign.min_delay_seconds` | 45 | 600 |
| `campaign.max_delay_seconds` | 180 | 3600 |
| `jobs.max_actions_per_session` | 20 | 100 |
| `jobs.max_concurrent_browsers` | 2 | 10 |
| `jobs.whatsapp_forward_delay_seconds` | 10 | 300 |
| `jobs.feed_scroll_max_posts` | 40 | 200 |
| `rate_limit.auth:login.max_requests` | 10 | 1000 |
| `rate_limit.auth:login.window_seconds` | 300 | 86400 |
| `rate_limit.profile:scan.max_requests` | 20 | 1000 |
| `rate_limit.profile:scan.window_seconds` | 3600 | 86400 |

`min_delay_seconds` is additionally checked against `max_delay_seconds`.

---

## 5. Admin API reference

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/admin/me` | Caller's roles (any signed-in user — drives the UI) |
| GET | `/api/v1/admin/overview` | Users, accounts, and jobs summary |
| GET | `/api/v1/admin/users?q=&limit=` | User list with roles and usage |
| PUT | `/api/v1/admin/users/{email}/roles` | Assign roles: `{"roles":["admin","customer"]}` |
| GET | `/api/v1/admin/settings` | Settings with bounds and descriptions |
| PUT | `/api/v1/admin/settings` | Update: `{"values":{"key":value}}` |
| GET | `/api/v1/admin/rate-limits` | Live counters and effective rules |
| POST | `/api/v1/admin/rate-limits/reset?identity=&bucket=` | Clear counters |

---

## 6. Migration

`d7f3a1b9c2e4` creates `roles`, `user_roles`, `app_settings`, and
`rate_limit_counters`, seeds the two roles, and backfills `user_roles` from the
existing `users.role` column — so current admins keep working with no manual
step. It is guarded by table-existence checks and is safe to re-run.

```bash
alembic upgrade head
```
