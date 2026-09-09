# AI Assistant — Architecture & Roadmap

**Status:** Phase 1 implemented (this document is both the plan and the map of what exists).

An in-app AI assistant for LinkEasy that talks to the customer, checks their
messages across every connected channel, answers "where do I…?" questions from
an in-repo knowledge base (RAG), and moves the user around the app.

```
┌─────────────────────────────  Browser  ─────────────────────────────┐
│  AssistantWidget (every /app page)                                  │
│   • text input + 🎤 mic (Web Speech API) + 🔊 read-aloud            │
│   • quick-prompt chips, channel cards, navigation buttons           │
│   • sends current_path ("system overlay") with every message        │
│   • auto-navigates on explicit requests, buttons for suggestions    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ POST /api/v1/assistant/chat
┌──────────────────────────────▼──────────────────────────────────────┐
│  api/v1/assistant.py   (auth, rate limit, ownership)                 │
│  services/ai/assistant/service.py  — the tool-calling loop           │
│    system prompt = identity + current page + user snapshot           │
│                  + retrieved knowledge sections                      │
│    model ↔ tools, up to AI_ASSISTANT_MAX_TOOL_ROUNDS rounds          │
│  services/ai/assistant/providers.py — any OpenAI-compatible API      │
│    (Groq default; OpenAI / OpenRouter / Together / Gemini / custom   │
│     are one settings change — no code)                               │
│  services/ai/assistant/tools.py — the capability registry            │
│    check_new_messages · list_connected_accounts · get_user_overview  │
│    search_app_guide · navigate                                      │
│  services/ai/assistant/knowledge/ — the RAG corpus (markdown)        │
│  services/ai/assistant/context.py — the user-data snapshot           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ reads (never writes) the user's own rows
             ┌─────────────────┼──────────────────┬─────────────────┐
             ▼                 ▼                  ▼                 ▼
   social_platform_    whatsapp sessions +   gmail_connections  assistant_
   connections (Meta)  live browser chats    (OAuth + unread)   conversations
                       (if live session is                        (chat history)
                       running)
```

## The three ingredients the user asked for

1. **"Reads the system overlay"** — every chat request carries the page the
   user is on (`current_path`). The backend resolves it to the page's title
   and purpose from the knowledge base, so the assistant always knows where
   the user is standing and can say things like "you're already on the Gmail
   inbox — tap Compose for a new mail".
2. **"Reads the current user data"** — `context.py` builds a snapshot from the
   caller's own rows: which channels are connected (Instagram, Messenger,
   WhatsApp, Gmail, LinkedIn, socials), whether the WhatsApp live browser is
   running, and counts of their campaigns/jobs/posts. It is injected into the
   system prompt. Only `owner_email == caller` rows are ever read.
3. **"RAG"** — `services/ai/assistant/knowledge/*.md` is a curated corpus of
   every page, route and how-to in the app. Retrieval is keyword-scored
   (title-boosted) — deliberately **no embeddings**, because the corpus is
   small, the provider must stay swappable (Groq has no embeddings endpoint),
   and a wrong retrieval here costs a wrong link, not a wrong answer. The
   corpus is the single source of truth: `navigate` validates paths against
   it, so the model can never invent a route.

## Tools (Phase 1)

| Tool | What it does | Notes |
|---|---|---|
| `check_new_messages` | Fans out to **Instagram**, **Messenger** (Meta Graph, per connected account), **WhatsApp** (live browser if running), **Gmail** (unread count + newest). | Per-channel failure isolation; previews truncated; result also returned to the frontend as `channels` for rich cards. |
| `list_connected_accounts` | The caller's social accounts, Gmail mailboxes, WhatsApp sessions, LinkedIn profiles. | Powers "what have I connected?" |
| `get_user_overview` | Counts: campaigns, feed-scan jobs, scheduled posts, scanner filters, leads. | Powers "give me a status update". |
| `search_app_guide` | RAG retrieval over the knowledge corpus. | The model calls this for how-to / where-is questions. |
| `navigate` | **Not executed by the backend** — recorded as an action for the frontend. Path must exist in the corpus. `explicit: true` (user said "open X") → frontend auto-navigates; otherwise → button. | Auto-nav also respects the user's in-widget toggle. |

Adding a capability later (analytics, weather, …) = one entry in
`tools.py::TOOL_SPECS` + one async function. Nothing else changes: the model
discovers it from the spec, the loop executes it, the frontend needs no update
unless the tool should render a card.

## Provider independence

`providers.py` speaks the OpenAI-compatible `/chat/completions` surface
(messages + tools/tool_calls) over `httpx` — the same wire format Groq,
OpenAI, OpenRouter, Together and Gemini's OpenAI-compat endpoint all accept.
Settings resolution:

```
AI_ASSISTANT_PROVIDER   groq (default) | openai | openrouter | together | gemini | custom
AI_ASSISTANT_API_KEY    falls back to GROQ_API_KEY when provider=groq
AI_ASSISTANT_BASE_URL   falls back to the provider preset, then GROQ_BASE_URL (groq)
AI_ASSISTANT_MODEL      falls back to the provider preset, then GROQ_MODEL (groq)
```

Switching providers is an environment change — the prompts, tool loop and
tests are provider-independent (tests inject a fake provider).

## Security posture (matches the repo's existing rules)

* **Key handling** — the AI key is read from backend settings at request time;
  never in a request/response body or log line.
* **Ownership** — conversations and every tool read are scoped to
  `owner_email == current_user.email`; a foreign conversation id is 404.
* **Untrusted tool data** — message previews fetched from inboxes are
  third-party text. They are JSON-encoded into tool results (quoted data) and
  the system prompt says tool output is data, never instructions; a preview
  saying "ignore your rules" can at worst produce a confusing sentence.
* **Navigate whitelist** — the model's `navigate` path must match a real route
  from the corpus; anything else is rejected and the model is told to retry.
* **Bounds** — message ≤ 2000 chars; ≤ 20 history messages replayed; ≤ 4 tool
  rounds; per-channel timeouts; `assistant:chat` rate bucket (60/hour/user).
* **Privacy** — checking messages necessarily sends a *summary* (counts +
  up to 3 truncated previews per channel) through the configured AI provider,
  the same trust domain as the existing copy parser. Previews are capped at
  140 chars; full message bodies are never sent.

## Phase 2+ roadmap (not yet built — the seams exist)

1. **Analytics tool** — when analytics data lands, add
   `get_analytics(period)` to `tools.py`; the widget renders a stats card from
   the returned `data` block (same pattern as `channels`).
2. **External info (weather/temperature)** — the user mentioned a Google
   temperature API. Add a `get_weather(location)` tool backed by the chosen
   API; a `tools.py` entry is the whole change. "If it is able to read from
   the system" — no system temperature is exposed to a web app by design, so
   an API-backed tool is the correct shape.
3. **Reply drafting** — `draft_reply(channel, conversation_id)` that calls the
   provider with the conversation tail and hands the draft to the inbox pages
   (one-click "use draft"). The inbox reply endpoints already exist.
4. **Proactive checks** — a Celery beat task that runs `check_new_messages`
   for opted-in users and stores a digest; the widget shows an unread badge.
5. **Voice out (TTS)** — the widget already has a read-aloud toggle using
   `speechSynthesis`; upgrading to a provider voice is a frontend-only change.
6. **Embedding retrieval** — if the corpus outgrows keyword retrieval, add an
   embeddings provider behind the same `knowledge.retrieve()` seam.

## Files

| File | Purpose |
|---|---|
| `services/ai/assistant/providers.py` | OpenAI-compatible async chat client + provider resolution |
| `services/ai/assistant/knowledge.py` (+ `knowledge/*.md`) | corpus loader, keyword retrieval, route registry |
| `services/ai/assistant/context.py` | user snapshot (connections, counts, live status) |
| `services/ai/assistant/tools.py` | tool specs + implementations |
| `services/ai/assistant/service.py` | system prompt, tool loop, persistence |
| `models/assistant.py` | `assistant_conversations`, `assistant_messages` |
| `schemas/assistant.py` | request/response models |
| `api/v1/assistant.py` | `/api/v1/assistant/*` routes |
| `frontend/src/components/assistant/AssistantWidget.jsx` | the floating chat widget |
| `frontend/src/hooks/useSpeechRecognition.js` | mic input (Web Speech API) |
| `frontend/src/api/assistant.js` | API client |
| `tests/test_assistant_api.py` | API + tool + provider + knowledge tests |
