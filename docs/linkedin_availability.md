# LinkedIn availability flag

LinkedIn automation ships **disabled** (`LINKEDIN_ENABLED=false`). This document
explains why, what exactly is gated, and how to turn it back on.

## Why it is off

LinkedIn treats sign-ins from datacenter IP ranges as suspicious. Driving the
login form — or restoring a saved session — from a hosted platform (Railway,
Fly, any cloud VM) is met with a CAPTCHA or a redirect to
`/checkpoint/challenge` on a large share of attempts. The practical result is
that accounts either never finish connecting, or connect and then get
challenged mid-campaign, which is worse: work stops halfway and the account
picks up a risk signal.

This is an **infrastructure** problem, not a bug in the automation. The fix is
to give every LinkedIn account its own sticky **residential proxy**, so traffic
originates from a consumer IP in a plausible location. The data model already
supports this: `LinkedInAccount.proxy_host` / `proxy_port` / `proxy_username` /
`proxy_password` exist and are read by `automation/browser.py` when launching a
profile. What is missing is a proxy subscription to populate them.

Residential proxies are a recurring per-account cost, so this is deferred until
the product is monetised or has enough users to justify the spend. Rather than
present a connect form that mostly fails, the feature is gated behind a flag
and the UI explains the situation.

**WhatsApp automation is unaffected and remains fully available.** WhatsApp Web
pairs by QR code rather than by password, so it does not penalise datacenter
IPs the same way.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `LINKEDIN_ENABLED` | `false` | Master switch for all LinkedIn automation. |
| `LINKEDIN_DISABLED_MESSAGE` | see `core/config.py` | User-facing copy shown wherever LinkedIn is gated. Override to add an ETA. |

Both are ordinary environment variables — flipping them needs a restart, not a
code change or a frontend rebuild.

## What the flag gates

The backend gate is `require_linkedin_enabled()` in `api/v1/linkedin.py`. It is
attached as a **route dependency**, so it runs before the handler and before
auth, and returns **503 Service Unavailable** with `LINKEDIN_DISABLED_MESSAGE`
as the `detail`.

503 is deliberate: it means "temporarily unavailable, try later", and — unlike
401/403 — it is not treated by the frontend's axios interceptor as an expired
LinkEasy session, so it does not bounce the user to `/login`.

Gated (16 routes) — everything that would open a Chromium profile against
linkedin.com:

- `POST /api/v1/linkedin/account`, `/account/verify`, `/account/verify-session`
- `POST /api/v1/linkedin/live/start`, `/chats/open`, `/chats/close`, `/messages/send`
- `GET  /api/v1/linkedin/live/status`, `/chats`, `/messages`
- `POST /api/v1/linkedin/profile/scan`
- `POST /api/v1/campaigns/{campaign_id}/start`, `/restart`
- `POST /api/v1/feed-scroll/jobs/{job_id}/scan`, `/activate`

Intentionally **not** gated:

- `GET` / `PATCH` / `DELETE /api/v1/linkedin/account` — users who connected
  before the gate must still be able to see and remove their account.
- `POST /api/v1/linkedin/live/stop` — lives on a separate ungated `stop_router`.
  A browser started while the flag was on must always be stoppable, otherwise
  its profile lock is held for the full 30-minute timeout.
- `POST /api/v1/campaigns/{id}/pause`, `POST /api/v1/feed-scroll/jobs/{id}/pause`
  — pausing is a way *out* of a running job, never a way to start one.
- All read endpoints (campaign status, leads, results) — historical data stays
  visible.
- Every WhatsApp route.

## Frontend behaviour

`GET /api/v1/features` (unauthenticated) reports the flags:

```json
{
  "linkedin": { "enabled": false, "message": "…" },
  "whatsapp": { "enabled": true,  "message": null }
}
```

- `frontend/src/hooks/useFeatures.js` fetches this once per tab and caches it.
  It fails **closed** for LinkedIn and **open** for WhatsApp: if the endpoint is
  unreachable, LinkedIn stays hidden rather than showing a form that would 503.
- `LinkedInUnavailableNotice.jsx` is the shared explanation card.
- `LinkedInFeatureRoute.jsx` wraps `/app/linkedin-live` and
  `/app/linkedin-profile`. Guarding at the route level means those pages never
  mount while the feature is off, so their status/chat/message polling
  intervals never start. It renders a spinner (not the notice) while the flags
  are still loading, so a slow request cannot flash "coming soon" on a
  deployment where LinkedIn is actually enabled.
- `LinkedInAccountPage.jsx` replaces the connect form with the notice, but
  keeps a "Previously connected account" card with a working Disconnect button.
- The sidebar badges LinkedIn-only tools as **Paused**.

## Re-enabling

1. Buy residential proxies (one sticky endpoint per LinkedIn account).
2. Populate `proxy_host` / `proxy_port` / `proxy_username` / `proxy_password` on
   each `LinkedInAccount`.
3. Set `LINKEDIN_ENABLED=true` and restart.

No code change is required. Verify with:

```bash
curl -s https://<host>/api/v1/features | jq .linkedin.enabled   # → true
```

## Tests

`tests/test_linkedin_feature_gate.py` covers the dependency's behaviour in both
states, asserts the gated/ungated route inventory above by introspecting
`main.app.routes`, and checks the `/api/v1/features` payload. Because the
inventory is asserted, adding a new browser-driving LinkedIn route without
gating it will fail the suite.
