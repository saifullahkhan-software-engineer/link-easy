"""AI Assistant provider — one client, every OpenAI-compatible API.

FILE: services/ai/assistant/providers.py

The assistant never imports an SDK: Groq, OpenAI, OpenRouter, Together and
Gemini's OpenAI-compat endpoint all accept the same ``/chat/completions``
wire format (``messages`` + ``tools``/``tool_calls``), so a plain ``httpx``
POST is the whole client and switching providers is an environment change
(``AI_ASSISTANT_PROVIDER`` + optional base URL/model/key overrides), never a
code change. httpx is already a pinned dependency (async, so the event loop
stays free — no ``asyncio.to_thread`` dance like the synchronous SDKs need).

Errors are raised as :class:`AssistantProviderError` with an ``http_status``
the route maps 1:1 onto its response, so callers never see raw provider
bodies (they can quote the key back, or leak internal topology).

Secrets: the API key is resolved from settings at request time, sent only in
the ``Authorization`` header, and never logged.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Thread-safe counter for round-robin API key selection
_key_index_lock = threading.Lock()
_key_index = 0

# Thread-safe counter for OpenRouter round-robin API key selection
_openrouter_key_index_lock = threading.Lock()
_openrouter_key_index = 0


def _get_next_groq_api_key() -> str:
    """Get the next Groq API key in round-robin order.

    When multiple keys are configured (comma-separated in GROQ_API_KEY),
    this function cycles through them to distribute load and avoid rate limits.
    """
    global _key_index
    keys = settings.groq_api_keys
    if not keys:
        return ""
    
    with _key_index_lock:
        if _key_index >= len(keys):
            _key_index = 0
        key = keys[_key_index]
        _key_index += 1
        return key


def _get_next_openrouter_api_key() -> str:
    """Get the next OpenRouter API key in round-robin order.

    When multiple keys are configured (comma-separated in AI_ASSISTANT_OPENROUTER_API_KEY),
    this function cycles through them to distribute load and avoid rate limits.
    """
    global _openrouter_key_index
    keys = settings.openrouter_api_keys
    if not keys:
        return ""
    
    with _openrouter_key_index_lock:
        if _openrouter_key_index >= len(keys):
            _openrouter_key_index = 0
        key = keys[_openrouter_key_index]
        _openrouter_key_index += 1
        return key

# Base URLs already include their ``/v1``-style suffix; ``chat()`` appends
# ``/chat/completions``. ``custom`` has no preset — the operator must set
# AI_ASSISTANT_BASE_URL (and normally AI_ASSISTANT_MODEL) for it.
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "qwen/qwen3.6-27b"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    # OpenRouter's free models carry a ``:free`` suffix
    # (e.g. ``meta-llama/llama-3.3-70b-instruct:free``) — set AI_ASSISTANT_MODEL
    # (or AI_ASSISTANT_OPENROUTER_MODEL for a fallback) to use one.
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "together": {"base_url": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    # Google's OpenAI-compatible endpoint for Gemini.
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
    },
    # Cerebras' OpenAI-compatible endpoint (free tier, no card).
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
    },
    # Pollinations is keyless (no signup at all). Tool/function calling is
    # unreliable there, so it only fits as a last-resort fallback for plain
    # answers — never as the primary provider.
    "pollinations": {
        "base_url": "https://text.pollinations.ai/openai",
        "model": "openai",
    },
    "custom": {"base_url": "", "model": ""},
}

#: Providers that work without any API key (the client sends a placeholder).
KEYLESS_PROVIDERS = frozenset({"pollinations"})


class AssistantProviderError(Exception):
    """Provider call failed. ``http_status`` is what the API route answers."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.http_status = http_status


class AssistantProviderUnavailable(AssistantProviderError):
    """No usable provider credentials → HTTP 503."""

    def __init__(self, message: str) -> None:
        super().__init__(message, http_status=503)


@dataclass
class ChatResult:
    """One completion: either final text, tool calls, or both."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ProviderConfig:
    """Fully resolved provider settings (what the request actually uses)."""

    provider: str
    base_url: str
    model: str
    api_key: str
    timeout: float


def resolve_provider_config() -> ProviderConfig:
    """Merge settings + presets into the concrete request configuration.

    Resolution order per field: explicit ``AI_ASSISTANT_*`` override →
    (Groq only) the existing ``GROQ_*`` values → the provider's preset, so an
    instance already running copy extraction enables the assistant with zero
    new environment variables, and its GROQ_MODEL override is honoured.
    """
    provider = (settings.AI_ASSISTANT_PROVIDER or "groq").strip().lower()
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        # Unknown provider names degrade to "custom" behaviour: whatever base
        # URL the operator set, no model default.
        preset = PROVIDER_PRESETS["custom"]

    def _resolve(override: str, groq_value: str, preset_value: str) -> str:
        value = (override or "").strip()
        if value:
            return value
        if provider == "groq":
            value = (groq_value or "").strip()
            if value:
                return value
        return (preset_value or "").strip()

    # For Groq, use provider-specific override if set
    groq_base_url = settings.AI_ASSISTANT_GROQ_BASE_URL if provider == "groq" else ""
    base_url = _resolve(
        settings.AI_ASSISTANT_BASE_URL, groq_base_url, preset["base_url"]
    )
    if not base_url:
        raise AssistantProviderUnavailable(
            "AI assistant: no base URL configured for the selected provider"
        )

    # For Groq, use provider-specific model override if set
    groq_model = settings.AI_ASSISTANT_GROQ_MODEL if provider == "groq" else ""
    model = _resolve(settings.AI_ASSISTANT_MODEL, groq_model, preset["model"])
    if not model:
        raise AssistantProviderUnavailable("AI assistant: no model configured for the selected provider")

    api_key = (settings.AI_ASSISTANT_API_KEY or "").strip()
    if not api_key and provider == "groq":
        # Use round-robin selection for multiple Groq API keys (assistant-specific)
        api_key = _get_next_groq_api_key()
    if not api_key and provider == "openrouter":
        # Use round-robin selection for multiple OpenRouter API keys (assistant-specific)
        api_key = _get_next_openrouter_api_key()
    if not api_key:
        # Logged without a value: the point is that it is absent.
        logger.warning("AI assistant: no API key configured — chat unavailable")
        raise AssistantProviderUnavailable(
            "The AI assistant is not configured on this instance (no AI provider key)."
        )

    return ProviderConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        timeout=float(settings.AI_ASSISTANT_TIMEOUT_SECONDS or 45.0),
    )


def _resolve_fallback_config(name: str) -> Optional[ProviderConfig]:
    """Resolve one fallback provider from its dedicated settings.

    Keys live in ``AI_ASSISTANT_<NAME>_API_KEY`` (e.g. AI_ASSISTANT_GEMINI_API_KEY)
    with an optional ``AI_ASSISTANT_<NAME>_MODEL`` override; base URL and the
    model default come from the preset. Returns ``None`` when the fallback is
    not usable (unknown name, no preset, or no key for a provider that needs
    one) so the chain simply skips it.
    """
    provider = (name or "").strip().lower()
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None or provider == "custom":
        if provider:
            logger.warning("AI assistant: ignoring unknown fallback provider %r", name)
        return None

    prefix = f"AI_ASSISTANT_{provider.upper()}"
    api_key = str(getattr(settings, f"{prefix}_API_KEY", "") or "").strip()
    
    # Use round-robin selection for OpenRouter when multiple keys are configured
    if provider == "openrouter" and not api_key:
        api_key = _get_next_openrouter_api_key()
    
    if not api_key:
        if provider in KEYLESS_PROVIDERS:
            api_key = "not-needed"  # keyless endpoint; the header is ignored
        else:
            logger.info("AI assistant: fallback %s has no API key — skipped", provider)
            return None

    model = str(getattr(settings, f"{prefix}_MODEL", "") or "").strip() or preset["model"]
    base_url = (preset["base_url"] or "").strip()
    if not base_url or not model:
        return None
    return ProviderConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        timeout=float(settings.AI_ASSISTANT_TIMEOUT_SECONDS or 45.0),
    )


def resolve_provider_chain() -> list[ProviderConfig]:
    """Primary config plus every usable fallback, in try order.

    The primary behaves exactly like :func:`resolve_provider_config` (raising
    :class:`AssistantProviderUnavailable` when unusable); fallbacks that lack
    keys are skipped silently. Duplicate provider names are dropped — retrying
    the same backend twice in one turn only burns the rate budget faster.
    """
    chain = [resolve_provider_config()]
    seen = {chain[0].provider}
    for name in str(settings.AI_ASSISTANT_FALLBACKS or "").split(","):
        name = name.strip().lower()
        if not name or name in seen:
            continue
        config = _resolve_fallback_config(name)
        if config is not None:
            seen.add(config.provider)
            chain.append(config)
    return chain


def _parse_completion(payload: dict[str, Any]) -> ChatResult:
    """Pull content + tool calls out of an OpenAI-format response body."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise AssistantProviderError("the AI provider returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)

    tool_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        tool_calls.append(
            {
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "arguments": str(function.get("arguments") or "{}"),
            }
        )
    return ChatResult(content=content or "", tool_calls=tool_calls)


class AssistantChatProvider:
    """Async OpenAI-compatible chat client with function/tool calling.

    Built per request from :func:`resolve_provider_config` (tests inject a
    subclass instead). Only the ``chat`` method is public; it returns the
    model's decision and never raises for a model-side refusal — only for
    transport, auth, quota and shape problems.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = f"{self.config.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise AssistantProviderError(
                "The AI provider took too long to answer.", http_status=504
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("AI assistant: provider transport failed: %s", type(exc).__name__)
            raise AssistantProviderError(
                "Could not reach the AI provider.", http_status=502
            ) from exc

        if response.status_code == 401:
            raise AssistantProviderError(
                "The AI provider rejected the configured key.", http_status=502
            )
        if response.status_code == 429:
            raise AssistantProviderError(
                "The AI provider is rate-limiting this instance. Try again in a moment.",
                http_status=429,
            )
        if response.status_code >= 400:
            # The body may echo inputs or reveal provider internals — log a
            # short slice internally, return a generic message.
            logger.warning(
                "AI assistant: provider answered HTTP %d: %s",
                response.status_code,
                response.text[:300],
            )
            raise AssistantProviderError(
                "The AI provider answered with an error.", http_status=502
            )

        try:
            return _parse_completion(response.json())
        except ValueError as exc:
            raise AssistantProviderError(
                "The AI provider returned a malformed response.", http_status=502
            ) from exc


__all__ = [
    "AssistantChatProvider",
    "AssistantProviderError",
    "AssistantProviderUnavailable",
    "ChatResult",
    "ProviderConfig",
    "PROVIDER_PRESETS",
    "resolve_provider_config",
]
