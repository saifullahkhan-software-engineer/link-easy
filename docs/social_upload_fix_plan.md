# Fix plan — Instagram Reels "not publicly reachable" & YouTube Shorts SSL EOF

Two publish failures, two different root causes. This document records the
diagnosis and the change plan.

> **Status update (after implementation):** the user narrowed the scope to two
> deliverables, which are now implemented and tested:
>
> 1. **Instagram Reels publish via direct upload for both "publish now" and
>    scheduled posts** — the worker always hands the stored file to the service,
>    the service centralizes the flow decision (direct upload by default, URL
>    flow only as a fallback), transient transport errors are retried, error
>    messages name the fix, and a misconfigured instance warns at startup.
> 2. **Facebook groups are asked for on every upload** — the group picker on the
>    upload page is no longer hidden behind "Facebook is a publish target".
>
> The YouTube Shorts `SSL: UNEXPECTED_EOF_WHILE_READING` fix (§2) remains
> planned but was deprioritized and is **not yet implemented**.

---

## 1. Instagram Reels — "video URL is not publicly reachable"

### Observed error

> Instagram downloads the video from a public URL, but this instance's video
> URL is not publicly reachable (`http://localhost:8000/uploads/soc…`)

### Root cause

There are two ways to hand a Reel to Meta (both already implemented in
`services/social/instagram.py`):

1. **Direct (resumable) upload** — the worker streams the stored file to
   `rupload.facebook.com`. No public URL needed. This is the default
   (`INSTAGRAM_DIRECT_UPLOAD=true`).
2. **URL flow** — Meta's crawler downloads `video_url`. Requires a public URL.

The failure text the user saw is produced only when the **URL flow** runs
against a non-public URL. Two code paths emit it:

* `worker/tasks/social_scheduler_tasks.py` — pre-check when
  `INSTAGRAM_DIRECT_UPLOAD` is **false** and `video_url` is not public:
  *"Instagram direct upload is disabled on this instance and the video URL is
  not publicly reachable (…). Either keep INSTAGRAM_DIRECT_UPLOAD enabled or
  set PUBLIC_API_URL."*
* `services/social/instagram.py::publish_reel` — the fall-through when neither
  path can work: *"…this instance has no publicly reachable video URL — set
  PUBLIC_API_URL or restore the uploaded file."*

So the practical cause is: **direct upload is disabled on this instance AND
`PUBLIC_API_URL` is empty**, so the stored `video_url` was derived from the
request's `Host` header — `http://localhost:8000/...` — which Meta can never
reach.

Why is `video_url` `localhost:8000`? `api/v1/social_scheduler.py::_public_video_url`
falls back to `request.base_url` when `PUBLIC_API_URL` is empty. The app runs
behind a proxy (Vite dev proxy / Railway / a self-host tunnel) that does not
forward the original scheme/host to uvicorn:

* `main.py` has **no** `ProxyHeadersMiddleware`;
* `start.sh` runs uvicorn **without** `--proxy-headers`;
* the Vite dev proxy (`frontend/vite.config.js`) uses `changeOrigin: true`, so
  the backend sees `Host: localhost:8000`.

### Changes

**1a. Trust the proxy's scheme/host when deriving the public URL.**

* `main.py` — add `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware`
  (trusted hosts configurable, default `"*"`), so `request.base_url` reflects
  `X-Forwarded-Proto`/`X-Forwarded-Host` instead of `localhost`.
* `start.sh` / `run_dev_server.py` / `main.py` `uvicorn.run(...)` — add
  `--proxy-headers --forwarded-allow-ips=*` as a belt-and-braces equivalent for
  the non-middleware path.
* `api/v1/social_scheduler.py::_public_video_url` — keep `PUBLIC_API_URL` as the
  override, else derive the base from `X-Forwarded-Proto` + `X-Forwarded-Host`,
  else `request.base_url`. (Same helper feeds `_redirect_uri`.)

This makes a *public* instance derive a real, reachable URL automatically —
no `PUBLIC_API_URL` needed. It deliberately does **not** make `localhost`
magically public; a laptop still needs direct upload.

**1b. Fail fast at schedule time, not publish time.**

The user learns about this hours later, from a failed post. Move the check to
the API so it's caught the moment the post is created or re-queued:

* `api/v1/social_scheduler.py::create_post` and `update_post` (the
  `cancelled/failed → pending` re-queue branch and any `scheduled_at` edit) —
  when `"instagram" in platforms`, `not settings.INSTAGRAM_DIRECT_UPLOAD` and
  `not is_public_video_url(video_url)`, return `HTTP 400` with the same
  actionable text: keep `INSTAGRAM_DIRECT_UPLOAD` enabled, or set
  `PUBLIC_API_URL` to a URL Meta can reach.
* Import `is_public_video_url` from `services.social.instagram` (the worker
  already does).

Decision point: hard `400` (recommended — the post is guaranteed to fail) vs a
soft `warning` field on the response that still lets the user schedule. See §5.

**1c. Startup diagnostic (no more silent misconfiguration).**

* `core/config.py` — add a check to the existing startup/lifespan warning pass
  (alongside `missing_optional_settings()`): if
  `INSTAGRAM_DIRECT_UPLOAD=false` **and** (`PUBLIC_API_URL` empty or
  `is_public_video_url(PUBLIC_API_URL)` false), log a loud warning that
  Instagram Reels will fail. Surface it in `/health` the same way other missing
  settings are surfaced.

**1d. Harden the direct path so disabling it isn't necessary.**

`services/social/instagram.py`:

* Wrap `_create_resumable_container`, `_upload_video_bytes` and the
  `_wait_for_processing` status GET in a small retry loop for transient
  transport errors (`aiohttp.ClientError`, `asyncio.TimeoutError`), ~3 attempts
  with backoff — the current code fails the whole publish on one dropped
  connection.
* When Meta rejects the resumable container because the app isn't eligible
  ("Facebook Login for Business" / app-review), raise a message that names the
  alternative explicitly: *"Direct upload is not available for this app yet —
  complete Meta app review, or set PUBLIC_API_URL and disable direct upload to
  use the URL flow."*

**1e. Docs/copy.**

* `docs/running_locally.md` and `frontend/src/pages/social-scheduler/SettingsPage.jsx`
  ("How publishing works") — only if behavior text changes (e.g., note the
  schedule-time validation).

---

## 2. YouTube Shorts — `[SSL: UNEXPECTED_EOF_WHILE_READING]`

### Root cause

`services/social/youtube.py::upload_short` uploads the whole file in **one**
HTTP request:

```python
media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
return youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
```

`resumable=True` without `chunksize` is a single-shot upload: google-api-python-client
(over its default httplib2 transport) PUTs the entire file in one connection. On
a large video or an unstable link, the connection is reset mid-transfer and the
`ssl.SSLError: UNEXPECTED_EOF_WHILE_READING` propagates uncaught — neither
httplib2 nor the service retries it, and the whole (already-streamed) upload is
lost. There is no `timeout`, no `chunksize`, and no retry anywhere in the path.

### Changes

**2a. Chunked resumable upload.**

In `upload_short`, replace the single `execute()` with a chunked loop:

```python
media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=CHUNK_SIZE)
request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
response = _upload_resumable_with_retry(request)
```

`MediaFileUpload` reads a chunk from a seeked file offset, so a failed chunk is
re-read from the same offset on the next `next_chunk()` — retrying is safe and
only re-sends the failed chunk, not the whole file.

**2b. Retry transient failures with backoff.**

New module-level helper in `services/social/youtube.py`:

```python
def _upload_resumable_with_retry(request, *, max_retries=..., base_delay=..., max_delay=...):
    response = None
    retries = 0
    while response is None:
        try:
            _status, response = request.next_chunk(num_retries=1)  # library-level 5xx retry
            retries = 0
        except Exception as exc:
            if not _is_retryable_upload_error(exc):
                raise
            retries += 1
            if retries > max_retries:
                raise
            time.sleep(min(base_delay * 2 ** (retries - 1), max_delay))
    return response
```

`_is_retryable_upload_error(exc)` returns True only for:

* `googleapiclient.errors.HttpError` with `exc.resp.status` in
  `{408, 429, 500, 502, 503, 504}`;
* `ssl.SSLError` (the reported bug);
* `socket.timeout`, `TimeoutError`, `ConnectionError`;
* `httplib2.HttpLib2Error` / `google.auth.exceptions.TransportError` (guarded
  imports, so the module still loads in minimal test environments).

Everything else (401/403 auth errors, 400 validation, `FileNotFoundError`) is
raised immediately — no pointless retry of auth failures.

**2c. Config knobs.**

* `core/config.py` — add, with safe defaults:
  * `YOUTUBE_UPLOAD_CHUNK_SIZE` (bytes, default `8 * 1024 * 1024`);
  * `YOUTUBE_UPLOAD_MAX_RETRIES` (default `5`);
  * `YOUTUBE_UPLOAD_RETRY_BASE_SECONDS` (default `2.0`).

**2d. Actionable final error.**

When retries are exhausted, raise a message that tells the user it was a
transport reset and what to try:

> `YouTube upload failed after N attempts: the connection to YouTube was reset
> (SSL EOF). This is usually transient — reschedule the post, or the file may be
> too large for the current network.`

The existing `except Exception as e: raise Exception(f"YouTube upload error: {e}")`
wrapper already stringifies the final exception; keep that wrapper and make the
raised message specific.

**2e. Related consideration (note, not part of this fix).**

`worker/tasks/social_scheduler_tasks.py` has `STALE_POSTING_SECONDS` (2 h) —
a post stuck in `posting` longer than that is reset to `pending` by the Beat
dispatcher. A very large Short on a slow uplink could exceed this and get
re-dispatched while the first upload is still running. With chunked uploads the
risk window shrinks (failed chunks retry, no full re-send), but consider raising
`STALE_POSTING_SECONDS` or making it upload-aware as a follow-up.

---

## 3. Files to change

| File | Change |
| --- | --- |
| `services/social/youtube.py` | chunked resumable upload + retry helper + actionable error (core YT fix) |
| `core/config.py` | new `YOUTUBE_UPLOAD_*` settings; Instagram misconfig startup warning |
| `api/v1/social_scheduler.py` | robust `_public_video_url` (forwarded headers); schedule-time 400 for Instagram |
| `main.py` | `ProxyHeadersMiddleware`; pass proxy flags to `uvicorn.run` |
| `start.sh`, `run_dev_server.py` | `--proxy-headers --forwarded-allow-ips=*` |
| `services/social/instagram.py` | retry transient errors in direct upload; clearer "resumable not eligible" message |
| `tests/test_youtube_service.py` | extend the `_Request` double with `next_chunk()`; add retry/success, give-up, and 403 tests |
| `tests/test_instagram_direct_upload.py` | pin new error text / retry behavior |
| `tests/test_social_scheduler_api.py`, `tests/test_social_scheduler_worker.py` | cover the new schedule-time 400 and any changed worker message |
| `docs/running_locally.md`, `frontend/.../SettingsPage.jsx` | copy updates only if behavior text changes |

---

## 4. Test plan

* `tests/test_youtube_service.py`:
  * chunked upload succeeds and returns `video_id` / `video_url`;
  * a scripted `ssl.SSLError` on the first `next_chunk()` then success → still returns the video;
  * retries exhausted → error mentions the attempt count;
  * a 403 `HttpError` → raised immediately (no retry loop).
* `tests/test_instagram_direct_upload.py`:
  * transient `aiohttp.ClientError` on the rupload POST → retried then published;
  * "resumable not eligible" container rejection → new actionable message;
  * existing `is_public_video_url` and fallback tests keep passing.
* `tests/test_social_scheduler_api.py`:
  * `POST /posts` with `instagram` + `INSTAGRAM_DIRECT_UPLOAD=false` + localhost URL → `400` with both fixes named;
  * same call with a public URL or direct upload on → `201`.
* `tests/test_social_scheduler_worker.py`: unchanged except any asserted error text.
* Full suite via `pytest`. (Note: this sandbox lacks `pytest`, `googleapiclient`,
  `httplib2` and the rest of `requirements.txt` — install them first to run the
  tests locally.)

---

## 5. Decisions to confirm before implementing

1. **Why is `INSTAGRAM_DIRECT_UPLOAD` off?** If it was disabled because Meta
   rejected the resumable flow (app not yet approved for *Facebook Login for
   Business*), the fix is the **URL path** (proxy headers + `PUBLIC_API_URL` +
   schedule-time validation). If it's just misconfiguration, re-enabling it is
   the one-line fix and §1d keeps it robust. Both are covered above.
2. **Schedule-time behavior:** hard `400` (recommended) vs soft warning on
   `POST /posts`.
3. **`PUBLIC_API_URL`**: is the instance actually reachable at a public
   hostname (Railway/tunnel), or is this a laptop? The proxy-header fix only
   helps the former; the latter depends on direct upload.

## 6. Suggested order & rollback

1. YouTube chunked-upload + retry (§2) — independent, highest-value.
2. Instagram URL derivation + schedule-time validation (§1a–1b).
3. Instagram direct-upload hardening (§1d).
4. Startup diagnostics + docs (§1c, §1e).
5. Tests updated alongside each step; verify the full suite after 1–2 and at the end.

Each step is independently revertible (no schema/migration changes), so a
rollback is a plain `git revert` of that step's commit.
