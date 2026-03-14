
import streamlit as st
from datetime import datetime

from core.config import (
    CLAUDE_API_KEY,
    OPENROUTER_API_KEY,
    TAVILY_API_KEY,
    HISTORY_LIMIT,
    DAILY_CALL_LIMIT,
)
from core.db import (
    init_db,
    save_message,
    load_history,
    list_sessions,
    delete_session,
    daily_call_count,
    is_over_daily_limit,
    log_call,
)
from core.llm import get_llm_response
from core.search import needs_search, search_web
from core.scraper import scrape_url_with_playwright


def new_session_id() -> str:
    return datetime.now().strftime("session_%Y%m%d_%H%M%S")


def main():
    st.set_page_config(
        page_title="AI Assistant",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
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
            help="Queries Tavily before each answer. Requires TAVILY_API_KEY in .env",
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
                if st.button(
                    label,
                    key=f"load_{sid}",
                    type="primary" if is_current else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.session_id = sid
                    st.session_state.messages = [
                        {"role": m["role"], "content": m["content"]}
                        for m in load_history(sid, limit=HISTORY_LIMIT)
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
                        f"\n\n[Web search results for: \"{prompt}\"]\n\n"
                        f"{results}\n\n"
                        "[End of search results. Use these to inform your answer.]"
                    )
                elif TAVILY_API_KEY:
                    st.caption(
                        "⚠️ No search results returned — answering from training knowledge."
                    )

        is_business_lookup = any(
            w in prompt.lower()
            for w in [
                "business",
                "businesses",
                "company",
                "companies",
                "owner",
                "owners",
                "registered",
                "incorporated",
                "restaurant",
                "shop",
                "store",
                "find",
                "list",
            ]
        )
        if is_business_lookup:
            with st.spinner("📋 Looking for public business records…"):
                records_query = f"public business registry database official {prompt}"
                portal_results = search_web(records_query, max_results=3)

                public_record_urls = []
                if portal_results:
                    import re

                    urls = re.findall(r"Source:\s*(https?://\S+)", portal_results)
                    for url in urls:
                        domain = url.lower()
                        if any(
                            x in domain
                            for x in [
                                ".gov",
                                "sos.",
                                "secretary",
                                "corporations",
                                "bizfile",
                                "sunbiz",
                                "opencorporates",
                                "companieshouse",
                                "abr.business",
                                "register",
                            ]
                        ):
                            public_record_urls.append(url)

                for url in public_record_urls[:1]:
                    scraped = scrape_url_with_playwright(url)
                    if scraped:
                        search_ran = True
                        search_context += (
                            f"\n\n[Public business registry data from {url}]\n"
                            "Note: Business registries are public record — "
                            "owner and registered agent names are legal public disclosures.\n\n"
                            f"{scraped}\n\n[End of registry data]"
                        )
                        break

        history = load_history(st.session_state.session_id, limit=HISTORY_LIMIT)
        api_messages = history[:-1]
        api_messages.append({"role": "user", "content": prompt + search_context})

        with st.spinner("Thinking…"):
            if is_over_daily_limit():
                answer = (
                    f"Daily call limit of {DAILY_CALL_LIMIT} reached. "
                    "Resets at midnight. You can raise DAILY_CALL_LIMIT in config."
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
