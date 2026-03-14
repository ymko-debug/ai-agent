"""
Local AI Assistant — client_app.py
Primary: OpenAI | Fallback: OpenRouter | Search: Tavily | Memory: SQLite
Run with: streamlit run client_app.py
"""

import os
import sqlite3
import requests
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

# ─────────────────────────────────────────
# CONFIG — change these to switch models
# ─────────────────────────────────────────
load_dotenv()

OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
TAVILY_API_KEY       = os.getenv("TAVILY_API_KEY", "")

OPENAI_MODEL         = "gpt-4o"               # change to gpt-4o-mini for lower cost
OPENROUTER_MODEL     = "anthropic/claude-haiku-4-5-20251001"  # fallback
MAX_TOKENS           = 1024
HISTORY_LIMIT        = 12
DAILY_CALL_LIMIT     = 200
DB_PATH              = "assistant_memory.db"

SYSTEM_PROMPT = """You are a helpful, honest general-purpose assistant for a small business owner.

Rules you must always follow:
- If web search results are included in the message, base your answer primarily on those results.
- If no search results are available, answer from your training knowledge.
- When uncertain, say so clearly. Never fabricate facts, statistics, names, or sources.
- For legal, financial, or medical questions, always note that professional advice should be sought.
- Be concise and direct. Avoid unnecessary filler."""


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
_SEARCH_SIGNALS = [
    "latest", "recent", "today", "this week", "this month", "right now",
    "current", "currently", "news", "price", "cost", "how much",
    "weather", "stock", "rate", "best", "top", "compare", "vs",
    "review", "2024", "2025", "2026", "who is", "what is the",
    "where can i", "find me", "look up", "search for",
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
    writing_tasks = ["write", "draft", "edit", "rewrite", "summarize",
                     "explain", "calculate", "help me", "create a", "make a",
                     "list", "give me", "suggest"]
    for task in writing_tasks:
        if text.startswith(task):
            return False
    return len(text.split()) > 8


# ─────────────────────────────────────────
# WEB SEARCH — Tavily
# ─────────────────────────────────────────
def search_web(query: str, max_results: int = 4) -> str | None:
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
# LLM CALLS
# ─────────────────────────────────────────
def call_openai(messages: list[dict]) -> str:
    client = OpenAI(api_key=OPENAI_API_KEY)
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=full_messages,
        max_tokens=MAX_TOKENS
    )
    return response.choices[0].message.content


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
    """Try OpenAI first; fall back to OpenRouter. Returns (text, provider_name)."""
    if OPENAI_API_KEY:
        try:
            return call_openai(messages), "OpenAI"
        except Exception as e:
            st.warning(f"OpenAI unavailable ({type(e).__name__}) — switching to fallback.", icon="⚠️")

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

    if "session_id" not in st.session_state:
        st.session_state.session_id = new_session_id()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "provider" not in st.session_state:
        st.session_state.provider = "—"

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
        st.markdown(f"OpenAI: {'✅' if OPENAI_API_KEY else '❌ missing'}")
        st.markdown(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '⚠️ no fallback'}")
        st.markdown(f"Tavily: {'✅' if TAVILY_API_KEY else '⚠️ search off'}")
        st.divider()
        calls_today = daily_call_count()
        st.caption(f"Calls today: {calls_today} / {DAILY_CALL_LIMIT}")

    st.header("Ask me anything")
    st.caption(f"Provider: **{st.session_state.provider}**")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Message…"):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        save_message(st.session_state.session_id, "user", prompt)

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

        history = load_history(st.session_state.session_id, limit=HISTORY_LIMIT)
        api_messages = history[:-1]
        api_messages.append({
            "role": "user",
            "content": prompt + search_context
        })

        with st.spinner("Thinking…"):
            if is_over_daily_limit():
                answer = (
                    f"Daily call limit of {DAILY_CALL_LIMIT} reached. "
                    "Resets at midnight. You can raise DAILY_CALL_LIMIT in client_app.py."
                )
                provider = "Blocked"
            else:
                answer, provider = get_llm_response(api_messages)
                log_call(provider)

        st.session_state.provider = provider

        with st.chat_message("assistant"):
            st.markdown(answer)
            search_label = "🔍 searched" if search_ran else "💭 no search"
            st.caption(f"via {provider} · {search_label}")

        st.session_state.messages.append({"role": "assistant", "content": answer})
        save_message(st.session_state.session_id, "assistant", answer)


if __name__ == "__main__":
    main()
