# WhatsApp live chat

The live chat feature lets a user click into any conversation and reply
manually through a dedicated Playwright browser. While live, the periodic
[scanner](whatsapp_filter_jobs.md) is paused — they cannot run on the same
WhatsApp account at the same time.

## How it works

```
┌────────── API ──────────┐    ┌───── Playwright process ──────┐
│ POST /live/start         │ ─►│ acquire profile_lock:whatsapp  │
│   (acquires profile lock)│    │ open persistent Chromium on    │
│                          │    │ the SAME user-data-dir as the  │
│ GET  /live/chats         │ ←─│ scanner (browses chat list)    │
│ POST /live/chats/open    │ ←─│ (clicks a chat)                │
│ GET  /live/messages      │ ←─│ (reads visible message DOM)    │
│ POST /live/messages/send ──►│ (clicks send)                  │
│ POST /live/stop          │ ──►│ release profile_lock:whatsapp  │
└──────────────────────────┘    └────────────────────────────────┘
```

* **Profile-shared**: the live browser uses the same persistent user-data-dir
  as the scanner's Celery task. The Redis `profile_lock:whatsapp` ensures
  only one process opens that dir at a time — Chromium's SingletonLock.
  While live, `tasks.check_whatsapp_messages` hits
  `worker.profile_lock.ProfileInUseError` and logs
  `⚠️ WhatsApp profile in use by live chat`, skipping the run.
* **Database gates**: `POST /live/start` returns 400 if there is no
  connected `WhatsAppSession`; the existing connect flow is reused.
* **Polling**: the frontend polls every 5s for status, every 8s for the chat
  list and every 3s while a chat is open.

## API

`api/v1/whatsapp_live.py` — `tags=["whatsapp-live"]`

| Method | Path                                       | Purpose                        |
| ------ | ------------------------------------------ | ------------------------------ |
| POST   | `/api/v1/whatsapp/live/start`              | Acquire the WhatsApp profile    |
| POST   | `/api/v1/whatsapp/live/stop`               | Release the profile            |
| GET    | `/api/v1/whatsapp/live/status`             | `idle`/`starting`/`running`/... |
| GET    | `/api/v1/whatsapp/live/chats?q=&limit=`    | Side panel                     |
| POST   | `/api/v1/whatsapp/live/chats/open`          | Set the active chat            |
| POST   | `/api/v1/whatsapp/live/chats/close`         | Return to the side panel       |
| GET    | `/api/v1/whatsapp/live/messages?limit=`    | Read the open chat              |
| POST   | `/api/v1/whatsapp/live/messages/send`      | Type + click send              |

All endpoints require `Bearer` auth via `get_current_user`. Live status,
chat, and message responses return 409 if the browser isn't currently
running, so the client can show the right empty state.

## Anti-block pacing

Manual sends go through the same `WHATSAPP_FORWARD_DELAY_SECONDS` knob
(`core/config.py`, default **10s**) that paces the scanner's forward
loop. Between consecutive sends the manager waits the configured delay:

* The UI mirrors that delay client-side so the input disables and shows a
  "Sending in 7.3s…" ticker.
* The 10s mark keeps the scan task and the live UI consistent — neither
  path can trip the spam filter by spamming sends.

To tune pacing for load tests, set
`WHATSAPP_FORWARD_DELAY_SECONDS=15` in `docker-compose.yml` and restart
the API process.

## Lifetime / shutdown

`main.py`'s `lifespan` calls `live_browser.stop()` on the way out so the
Playwright driver and the Redis profile lock are released even on
unclean shutdowns.

## Limitations

* **One live session at a time** — the profile lock is global to the
  WhatsApp account, not per-user. Starting live while already live is a
  no-op (the manager is idempotent).
* **No persistence**: the live view is a read-then-render. Closing the
  panel doesn't persist messages; the scanner's table is the
  authoritative history.
* **No SSE / WebSocket**: polling every 3s is sufficient for human typing.
  Switching to SSE is a one-line swap on `whatsappLiveApi.getMessages`
  should real-time be needed later.
