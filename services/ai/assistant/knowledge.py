"""Assistant knowledge base — the RAG corpus and its retrieval.

FILE: services/ai/assistant/knowledge.py

The corpus is the ``knowledge/*.md`` files next to this module: one ``##``
section per page or how-to, written for retrieval (short, task-oriented,
keywords line). It is the single source of truth for three things:

* **retrieval** — :func:`retrieve` scores sections against the user's message
  and the winners are injected into the system prompt, so the model answers
  "where do I schedule a post?" from the guide instead of its imagination.
* **navigation whitelist** — :func:`known_route` / :func:`resolve_route`
  validate every ``navigate`` tool call against paths the corpus declares;
  a hallucinated route can never reach the browser.
* **current page naming** — :func:`page_title` turns the ``current_path``
  the widget sends into "Gmail Inbox (/app/gmail)" for the system prompt.

Retrieval is keyword-scored on purpose: the corpus is small, every provider
must stay swappable (Groq has no embeddings endpoint, and an embeddings
provider would be a second vendor to configure), and the failure mode of a
slightly-off section is a slightly-off link, not a wrong answer. If the
corpus ever outgrows this, swap the body of :func:`retrieve` for an
embeddings lookup — its signature is the seam.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

_WORD_RE = re.compile(r"[a-z0-9']+")
# Words so common they score nothing on their own.
_STOPWORDS = frozenset(
    """a an and are as at be but by can do does for from go has have how i in
    is it me my new of on or that the this to was what when where which who
    will with you your""".split()
)


@dataclass
class Section:
    """One ``##`` block of the corpus."""

    title: str
    body: str
    paths: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


def _parse_sections(text: str) -> list[Section]:
    """Split a corpus file on ``##`` headings into Sections."""
    sections: list[Section] = []
    for block in re.split(r"(?m)^##\s+", text):
        block = block.strip()
        if not block:
            continue
        title, _, body = block.partition("\n")
        section = Section(title=title.strip(), body=body.strip())
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- Path:"):
                section.paths += [
                    p.strip() for p in stripped[len("- Path:"):].split(",") if p.strip()
                ]
            elif stripped.startswith("- Keywords:"):
                section.keywords += [
                    k.strip().lower()
                    for k in stripped[len("- Keywords:"):].split(",")
                    if k.strip()
                ]
        sections.append(section)
    return sections


def _load_sections(directory: Path) -> list[Section]:
    sections: list[Section] = []
    for path in sorted(directory.glob("*.md")):
        try:
            sections.extend(_parse_sections(path.read_text(encoding="utf-8")))
        except OSError as exc:  # pragma: no cover - corrupt install
            logger.error("assistant knowledge: could not read %s: %s", path.name, exc)
    return sections


SECTIONS: list[Section] = _load_sections(KNOWLEDGE_DIR)


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _score(section: Section, query_tokens: list[str], query_lower: str) -> int:
    """Overlap score: keyword hits weigh most, then title, then body."""
    if not query_tokens:
        return 0
    score = 0
    keyword_blob = " ".join(section.keywords)
    for token in query_tokens:
        if token in keyword_blob:
            score += 3
        elif token in section.title.lower():
            score += 2
        elif token in section.body.lower():
            score += 1
    # A keyword phrase appearing verbatim ("whatsapp group scan") is the
    # strongest signal the corpus can give.
    for phrase in section.keywords:
        if len(phrase.split()) > 1 and phrase in query_lower:
            score += 4
    return score


def retrieve(query: str, k: int = 3, min_score: int = 2) -> list[Section]:
    """The top ``k`` sections for a query, weakest matches dropped."""
    query_lower = (query or "").lower().strip()
    query_tokens = _tokens(query_lower)
    scored = sorted(
        ((s, _score(s, query_tokens, query_lower)) for s in SECTIONS),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [section for section, score in scored[:k] if score >= min_score]


# ── route registry (the navigation whitelist) ────────────────────────────────

def _build_route_index() -> dict[str, Section]:
    """path → the section that declares it (first declaration wins)."""
    index: dict[str, Section] = {}
    for section in SECTIONS:
        for path in section.paths:
            index.setdefault(path, section)
    return index


ROUTE_INDEX: dict[str, Section] = _build_route_index()


def known_route(path: str) -> bool:
    """True when the corpus declares this exact route."""
    return (path or "").strip() in ROUTE_INDEX


def resolve_route(path: str) -> tuple[str | None, str | None]:
    """Match a model-suggested path against the whitelist.

    Returns ``(resolved_path, label)``. Exact match first; a path missing a
    ``/app`` prefix is retried with it (models love writing ``/gmail`` for
    ``/app/gmail``). Returns ``(None, None)`` when nothing matches — the
    caller tells the model to pick again, so a hallucinated route never
    reaches the browser.
    """
    candidate = (path or "").strip()
    if not candidate.startswith("/"):
        return (None, None)

    def _lookup(value: str) -> tuple[str | None, Section | None]:
        section = ROUTE_INDEX.get(value)
        return (value, section) if section else (None, None)

    resolved, section = _lookup(candidate)
    if resolved is None and not candidate.startswith("/app"):
        resolved, section = _lookup(f"/app{candidate}")
    if resolved is None:
        return (None, None)
    return resolved, section.title


def page_title(path: str) -> str | None:
    """Name the page the user is on, from the corpus."""
    if not path:
        return None
    section = ROUTE_INDEX.get((path or "").strip())
    return section.title if section else None


def render_for_prompt(sections: list[Section]) -> str:
    """Sections → the compact text block injected into the system prompt."""
    if not sections:
        return ""
    blocks = []
    for section in sections:
        paths = f"  Paths: {', '.join(section.paths)}" if section.paths else ""
        # Bodies are already short; a hard ceiling keeps a future verbose
        # corpus from eating the prompt.
        body = section.body if len(section.body) <= 900 else section.body[:900] + "…"
        blocks.append(f"### {section.title}\n{body}{paths}")
    return "\n\n".join(blocks)


def route_suggestions() -> list[dict[str, str]]:
    """Every declared route with its label (used by tests and the widget)."""
    return [{"path": path, "label": section.title} for path, section in ROUTE_INDEX.items()]


__all__ = [
    "SECTIONS",
    "Section",
    "KNOWLEDGE_DIR",
    "known_route",
    "page_title",
    "render_for_prompt",
    "resolve_route",
    "retrieve",
    "route_suggestions",
]
