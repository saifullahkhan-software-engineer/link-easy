# Gmail — setup & how it works

LinkEasy connects users' Gmail mailboxes through Google OAuth so the app can
**check for new mail, read messages and threads, search, manage labels, and
send replies** from inside the app module (beside WhatsApp and LinkedIn).

This page covers:

1. What the integration can do (and the scope it asks for).
2. The Google Cloud setup an operator needs once per deployment.
3. Environment variables & callback URLs (local and production).
4. Common Google Console / OAuth issues.

---

## 1. Capabilities and permission scope

Connected users get a Gmail section in the app:

| Capability | What it means |
| --- | --- |
| Check mail | Manual "Check mail" button plus an automatic live check every 45 s while the Gmail page is open, with unread-count toasts |
| Read email | Full inbox, per-label views, "All mail", search (Gmail query syntax: `from:`, `subject:`, `is:unread`, …), thread reading with text + sandboxed HTML rendering |
| Read threads | One conversation at a time, oldest → newest, with replies |
| Manage labels | Mark read/unread, star, archive, trash/restore, add/remove custom labels |
| Download attachments | Files are proxied through the backend; the browser never sees the OAuth token |
| Send email | Compose and reply (Cc/Bcc, threading headers); always sent **as the connected account** |

Two OAuth scopes are requested — never full access:

| Scope | Why |
| --- | --- |
| `https://www.googleapis.com/auth/gmail.modify` | Read/search messages and threads; manage labels, read-state, archive and trash. **Does not** allow permanent deletion |
| `https://www.googleapis.com/auth/gmail.send` | Compose and send |

`https://mail.google.com/` (full access) is a **restricted** scope requiring
Google's security assessment and is never requested. `gmail.modify` alone is
not enough to send, which is why `gmail.send` is requested alongside it.

> Google classifies the Gmail `gmail.*` scopes as **sensitive** scopes.
> While your app is in "Testing" mode only test accounts can connect. Before
> real users connect, complete OAuth verification:
> * [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
> * [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)

**Sending limits**: Google applies per-account sending limits and restricts
accounts that send excessive or unwanted email. Gmail is for your own
outreach/replies — LinkEasy does not implement bulk cold-email automation on
top of it, and the send endpoint is rate-limited server-side too
(`gmail:send`: 60/hour per user).

### Personal Gmail vs Google Workspace

Both work through the same OAuth flow:

* A normal **personal `@gmail.com`** account needs no paid Google Workspace
  subscription.
* A **Workspace** address (`you@yourcompany.com`) works the same way; the
  Google Cloud OAuth app is what your instance operator configures, not the
  end user's mailbox.

---

## 2. Google Cloud setup (one time, per deployment)

This is the operator's job; regular users just click **Connect Gmail** in the
app.

1. Go to <https://console.cloud.google.com/> and open (or create) the project
   for this deployment.
2. **Enable the Gmail API**
   — *APIs & Services → Library → "Gmail API" → Enable*.
3. **Configure the OAuth consent screen**
   — *APIs & Services → OAuth consent screen*.
   * User type: **External** (your users are people outside your org).
   * App name, support email, developer contact email.
   * **Audience → Testing**: add every test account (`@gmail.com` works) that
     will connect before verification.
   * Scopes: the console offers "Gmail API" scope groups; `gmail.modify` and
     `gmail.send` are enough. If a user is later shown the consent screen
     without `gmail.send`, sending fails with "reconnect Gmail".
4. **Create an OAuth web client**
   — *APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application*.
   * Authorized redirect URIs: see the table below — **must match exactly**,
     including `http` vs `https` and the trailing path.
5. Put the **Client ID** and **Client secret** into the deployment's
   environment as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (plus the two
   optional variables). Restart the API. `GET /health` stops listing them as
   missing and `GET /api/v1/gmail/status` reports `configured: true`.

### Callback (redirect) URIs

The callback path implemented by the backend is:

```
https://YOUR-BACKEND-DOMAIN.com/api/v1/gmail/callback
```

For local development Google accepts localhost URIs (unlike TikTok):

```
http://localhost:8000/api/v1/gmail/callback
```

| Variable | Default when empty |
| --- | --- |
| `GOOGLE_REDIRECT_URI` | `<PUBLIC_API_URL or request origin>/api/v1/gmail/callback` |
| `GOOGLE_OAUTH_RETURN_URL` | first `BACKEND_CORS_ORIGINS` origin + `/app/gmail` |

The callback itself always ends by redirecting the browser to
`GOOGLE_OAUTH_RETURN_URL` with `?connected=1` or `?error=…`.

---

## 3. Environment variables

```env
# Required to enable Gmail:
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Recommended in production (must be registered in the console):
GOOGLE_REDIRECT_URI=https://api.example.com/api/v1/gmail/callback

# Optional — where the browser lands after connect:
GOOGLE_OAUTH_RETURN_URL=https://app.example.com/app/gmail
```

These are read by the API process only. The Celery worker does not talk to
Gmail, but docker-compose mirrors them to both services for simplicity.

### Where do tokens live?

`gmail_connections` (one row per LinkEasy user):

* `encrypted_access_token` / `encrypted_refresh_token` — AES-256-GCM
  ciphertext via `core.security` (never plaintext; rotating
  `CREDENTIAL_ENCRYPTION_KEY` invalidates them → users see "Reconnect Gmail").
* `account_email` — the connected mailbox, unique across users (one mailbox
  cannot be linked to two LinkEasy accounts).
* `granted_scopes`, profile totals and `last_checked_at` for the UI.

Access tokens are refreshed automatically when they near expiry using the
stored refresh token (`access_type=offline` + `prompt=consent` are set on the
auth URL so a refresh token is always issued, even on re-consent). If Google
rejects the refresh (revoked access, password change, …) the API answers
**409 "reconnect Gmail"** and the UI offers a one-click reconnect.

---

## 4. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| "Gmail is not configured on this instance" | `GOOGLE_CLIENT_ID`/`SECRET` empty. Set them and restart. Also visible on `GET /health` (`missing_configuration`) and on the Accounts page ("Needs operator setup"). |
| Google shows "Error 400: redirect_uri_mismatch" | The URI in the console doesn't exactly match the API's callback (`/api/v1/gmail/callback`). Compare the `redirect_uri` query parameter of the consent URL with the console entry. |
| "Access blocked: … not been verified" | App is in Testing mode or verification is pending; only accounts added as test users can connect. |
| Connect succeeds but pages answer "Gmail access was revoked or has expired. Reconnect Gmail." | Token refresh failed — user changed the Google password, revoked the app at myaccount.google.com, or the stored ciphertext no longer decrypts (encryption key rotated). Reconnect from the Gmail page. |
| "Gmail is rate-limiting requests right now" (429) | Google per-user quota. Wait a moment; the UI retries automatically. |
| "That Gmail address is already connected to another LinkEasy account." | A mailbox can be linked once. Disconnect it from the original account first. |
| Sending fails with insufficientPermissions | The account's stored grant lacks `gmail.send` (e.g. an old consent). Reconnect — the flow requests both scopes again. |

### Testing checklist

* Connect a personal `@gmail.com` and confirm the inbox loads with subjects,
  snippets and unread dots; opening a message marks it read.
* Compose a reply and confirm it lands in **Sent** and threads in the mailbox.
* Toggle "Live: on" and send yourself an email — expect the new-unread toast
  within ~45 s.
* Disconnect: the row disappears, the token is revoked at Google, and
  messages in Gmail are untouched.
