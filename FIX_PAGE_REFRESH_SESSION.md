# Fix: Page Refresh Session Handling

## Problem

Refreshing any page while logged in behaved badly because the app decided
auth state purely from *localStorage token presence* and never validated the
session first:

1. **Valid session** → on refresh, protected pages mounted and fired their
   API calls immediately. With an expired access token this meant a burst of
   401s before the interceptor quietly refreshed — any race or page-level
   error handling surfaced as visible errors.
2. **Invalid session** (both tokens expired) → pages mounted, every API call
   401'd, the refresh failed, and the user was thrown at `/login` by a hard
   `window.location.assign()` with **no explanation** — no popup, no message,
   just a confusing bounce after a flash of page errors.

## Requested behaviour

- On page refresh, check whether the user session is valid.
- **Not valid** → show the login popup and take the user to the login page.
- **Valid** → refresh renders normally with **no error**.

## Solution

### 1. Boot-time session validation (`src/context/AuthContext.jsx`)

`AuthProvider` now validates the session on every app mount (i.e. every page
refresh), *before* protected pages are allowed to render:

- **Access token still valid** → nothing to do; the page renders without any
  error.
- **Access token expired, refresh succeeds** (`POST /auth/refresh`) → the
  token is silently renewed and the user stays exactly where they were.
- **Expired and cannot be refreshed** (401/403 from refresh, or no refresh
  token stored) → the session is cleared, the **login popup** is shown, and
  the user is routed to `/login`.
- **Backend unreachable / 5xx** → the session was *not* proven invalid, so it
  is kept; pages surface their normal API errors instead of wrongly bouncing
  the user to the login screen.

While the check is in flight (`isCheckingSession`), `ProtectedRoute` and
`AdminRoute` render a neutral full-page "Restoring your session…" loader, so
no page content flashes and no doomed API calls fire.

### 2. Login popup (`src/components/SessionExpiredDialog.jsx`)

A global modal rendered by `AuthProvider` (reusing the existing `Modal`):
"Session expired — Your session has expired or is no longer valid. Please log
in again…" with a **Log in** button. It appears on top of the login page the
user was routed to, explaining *why* they were signed out instead of leaving
them guessing.

### 3. Mid-session expiry uses the same flow (`src/api/client.js`)

The axios 401 interceptor no longer hard-redirects. On an unrefreshable 401
it clears the session and broadcasts `auth:session-expired`; `AuthProvider`
listens and runs the identical popup + `/login` flow — no jarring full-page
reload. The refresh is now shared single-flight (`refreshSession()`) between
the interceptor and the boot check, so a page refresh never fires two
refreshes at once.

Failure classification (`isDefinitiveAuthFailure`): only 401/403 from the
refresh endpoint or a missing refresh token counts as a dead session.
Network/5xx failures keep the session intact — backend downtime no longer
logs the user out.

### 4. Fixed two-hour session lifetime

`SESSION_EXPIRE_MINUTES` defaults to `120`. Login puts the same
`session_expires_at` claim on both JWTs. Access tokens and refreshed tokens are
capped at that deadline, and the refresh endpoint preserves the original
deadline instead of extending it. The frontend schedules the same deadline
and clears local credentials, shows the expiry dialog, and routes to `/login`
when the two hours are reached, even if the tab has no API traffic.

### 5. Provider order (`src/App.jsx`)

`BrowserRouter` now wraps `AuthProvider` so the auth layer can route to
`/login` with `useNavigate` (soft navigation, popup preserved).

## Files changed

- `core/config.py`, `core/security.py`, `schemas/auth.py`, and
  `api/v1/auth.py` / `api/dependencies.py` — the default two-hour absolute
  session deadline and server-side enforcement.
- `frontend/src/api/client.js` — `isAccessTokenExpired()`, the absolute
  session-deadline helpers, `refreshSession()` single-flight,
  `isDefinitiveAuthFailure()`, and `SESSION_EXPIRED_EVENT` broadcast instead
  of hard redirect.
- `frontend/src/context/AuthContext.jsx` — boot-time validation, the
  two-hour expiry timer, `isCheckingSession`, `sessionExpired` + popup wiring,
  and interceptor event listener.
- `frontend/src/components/SessionExpiredDialog.jsx` — new login popup.
- `frontend/src/components/ProtectedRoute.jsx` / `AdminRoute.jsx` —
  "Restoring your session…" gate while the check runs.
- `frontend/src/App.jsx` — provider order swapped.
- `frontend/smoke-test.mjs` — four regression cases.

## Testing

`npm run smoke` (build + jsdom smoke suite) — regression cases include:

- **page refresh — expired access token is renewed silently, no errors, page
  renders** — expired token at boot → refresh succeeds → the accounts hub
  renders normally, no login screen, no popup.
- **page refresh — invalid session shows the login popup and lands on
  /login** — refresh returns 401 at boot → popup text *and* the login form
  both render.
- **absolute two-hour session deadline** — an already-expired session claim
  logs the user out even when the access token is still valid and no API call
  is made.
- **mid-session unrefreshable 401 — login popup and redirect to /login** —
  an API call 401s with a dead refresh token → interceptor path produces the
  same popup + redirect.

All previously-passing cases still pass. (Three cases — `admin accounts`,
both `whatsapp connect page` cases — fail identically on the base commit and
are unrelated pre-existing issues.)

## Notes

- The expiry check is decode-only, matching the existing
  `decodeToken`/`getUserRoles` philosophy: the backend remains the authority;
  the local check only decides whether a refresh is worth attempting *before*
  rendering.
- Tokens without an `exp` claim are treated as not-locally-expired and left
  to the normal 401 → refresh flow. Refresh tokens from before the absolute
  session claim was introduced are rejected so they cannot bypass the
  two-hour limit; those users simply log in again.
- Manual logout, signup, and unauthenticated browsing are untouched.
