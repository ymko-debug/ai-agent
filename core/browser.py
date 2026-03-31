# core/browser.py
"""
Live browser automation using Playwright Chromium.

New in this version:
  - playwright-stealth: patches 25+ fingerprint vectors automatically
  - Human-like delays and mouse movement before/after navigation
  - CAPTCHA detection: returns CAPTCHA_BLOCKED signal instead of cached garbage
  - Improved anti-detection args

Install requirements:
  pip install playwright-stealth
  playwright install chromium
"""

import sys
import asyncio
import random
import time

# ── Fix: Python 3.13 + Windows + Streamlit asyncio compatibility ──────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from typing import Dict, Any

# ── module-level state ─────────────────────────────────────────────────────────
_playwright_ctx = None
_browser        = None
_page           = None

# CAPTCHA / bot-block signals — if any appear in page text, return blocked signal
_CAPTCHA_SIGNALS = [
    "captcha", "are you a robot", "press & hold", "press and hold",
    "cloudflare", "i'm not a robot", "verify you are human",
    "security check", "access denied", "bot detection",
    "unusual traffic", "please verify", "checking your browser",
    "enable javascript", "ray id",
]


def _human_delay(min_ms: int = 600, max_ms: int = 2000):
    """Random pause simulating human reading/thinking time."""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def _ensure_browser(headless: bool = False):
    """Launch browser with stealth patches if not already running."""
    global _playwright_ctx, _browser, _page
    if _page is not None:
        return

    from playwright.sync_api import sync_playwright
    _playwright_ctx = sync_playwright().start()
    _browser = _playwright_ctx.chromium.launch(
        headless = headless,
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-plugins-discovery",
            "--disable-extensions-except=",
            # Mimic real Chrome install
            "--enable-features=NetworkService,NetworkServiceLogging",
            "--disable-web-security",
        ],
    )

    context = _browser.new_context(
        user_agent      = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport        = {"width": 1366, "height": 768},
        locale          = "en-US",
        timezone_id     = "America/Los_Angeles",
        device_scale_factor = 1,
        has_touch       = False,
        java_script_enabled = True,
        # Accept all content types real browsers accept
        extra_http_headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    _page = context.new_page()

    # Apply playwright-stealth — patches 25+ automation detection vectors
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(_page)
    except ImportError:
        # stealth not installed — still works but detection risk is higher
        _page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    # Fallback webdriver hide (belt + suspenders)
    _page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )


def _page_snapshot(max_chars: int = 4000) -> str:
    """Return a readable text snapshot of the current page."""
    global _page
    if _page is None:
        return "(no page open)"
    try:
        title = _page.title()
        url   = _page.url
        text  = _page.evaluate(
            """() => {
                const els = document.querySelectorAll(
                    'h1,h2,h3,h4,p,li,a,button,input,label,span,td,th'
                );
                return Array.from(els)
                    .map(el => (el.innerText || el.value || '').trim())
                    .filter(t => t && t.length > 1)
                    .join('\\n');
            }"""
        )
        snapshot = f"[Page: {title}]\n[URL: {url}]\n\n{text}"
        return snapshot[:max_chars]
    except Exception as e:
        return f"(could not read page: {e})"


def _check_for_captcha(snapshot: str, url: str) -> str | None:
    """
    Returns a CAPTCHA_BLOCKED string if the page is a bot-block page,
    or None if the page looks normal.
    """
    lower = snapshot.lower()
    if any(s in lower for s in _CAPTCHA_SIGNALS):
        return (
            f"CAPTCHA_BLOCKED: {url} requires human verification. "
            "Do not cache this result. Use web_search as fallback."
        )
    return None


# ── public actions ─────────────────────────────────────────────────────────────

def browser_navigate(url: str) -> str:
    """Navigate to a URL with human-like behaviour. Returns text snapshot or CAPTCHA signal."""
    global _page
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        _ensure_browser()
        # Human-like pre-navigation pause
        _human_delay(400, 1200)
        _page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # Wait for JS to settle + simulate reading time
        _human_delay(1500, 3000)
        # Move mouse to a random position — real users do this on page load
        _page.mouse.move(
            random.randint(100, 900),
            random.randint(100, 600),
        )
        _human_delay(200, 600)

        snapshot = _page_snapshot()

        # CAPTCHA check before returning
        blocked = _check_for_captcha(snapshot, url)
        if blocked:
            return blocked

        return snapshot
    except Exception as e:
        return f"(browser_navigate failed: {e})"


def browser_click(target: str) -> str:
    """Click an element by visible text or CSS selector."""
    global _page
    if _page is None:
        return "(no browser open — navigate somewhere first)"
    try:
        try:
            _page.get_by_text(target, exact=False).first.click(timeout=8000)
        except Exception:
            _page.click(target, timeout=8000)
        _human_delay(800, 1500)
        snapshot = _page_snapshot()
        blocked  = _check_for_captcha(snapshot, _page.url)
        return blocked or snapshot
    except Exception as e:
        return f"(browser_click failed — could not find '{target}': {e})"


def browser_type(selector: str, text: str, press_enter: bool = False) -> str:
    """Type text into an input field identified by placeholder, label, or CSS selector."""
    global _page
    if _page is None:
        return "(no browser open — navigate somewhere first)"
    try:
        try:
            loc = _page.get_by_placeholder(selector, exact=False).first
            loc.click(timeout=5000)
            # Type character by character like a human (slower but less detectable)
            for char in text:
                loc.type(char)
                time.sleep(random.uniform(0.04, 0.12))
        except Exception:
            try:
                loc = _page.get_by_label(selector, exact=False).first
                loc.click(timeout=5000)
                loc.fill(text)
            except Exception:
                _page.click(selector, timeout=5000)
                _page.fill(selector, text)

        if press_enter:
            _human_delay(300, 700)
            _page.keyboard.press("Enter")
            _human_delay(1500, 3000)

        snapshot = _page_snapshot()
        blocked  = _check_for_captcha(snapshot, _page.url)
        return blocked or snapshot
    except Exception as e:
        return f"(browser_type failed — could not find '{selector}': {e})"


def browser_get_page_text() -> str:
    """Return a text snapshot of whatever page is open."""
    if _page is None:
        return "(no page open)"
    snapshot = _page_snapshot()
    blocked  = _check_for_captcha(snapshot, _page.url if _page else "")
    return blocked or snapshot


def browser_close() -> str:
    """Close the browser session."""
    global _playwright_ctx, _browser, _page
    try:
        if _browser:
            _browser.close()
        if _playwright_ctx:
            _playwright_ctx.stop()
    except Exception:
        pass
    _playwright_ctx = None
    _browser        = None
    _page           = None
    return "(browser closed)"


# ── top-level dispatcher ───────────────────────────────────────────────────────

def run_browser_action(intent: Dict[str, Any]) -> str:
    """Execute a browser action based on a detected intent dict."""
    action = intent.get("action")

    if action == "navigate":
        return browser_navigate(intent["url"])

    elif action == "search":
        result = browser_navigate("https://www.google.com")
        if "failed" in result or "CAPTCHA" in result:
            return result
        return browser_type("Search", intent["query"], press_enter=True)

    elif action == "click":
        return browser_click(intent["target"])

    elif action == "type":
        return browser_type(
            intent["target"],
            intent["text"],
            press_enter=intent.get("press_enter", False),
        )

    elif action == "read":
        return browser_get_page_text()

    elif action == "close":
        return browser_close()

    return "(unknown browser action)"
