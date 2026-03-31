
import re
import requests
from .config import TAVILY_API_KEY


_SEARCH_SIGNALS = [
    "latest", "recent", "today", "this week", "this month", "right now",
    "current", "currently", "news", "price", "cost", "how much",
    "weather", "stock", "rate", "best", "top", "compare", "vs",
    "review", "2024", "2025", "2026", "who is", "what is the",
    "where can i", "find me", "look up", "search for",
    "find", "business", "businesses", "company", "companies", "store",
    "restaurant", "shop", "owner", "owners", "directory", "address",
    "phone", "contact", "near", "in olympia", "in seattle", "in wa",
    "local", "nearby",
]

_NO_SEARCH_PATTERNS = [
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
    "sure", "yes", "no", "great", "good", "got it", "understood",
    "bye", "goodbye", "help", "what can you do", "who are you",
]


def needs_search(prompt: str) -> bool:
    text = prompt.strip().lower()
    if len(text.split()) <= 3:
        for pat in _NO_SEARCH_PATTERNS:
            if text == pat or text.startswith(pat):
                return False
        if len(text.split()) <= 2:
            return False
    for signal in _SEARCH_SIGNALS:
        if signal in text:
            return True
    if "?" in text and len(text.split()) > 5:
        return True
    writing_tasks = [
        "write", "draft", "edit", "rewrite", "summarize",
        "explain", "calculate", "help me", "create a", "make a",
        "list", "give me", "suggest",
    ]
    for task in writing_tasks:
        if text.startswith(task):
            return False
    return len(text.split()) > 8


# ─────────────────────────────────────────
# BROWSER INTENT DETECTION
# ─────────────────────────────────────────

def detect_browser_intent(prompt: str) -> dict:
    """
    Detect if the user wants a browser action.
    Returns a dict with at minimum {"action": <str or None>}.
    """
    text = prompt.strip().lower()

    # ── register / sign up / login (Skill-First Override) ──────────────────────
    # We check these first so they aren't hijacked by generic URL navigation.
    form_signals = [r'\bregister\b', r'\bsign\s*up\b', r'\bcreate\s+account\b', r'\bjoin\b', r'\blogin\b', r'\bsign\s*in\b']
    if any(re.search(s, text) for s in form_signals):
        return {"action": None} # Forces LLM-based planning/skill-building

    # ── navigate / open / go to ────────────────────────────────────────────────
    nav_match = re.search(
        r'(?:open|go to|navigate to|visit|browse to|load|show me)?\s*'
        r'(https?://[^\s]+|[a-z0-9\-]+\.(?:com|org|net|io|gov|edu|co|ai|app|dev)[^\s]*)',
        text,
    )
    if nav_match or re.search(r'\b(open|go to|navigate to|visit)\b', text):
        # Extract URL-like token
        url_match = re.search(
            r'(https?://[^\s]+|[a-z0-9\-]+\.(?:com|org|net|io|gov|edu|co|ai|app|dev)[^\s]*)',
            text,
        )
        if url_match:
            return {"action": "navigate", "url": url_match.group(1)}

    # ── google search / search for ─────────────────────────────────────────────
    google_match = re.search(
        r'(?:search(?:\s+for)?|google(?:\s+for)?)\s+(.+)',
        text,
    )
    if google_match:
        query = google_match.group(1).strip().rstrip('.')
        return {"action": "search", "query": query}

    # ── click ──────────────────────────────────────────────────────────────────
    click_match = re.search(r'\bclick(?:\s+on)?\s+["\']?(.+?)["\']?$', text)
    if click_match:
        return {"action": "click", "target": click_match.group(1).strip()}

    # ── type into field ────────────────────────────────────────────────────────
    type_match = re.search(
        r'type\s+["\']?(.+?)["\']?\s+(?:in(?:to)?|on)\s+(.+)',
        text,
    )
    if type_match:
        return {
            "action": "type",
            "text": type_match.group(1).strip(),
            "target": type_match.group(2).strip(),
            "press_enter": bool(re.search(r'\b(and\s+)?(?:press|hit|submit)\b', text)),
        }

    # ── register / sign up / login (already handled above) ─────────────────────

    # ── read / what's on the page ──────────────────────────────────────────────
    if re.search(r"\b(read|what'?s? on|show|describe)\s+(the\s+)?page\b", text):
        return {"action": "read"}

    # ── close browser ──────────────────────────────────────────────────────────
    if re.search(r'\bclose\s+(the\s+)?browser\b', text):
        return {"action": "close"}

    return {"action": None}


def search_web(query: str, max_results: int = 4) -> str | None:
    if not TAVILY_API_KEY:
        return None
    import time

    for attempt in range(2):
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                },
                timeout=12,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return None
            lines = []
            for r in results[:max_results]:
                lines.append(
                    f"Source: {r.get('url', '')}\nTitle: {r.get('title', '')}\n{r.get('content', '')}"
                )
            return "\n\n---\n\n".join(lines)
        except requests.exceptions.Timeout:
            if attempt == 0:
                time.sleep(1.5)
                continue
            import logging; logging.getLogger("search").warning("Tavily timeout for query: %s", query)
            return "[Search timed out — answering from memory]"
        except requests.exceptions.ConnectionError:
            import logging; logging.getLogger("search").warning("Tavily unreachable (connection error)")
            return "[Search unavailable — answering from memory]"
        except requests.exceptions.HTTPError as e:
            import logging; logging.getLogger("search").warning("Tavily HTTP error %s for query: %s", e, query)
            return None
        except Exception as e:
            import logging; logging.getLogger("search").warning("Tavily unexpected error: %s", e)
            if attempt == 0:
                time.sleep(1.5)
                continue
            return None
    return None
