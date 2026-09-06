# Accounts and Ultimate Inbox

## Navigation

The customer sidebar is now ordered:

1. Accounts
2. Social Scheduler
3. Gmail
4. Ultimate Inbox
   - WhatsApp Chat
   - Instagram Chat
   - Messenger Chat
   - WhatsApp Business Chat (coming soon)
5. LinkedIn
6. WhatsApp Scan

The same navigation is used by the desktop sidebar and mobile drawer.
LinkedIn's existing tools and WhatsApp group scanning are unchanged.

## Connection management

`/app/account` is the connection hub:

- **Main accounts:** WhatsApp, LinkedIn, Gmail. Manage pages are under
  `/app/account/whatsapp`, `/app/account/linkedin`, and `/app/account/gmail`.
- **Socials** (`/app/account#socials`): YouTube, Facebook, Instagram, TikTok,
  and a disabled WhatsApp Business card marked **Coming soon**.
- Social connect, reconnect, disconnect, and admin-only app-credential
  controls were moved from Social Scheduler into the Socials section.
  Existing encrypted tokens and connections are reused, not migrated.
- Facebook's connection is used for Page publishing and Messenger Chat;
  Instagram's connection is used for publishing and Instagram Chat.
- Saved Facebook groups remain manual-share destinations, not connections.

The scheduler's Settings sidebar item and tab are removed. Connection
prompts throughout the scheduler point to Accounts → Socials.

### Compatibility and OAuth

- `/app/whatsapp-live` redirects to `/app/inbox/whatsapp`.
- `/app/social-scheduler/settings` redirects to `/app/account#socials`,
  preserving OAuth result query parameters before they are displayed/cleared.
- Default `SOCIAL_OAUTH_RETURN_URL` now resolves to `/app/account`.
- Default `GOOGLE_OAUTH_RETURN_URL` now resolves to `/app/account/gmail`.
  Old Gmail callbacks landing on `/app/gmail` are forwarded to that manage page.
- Explicit operator-configured return URLs still take precedence. Old return
  URLs continue working, so changing deployment variables is optional.
- Provider callback URLs (`/api/v1/.../callback`) have **not** changed.
- No database migration is required.

## Instagram and Messenger

The new pages call authenticated `/api/v1/inbox/{channel}` routes, with
`channel` equal to `instagram` or `messenger`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/conversations?after=...` | Recent conversations; optional opaque paging cursor |
| GET | `/messages?conversation_id=...` | Latest 20 messages, oldest first |
| POST | `/messages` | Text reply; body contains `conversation_id` and `text` |

The UI offers search of loaded conversations, pagination, manual refresh,
reading, and text replies. Replies are limited to 1,000 UTF-8 bytes. Rich
messages display a note to view the original app; attachment sending,
webhooks, delivery/read receipts, and background synchronization are not
implemented. Sending errors keep the draft. Sends are never automatically
retried, and no success bubble is shown before Meta acknowledges a send.

### Meta setup

Messenger uses the connected **Facebook Page**, not a personal Facebook
inbox. Instagram uses a **Business or Creator account linked to a Page**.
Both use Meta's Facebook Login/Page-token Conversations and Send APIs.
Meta's messaging permissions, Page roles, App Review/Advanced Access, and
reply-window rules apply. [1](https://developers.facebook.com/docs/messenger-platform/conversations/)
[2](https://developers.facebook.com/documentation/business-messaging/messenger-platform/send-messages)

The existing OAuth scope lists now also request `pages_manage_metadata`.
Reconnect existing Facebook/Instagram accounts from Accounts → Socials to
approve the required permissions. A working publishing connection alone
is not proof that Meta has granted messaging access; permission failures
are shown with recovery instructions instead of an empty/fabricated inbox.

### Security

- Connections are looked up by the authenticated user's email and platform.
- Facebook uses that connection's encrypted Page token. Instagram resolves
  a Page token in memory from its stored publishing token and verifies the
  Page is still linked to the connected Instagram account.
- Reading/replying verifies the conversation's participants and its presence
  in the requested channel on the connected Page. The client cannot choose
  an arbitrary recipient, token, or Page.
- Tokens, raw Graph errors, and token-bearing paging URLs never reach the
  browser. Provider requests use a fixed Graph host, bounded timeouts, and
  no redirects. Inbox responses are marked `Cache-Control: no-store`.
- Reads and replies use the existing database rate limiter.
- No messages, extra credentials, or browser sessions are persisted.

WhatsApp continues to use the existing WhatsApp live-chat API. WhatsApp
Business is informational only: it has no active connection API, sending
controls, or publishing target.

## Verification

```bash
cd frontend && npm run smoke
# From the repository root:
.venv/bin/python -m pytest -q tests/test_inbox_api.py \
  tests/test_social_scheduler_api.py tests/test_social_platform_credentials.py \
  tests/test_instagram_service.py tests/test_facebook_service.py \
  tests/test_gmail_api.py tests/test_rbac_and_rate_limits.py
```

UI smoke coverage includes sidebar order, mobile navigation, account sections,
OAuth redirects, disconnect confirmations, provider errors, and text replies.
API tests cover owner/channel isolation, encrypted-token handling, request
validation, permissions, and future-only channels. These tests use local
fixtures/mocked Meta responses; end-to-end verification with real Meta
accounts still requires a configured and approved operator app.
