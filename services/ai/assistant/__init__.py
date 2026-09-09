"""The in-app AI assistant service package.

See docs/ai_assistant_plan.md for the architecture. Modules:

* ``providers``  — any OpenAI-compatible chat API, resolved from settings.
* ``knowledge``  — the app-guide corpus and its retrieval (RAG).
* ``context``    — the caller's own data snapshot for the system prompt.
* ``tools``      — the capability registry the model can call.
* ``service``    — the turn orchestrator (prompt → model ⇄ tools → reply).
"""
