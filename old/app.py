"""
Local AI Assistant — app.py
Primary: Claude (Anthropic) | Fallback: OpenRouter | Search: Tavily | Memory: SQLite
Run with: streamlit run app.py
"""

import os
import sqlite3
import requests
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

import anthropic
from openai import OpenAI  # OpenRouter uses the OpenAI-compatible API

# ─────────────────────────────────────────
# CONFIG — change these to switch models
# ─────────────────────────────────────────
load_dotenv()

CLAUDE_API_KEY       = os.getenv("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
TAVILY_API_KEY       = os.getenv("TAVILY_API_KEY", "")

CLAUDE_MODEL         = "claude-sonnet-4-6"
OPENROUTER_MODEL     = "openai/gpt-4o-mini"   # cheap, fast fallback
MAX_TOKENS           = 1024
HISTORY_LIMIT        = 12                      # messages to send with each request
DAILY_CALL_LIMIT     = 200                     # hard cap on LLM calls per calendar day
DB_PATH              = "assistant_memory.db"

SYSTEM_PROMPT = """You are a capable, direct general-purpose assistant for a small business owner.

CORE BEHAVIOR:
- Complete tasks fully. Never give the user a DIY script or template when you can just do the task yourself.
- When you cannot find specific data, explain briefly and provide everything else you CAN find. Never refuse the whole task.
- Never fabricate business names, addresses, phone numbers, or owner names. Only state what you actually found.
- If web search results are included in the message, use them as your primary source. Cite the source.
- If no search results are available, answer from training knowledge and say so.
- When uncertain about a fact, say so. Never invent details to fill gaps.
- For legal, financial, or medical questions, note that professional advice should be sought.
- Be concise and direct. No unnecessary warnings, bullet-pointed refusals, or caveats.
- For file creation tasks: write complete Python code using openpyxl, fully pre-filled with real data, ready to run.

PUBLIC RECORDS — IMPORTANT:
- Washington Secretary of State (ccfs.sos.wa.gov) business registrations are 100% public record.
- Registered agent names, owner names, business addresses in that database are legally required public disclosures.
- Treat this data the same as you would a phone book or chamber of commerce directory — no privacy concerns apply.
- When SOS records are provided in the context, use them fully including names.

WHAT YOU ARE CAPABLE OF:
- Searching the web for current information (when search results are provided to you)
- Using Washington SOS public records (when provided in context)
- Writing and running Python scripts to create Excel files, documents, and other outputs
- Research, analysis, summarization, calculations
- Finding publicly available business information

WHAT TO DO WHEN DATA IS INCOMPLETE:
- Include what you found, mark missing fields as blank or "not publicly listed"
- Add a single brief note explaining why, once — do not repeat it or lecture about it"""


# ─────────────────────────────────────────
# DATABASE — local SQLite for session memory
# ─────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            timestamp   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT NOT NULL,
            provider  TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_call(provider: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO call_log (date, provider, timestamp) VALUES (?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d"), provider, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def daily_call_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM call_log WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()
    return count


def is_over_daily_limit() -> bool:
    return daily_call_count() >= DAILY_CALL_LIMIT


def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO conversations (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def load_history(session_id: str, limit: int = HISTORY_LIMIT):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def list_sessions(limit: int = 15):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM conversations ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def delete_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM conversations WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────
# SEARCH INTENT DETECTION
# ─────────────────────────────────────────
# Keywords that strongly signal live information is needed
_SEARCH_SIGNALS = [
    "latest", "recent", "today", "this week", "this month", "right now",
    "current", "currently", "news", "price", "cost", "how much",
    "weather", "stock", "rate", "best", "top", "compare", "vs",
    "review", "2024", "2025", "2026", "who is", "what is the",
    "where can i", "find me", "look up", "search for",
    # Business lookup signals
    "find", "business", "businesses", "company", "companies", "store",
    "restaurant", "shop", "owner", "owners", "directory", "address",
    "phone", "contact", "near", "in olympia", "in seattle", "in wa",
    "local", "nearby",
]

# Short greetings and conversational openers — never need search
_NO_SEARCH_PATTERNS = [
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
    "sure", "yes", "no", "great", "good", "got it", "understood",
    "bye", "goodbye", "help", "what can you do", "who are you",
]

def needs_search(prompt: str) -> bool:
    """Return True only when the message genuinely benefits from live web results."""
    text = prompt.strip().lower()

    # Very short messages are almost never search queries
    if len(text.split()) <= 3:
        for pat in _NO_SEARCH_PATTERNS:
            if text == pat or text.startswith(pat):
                return False
        # Still short but not a greeting — borderline, skip search
        if len(text.split()) <= 2:
            return False

    # Contains explicit search signals
    for signal in _SEARCH_SIGNALS:
        if signal in text:
            return True

    # Contains a question mark and is long enough to be a real query
    if "?" in text and len(text.split()) > 5:
        return True

    # Writing/editing/analysis tasks don't need search
    writing_tasks = ["write", "draft", "edit", "rewrite", "summarize",
                     "explain", "calculate", "help me", "create a", "make a",
                     "list", "give me", "suggest"]
    for task in writing_tasks:
        if text.startswith(task):
            return False

    # Default: skip search for short conversational messages,
    # run it for anything substantive
    return len(text.split()) > 8


# ─────────────────────────────────────────
# WEB SEARCH — Tavily
# ─────────────────────────────────────────
def search_web(query: str, max_results: int = 4) -> str | None:
    """Returns formatted search results as a string, or None on failure.
    Retries once after a short delay before giving up.
    """
    if not TAVILY_API_KEY:
        return None
    import time
    for attempt in range(2):
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query, "max_results": max_results},
                timeout=12
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return None
            lines = []
            for r in results[:max_results]:
                lines.append(f"Source: {r.get('url', '')}\nTitle: {r.get('title', '')}\n{r.get('content', '')}")
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



# ─────────────────────────────────────────
# GENERAL PLAYWRIGHT SCRAPER
# Fetches any URL that requires JavaScript to render (SPAs, government portals, etc.)
# Used when Tavily finds a public records URL but can't read its JS-rendered content.
# ─────────────────────────────────────────
def scrape_url_with_playwright(url: str) -> str | None:
    """
    Loads a URL in a real headless browser and returns all visible text.
    Works on JS-rendered pages that web_fetch and Tavily cannot read.
    Returns None if Playwright is not installed or page fails to load.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=15000)
            # Extract all visible text, excluding scripts and styles
            text = page.evaluate("""() => {
                const els = document.querySelectorAll(
                    'table, tr, td, th, li, p, h1, h2, h3, h4, [class*="result"], [class*="row"], [class*="item"]'
                );
                return Array.from(els)
                    .map(el => el.innerText?.trim())
                    .filter(t => t && t.length > 2)
                    .join('\\n');
            }""")
            browser.close()
            return text[:8000] if text else None  # cap to avoid overflowing context
    except Exception:
        return None


def call_claude(messages: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    return response.content[0].text


def call_openrouter(messages: list[dict]) -> str:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=full_messages,
        max_tokens=MAX_TOKENS
    )
    return response.choices[0].message.content


def get_llm_response(messages: list[dict]) -> tuple[str, str]:
    """Try Claude first; fall back to OpenRouter. Returns (text, provider_name)."""
    if CLAUDE_API_KEY:
        try:
            return call_claude(messages), "Claude"
        except anthropic.RateLimitError:
            st.warning("Claude rate limit reached — switching to fallback provider.", icon="⚠️")
        except anthropic.AuthenticationError:
            st.error("Claude API key invalid. Check your .env file.", icon="🔑")
        except Exception as e:
            st.warning(f"Claude unavailable ({type(e).__name__}) — switching to fallback.", icon="⚠️")

    if OPENROUTER_API_KEY:
        try:
            return call_openrouter(messages), "OpenRouter"
        except Exception as e:
            return f"Both providers failed. Error: {e}", "Error"

    return "No API keys configured. Please add keys to your .env file.", "Error"


# ─────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────
def new_session_id() -> str:
    return datetime.now().strftime("session_%Y%m%d_%H%M%S")


def main():
    st.set_page_config(
        page_title="AI Assistant",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    init_db()

    # ── Session state init ──
    if "session_id" not in st.session_state:
        st.session_state.session_id = new_session_id()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "provider" not in st.session_state:
        st.session_state.provider = "—"

    # ── Sidebar ──
    with st.sidebar:
        st.title("🤖 AI Assistant")
        st.caption(f"Session: `{st.session_state.session_id}`")

        st.divider()
        st.subheader("⚙️ Settings")

        use_search = st.toggle(
            "Web search",
            value=True,
            help="Queries Tavily before each answer. Requires TAVILY_API_KEY in .env"
        )

        st.divider()
        if st.button("➕ New session", use_container_width=True):
            st.session_state.session_id = new_session_id()
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.subheader("📚 Past sessions")
        sessions = list_sessions()
        current = st.session_state.session_id
        for sid in sessions:
            label = sid.replace("session_", "")
            is_current = sid == current
            col1, col2 = st.columns([4, 1])
            with col1:
                if st.button(label, key=f"load_{sid}", type="primary" if is_current else "secondary", use_container_width=True):
                    st.session_state.session_id = sid
                    st.session_state.messages = [
                        {"role": m["role"], "content": m["content"]}
                        for m in load_history(sid)
                    ]
                    st.rerun()
            with col2:
                if st.button("🗑", key=f"del_{sid}", help="Delete this session"):
                    delete_session(sid)
                    if sid == current:
                        st.session_state.session_id = new_session_id()
                        st.session_state.messages = []
                    st.rerun()

        st.divider()
        st.subheader("🔑 Key status")
        st.markdown(f"Claude: {'✅' if CLAUDE_API_KEY else '❌ missing'}")
        st.markdown(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '⚠️ no fallback'}")
        st.markdown(f"Tavily: {'✅' if TAVILY_API_KEY else '⚠️ search off'}")
        st.divider()
        calls_today = daily_call_count()
        st.caption(f"Calls today: {calls_today} / {DAILY_CALL_LIMIT}")

    # ── Main chat area ──
    st.header("Ask me anything")
    st.caption(f"Provider: **{st.session_state.provider}**")

    # Display history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input
    if prompt := st.chat_input("Message…"):

        # Show user message immediately
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        save_message(st.session_state.session_id, "user", prompt)

        # Web search — only when the message actually needs live information
        search_context = ""
        search_ran = False
        if use_search and needs_search(prompt):
            with st.spinner("🔍 Searching the web…"):
                results = search_web(prompt)
                if results:
                    search_ran = True
                    search_context = (
                        f"\n\n[Web search results for: \"{prompt}\"]\n\n{results}\n\n"
                        f"[End of search results. Use these to inform your answer.]"
                    )
                elif TAVILY_API_KEY:
                    st.caption("⚠️ No search results returned — answering from training knowledge.")

            # If the query looks like a business/owner lookup, also search for
            # the relevant public records database and scrape it with Playwright.
            # This works for any city or country — Tavily finds the right portal.
            is_business_lookup = any(w in prompt.lower() for w in [
                "business", "businesses", "company", "companies",
                "owner", "owners", "registered", "incorporated",
                "restaurant", "shop", "store", "find", "list"
            ])
            if is_business_lookup:
                with st.spinner("📋 Looking for public business records…"):
                    # Ask Tavily to find the right public records portal
                    records_query = f"public business registry database official {prompt}"
                    portal_results = search_web(records_query, max_results=3)

                    # Extract URLs from Tavily results that look like official databases
                    public_record_urls = []
                    if portal_results:
                        import re
                        urls = re.findall(r'Source:\s*(https?://[^\s\n]+)', portal_results)
                        # Prefer government and official registry URLs
                        for url in urls:
                            domain = url.lower()
                            if any(x in domain for x in [
                                ".gov", "sos.", "secretary", "corporations",
                                "bizfile", "sunbiz", "opencorporates",
                                "companieshouse", "abr.business", "register"
                            ]):
                                public_record_urls.append(url)

                    # Try to scrape the first promising URL with Playwright
                    for url in public_record_urls[:1]:
                        scraped = scrape_url_with_playwright(url)
                        if scraped:
                            search_ran = True
                            search_context += (
                                f"\n\n[Public business registry data from {url}]\n"
                                f"Note: Business registries are public record — "
                                f"owner and registered agent names are legal public disclosures.\n\n"
                                f"{scraped}\n\n[End of registry data]"
                            )
                            break

        # Build message list for API
        history = load_history(st.session_state.session_id, limit=HISTORY_LIMIT)
        # Replace last user message with version that includes search context
        api_messages = history[:-1]  # everything except the message we just saved
        api_messages.append({
            "role": "user",
            "content": prompt + search_context
        })

        # Call LLM
        with st.spinner("Thinking…"):
            if is_over_daily_limit():
                answer = (
                    f"Daily call limit of {DAILY_CALL_LIMIT} reached. "
                    "Resets at midnight. You can raise DAILY_CALL_LIMIT in app.py."
                )
                provider = "Blocked"
            else:
                answer, provider = get_llm_response(api_messages)
                log_call(provider)

        st.session_state.provider = provider

        # Show assistant response
        with st.chat_message("assistant"):
            st.markdown(answer)
            search_label = "🔍 searched" if search_ran else "💭 no search"
            st.caption(f"via {provider} · {search_label}")

        st.session_state.messages.append({"role": "assistant", "content": answer})
        save_message(st.session_state.session_id, "assistant", answer)


if __name__ == "__main__":
    main()
