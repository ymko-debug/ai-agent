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
import logging
from typing import Dict, Any, Optional
from .config import SCRAPE_CHAR_LIMIT

# ── Fix: Python 3.13 + Windows + Streamlit asyncio compatibility ──────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ── session-scoped state ────────────────────────────────────────────────────────
# Maps session_id → {"pw": playwright, "browser": browser, "page": page}
_sessions: Dict[str, Dict[str, Any]] = {}

# CAPTCHA / bot-block signals — if any appear in page text, return blocked signal
_CAPTCHA_SIGNALS = [
    "captcha", "are you a robot", "press & hold", "press and hold",
    "cloudflare", "i'm not a robot", "verify you are human",
    "security check", "access denied", "bot detection",
    "unusual traffic", "please verify", "checking your browser",
    "enable javascript", "ray id",
]

# ── RC20: Health Check Helpers ──────────────────────────────────────────────────

def _is_page_poisoned(page) -> bool:
    """Check if the page is dead, closed, or showing a CAPTCHA/Block screen."""
    try:
        # 1. Physical checks
        if page.is_closed():
            return True
        if not page.context.browser or not page.context.browser.is_connected():
            return True
            
        # 2. Content-based poisoning checks
        url = page.url.lower()
        if "cloudflare" in url or "captcha" in url:
            return True
            
        title = page.title().lower()
        # Common block signals in page titles
        if any(s in title for s in ["captcha", "robot", "human verification", "access denied", "403 forbidden"]):
            return True
            
        return False
    except Exception:
        return True # Assume poisoned if any check fails (safest)

def _human_delay(min_ms: int = 600, max_ms: int = 2000):
    """Random pause simulating human reading/thinking time."""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def _get_session_page(session_id: str, headless: bool = True):
    """Get or create isolated page for this session."""
    global _sessions
    
    # ── RC20: Health Check ──────────────────────────────────────────────────
    if session_id in _sessions:
        sess = _sessions[session_id]
        page = sess.get("page")
        if page:
            if _is_page_poisoned(page):
                logging.getLogger("browser").info(f"Session {session_id} page is poisoned/closed. Resetting...")
                browser_close(session_id)
            else:
                return page
    # ── End Health Check ────────────────────────────────────────────────────

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless = headless,
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-plugins-discovery",
            "--disable-extensions-except=",
            "--enable-features=NetworkService,NetworkServiceLogging",
        ],
    )

    context = browser.new_context(
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
        extra_http_headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    page = context.new_page()

    # Apply playwright-stealth
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    # Fallback webdriver hide
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    
    _sessions[session_id] = {"pw": pw, "browser": browser, "page": page}
    return page


def _page_snapshot(page, max_chars: int = 4000) -> str:
    """Return a readable text snapshot of a page."""
    if page is None:
        return "(no page open)"
    try:
        title = page.title()
        url   = page.url
        text  = page.evaluate(
            """() => {
                const selectors = [
                    'h1', 'h2', 'h3', 'h4', 'p', 'li', 'a', 'button', 'input', 
                    'label', 'span', 'td', 'th', 'table', 'tr',
                    '[class*="result"]', '[class*="row"]', '[class*="item"]'
                ];
                const els = document.querySelectorAll(selectors.join(','));
                return Array.from(els)
                    .map(el => (el.innerText || el.value || '').trim())
                    .filter(t => t && t.length > 2)
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

def scrape_url_with_playwright(url: str, session_id: str = "default") -> Optional[str]:
    """One-shot scrape of a URL using an isolated session context."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    try:
        page = _get_session_page(session_id)
        # Navigation with defensive wait
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass # Continue even if network doesn't settle perfectly

        snapshot = _page_snapshot(page)
        blocked = _check_for_captcha(snapshot, url)
        
        content = blocked or snapshot
        if content:
            return content[:SCRAPE_CHAR_LIMIT]
        return None
    except Exception as e:
        logging.getLogger("browser").warning("scrape_url_with_playwright failed: %s", e)
        return None


def browser_navigate(url: str, session_id: str = "default") -> str:
    """Navigate to a URL with isolated context. Returns text snapshot or CAPTCHA signal."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        page = _get_session_page(session_id)
        # Human-like pre-navigation pause
        _human_delay(400, 1200)
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # Wait for JS to settle + simulate reading time
        _human_delay(1500, 3000)
        # Move mouse to a random position
        page.mouse.move(
            random.randint(100, 900),
            random.randint(100, 600),
        )
        _human_delay(200, 600)

        snapshot = _page_snapshot(page)

        # CAPTCHA check before returning
        blocked = _check_for_captcha(snapshot, url)
        if blocked:
            return blocked

        return snapshot
    except Exception as e:
        return f"(browser_navigate failed: {e})"


def browser_click(target: str, session_id: str = "default") -> str:
    """Click an element in the session's isolated context."""
    page = _get_session_page(session_id)
    try:
        try:
            page.get_by_text(target, exact=False).first.click(timeout=8000)
        except Exception:
            page.click(target, timeout=8000)
        _human_delay(800, 1500)
        snapshot = _page_snapshot(page)
        blocked  = _check_for_captcha(snapshot, page.url)
        return blocked or snapshot
    except Exception as e:
        return f"(browser_click failed — could not find '{target}': {e})"


def browser_type(selector: str, text: str, press_enter: bool = False, session_id: str = "default") -> str:
    """Type text into an input field in the session's isolated context."""
    page = _get_session_page(session_id)
    try:
        try:
            loc = page.get_by_placeholder(selector, exact=False).first
            loc.click(timeout=5000)
            # Type character by character like a human
            for char in text:
                loc.type(char)
                time.sleep(random.uniform(0.04, 0.12))
        except Exception:
            try:
                loc = page.get_by_label(selector, exact=False).first
                loc.click(timeout=5000)
                loc.fill(text)
            except Exception:
                page.click(selector, timeout=5000)
                page.fill(selector, text)

        if press_enter:
            _human_delay(300, 700)
            page.keyboard.press("Enter")
            _human_delay(1500, 3000)

        snapshot = _page_snapshot(page)
        blocked  = _check_for_captcha(snapshot, page.url)
        return blocked or snapshot
    except Exception as e:
        return f"(browser_type failed — could not find '{selector}': {e})"


def browser_get_page_text(session_id: str = "default") -> str:
    """Return a text snapshot of the session's open page."""
    page = _get_session_page(session_id)
    snapshot = _page_snapshot(page)
    blocked  = _check_for_captcha(snapshot, page.url)
    return blocked or snapshot


def browser_close(session_id: str = "default") -> str:
    """Close the isolated browser session."""
    global _sessions
    try:
        sess = _sessions.pop(session_id, None)
        if sess:
            if sess.get("browser"):
                sess["browser"].close()
            if sess.get("pw"):
                sess["pw"].stop()
    except Exception:
        pass
    return f"(browser closed for session {session_id})"


def run_browser_action(intent: Dict[str, Any], session_id: str = "default") -> str:
    """Execute a browser action based on a detected intent dict and session context."""
    action = intent.get("action")

    if action == "navigate":
        return browser_navigate(intent["url"], session_id=session_id)

    elif action == "search":
        result = browser_navigate("https://www.google.com", session_id=session_id)
        if "failed" in result or "CAPTCHA" in result:
            return result
        return browser_type("Search", intent["query"], press_enter=True, session_id=session_id)

    elif action == "click":
        return browser_click(intent["target"], session_id=session_id)

    elif action == "type":
        return browser_type(
            intent["target"],
            intent["text"],
            press_enter=intent.get("press_enter", False),
            session_id=session_id
        )

    elif action == "read":
        return browser_get_page_text(session_id=session_id)

    elif action == "close":
        return browser_close(session_id=session_id)

    return "(unknown browser action)"
