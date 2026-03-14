
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
            return None
        except Exception:
            if attempt == 0:
                time.sleep(1.5)
                continue
            return None
    return None
