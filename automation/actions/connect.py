"""
Action: Send a LinkedIn connection request with optional personalised note.
FILE: automation/actions/connect.py

Day 2 of the drip sequence. Uses {{first_name}} placeholder substitution
from the campaign's connection_note_template.

Like the message action, ``sent`` is only True when the request was
*positively* confirmed — the invitation modal closed and the profile now
shows a Pending state (or LinkedIn raised no error).

Connect-button discovery is *structural*, not a fixed CSS wish-list.  The
previous version waited on four hard-coded aria-label selectors (6 s each)
and then three "More actions" variants (4 s each).  LinkedIn renders the
profile action row differently across A/B tests, locales and account
languages, so every miss cost ~36 s of serial timeouts and — worse — the
failure log only said "not found", with no evidence of what was actually
rendered on the profile.  This version instead:

  1. Anchors on the profile top card (the ``section`` that wraps the
     ``main h1``), inventories every interactive element in it with one
     JS evaluation per node (aria-label, visible text,
     ``data-control-name``/``data-view-name``, dropdown classes), and
     classifies buttons in Python — immune to class-name churn.
  2. Polls the whole scan until a single deadline instead of serial
     per-selector timeouts, so a rendered Connect is found in <1 s and a
     genuinely absent one still waits out lazy rendering.
  3. Opens the More-actions overflow and picks the *smallest* matching
     menu node, recognising the "Withdraw invitation" item as the
     already-pending state.
  4. Handles follow-first profiles — members LinkedIn serves with only a
     Follow action (creator-mode / large-audience / restricted members) —
     by following the member and re-scanning the top card and the More
     menu, since Connect usually renders once the follow lands.
  5. When nothing matches, logs the rendered action inventory, page title
     and UI language, saves a screenshot/HTML snapshot in development,
     and embeds the inventory in the error so the worker log explains
     *what LinkedIn showed* instead of the page being a black box.
"""
import asyncio
from patchright.async_api import Page
from automation.human import human_click, human_type, human_scroll, random_idle_pause
from automation.actions.utils import recover_blank_page
from core.logging_config import get_logger, should_take_screenshots

logger = get_logger(__name__)

# LinkedIn caps personalised invitation notes.
MAX_NOTE_LENGTH = 300

# Single polling deadlines for the structural scans.  LinkedIn lazy-renders
# the profile action row, so the poll must be generous; each individual scan
# costs a fraction of a second, so the common case resolves instantly.
CONNECT_SCAN_TIMEOUT_SECONDS = 14.0
MORE_SCAN_TIMEOUT_SECONDS = 6.0
MENU_SCAN_TIMEOUT_SECONDS = 6.0

# Follow-first fallback timing: the action row re-renders quickly after the
# follow click, but the rescan stays generous for slow SPA updates.
FOLLOW_CONFIRM_TIMEOUT_SECONDS = 5.0
FOLLOW_SCAN_TIMEOUT_SECONDS = 8.0

# Elements that can host the profile's actions inside the top card.
_ACTION_CANDIDATE_SELECTOR = (
    "button, [role='button'], .artdeco-dropdown__trigger"
)

# Containers LinkedIn uses for the opened overflow menu.  The new React UI
# portals menus to <body>, so lookups must NOT be scoped to <main>.
_MENU_CONTAINER_SELECTOR = (
    "[role='menu'], .artdeco-dropdown__content, div[data-testid='popover']"
)

# Resolve the profile top card: the <section> wrapping the profile name.
# Falls back to the first section in <main> that contains any button.
_TOP_CARD_JS = """
() => {
    const main = document.querySelector('main');
    if (!main) return null;
    const h1 = main.querySelector('h1');
    if (h1) {
        const card = h1.closest('section');
        if (card) return card;
    }
    for (const section of main.querySelectorAll('section')) {
        if (section.querySelector('button')) return section;
    }
    return null;
}
"""

# Inventory a single candidate node.  Reading everything relevant in one
# evaluation keeps this cheap and makes classification independent from CSS.
_BUTTON_INFO_JS = """
el => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
        text: (el.innerText || el.textContent || '').trim(),
        aria: el.getAttribute('aria-label') || '',
        control: el.getAttribute('data-control-name') || '',
        dataview: el.getAttribute('data-view-name') || '',
        classes: el.className && el.className.baseVal !== undefined
            ? el.className.baseVal : String(el.className || ''),
        haspopup: el.getAttribute('aria-haspopup') || '',
        expanded: el.getAttribute('aria-expanded'),
        disabled: Boolean(el.disabled)
            || el.getAttribute('aria-disabled') === 'true',
        visible: style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0,
    };
}
"""

# Locate the smallest Connect-shaped node inside any visible overflow menu.
# Preferring the smallest node avoids returning a wrapper that contains the
# whole action list (its combined label also contains "connect").
_MENU_CONNECT_JS = """
() => {
    const norm = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const visible = el => {
        if (!el || typeof el.getBoundingClientRect !== 'function') return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    };
    const labelOf = el => norm(
        ((el.getAttribute && el.getAttribute('aria-label')) || '')
        + ' ' + (el.innerText || el.textContent || '')
    );
    const isConnect = label => {
        if (!label) return false;
        if (label.includes('disconnect') || label.includes('remove connection')) return false;
        if (label.includes('report') || label.includes('block')) return false;
        return label.includes('connect') || label.includes('invite');
    };
    const containers = Array.from(document.querySelectorAll(
        "[role='menu'], .artdeco-dropdown__content, div[data-testid='popover']"
    )).filter(visible);
    for (const container of containers) {
        const items = container.querySelectorAll(
            "button, [role='menuitem'], li, a, div, span"
        );
        for (const item of items) {
            if (!visible(item)) continue;
            if (!isConnect(labelOf(item))) continue;
            // Skip a node whose child also matches — prefer the smaller one.
            let childMatches = false;
            for (const child of item.querySelectorAll(
                "button, [role='menuitem'], li, a, div, span"
            )) {
                if (visible(child) && isConnect(labelOf(child))) {
                    childMatches = true;
                    break;
                }
            }
            if (childMatches) continue;
            return item.closest("button, [role='menuitem'], li, a") || item;
        }
    }
    return null;
}
"""

# Diagnostic inventory: what the visitor actually sees.
_TOP_CARD_INVENTORY_JS = """
() => {
    const norm = value => (value || '').replace(/\\s+/g, ' ').trim();
    const visible = el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    };
    const out = [];
    for (const el of document.querySelectorAll(
        "main button, main [role='button'], main .artdeco-dropdown__trigger"
    )) {
        if (!visible(el)) continue;
        const parts = [
            el.getAttribute('aria-label') || '',
            el.innerText || el.textContent || '',
        ].map(norm).filter(Boolean);
        const label = [...new Set(parts)].join(' | ');
        if (label) out.push(label.slice(0, 80));
        if (out.length >= 20) break;
    }
    return out;
}
"""

_MENU_INVENTORY_JS = """
() => {
    const norm = value => (value || '').replace(/\\s+/g, ' ').trim();
    const visible = el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    };
    const out = [];
    const containers = Array.from(document.querySelectorAll(
        "[role='menu'], .artdeco-dropdown__content, div[data-testid='popover']"
    )).filter(visible);
    for (const container of containers) {
        for (const item of container.querySelectorAll(
            "button, [role='menuitem'], li, a"
        )) {
            if (!visible(item)) continue;
            const parts = [
                item.getAttribute('aria-label') || '',
                item.innerText || item.textContent || '',
            ].map(norm).filter(Boolean);
            const label = [...new Set(parts)].join(' | ');
            if (label) out.push(label.slice(0, 80));
        }
    }
    return out.slice(0, 12);
}
"""

# Follow-first profiles (creator-mode / large-audience / restricted members)
# render only a Follow action — Connect appears (top card or More menu) only
# after the visitor follows the member.  Locate that Follow action inside the
# profile top card.  "Following" (already-following state) and "Follow back"
# are explicitly excluded: only an actionable Follow click helps here.
_FOLLOW_BUTTON_JS = """
() => {
    const norm = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const visible = el => {
        if (!el || typeof el.getBoundingClientRect !== 'function') return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    };
    const main = document.querySelector('main');
    if (!main) return null;
    const h1 = main.querySelector('h1');
    const card = (h1 && h1.closest('section')) || main;
    for (const el of card.querySelectorAll(
        "button, [role='button'], .artdeco-dropdown__trigger"
    )) {
        if (!visible(el)) continue;
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
        const aria = norm(el.getAttribute('aria-label'));
        const text = norm(el.innerText || el.textContent);
        if (text.startsWith('following') || aria.startsWith('following')) continue;
        if (text.startsWith('follow back') || aria.startsWith('follow back')) continue;
        if (text === 'follow' || aria === 'follow' || aria.startsWith('follow ')) {
            return el;
        }
    }
    return null;
}
"""

# Confirmation that the follow landed: the top-card action switches to a
# "Following" toggle (often labelled with an Unfollow intent).
_FOLLOWING_STATE_JS = """
() => {
    const norm = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const visible = el => {
        if (!el || typeof el.getBoundingClientRect !== 'function') return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 0 && rect.height > 0;
    };
    const main = document.querySelector('main');
    if (!main) return false;
    const h1 = main.querySelector('h1');
    const card = (h1 && h1.closest('section')) || main;
    for (const el of card.querySelectorAll("button, [role='button']")) {
        if (!visible(el)) continue;
        const aria = norm(el.getAttribute('aria-label'));
        const text = norm(el.innerText || el.textContent);
        if (text.startsWith('following') || aria.startsWith('following')
            || text.includes('unfollow') || aria.includes('unfollow')) return true;
    }
    return false;
}
"""

ADD_NOTE_SELECTORS = [
    "button[aria-label='Add a note']",
    "button[aria-label*='Add a note' i]",
    "button:has-text('Add a note')",
]
NOTE_BOX_SELECTORS = [
    "textarea[name='message']",
    "textarea#custom-message",
    ".send-invite__custom-message",
]
SEND_BUTTON_SELECTORS = [
    "button[aria-label='Send now']",
    "button[aria-label='Send invitation']",
    "button[aria-label='Send without a note']",
    "button[aria-label*='Send without' i]",
    "button[aria-label*='Send' i]",
    ".artdeco-modal button:has-text('Send')",
]

MODAL_SELECTOR = ".artdeco-modal, div[role='dialog']"

# Selectors for specific buttons that definitively indicate the lead's
# connection state.  Checked *as elements* rather than as text substrings so
# that a "Message" or "Follow" button on a 2nd-degree profile is never
# confused with a "we're already connected" state.
_CONNECTED_INDICATOR_SELECTORS = (
    "button[aria-label*='Connected' i]",
    "button[aria-label*='Following' i]",
    "button[aria-label*='Pending' i]",
    "button[aria-label*='Withdraw' i]",
    "button:has-text('Connected')",
    "button:has-text('Following')",
    "button:has-text('Pending')",
    "button:has-text('Withdraw')",
)

# LinkedIn's invite throttling / rejection copy.
BLOCKED_HINTS = (
    "you've reached the weekly invitation limit",
    "weekly invitation limit",
    "no longer accept invitations",
    "can't send an invitation",
    "cannot send an invitation",
    "try again later",
    "something went wrong",
)

# A modal asking for the recipient's email address means the lead restricts
# invitations; no Connect flow can succeed, so report it precisely instead
# of "Send button not found".
_EMAIL_GATE_HINTS = (
    "email address",
    "their email",
    "to connect with",  # "... you'll need to know their email"
)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").split())


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 4000):
    """Return the first visible element matching any selector, else None."""
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        except Exception:
            continue
        if not element:
            continue
        try:
            if await element.is_enabled():
                return element
        except Exception:
            return element
    return None


async def _page_state_text(page: Page) -> str:
    """Lowercased text of the profile's top-card action row (for state checks)."""
    for selector in [".pv-top-card-v2-ctas", ".ph5.pb5", "main"]:
        try:
            node = await page.query_selector(selector)
            if node:
                return _normalize(await node.inner_text()).lower()
        except Exception:
            continue
    return ""


async def _is_already_connected(page: Page) -> bool:
    """Check if the profile shows we're already connected / following / pending.

    Uses element selectors (not text substrings) so that a "Message" or "Follow"
    button on a 2nd-degree profile is never confused with an "already connected"
    state.  Returns ``True`` only when a specific Connected / Following / Pending
    / Withdraw button is visible in the top-card action row.
    """
    for selector in [".pv-top-card-v2-ctas", ".ph5.pb5", "main"]:
        try:
            container = await page.query_selector(selector)
            if not container:
                continue
            for indicator in _CONNECTED_INDICATOR_SELECTORS:
                try:
                    element = await container.query_selector(indicator)
                    if element and await element.is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    # Fallback: no container was found — don't claim "already connected".
    return False


def _classify_top_card_action(info: dict) -> str | None:
    """Classify an inventoried top-card node as ``connect`` / ``more`` / None.

    Pure function over the JS inventory record (aria-label, visible text,
    ``data-control-name``/``data-view-name``, classes) so classification is
    unit-testable without a browser and independent of LinkedIn's rotating
    class names.
    """
    if not info.get("visible") or info.get("disabled"):
        return None
    aria = _normalize(info.get("aria")).lower()
    text = _normalize(info.get("text")).lower()
    control = _normalize(info.get("control")).lower()
    dataview = _normalize(info.get("dataview")).lower()
    classes = _normalize(info.get("classes")).lower()
    haystack = f"{aria} {text} {control} {dataview}"

    if "disconnect" in haystack or "remove connection" in haystack:
        return None

    # "Connect", "Invite <name> to connect", data-control-name="connect".
    if "connect" in haystack or "invite" in haystack:
        return "connect"

    # The overflow trigger: "More actions", "More actions for <name>",
    # or the icon-only artdeco dropdown trigger in the top card.
    if "more actions" in haystack or "more-actions" in haystack:
        return "more"
    if aria == "more" or aria.startswith("more "):
        return "more"
    if text == "more" and (
        info.get("haspopup")
        or info.get("expanded") is not None
        or "dropdown__trigger" in classes
    ):
        return "more"
    return None


async def _button_info(element) -> dict:
    """Collect the classification record for one candidate node."""
    try:
        info = await element.evaluate(_BUTTON_INFO_JS)
    except Exception:
        return {}
    return info if isinstance(info, dict) else {}


async def _scan_top_card_actions(page: Page):
    """One pass over the top card: ``(connect_el, more_el, inventory)``.

    ``inventory`` lists every visible action node (for diagnostics), even
    when none classifies as Connect/More — it is the evidence of what
    LinkedIn actually rendered.
    """
    scope = None
    try:
        handle = await page.evaluate_handle(_TOP_CARD_JS)
        scope = handle.as_element() if handle else None
    except Exception:
        scope = None
    if scope is None:
        try:
            scope = await page.query_selector("main")
        except Exception:
            scope = None
    if scope is None:
        return None, None, []

    try:
        candidates = await scope.query_selector_all(_ACTION_CANDIDATE_SELECTOR)
    except Exception:
        candidates = []

    connect_el = None
    more_el = None
    inventory: list[dict] = []
    for candidate in candidates:
        info = await _button_info(candidate)
        if not info:
            continue
        if info.get("visible"):
            inventory.append(info)
        kind = _classify_top_card_action(info)
        if kind == "connect" and connect_el is None:
            connect_el = candidate
        elif kind == "more" and more_el is None:
            more_el = candidate
    return connect_el, more_el, inventory


async def _poll_top_card_actions(page: Page, timeout_seconds: float):
    """Poll the top card until a Connect/More action renders or time runs out."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    connect_el = more_el = None
    inventory: list[dict] = []
    while True:
        scanned = await _scan_top_card_actions(page)
        connect_el, more_el, inventory = scanned
        if connect_el is not None:
            break
        if asyncio.get_running_loop().time() >= deadline:
            # More is still useful at the deadline even without Connect.
            break
        await asyncio.sleep(0.4)
    return connect_el, more_el, inventory


async def _poll_menu_connect(page: Page, timeout_seconds: float):
    """Poll the opened overflow menu for its Connect item."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            handle = await page.evaluate_handle(_MENU_CONNECT_JS)
            element = handle.as_element() if handle else None
        except Exception:
            element = None
        if element is not None:
            return element
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.35)


async def _top_card_inventory_labels(page: Page, fallback: list[dict]) -> list[str]:
    """Human-readable labels of visible profile actions (diagnostics)."""
    fallback_labels = [
        f"{_normalize(info.get('aria'))} | {_normalize(info.get('text'))}".strip(" |")
        for info in fallback
        if info.get("visible")
    ]
    try:
        labels = await page.evaluate(_TOP_CARD_INVENTORY_JS)
        if labels:
            return [str(label) for label in labels]
    except Exception:
        pass
    return [label for label in fallback_labels if label]


async def _menu_inventory_labels(page: Page) -> list[str]:
    """Human-readable labels of the items in the currently open menu."""
    try:
        labels = await page.evaluate(_MENU_INVENTORY_JS)
        return [str(label) for label in labels or []]
    except Exception:
        return []


async def _describe_missing_connect(page: Page, where: str, inventory: list[str]) -> str:
    """Build (and log) an evidence-backed reason for a missing Connect action.

    The error embeds what was rendered instead of Connect — a follow-first
    profile, a restricted profile, a non-English UI or a LinkedIn layout
    change each look different here, so the worker log itself answers
    "why couldn't it find the button".
    """
    parts = [f"Connect button not found on the profile ({where})."]
    if inventory:
        parts.append("Rendered actions: [" + "; ".join(inventory[:12]) + "].")
    else:
        parts.append("No top-card actions rendered at all.")
    try:
        lang = await page.evaluate("() => (document.documentElement.lang || '')")
    except Exception:
        lang = ""
    if lang and not str(lang).lower().startswith("en"):
        parts.append(
            f"LinkedIn UI language is {lang!r} — text-based action discovery "
            "expects an English LinkedIn interface."
        )
    try:
        title = _normalize(await page.title())[:80]
    except Exception:
        title = ""
    if title:
        parts.append(f"Page title: {title!r}.")
    message = " ".join(parts)
    logger.warning("⚠️ %s", message)
    if should_take_screenshots():
        try:
            await page.screenshot(path="connect_no_button_debug.png", full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            with open("connect_no_button_debug.html", "w", encoding="utf-8") as fh:
                fh.write(html)
        except Exception:
            pass
    return message


async def _close_open_menu(page: Page) -> None:
    """Dismiss a leftover overflow menu so later steps start from a clean page."""
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def _blocked_reason(page: Page) -> str | None:
    """Return LinkedIn's own rejection text if the invite was refused."""
    for selector in [
        "div[data-test-artdeco-toast-item-type='error']",
        ".artdeco-toast-item--error",
        ".artdeco-modal",
        "div[role='alert']",
    ]:
        try:
            nodes = await page.query_selector_all(selector)
        except Exception:
            continue
        for node in nodes:
            try:
                if not await node.is_visible():
                    continue
                text = _normalize(await node.inner_text())
            except Exception:
                continue
            lowered = text.lower()
            for hint in BLOCKED_HINTS:
                if hint in lowered:
                    return text[:240]
    return None


async def _email_gate_reason(page: Page) -> str | None:
    """Detect the dialog that demands the recipient's email to send an invite."""
    try:
        modal = await page.query_selector(MODAL_SELECTOR)
        if not modal or not await modal.is_visible():
            return None
        text = _normalize(await modal.inner_text())
    except Exception:
        return None
    lowered = text.lower()
    if "email" in lowered and any(hint in lowered for hint in _EMAIL_GATE_HINTS):
        return text[:240]
    return None


async def _modal_closed(page: Page, timeout_seconds: float = 8.0) -> bool:
    """Wait for the invitation modal to disappear after clicking Send."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            modals = await page.query_selector_all(MODAL_SELECTOR)
            if not any([await m.is_visible() for m in modals]):
                return True
        except Exception:
            return True
        await asyncio.sleep(0.4)
    return False


async def _invite_confirmed(page: Page, timeout_seconds: float = 8.0) -> bool:
    """Positive confirmation: the profile now shows a Pending/Withdraw state."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            pending = await page.query_selector(
                "button[aria-label*='Pending' i], button[aria-label*='Withdraw' i]"
            )
            if pending and await pending.is_visible():
                return True
        except Exception:
            pass
        state = await _page_state_text(page)
        if "pending" in state or "invitation sent" in state:
            return True
        await asyncio.sleep(0.5)
    return False


async def _find_follow_button(page: Page):
    """Return the top-card Follow action when the profile is follow-first."""
    try:
        handle = await page.evaluate_handle(_FOLLOW_BUTTON_JS)
    except Exception:
        return None
    return handle.as_element() if handle else None


async def _confirm_following(page: Page, timeout_seconds: float) -> bool:
    """LinkedIn confirms the follow by switching the action to Following."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            state = await page.evaluate(_FOLLOWING_STATE_JS)
        except Exception:
            state = False
        if state:
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.4)


async def _follow_then_find_connect(page: Page):
    """Follow-first fallback: follow the member, then rescan for Connect.

    Some profiles (creator-mode / large-audience / restricted members) are
    served with Follow as the only action; after following, Connect usually
    renders in the top card or inside the refreshed More actions menu.

    Returns ``(connect_el, follow_attempted, inventory)`` — ``inventory`` is
    the post-follow action scan so failure diagnostics describe what LinkedIn
    showed *after* the follow.
    """
    follow_btn = await _find_follow_button(page)
    if follow_btn is None:
        return None, False, []
    logger.info("👤 Follow-first profile: no Connect offered; following the member first")
    await human_click(page, follow_btn)
    await random_idle_pause(1.5, 3.0)
    if not await _confirm_following(page, FOLLOW_CONFIRM_TIMEOUT_SECONDS):
        logger.warning(
            "⚠️ LinkedIn never confirmed the follow; rescanning for Connect anyway"
        )
    connect_el, more_el, inventory = await _poll_top_card_actions(
        page, FOLLOW_SCAN_TIMEOUT_SECONDS
    )
    if connect_el is not None:
        logger.info("✅ Connect appeared in the top card after following the member")
        return connect_el, True, inventory
    if more_el is not None:
        await human_click(page, more_el)
        await random_idle_pause(0.8, 1.8)
        connect_el = await _poll_menu_connect(page, MENU_SCAN_TIMEOUT_SECONDS)
        if connect_el is not None:
            logger.info("✅ Connect appeared in the More menu after following")
            return connect_el, True, inventory
        await _close_open_menu(page)
    return None, True, inventory


async def _open_connect_dialog(page: Page) -> tuple[bool, str | None, bool]:
    """Click Connect (top card, else More menu).  Returns ``(opened, error, already_connected)``."""
    connect_btn, more_btn, inventory = await _poll_top_card_actions(
        page, CONNECT_SCAN_TIMEOUT_SECONDS
    )

    menu_open = False
    if not connect_btn:
        logger.info("🔎 Connect not in the top card; checking the More actions menu")
        if not more_btn:
            # Give the More trigger a brief extra chance to lazy-render.
            _, more_btn, inventory = await _poll_top_card_actions(
                page, MORE_SCAN_TIMEOUT_SECONDS
            )

    if not connect_btn and more_btn:
        await human_click(page, more_btn)
        menu_open = True
        await random_idle_pause(0.8, 1.8)
        connect_btn = await _poll_menu_connect(page, MENU_SCAN_TIMEOUT_SECONDS)

    follow_attempted = False
    if not connect_btn:
        menu_labels = await _menu_inventory_labels(page) if menu_open else []
        if menu_open:
            await _close_open_menu(page)
        # The More menu of a pending lead shows "Withdraw invitation".
        joined = " ".join(menu_labels).lower()
        if "withdraw" in joined or await _is_already_connected(page):
            return False, (
                "Connect button not available — the lead is already connected, "
                "or an invitation is already pending."
            ), True

        # Follow-first fallback: this member currently only offers Follow;
        # following them usually makes Connect available.
        connect_btn, follow_attempted, follow_inventory = (
            await _follow_then_find_connect(page)
        )
        if follow_attempted and follow_inventory:
            inventory = follow_inventory

    if not connect_btn:
        card_labels = await _top_card_inventory_labels(page, inventory)
        if follow_attempted:
            error = await _describe_missing_connect(
                page,
                "follow-only or restricted profile: Connect did not appear "
                "even after following the member (top card and More menu)",
                card_labels,
            )
        elif menu_labels:
            error = await _describe_missing_connect(
                page,
                "More menu contains only: [" + "; ".join(menu_labels[:8]) + "]",
                card_labels,
            )
        else:
            error = await _describe_missing_connect(
                page, "top card and More menu", card_labels
            )
        return False, error, False

    # Clicking a menu item closes the dropdown automatically; no cleanup needed.
    await human_click(page, connect_btn)
    await random_idle_pause(1.0, 2.5)
    return True, None, False


async def send_connection_request(page: Page, profile_url: str,
                                   first_name: str = None,
                                   note_template: str = None) -> dict:
    """
    Sends a connection request to the LinkedIn profile at profile_url.

    note_template: string with optional {{first_name}} placeholder.
    If None, sends without a note (higher acceptance rate for cold outreach).

    ``sent`` is True only when LinkedIn confirmed the invitation; ``error``
    explains every other outcome (already connected, weekly limit reached,
    note never landed, modal never closed).
    """
    result = {"sent": False, "with_note": False, "error": None}

    try:
        # Use domcontentloaded (not networkidle) — LinkedIn pages have
        # continuous background requests that prevent networkidle from
        # ever firing, causing a 30 s timeout on every navigation.
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as nav_exc:
            # A goto timeout/error is not necessarily fatal — the page may
            # have partially loaded.  Let the recovery path decide.
            logger.warning("⚠️ Navigation to %s raised: %s", profile_url, nav_exc)
        await random_idle_pause(3, 6)

        # Blank/white-page recovery: wait for the SPA to mount, reload once,
        # then probe the session on /feed/ and retry.  ``session_stale`` is
        # True only when the session itself is dead (login/checkpoint
        # redirect or LinkedIn no longer serving any page).
        recovered, load_error, session_stale = await recover_blank_page(page, profile_url)
        if not recovered:
            if should_take_screenshots():
                try:
                    await page.screenshot(path="connect_blank_page_debug.png", full_page=True)
                except Exception:
                    pass
            result["error"] = load_error or "Page failed to load (blank page after recovery attempts)."
            result["page_load_failed"] = True
            result["session_stale"] = session_stale
            return result

        if "/in/" not in page.url:
            result["error"] = f"Not a profile page: {page.url}"
            return result

        # Already connected / already pending — never a "failure", but never
        # a send either.  Detect it up front so it isn't retried forever.
        if await _invite_confirmed(page, timeout_seconds=1.5):
            result["error"] = "An invitation is already pending for this lead."
            result["already_connected"] = True
            return result

        # Scroll a bit before clicking connect (natural behaviour)
        await human_scroll(page)
        await random_idle_pause(1.5, 4.0)

        opened, open_error, already_connected = await _open_connect_dialog(page)
        if not opened:
            result["error"] = open_error
            if already_connected:
                result["already_connected"] = True
            return result

        blocked = await _blocked_reason(page)
        if blocked:
            logger.error("❌ LinkedIn refused the invitation: %s", blocked)
            result["error"] = f"LinkedIn refused the invitation: {blocked}"
            return result

        # Some profiles only accept invitations from members who know their
        # email address; LinkedIn shows that dialog instead of Send/Add a note.
        email_gate = await _email_gate_reason(page)
        if email_gate:
            await _close_open_menu(page)  # also dismisses the dialog
            logger.warning("⚠️ Invitation requires the lead's email address: %s", email_gate)
            result["error"] = (
                "LinkedIn requires entering the lead's email address to send "
                f"this invitation (restricted profile): {email_gate}"
            )
            result["connect_restricted"] = True
            return result

        # ── Add a personalised note if template is provided ───────────────────
        if note_template:
            note = note_template.replace("{{first_name}}", first_name or "there").strip()
            if len(note) > MAX_NOTE_LENGTH:
                logger.warning(
                    "⚠️ Connection note is %d chars; truncating to LinkedIn's %d-char limit",
                    len(note), MAX_NOTE_LENGTH,
                )
                note = note[:MAX_NOTE_LENGTH].rstrip()

            add_note_btn = await _first_visible(page, ADD_NOTE_SELECTORS, timeout_ms=3000)
            if not add_note_btn:
                # Free accounts lose "Add a note" once the monthly note quota
                # is spent.  Sending without a note is still worthwhile.
                logger.warning("⚠️ 'Add a note' unavailable; sending the invitation without a note")
            elif note:
                await human_click(page, add_note_btn)
                await random_idle_pause(0.8, 1.8)

                note_box = await _first_visible(page, NOTE_BOX_SELECTORS, timeout_ms=4000)
                if not note_box:
                    logger.warning("⚠️ Note textarea never appeared; sending without a note")
                else:
                    await human_type(page, note_box, note)
                    await random_idle_pause(0.5, 1.5)
                    # Verify the note actually landed before submitting.
                    try:
                        typed = _normalize(await note_box.input_value())
                    except Exception:
                        typed = ""
                    if not typed:
                        # Same failure mode as the message composer: the click
                        # was intercepted and the keystrokes went nowhere.
                        try:
                            await note_box.fill(note)
                            await asyncio.sleep(0.5)
                            typed = _normalize(await note_box.input_value())
                        except Exception:
                            typed = ""
                    if typed:
                        result["with_note"] = True
                    else:
                        logger.warning("⚠️ Note text never landed in the textarea; sending without a note")

        # Click Send ("Send now", "Send invitation", or "Send without a note")
        send_btn = await _first_visible(page, SEND_BUTTON_SELECTORS, timeout_ms=5000)
        if not send_btn:
            if should_take_screenshots():
                try:
                    await page.screenshot(path="connect_send_missing_debug.png", full_page=True)
                except Exception:
                    pass
            result["error"] = "Send button not found in the invitation dialog; nothing was submitted."
            return result

        await human_click(page, send_btn)
        await random_idle_pause(2, 4)

        blocked = await _blocked_reason(page)
        if blocked:
            logger.error("❌ LinkedIn rejected the invitation: %s", blocked)
            result["error"] = f"LinkedIn rejected the invitation: {blocked}"
            return result

        if await _invite_confirmed(page):
            logger.info("✅ Invitation confirmed (profile now shows Pending)")
            result["sent"] = True
            return result

        # The Pending badge is not always re-rendered without a reload, so a
        # cleanly closed modal with no error is accepted as sent.
        if await _modal_closed(page):
            logger.info("✅ Invitation dialog closed with no error; treating as sent")
            result["sent"] = True
            return result

        if should_take_screenshots():
            try:
                await page.screenshot(path="connect_unconfirmed_debug.png", full_page=True)
            except Exception:
                pass
        result["error"] = "LinkedIn did not confirm the invitation; the dialog stayed open."

    except Exception as e:
        result["error"] = str(e)

    return result
