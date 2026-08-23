"""
Browser factory — launches a stealth Patchright *persistent* browser context
for one LinkedIn account.
FILE: automation/browser.py

Each LinkedInAccount owns a durable Chromium user-data-dir on disk
(``account.profile_dir``). Every launch for that account — login, session
verification, and every Celery campaign session — opens that SAME directory
via ``launch_persistent_context()``. The directory itself is the source of
truth for session state (cookies, localStorage, IndexedDB, service-worker
caches); Chromium persists to disk continuously as a side effect of normal
operation, so there is no explicit "save" step after actions.

We use `patchright` (an API-compatible fork of Playwright that patches the
CDP Runtime.enable leak and several other automation tells) instead of plain
Playwright with a JS stealth layer bolted on. The manual add_init_script
patches below are kept as defense-in-depth on top of patchright.

Anti-detection contract — PIN, DON'T ROTATE:
    user_agent, viewport, timezone, locale, hardware_concurrency and
    device_memory are generated ONCE per account on first launch, persisted
    back to the account row immediately, and reused unchanged on every
    subsequent launch. Randomization only ever happens BETWEEN different
    accounts, never within one account's lifetime. The account's proxy
    (account.proxy_*) is likewise sticky — one proxy per account, forever.

REQUIRES:
    pip install patchright
    patchright install chromium --with-deps
"""
import os
import random

from patchright.async_api import async_playwright, BrowserContext, Page

from core.config import settings  # noqa: F401  (documented dependency: PROFILE_STORAGE_DIR)
from core.logging_config import get_logger
from core.security import decrypt_credential

logger = get_logger(__name__)

# Real Chrome user-agent strings — chosen ONCE per account, then pinned forever.
# Keep the majors current (patchright 1.60 launches Chromium ≈ 148): LinkedIn
# serves degraded/blocked experiences to browsers it considers outdated, and a
# UA claiming a 2-year-old Chrome while the engine is brand new is also a
# fingerprint mismatch. Already-pinned accounts keep their stored UA.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]

# (timezone_id, locale) pairs — picked once per account. US timezones match
# the US-based Webshare proxies assigned to accounts; keep timezone/locale
# consistent with the account's proxy geography.
TIMEZONE_LOCALES = [
    ("America/New_York", "en-US"),
    ("America/Chicago", "en-US"),
    ("America/Denver", "en-US"),
    ("America/Los_Angeles", "en-US"),
]

# Plausible, internally-consistent hardware profiles (also pinned per account).
HARDWARE_CONCURRENCIES = [4, 8, 16]
DEVICE_MEMORIES = [4, 8, 16]


def ensure_profile_dir(account) -> str:
    """
    Create the account's persistent profile directory with restrictive 0o700
    permissions, so other local users/processes on the host cannot read this
    account's session data (cookies live here in plaintext on disk).

    Idempotent — permissions are re-asserted on every call because
    os.makedirs(mode=...) is subject to the process umask.
    """
    os.makedirs(account.profile_dir, mode=0o700, exist_ok=True)
    os.chmod(account.profile_dir, 0o700)
    return account.profile_dir


def pin_account_fingerprint(account) -> None:
    """
    Generate the account's browser fingerprint ONCE (on its first-ever
    launch) and store it on the account row. If the fingerprint is already
    pinned, this is a no-op and the stored values are reused exactly.

    The caller is responsible for committing the DB session so the pinned
    values survive. Callers of launch_persistent_browser() commit right after
    launch (first launch only writes; later launches don't modify the row).
    """
    already_pinned = all(
        getattr(account, attr) is not None
        for attr in (
            "user_agent",
            "viewport_width",
            "viewport_height",
            "timezone_id",
            "locale",
            "hardware_concurrency",
            "device_memory",
        )
    )
    if already_pinned:
        return

    # First-ever launch for this account: pick a coherent fingerprint set.
    # Randomization happens ONLY here, ONLY between different accounts.
    viewport = random.choice(VIEWPORTS)
    timezone_id, locale = random.choice(TIMEZONE_LOCALES)

    account.user_agent = random.choice(USER_AGENTS)
    account.viewport_width = viewport["width"]
    account.viewport_height = viewport["height"]
    account.timezone_id = timezone_id
    account.locale = locale
    account.hardware_concurrency = random.choice(HARDWARE_CONCURRENCIES)
    account.device_memory = random.choice(DEVICE_MEMORIES)

    logger.info(
        "🆔 Pinned browser fingerprint for account %s: ua=%s viewport=%sx%s tz=%s locale=%s cpu=%s mem=%s",
        account.id, account.user_agent, account.viewport_width, account.viewport_height,
        account.timezone_id, account.locale, account.hardware_concurrency, account.device_memory,
    )


async def launch_persistent_browser(account, headless: bool = True):
    """
    Launches a stealth Chromium *persistent context* bound to the account's
    durable profile directory (account.profile_dir).

    On the first-ever launch for the account, the fingerprint columns
    (user_agent, viewport, timezone, locale, hardware_concurrency,
    device_memory) are generated once and persisted to the account row —
    commit the DB session after this call. Every later launch reuses the
    pinned values exactly.

    Returns:
        (playwright_instance, browser, context, page)

        NOTE: `browser` is always None — launch_persistent_context() returns
        the context directly; there is no separate Browser object. Callers
        must `await context.close()` and then `await pw.stop()` when done.
    """
    ensure_profile_dir(account)
    pin_account_fingerprint(account)

    # Sticky proxy: read from the account's permanently-assigned proxy
    # columns. Never rotate per-session — account.proxy_* is the single
    # source of truth, written once when the proxy was assigned.
    proxy_config = None
    if account.proxy_host:
        proxy_config = {
            "server": f"http://{account.proxy_host}:{account.proxy_port}",
            "username": account.proxy_username,
            "password": decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        }

    logger.info(
        "🚀 Launching persistent browser for account %s | ua=%s viewport=%sx%s tz=%s locale=%s cpu=%s mem=%s profile=%s",
        account.id, account.user_agent, account.viewport_width, account.viewport_height,
        account.timezone_id, account.locale, account.hardware_concurrency, account.device_memory,
        account.profile_dir,
    )

    pw = await async_playwright().start()
    try:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=account.profile_dir,
            headless=headless,
            proxy=proxy_config,
            viewport={"width": account.viewport_width, "height": account.viewport_height},
            user_agent=account.user_agent,
            locale=account.locale,
            timezone_id=account.timezone_id,
            # Permissions that a real browser would have
            permissions=["geolocation"],
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--no-first-run",
            ],
        )
        page: Page = await context.new_page()
    except Exception:
        # Don't leak the driver subprocess if the context launch fails.
        try:
            await pw.stop()
        except Exception:
            pass
        raise

    # Manual stealth patches — defense-in-depth on top of patchright's CDP
    # patches. hardwareConcurrency/deviceMemory come from the account's PINNED
    # values: reporting a different hardware profile on every launch of the
    # same account would itself be a detectable inconsistency.
    await context.add_init_script(
        """
        // Fake realistic plugin count (real browsers have plugins)
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        // Pinned CPU core count (stable across every launch of this account)
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => __HW_CONCURRENCY__
        });
        // Remove automation-related chrome flags
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        // Pinned device memory (stable across every launch of this account)
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => __DEVICE_MEMORY__
        });
        """.replace("__HW_CONCURRENCY__", str(account.hardware_concurrency))
          .replace("__DEVICE_MEMORY__", str(account.device_memory))
    )

    return pw, None, context, page
