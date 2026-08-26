# Connecting LinkedIn with an imported session cookie

## The problem

`POST /api/v1/linkedin/account` drives LinkedIn's real sign-in form from the
server with Playwright. From a datacenter IP — Railway, and essentially every
hosted platform — LinkedIn treats that sign-in as suspicious far more often
than a home connection, and answers with a CAPTCHA or a
`/checkpoint/challenge`. That surfaces as `LinkedInSessionStatus.CAPTCHA` /
`CHECKPOINT` and the account simply cannot be connected.

The complete fix is a residential per-account proxy (the `proxy_*` columns on
`LinkedInAccount`). Until those IPs exist, cookie import removes the worst
part of the problem.

## The approach

The user signs in to LinkedIn **in their own browser** — their own IP, their
own device, their own fingerprint — and pastes the resulting `li_at` session
cookie. We inject it into the account's durable Chromium profile and confirm
it lands on the feed.

LinkedIn never sees a login from our server, so the login CAPTCHA never
happens.

```
POST /api/v1/linkedin/account/cookie
{
  "linkedin_email": "user@example.com",
  "session_cookie": "AQEDAT...",
  "label": "Work account"
}
```

### What it does NOT fix

Requests still egress from the server's IP. LinkedIn can see a session that
was created in Lahore now being used from a datacenter and may still raise a
checkpoint — the endpoint returns a message that says exactly this rather than
a misleading "wrong password". Cookie import is a **significant reduction in
failure rate, not a guarantee**. Sticky proxies remain the real fix.

## Accepted input formats

`automation/cookie_import.parse_cookie_input` auto-detects all of these, so
users never have to pick a format:

| Format | Example |
|---|---|
| Bare value | `AQEDAT...` |
| Name=value | `li_at=AQEDAT...` |
| Cookie header | `li_at=AQEDAT...; JSESSIONID="ajax:123"; lang=v=2` |
| JSON array | `[{"name":"li_at","value":"AQEDAT...","domain":".linkedin.com"}]` |
| JSON object | `{"cookies":[...]}` |
| Flat map | `{"li_at":"AQEDAT..."}` |

Only relevant cookies are kept (`li_at`, `JSESSIONID`, `liap`, `lang`,
`bcookie`, `bscookie`, `li_gc`, `li_mc`, `lidc`). Everything else in a paste —
analytics cookies, cookies from other domains in a full-browser export — is
discarded so unrelated tracking state never enters the profile.

`li_at` is the session token and is required. `JSESSIONID` is kept
non-`httpOnly` on purpose: LinkedIn's own JavaScript reads it to build the
CSRF header for its internal XHR API.

## Data model

| Column | Meaning |
|---|---|
| `auth_method` | `"password"` or `"cookie"` |
| `encrypted_password` | now **nullable** — NULL for cookie accounts |

Migration `e1a4b7c9d2f3_linkedin_cookie_auth` adds `auth_method` (defaulting
existing rows to `password`) and relaxes the `encrypted_password` NOT NULL
constraint. It is idempotent and safe to re-run.

### Consequence: no automatic relogin

The credential-relogin fallback in `POST /account/verify-session` decrypts
`encrypted_password` when a session expires. A cookie account has no password,
so that path is explicitly skipped — it returns `FAILED` with "your imported
session has expired, paste a fresh cookie" instead of crashing on
`decrypt_credential(None)`.

Setting a password later via `PATCH /account` flips `auth_method` back to
`password` and re-enables automatic relogin.

## Session lifetime

`li_at` is typically valid for up to a year, but it dies early if:

* the user logs out of LinkedIn in the browser they copied it from
  (this revokes the token — tell users to just close the tab instead);
* LinkedIn forces a re-auth after a password change or security event;
* the session is challenged from our IP and the user does not clear it.

When that happens the next `verify-session` marks the account `FAILED` and the
user re-imports.

## Security notes

* The cookie is a **live credential** — equivalent to a logged-in session.
  It is written only into the account's `0o700` profile directory, and is
  never logged. `CookieImportError` messages deliberately never echo the
  pasted value.
* Only cookie **names** are logged, never values.
* The frontend clears the textarea state immediately after a successful
  import.
* The cookie is never stored in the database — unlike a password there is no
  encrypted column for it. The profile directory is the only place it lives,
  which is why that volume must be treated as secret material.

## Testing

```bash
pytest tests/test_linkedin_cookie_import.py    # parser: formats + rejections
pytest tests/test_linkedin_cookie_connect.py   # endpoint + relogin guard
```
