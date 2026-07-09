"""
Browser factory — launches a stealth Playwright browser for one LinkedIn account.
FILE: automation/browser.py
 
REQUIRES:
    pip install playwright playwright-stealth
    playwright install chromium --with-deps
"""
import random
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
 
# Real Chrome user-agent strings — rotate to avoid fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
 
VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]
 
 
async def launch_browser(proxy_host: str = None, proxy_port: str = None,
                          proxy_user: str = None, proxy_pass: str = None,
                          user_agent: str = None):
    """
    Launches a stealth Chromium browser.
    Returns (playwright_instance, browser, context).
    Caller must close all three when done.
    
    Args:
        user_agent: Specific User-Agent to use (if None, random one is chosen)
    """
    from playwright_stealth import Stealth
 
    proxy_config = None
    if proxy_host:
        proxy_config = {
            "server": f"http://{proxy_host}:{proxy_port}",
            "username": proxy_user,
            "password": proxy_pass,
        }
 
    viewport = random.choice(VIEWPORTS)
    user_agent = user_agent or random.choice(USER_AGENTS)
 
    pw = await async_playwright().start()
    browser: Browser = await pw.chromium.launch(
        headless=True,
        proxy=proxy_config,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--no-first-run",
        ],
    )
 
    context: BrowserContext = await browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        locale="en-US",
        # Match timezone to your Webshare proxy's city.
        # US proxies → America/New_York. Adjust per account region.
        timezone_id="America/New_York",
        # Permissions that a real browser would have
        permissions=["geolocation"],
    )
 
    # Inject stealth patches — removes navigator.webdriver, randomises canvas fingerprint etc.
    page: Page = await context.new_page()
    stealth = Stealth()
    # Stealth disabled for debugging - re-enable for production to avoid LinkedIn detection
    await stealth.apply_stealth_async(context)
 
    # Additional manual patches on top of playwright-stealth
    await context.add_init_script("""
        // Fake realistic plugin count (real browsers have plugins)
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        // Randomise CPU core count
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => [2, 4, 8][Math.floor(Math.random() * 3)]
        });
        // Remove automation-related chrome flags
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        // Fake device memory (real browsers expose this)
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => [2, 4, 8][Math.floor(Math.random() * 3)]
        });
    """)
 
    return pw, browser, context, page, user_agent
