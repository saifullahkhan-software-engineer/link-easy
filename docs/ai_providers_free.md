# Free AI providers for the assistant (no credit card)

The assistant talks to any OpenAI-compatible `/chat/completions` endpoint, so
switching providers is an environment change — never a code change. These
options all work **without adding a card** (free tiers as of 2026; limits
change, so check the linked console if a key stops working):

| Provider | Where to get a free key | Notes |
|---|---|---|
| **Groq** (default) | https://console.groq.com → API Keys | Very fast; generous daily limits (e.g. 14,400 req/day on smaller models). No card for the free tier. |
| **Google AI Studio (Gemini)** | https://aistudio.google.com → Get API key | Free quota with a Google account; good fallback next to Groq. |
| **Cerebras** | https://cloud.cerebras.ai → API Keys | Free tier, no card; OpenAI-compatible. |
| **OpenRouter** | https://openrouter.ai → Keys | Many `:free`-suffixed models cost nothing (e.g. `meta-llama/llama-3.3-70b-instruct:free`). ~50 free req/day on some models. |
| **Pollinations** | none needed — keyless | No signup at all. Only a last-resort fallback: tool calling is unreliable there, so it answers plain text but can't check inboxes. |

Avoid Together AI as a "free" pick — it needs a card / minimum spend.

## Recommended setup (primary + fallbacks)

```bash
# Primary: Groq (or leave AI_ASSISTANT_* empty to reuse GROQ_API_KEY)
AI_ASSISTANT_PROVIDER=groq
AI_ASSISTANT_API_KEY=gsk_...

# Fallbacks, tried in order when the primary is rate-limited or erroring:
AI_ASSISTANT_FALLBACKS=gemini,cerebras
AI_ASSISTANT_GEMINI_API_KEY=AIza...
AI_ASSISTANT_CEREBRAS_API_KEY=csk-...
```

Optional per-fallback models (defaults are the presets in
`services/ai/assistant/providers.py`):

```bash
AI_ASSISTANT_GEMINI_MODEL=gemini-2.0-flash
AI_ASSISTANT_CEREBRAS_MODEL=llama-3.3-70b
AI_ASSISTANT_OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
```

Keyless last resort (plain answers only, no tools):

```bash
AI_ASSISTANT_FALLBACKS=gemini,pollinations
```

## How failover behaves

- Each model call walks the chain: primary first, then fallbacks in order.
- Failover triggers on rate limits and provider errors (HTTP 429/502/504);
  a rejected primary key (401) also fails over instead of failing the chat.
- Missing keys are skipped silently — list fallbacks freely; only configured
  ones are tried. The chain is re-walked every tool round, so a recovered
  primary is picked up again mid-conversation.
- Keys are backend-only: read at request time, sent only in the
  `Authorization` header, never logged or returned to the browser.

## Urdu, voice, and "better APIs"

No extra model or library is needed for Urdu: the browser's speech
recognition/TTS already ships Urdu (`ur-PK`) voices in Chrome/Edge, and the
assistant mirrors whatever language you write in (English, Urdu script, or
Roman Urdu). If free-tier quality ever feels weak, the fix is the same chain
above — point the primary at Gemini 2.5 Flash or a larger OpenRouter free
model instead of adding anything to the stack.
