"""
core/agent.py — Autonomous agent orchestrator.

New in this version:
  - Atomic writing: No more 'Brain-Damage' if the agent is stopped mid-write
  - Stop sensitivity: The meta-loop now responds instantly to the 'Stop' button
  - Signal-based decoupling: Moved stop flags to core.signals to prevent circular imports
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import (
    TAVILY_API_KEY,
    DAILY_CALL_LIMIT,
    HISTORY_LIMIT,
    MAX_HISTORY_CHARS,
    MAX_TOOL_ROUNDS,
    MAX_TOOL_ROUNDS_COMPLEX,
    MAX_TOOLS_PER_ROUND,
    SCRAPE_CHAR_LIMIT,
    SYSTEM_PROMPT,
)
from core.db import (
    load_history,
    is_over_daily_limit,
    log_call,
    get_cached_search,
    save_cached_search,
)
from core.llm import route_llm
from core.search import needs_search, search_web, detect_browser_intent
from core.scraper import scrape_url_with_playwright
from core.browser import run_browser_action
from core.signals import evict_stop_flag
from core.meta import run_meta_skill_loop
from core.memory import (
    format_memory_by_namespace,
    get_session_summary,
    maybe_summarize_session,
    extract_core_facts,
)
from leadgen.tools import extract_leads_from_text, save_leads_to_spreadsheet

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# MAX_TOOL_ROUNDS, MAX_TOOL_ROUNDS_COMPLEX, MAX_TOOLS_PER_ROUND
# imported from core.config
TOOLS_REGISTRY     = Path("tools_registry.json")
SKILLS_DIR         = Path("skills")

# Tools that receive untrusted external content — sanitize outputs
_WEB_TOOLS = {"browse", "scrape_url", "web_search"}

# Complex task signals — trigger explicit planner
_COMPLEX_SIGNALS = [
    "research", "compare", "analyse", "analyze", "competitors",
    "find all", "list all", "summarize", "build a report",
    "step by step", "multiple", "each", "for every",
    "register", "signup", "login", "fill", "form",
]

# Sites known to aggressively block automation — skip browser, use web_search
BROWSER_BLOCKLIST = [
    "skyscanner.com", "kayak.com", "expedia.com",
    "booking.com", "tripadvisor.com", "hotels.com",
    "google.com/travel", "google.com/flights",
]

# Strings that indicate a CAPTCHA or bot-block page
CAPTCHA_SIGNALS = [
    "captcha", "are you a robot", "press & hold", "press and hold",
    "cloudflare", "i'm not a robot", "verify you are human",
    "security check", "access denied", "bot detection",
    "unusual traffic", "please verify", "checking your browser",
]

# Strings that should never be saved to the search cache
INVALID_CACHE_SIGNALS = CAPTCHA_SIGNALS + [
    "403 forbidden", "404 not found", "page not found",
    "error occurred", "service unavailable",
]

# ── Stop flag registry ─────────────────────────────────────────────────────────
# Maps session_id → threading.Event. When set, the agentic loop exits cleanly.
_stop_flags: Dict[str, threading.Event] = {}


def get_stop_flag(session_id: str) -> threading.Event:
    if session_id not in _stop_flags:
        _stop_flags[session_id] = threading.Event()
    return _stop_flags[session_id]


def request_stop(session_id: str):
    """Called by app.py STOP button. Signals the loop to exit after current tool."""
    get_stop_flag(session_id).set()
    logger.info("Stop requested for session %s", session_id)


def clear_stop(session_id: str):
    """Called at the start of a new task to reset the stop flag."""
    get_stop_flag(session_id).clear()


def is_stopped(session_id: str) -> bool:
    return get_stop_flag(session_id).is_set()


def cleanup_session(session_id: str):
    """Evict the stop flag for a deleted session to prevent memory leak."""
    evict_stop_flag(session_id)


# ---------------------------------------------------------------------------
# Prompt injection sanitization
# ---------------------------------------------------------------------------

INJECTION_RE = re.compile(r"<toolcall>.*?</toolcall>", re.DOTALL | re.IGNORECASE)


def sanitize_tool_output(tool_name: str, raw: str) -> str:
    if tool_name not in _WEB_TOOLS:
        return str(raw)
    cleaned = INJECTION_RE.sub("[removed]", str(raw))
    # Hard length cap only for browser/scrape — Tavily snippets are short
    if tool_name in ("browse", "scrape_url") and len(cleaned) > SCRAPE_CHAR_LIMIT:
        cleaned = cleaned[:SCRAPE_CHAR_LIMIT] + "\n\n[Page truncated — ask to continue reading if needed]"
    return cleaned


# ---------------------------------------------------------------------------
# CAPTCHA / bad content detection
# ---------------------------------------------------------------------------

def _is_captcha_or_blocked(text: str) -> bool:
    lower = text.lower()
    return any(s in lower for s in CAPTCHA_SIGNALS)


def _is_blocked_site(url: str) -> bool:
    return any(blocked in url.lower() for blocked in BROWSER_BLOCKLIST)


# ---------------------------------------------------------------------------
# Cache guard — never save CAPTCHA or error pages
# ---------------------------------------------------------------------------

def _cached_search_web(query: str, max_results: int = 4) -> str | None:
    cached = get_cached_search(query, ttl_hours=1)
    if cached:
        # Validate cache isn't a CAPTCHA or error page
        if _is_captcha_or_blocked(cached) or any(s in cached.lower() for s in INVALID_CACHE_SIGNALS):
            logger.warning("Stale/invalid content in cache for: %s — purging", query[:60])
            save_cached_search(query, "")   # overwrite with empty to expire it
            cached = None
        else:
            logger.debug("Cache hit: %s", query[:60])
            return cached

    result = search_web(query, max_results=max_results)
    # Only cache clean results
    if result and not _is_captcha_or_blocked(result):
        save_cached_search(query, result)
    return result


# ---------------------------------------------------------------------------
# Tool result validation — SOFTER version to prevent infinite retry spirals
# ---------------------------------------------------------------------------

def _result_is_sufficient(query: str, tool_name: str, result: str) -> bool:
    """
    Returns False only when the result is clearly empty or a CAPTCHA block.
    Does NOT call the LLM for a YES/NO check — that was causing spiral loops
    where partial results were repeatedly retried instead of synthesised.
    Claude is smart enough to decide when to stop searching on its own.
    """
    if tool_name not in _WEB_TOOLS:
        return True
    if len(result.strip()) < 40:
        return False
    if _is_captcha_or_blocked(result):
        return False
    return True   # let Claude decide if the content is good enough


# ---------------------------------------------------------------------------
# Optional explicit planner for complex tasks
# ---------------------------------------------------------------------------

def _is_complex_task(prompt: str) -> bool:
    return any(s in prompt.strip().lower() for s in _COMPLEX_SIGNALS)


def _run_planner(prompt: str, actions_list: str) -> str:
    try:
        plan, _ = route_llm(
            [{"role": "user", "content": (
                f'Plan how to complete: "{prompt}"\n\n'
                f"Available actions:\n{actions_list}\n\n"
                "Write a numbered plan (max 5 steps). Do NOT execute — only plan."
            )}],
            task_type="planner",
        )
        return plan
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Build actions list
# ---------------------------------------------------------------------------

def build_actions_list() -> str:
    lines: List[str] = []
    builtins = [
        ("web_search",         "Search web via Tavily. query"),
        ("browse",             "Browser navigate/read ONLY. Use THIS to research a page. args: url, action (navigate/read), target (selector for read). FOR ANY INTERACTION (clicking, typing, form filling), YOU MUST USE create_skill."),
        ("scrape_url",         "Scrape page content. url"),
        ("create_skill",       "Create a specialized Python Skill for a complex task (like registration, form filling, or site-specific automation). skill_name"),
        ("run_skill",          "Run a specialized Python Skill. skill_name, input_data"),
        ("updatecorememory",   "Write fact to memory. namespace, key, value, confidence, source"),
        ("listcorememory",     "Read all memory by namespace"),
        ("deletecorememory",   "Delete memory fact. namespace, key"),
    ]
    for name, desc in builtins:
        lines.append(f"- {name}: {desc}")

    if TOOLS_REGISTRY.exists():
        try:
            registry = json.loads(TOOLS_REGISTRY.read_text(encoding="utf-8"))
            for skill in registry.get("registered_skills", []):
                name  = skill.get("name", "unknown")
                short = skill.get("description", "no description").replace("\n", " ").strip()
                short = (short[:100] + "…") if len(short) > 100 else short
                lines.append(f"- run_skill('{name}'): {short}")
        except Exception as e:
            logger.warning("Could not read tools_registry.json: %s", e)

    if SKILLS_DIR.exists():
        registered: set = set()
        if TOOLS_REGISTRY.exists():
            try:
                reg        = json.loads(TOOLS_REGISTRY.read_text(encoding="utf-8"))
                registered = {s.get("name") for s in reg.get("registered_skills", [])}
            except Exception:
                pass
        for f in sorted(SKILLS_DIR.glob("*.py")):
            name = f.stem.removeprefix("tools_")
            if name not in registered:
                first = f.read_text(encoding="utf-8").split("\n")[0].strip(" #\"'")
                lines.append(f"- run_skill('{name}') [unregistered]: {first or 'no description'}")

    return "\n".join(lines) if lines else "No skills registered yet."


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

def build_system_prompt(session_id: str, current_query: str = "") -> str:
    from core.memory import format_core_memory_for_prompt, format_memory_by_namespace
    # Selective retrieval: only top facts relevant to the current query
    user_mem = format_core_memory_for_prompt(current_query)
    
    # Read ONLY task facts into task_memory slot (keep full for now as they are short-lived)
    task_mem = format_memory_by_namespace(["task"])
    
    # NOTE: "research" is explicitly excluded from the system prompt!

    # Lever 2C: cap session summary to ~100 tokens
    summary = get_session_summary(session_id) or ""
    if summary and len(summary) > 400:
        summary = summary[:400] + "\u2026"
    
    return SYSTEM_PROMPT.format(
        actions_list    = build_actions_list(),
        user_memory     = user_mem or "",           # Lever 2B: empty string, not label
        task_memory     = task_mem or "",            # Lever 2B: empty string, not label
        session_summary = summary,
    )


# ---------------------------------------------------------------------------
# Skill execution
# ---------------------------------------------------------------------------

def _execute_skill(skill_name: str, input_data: dict) -> dict:
    candidates = [SKILLS_DIR / f"{skill_name}.py", SKILLS_DIR / f"tools_{skill_name}.py"]
    skill_path = next((p for p in candidates if p.exists()), None)
    if not skill_path:
        return {"error": f"Skill '{skill_name}' not found in {SKILLS_DIR}/"}
    import importlib.util
    spec   = importlib.util.spec_from_file_location(skill_name, skill_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return {"error": f"Skill load failed: {e}"}
    if not hasattr(module, "run"):
        return {"error": f"Skill '{skill_name}' has no run() function"}
    try:
        return {"result": module.run(input_data)}
    except Exception as e:
        return {"error": f"Skill execution failed: {e}"}


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(tool_name: str, tool_input: dict, session_id: str) -> Any:

    # ── Browse: check blocklist + CAPTCHA fallback ───────────────────────────
    if tool_name == "browse":
        url = tool_input.get("url", "")
        if _is_blocked_site(url):
            logger.info("Blocked site %s — routing to web_search fallback", url)
            domain  = re.sub(r"https?://", "", url).split("/")[0]
            fallback = _cached_search_web(f"{domain} {tool_input.get('query', '')}")
            return {"result": fallback, "note": f"Browser blocklist — used web_search for {domain}"}
        # Pass the full tool_input dict so run_browser_action has everything (target, text, etc)
        # We ensure basic defaults exist to prevent KeyErrors in browser.py
        browser_intent = {
            "action":      tool_input.get("action", "navigate"),
            "url":         tool_input.get("url", ""),
            "target":      tool_input.get("target", ""),
            "text":        tool_input.get("text", ""),
            "query":       tool_input.get("query", ""),
            "press_enter": tool_input.get("press_enter", False),
        }
        result = run_browser_action(browser_intent)
        # CAPTCHA fallback
        if isinstance(result, str) and _is_captcha_or_blocked(result):
            logger.info("CAPTCHA detected on %s — falling back to web_search", url)
            domain   = re.sub(r"https?://", "", url).split("/")[0]
            fallback = _cached_search_web(f"site info {domain} {tool_input.get('query', '')}")
            return {
                "result":  fallback or "No fallback results available.",
                "warning": f"CAPTCHA on {url} — results are from web_search fallback, not live page.",
            }
        return result

    # ── Scrape: CAPTCHA check ────────────────────────────────────────────────
    if tool_name == "scrape_url":
        url    = tool_input.get("url", "")
        result = scrape_url_with_playwright(url)
        if isinstance(result, str) and _is_captcha_or_blocked(result):
            logger.info("CAPTCHA on scrape_url %s — falling back to web_search", url)
            return {
                "result":  _cached_search_web(url) or "No fallback available.",
                "warning": f"CAPTCHA on {url} — used web_search fallback.",
            }
        return result

    if tool_name == "web_search":
        return _cached_search_web(tool_input.get("query", ""))

    if tool_name == "run_skill":
        result = _execute_skill(tool_input.get("skill_name", ""), tool_input.get("input_data", {}))
        # ── Structured Result Contract ────────────────────────────────────
        # Validate skill outcome instead of blindly trusting content checks
        inner = result.get("result", result) if isinstance(result, dict) else result
        if isinstance(inner, dict) and not inner.get("success", True):
            error_msg = inner.get("result", inner.get("error", "unknown"))
            retry_hint = inner.get("retry_hint", "")
            logger.warning("Skill '%s' failed: %s", tool_input.get("skill_name"), error_msg)
            return {"success": False, "error": error_msg, "retry_hint": retry_hint}
        return result
        # ─────────────────────────────────────────────────────────────────

    if tool_name == "create_skill":
        return {"result": run_meta_skill_loop(
            tool_input.get("skill_name", "unnamed"),
            session_id=session_id,
            target_url=tool_input.get("url") or tool_input.get("target_url")
        )}

        from core.db import upsert_memory_with_embedding, NS_USER
        ns        = tool_input.get("namespace", NS_USER)
        key       = tool_input.get("key", "").strip()
        value     = tool_input.get("value", "").strip()
        confidence = float(tool_input.get("confidence", 0.9))
        source    = tool_input.get("source", "llm_explicit")
        expires_days = tool_input.get("expires_days", None)

        if not key or not value:
            return {"error": "key and value are required"}

        success = upsert_memory_with_embedding(
            namespace=ns, key=key, value=value,
            source=source, confidence=confidence,
            session_id=session_id,
            expires_days=int(expires_days) if expires_days else None
        )
        if success:
            return {"success": True, "namespace": ns, "key": key}
        else:
            return {"error": f"Write blocked — confidence {confidence} too low for namespace '{ns}'"}

    if tool_name == "listcorememory":
        from core.db import get_core_memory
        entries = get_core_memory()

        if not entries:
            return "Memory is empty."

        grouped = {}
        for e in entries:
            grouped.setdefault(e["namespace"], []).append(e)

        lines = []
        for ns_key in sorted(grouped.keys()):
            lines.append(f"\n[{ns_key}]")
            for e in grouped[ns_key]:
                exp = f", expires {e['expires_at'][:10]}" if e["expires_at"] else ""
                lines.append(
                    f"  {e['key']} = {e['value']}  "
                    f"(conf={e['confidence']:.2f}, src={e['source']}{exp})"
                )
        return "\n".join(lines)

    if tool_name == "deletecorememory":
        from core.memory import delete_core_memory
        from core.db import NS_USER
        ns  = tool_input.get("namespace", NS_USER)
        key = tool_input.get("key", "").strip()
        if not key:
            return {"error": "key is required"}
        deleted = delete_core_memory(ns, key)
        if deleted:
            return {"success": True, "namespace": ns, "key": key}
        else:
            return {"error": f"No entry found for [{ns}] {key}"}

    return {"error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

TOOL_CALL_RE = re.compile(r"<toolcall>\s*(\{.*?\})\s*</toolcall>", re.DOTALL)


def agentic_loop(
    messages:          List[Dict],
    system_prompt:     str,
    provider_override: Optional[str],
    session_id:        str,
    original_prompt:   str = "",
    is_complex:        bool = False,
) -> tuple[str, str]:
    """Returns (final_answer, provider_used)."""
    max_rounds    = MAX_TOOL_ROUNDS_COMPLEX if is_complex else MAX_TOOL_ROUNDS
    conversation  = list(messages)
    provider_used = "Unknown"
    answer        = ""
    # ── Per-Tool Attempt Cap (Circuit Breaker) ───────────────────────
    # Local variable — resets fresh for every agentic_loop invocation
    _breaker_counts: Dict[str, int] = {}
    # ────────────────────────────────────────────────────────────────

    for round_num in range(max_rounds):

        # Check stop flag before each round
        if is_stopped(session_id):
            logger.info("Stop flag set — exiting agentic loop at round %d", round_num)
            return (
                answer or "⏹️ Task stopped by user.",
                provider_used,
            )

        full_messages = [{"role": "system", "content": system_prompt}] + conversation
        override      = None if provider_override in (None, "Auto (Default)") else provider_override
        answer, provider_used = route_llm(
            full_messages, task_type="general", provider_override=override
        )

        tool_matches = TOOL_CALL_RE.findall(answer)
        if not tool_matches:
            return answer, provider_used   # final answer — no tools needed

        # Cap tool calls per round — prevents Claude from issuing 10 calls at once
        if len(tool_matches) > MAX_TOOLS_PER_ROUND:
            logger.info(
                "Capping tool calls: %d → %d per round", len(tool_matches), MAX_TOOLS_PER_ROUND
            )
            tool_matches = tool_matches[:MAX_TOOLS_PER_ROUND]

        tool_results: List[str] = []

        # ── Per-Tool Attempt Cap (Circuit Breaker) ───────────────────────
        tool_attempt_counts = _breaker_counts  # Defined at task start
        # ────────────────────────────────────────────────────────────────

        for raw in tool_matches:

            # Check stop flag between tool executions too
            if is_stopped(session_id):
                tool_results.append("[Task stopped by user — no further tools executed]")
                break

            try:
                parsed     = json.loads(raw)
                tool_name  = parsed.get("tool", "")
                tool_input = parsed.get("input", {})
            except json.JSONDecodeError as e:
                tool_results.append(f"[Tool parse error: {e} — raw: {raw[:100]}]")
                continue

            # ── Circuit Breaker: same tool called 3+ times = STOP ────────
            tool_key = f"{tool_name}:{tool_input.get('skill_name', tool_input.get('url', tool_input.get('key', str(tool_input)[:40])))}"
            tool_attempt_counts[tool_key] = tool_attempt_counts.get(tool_key, 0) + 1
            if tool_attempt_counts[tool_key] > 2:
                blocked_msg = (
                    f"🛑 Task blocked: '{tool_name}' attempted {tool_attempt_counts[tool_key]} times "
                    f"without success. Human action required."
                )
                logger.warning(blocked_msg)
                tool_results.append(f"[Tool: {tool_name}]\n{blocked_msg}")
                continue
            # ────────────────────────────────────────────────────────────

            logger.info("Round %d — %s(%s)", round_num + 1, tool_name,
                        json.dumps(tool_input)[:120])

            result      = dispatch_tool(tool_name, tool_input, session_id)
            safe_result = sanitize_tool_output(tool_name, json.dumps(result, ensure_ascii=False))

            # Softer validation — only flag truly empty or CAPTCHA results
            if not _result_is_sufficient(original_prompt or tool_input.get("query", ""),
                                         tool_name, safe_result):
                logger.info("Empty/blocked result from %s", tool_name)
                safe_result += (
                    "\n\n[Note: this result appears empty or blocked. "
                    "Synthesise what you have from other sources if available.]"
                )

            tool_results.append(f"[Tool: {tool_name}]\n{safe_result}")

        conversation.append({"role": "assistant", "content": answer})
        conversation.append({
            "role":    "user",
            "content": (
                "[Tool results — use these to continue your answer. "
                "If results are partial, synthesise the best answer you can "
                "rather than retrying indefinitely.]\n\n"
                + "\n\n".join(tool_results)
            ),
        })

    logger.warning("MAX_TOOL_ROUNDS (%d) reached — session %s", max_rounds, session_id)
    return answer or "Agent reached the maximum tool-call rounds without a final answer.", provider_used


# ── Extraction pre-screen ─────────────────────────────────────────────────────

SKIP_EXTRACTION_PATTERNS = [
    r"^(ok|okay|thanks|thank you|got it|sure|sounds good|great)[.!]?$",
    r"^(yes|no|maybe|correct|right|exactly)[.!]?$",
    r"^(can you )?(rephrase|clarify|explain that again)[.?]?$",
    r"^(go ahead|proceed|continue|next)[.!]?$",
    r"^[\U00010000-\U0010ffff\U00002600-\U000027ff]+$",  # emoji-only
]

# Turns containing these signals are ALWAYS extracted regardless of length.
# These are the highest-value facts in the system — never skip them.
IDENTITY_SIGNALS = [
    r"\bmy name is\b", r"\bi('m| am)\b", r"\bcall me\b",
    r"\bwe are\b",     r"\bour company\b", r"\bi run\b",
    r"\bi own\b",      r"\bmy business\b", r"\bmy budget\b",
    r"\bmy goal\b",    r"\bmy email\b",    r"\bmy phone\b",
]

# Turns containing these signals likely produce task: or research: facts.
# Always extract — they carry structured business content worth storing.
RICH_CONTENT_SIGNALS = [
    r"\bfind\b",     r"\bresearch\b", r"\banalyze\b", r"\banalyse\b",
    r"\bbudget\b",   r"\bdeadline\b", r"\bschedule\b", r"\bcompany\b",
    r"\bcontact\b",  r"\bemail\b",    r"\blead\b",     r"\bproject\b",
]

# Short structured data — zip codes, dollar amounts, dates, durations.
# These bypass the length gate because they are high-value despite being brief.
STRUCTURED_DATA_SIGNALS = [
    r"\b\d{4,5}\b",                                # zip codes, years, budgets
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}\b",  # dates
    r"\$[\d,]+",                                    # dollar amounts
    r"\b\d+\s*(?:days?|weeks?|months?)\b",          # durations
    r"\b[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}\b",         # UK postcodes
]

def should_extract(prompt: str, answer: str) -> bool:
    """
    Returns True if this turn is worth sending to the extraction LLM.

    Decision order:
      1. Identity signal in prompt  → always True  (user: namespace facts)
      2. Rich content signal        → always True  (task:/research: facts)
      3. Structured data signal     → always True  (dates, money, codes)
      4. Combined length < 40 chars → False        (trivially short exchange)
      5. Pure acknowledgment prompt → False        (ok / yes / thanks / etc.)
      6. Default                    → True
    """
    # Priority 1 — identity override: short but high value, never skip
    if any(re.search(s, prompt, re.IGNORECASE) for s in IDENTITY_SIGNALS):
        return True

    # Priority 2 — rich content: research/task facts, never skip
    if any(re.search(s, prompt, re.IGNORECASE) for s in RICH_CONTENT_SIGNALS):
        return True

    # Priority 3 — structured data: dates, money, zip codes — short but valuable
    if any(re.search(s, prompt) for s in STRUCTURED_DATA_SIGNALS):
        return True

    # Gate 1 — combined length: too short to contain anything memorable
    if len((prompt + " " + answer).strip()) < 40:
        logger.debug(f"Extraction skipped (short exchange): '{prompt[:60]}'")
        return False

    # Gate 2 — pure acknowledgment pattern
    for pattern in SKIP_EXTRACTION_PATTERNS:
        if re.match(pattern, prompt.strip(), re.IGNORECASE):
            logger.debug(f"Extraction skipped (ack pattern): '{prompt[:60]}'")
            return False

    return True

def safe_extract_core_facts(prompt: str, answer: str, session_id: str,
                             from_skill_success: bool = False):
    """
    Background thread: extract facts from a turn and write to corememory.
    Pre-screens the turn before making any LLM call.
    All writes pass through the confidence gate in update_core_memory().
    """
    # Gate 1 — only extract from user identity OR confirmed skill success
    is_user_identity = any(
        re.search(s, prompt, re.IGNORECASE) for s in IDENTITY_SIGNALS
    )
    if not is_user_identity and not from_skill_success:
        return

    # Gate 2 — never extract if response contains failure language
    POISON_SIGNALS = [
        "cannot", "unable", "can't", "limitation", "incapable",
        "failed", "error", "blocked", "timeout", "captcha",
        "cannot display", "cannot solve", "cannot show"
    ]
    if any(s in answer.lower() for s in POISON_SIGNALS) and not is_user_identity:
        return

    # ── NEW: pre-check — free exit before any LLM cost ───────────────────────
    if not should_extract(prompt, answer):
        return
    # ─────────────────────────────────────────────────────────────────────────

    try:
        from core.db import update_core_memory, NS_USER
        raw = extract_core_facts(prompt, answer, route_llm)
        if not raw or not raw.strip():
            return

        written, skipped = 0, 0
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                fact = json.loads(line)

                # ── SHIELD: NEVER extract 'Agent Limitations' or 'Incapabilities' ──
                # This prevents the 'I cannot' loop where the agent teaches itself it is incompetent.
                if fact.get("namespace") == "agent":
                    val = str(fact.get("value", "")).lower()
                    if any(s in val for s in ["cannot", "unable", "limitation", "incapable", "don't have", "do not have"]):
                        logger.debug(f"Shielded agent limitation fact: {fact['key']}='{val}'")
                        continue
                # ───────────────────────────────────────────────────────────────────

                ns        = fact.get("namespace", "task")
                key       = fact.get("key", "").strip()
                val       = fact.get("value", "").strip()
                source    = fact.get("source", "agent_inferred")
                conf      = float(fact.get("confidence", 0.5))
                expires_days = fact.get("expires_days")

                # 3c. Add default TTL for extracted facts (low-confidence)
                if expires_days is None and conf < 0.95:
                    expires_days = 7  # auto-extracted facts expire in 7 days

                if key and val:
                    success = upsert_memory_with_embedding(
                        namespace=ns, key=key, value=val,
                        source=source, confidence=conf,
                        session_id=session_id,
                        expires_days=int(expires_days) if expires_days else None
                    )
                    if success:
                        written += 1
                    else:
                        skipped += 1
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.debug(f"Skipping malformed memory fact: {e} — {line[:80]}")
                skipped += 1

        logger.debug(f"Memory extraction: {written} written, {skipped} skipped/blocked")

    except Exception as e:
        logger.debug(f"Background core-fact extraction failed: {e}")


# ---------------------------------------------------------------------------
# Main entry point — called by app.py
# ---------------------------------------------------------------------------

def process_user_message(
    prompt:            str,
    session_id:        str,
    use_search:        bool = True,
    provider_override: Optional[str] = None,
) -> Dict[str, Any]:

    # Reset stop flag for new task
    clear_stop(session_id)

    if is_over_daily_limit():
        return {"answer": f"Daily call limit ({DAILY_CALL_LIMIT}) reached. Resets at midnight.",
                "provider": "Blocked", "search_label": "⛔ limit"}

    # ── HITL: Check for active task first ──────────────────────────────
    from core.db import get_active_task, clear_active_task, save_active_task
    active = get_active_task(session_id)
    RESUME_WORDS = r"^(continue|proceed|go ahead|yes|do it|try again|ok|okay|next)[\s!.]*$"

    if active and re.match(RESUME_WORDS, prompt.strip(), re.IGNORECASE):
        logger.info("HITL: Resuming task '%s' for session %s", active['task_type'], session_id)
        # Execute the saved skill again
        result = _execute_skill(active["task_type"], active["task_input"])
        inner  = result.get("result", result) if isinstance(result, dict) else result

        # Clear if success
        if isinstance(inner, dict) and inner.get("success"):
            clear_active_task(session_id)
            # Re-format (copying logic from router for consistency)
            ev     = ", ".join(inner.get("evidence", ["completed"]))
            return {
                "answer": f"✅ Resumed and succeeded!\n\n**Evidence:** {ev}",
                "provider": "HITL Resume",
                "search_label": "🎯 resumed",
            }
        
        # If still failing, keep it active (or update if skill changed internal state)
        return {
            "answer": f"❌ Resumption failed: {inner.get('error') if isinstance(inner, dict) else str(inner)}",
            "provider": "HITL Resume",
            "search_label": "🛑 resume fail",
        }

    # /leads
    if prompt.strip().startswith("/leads"):
        match = re.search(r"/leads\s+niche=(.+?)\s+location=(.+)", prompt, re.IGNORECASE)
        if match:
            niche, location = match.group(1).strip(), match.group(2).strip()
            raw_context = _cached_search_web(
                f"top {niche} businesses in {location} contact info directory", max_results=5
            ) or "No web results found."
            leads, prov = extract_leads_from_text(
                raw_context, extra_instructions=f"Focus on {niche} in {location}."
            )
            if leads:
                os.makedirs("output", exist_ok=True)
                fp   = f"output/leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                save_leads_to_spreadsheet(leads, fp)
                body = f"**Extracted {len(leads)} leads using {prov}!**\n\n"
                body += "".join(
                    f"- **{l.company}** ({l.name}, {l.title}) "
                    f"— {l.email or 'No email'} | {l.phone or 'No phone'}\n"
                    for l in leads
                )
                body += f"\n📁 Saved to `{fp}`"
                return {"answer": body, "provider": prov, "search_label": "🎣 generated leads"}
            return {"answer": f"No leads found for '{niche}' in '{location}'.",
                    "provider": "Tools", "search_label": "🎣 no leads"}
        return {"answer": "Use: `/leads niche=... location=...`",
                "provider": "System", "search_label": "⚠️ error"}

    # /add_skill
    if prompt.strip().startswith("/add_skill"):
        match = re.search(r"/add_skill\s+(.+)", prompt, re.IGNORECASE)
        if match:
            raw_input = match.group(1).strip()
            # Try to extract a URL from the prompt as well
            url_match = re.search(r'https?://\S+', prompt)
            target_url = url_match.group(0).rstrip('.,)') if url_match else None
            
            skill_name = re.sub(r'https?://\S+', '', raw_input).strip().replace(" ", "_").lower()
            
            return {
                "answer": run_meta_skill_loop(skill_name, target_url=target_url, session_id=session_id),
                "provider": "Meta Loop",
                "search_label": "🧠 added skill"
            }
        return {"answer": "Use: `/add_skill name [url]`",
                "provider": "System", "search_label": "⚠️ error"}

    # ── Pre-Flight Task Router ──────────────────────────────────────────────────
    # Routes by VERB (intent), never by site name.
    # Works for any website forever — zero code changes needed per new site.
    TASK_ROUTES = {
        r"(register|sign[\s-]?up|create\s+an?\s+account|sign\s+me\s+up)": "register_on_website",
        # Future verbs — uncomment as you build skills:
        # r"(post|upload|publish).+(artwork|painting|image)":   "post_to_platform",
        # r"(find|get|scrape).+(leads?|contacts?)":             "lead_gen",
        # r"(monitor|track|watch|alert).+(price|cost)":         "price_monitor",
    }

    for pattern, skill_name in TASK_ROUTES.items():
        if re.search(pattern, prompt, re.IGNORECASE):
            logger.info("Pre-flight router: '%s' → '%s'", pattern, skill_name)

            from core.db import get_core_memory
            mem = {e["key"]: e["value"] for e in (get_core_memory() or [])}

            # ── Smart URL inference ─────────────────────────────────────────────
            url_in_prompt = re.search(r'https?://\S+', prompt)

            if url_in_prompt:
                # Explicit URL in prompt — use directly
                reg_url = url_in_prompt.group(0).rstrip('.,)"\' ')

            else:
                # Extract site name from prompt, then search for the real URL
                domain_match = re.search(
                    r'(?:on|at|for|with|to)\s+([\w][\w\s]{1,30}?)(?:\s+now|\s+please|[.,!?]|$)',
                    prompt, re.IGNORECASE
                )
                if domain_match:
                    site_name = domain_match.group(1).strip()
                    logger.info("Router: no URL found — searching for '%s' registration page", site_name)

                    # Search for real registration URL
                    search_result = _cached_search_web(
                        f"{site_name} artist registration sign up page site:*.com OR site:*.org",
                        max_results=3
                    ) or ""

                    # Extract first URL containing registration signals
                    reg_url = ""
                    for candidate in re.findall(r'https?://\S+', search_result):
                        candidate = candidate.rstrip('.,)"\'')
                        if any(sig in candidate.lower() for sig in
                               ["/register", "/signup", "/sign-up", "/join",
                                "/create", "/account", "whysell", "/sell"]):
                            reg_url = candidate
                            break

                    # Fallback: take first URL from search if no registration signal found
                    if not reg_url:
                        u_match = re.search(r'https?://\S+', search_result)
                        if u_match:
                            reg_url = u_match.group(0).rstrip('.,)"\'') + "/register"

                    if not reg_url:
                        return {
                            "answer": (
                                f"I couldn't find the registration page for **{site_name}**. "
                                f"Please paste the direct URL and I'll register you immediately."
                            ),
                            "provider": "Pre-Flight Router",
                            "search_label": "⚠️ url not found",
                        }
                else:
                    return {
                        "answer": (
                            "Which website would you like me to register you on? "
                            "Please include the site name or URL."
                        ),
                        "provider": "Pre-Flight Router",
                        "search_label": "⚠️ missing target",
                    }

            # ── Build skill input from memory ───────────────────────────────────
            skill_input = {
                "url":        reg_url,
                "first_name": mem.get("first_name", ""),
                "last_name":  mem.get("last_name", ""),
                "email":      mem.get("email", ""),
                "password": (
                    mem.get("registration_password") or
                    mem.get("saatchi_art_password") or
                    mem.get("password", "")
                ),
            }

            # Prompt override — email explicitly in prompt
            email_in_prompt = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', prompt)
            if email_in_prompt:
                skill_input["email"] = email_in_prompt.group(0)

            # ── Guard: refuse if credentials missing ────────────────────────────
            missing = []
            if not skill_input["email"]:    missing.append("email")
            if not skill_input["password"]: missing.append("password")
            if missing:
                return {
                    "answer": (
                        f"I need your {' and '.join(missing)} before I can register. "
                        f"Tell me once and I'll remember it for all future registrations."
                    ),
                    "provider": "Pre-Flight Router",
                    "search_label": "⚠️ missing credentials",
                }

            # ── Execute skill ───────────────────────────────────────────────────
            logger.info("Router: registering at %s", reg_url)
            save_active_task(session_id, skill_name, skill_input)
            result = _execute_skill(skill_name, skill_input)
            inner  = result.get("result", result) if isinstance(result, dict) else result

            # ── Human-language response — never raw JSON ────────────────────────
            if isinstance(inner, dict):
                if inner.get("success"):
                    clear_active_task(session_id)
                    ev     = ", ".join(inner.get("evidence", ["completed"]))
                    unfill = inner.get("unfilled_fields", [])
                    answer = (
                        f"✅ Registered successfully!\n\n"
                        f"**Evidence:** {ev}\n"
                        f"**Final URL:** {inner.get('final_url', reg_url)}"
                    )
                    if unfill:
                        answer += f"\n\n⚠️ Skipped fields: `{', '.join(unfill)}`"
                else:
                    error  = inner.get("error") or inner.get("result", "Unknown error")
                    hint   = inner.get("retry_hint", "")
                    unfill = inner.get("unfilled_fields", [])
                    answer = f"❌ Registration did not complete.\n\n**Reason:** {error}"
                    if hint:   answer += f"\n**What to check:** {hint}"
                    if unfill: answer += f"\n**Fields not filled:** `{', '.join(unfill)}`"
            else:
                answer = str(inner)

            return {
                "answer":       answer,
                "provider":     "Pre-Flight Router",
                "search_label": "🎯 routed skill",
            }
    # ── End Pre-Flight Task Router ──────────────────────────────────────────────

    # ── Parallel pre-processing ─────────────────────────────────────────────
    # Summarize, plan, and search/browse run concurrently in a thread pool.
    # This turns total time from sum(all) → max(longest single task).
    import concurrent.futures

    # Browser intent detection is instant (pure regex) — run synchronously
    intent = detect_browser_intent(prompt)

    # Build system prompt + actions list (fast, needed by planner)
    system_prompt = build_system_prompt(session_id, current_query=prompt)
    actions_list  = build_actions_list()

    # ── Thread-safe worker functions ──────────────────────────────────────
    def _worker_summarize():
        """Layer 2: rolling summarisation (LLM call, 2-8s)."""
        if is_stopped(session_id):
            return
        try:
            maybe_summarize_session(session_id, route_llm)
        except Exception as e:
            logger.warning("Summarization worker failed: %s", e)

    def _worker_plan():
        """Optional explicit planner for complex tasks (LLM call, 2-5s)."""
        if is_stopped(session_id):
            return ""
        if _is_complex_task(prompt):
            logger.info("Complex task — running planner: %s", prompt[:80])
            return _run_planner(prompt, actions_list)
        return ""

    def _worker_search():
        """Web search, browser action, or registry scrape (network, 1-15s)."""
        if is_stopped(session_id):
            return "", False, False

        context     = ""
        search_ran  = False
        browser_ran = False

        # Browser action (if intent detected)
        if intent["action"]:
            browser_ran    = True
            search_ran     = True
            browser_result = run_browser_action(intent)
            if _is_captcha_or_blocked(str(browser_result)):
                browser_result = _cached_search_web(intent.get("url", prompt)) or browser_result
            context = (
                f"\n\n[Browser action — {intent['action']}]\n\n"
                f"{browser_result}\n\n[End of browser result]"
            )
        # Web search (if no browser intent)
        elif use_search and needs_search(prompt):
            results = _cached_search_web(prompt)
            if results:
                search_ran = True
                context = (
                    f'\n\n[Web search results for: "{prompt}"]\n\n{results}\n\n'
                    "[End of search results. Use these to inform your answer.]"
                )

        # Public business registry scraping
        if not browser_ran and not is_stopped(session_id) and any(w in prompt.lower() for w in [
            "business", "businesses", "company", "companies", "owner", "owners",
            "registered", "incorporated", "restaurant", "shop", "store", "find", "list"
        ]):
            portal = _cached_search_web(
                f"public business registry database official {prompt}", max_results=3
            )
            if portal:
                for url in re.findall(r"Source:\s*(https?://\S+)", portal):
                    if any(x in url.lower() for x in [
                        ".gov", "sos.", "secretary", "corporations", "bizfile",
                        "sunbiz", "opencorporates", "companieshouse", "abr.business"
                    ]):
                        scraped = scrape_url_with_playwright(url)
                        if scraped and not _is_captcha_or_blocked(scraped):
                            search_ran  = True
                            context += (
                                f"\n\n[Public registry — {url}]\n"
                                "Owner/agent names are legal public disclosures.\n\n"
                                f"{scraped}\n\n[End of registry data]"
                            )
                        break

        return context, search_ran, browser_ran

    # ── Launch all three in parallel ──────────────────────────────────────
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_summarize = executor.submit(_worker_summarize)
        f_plan      = executor.submit(_worker_plan)
        f_search    = executor.submit(_worker_search)
        concurrent.futures.wait([f_summarize, f_plan, f_search])

    # Final stop check after all workers finish
    if is_stopped(session_id):
        return {"answer": "⏹️ Task stopped by user.", "provider": "System", "search_label": ""}

    plan_result = f_plan.result() or ""
    plan_context = f"\n\n[Task plan — follow these steps]\n{plan_result}\n[End of plan]" if plan_result else ""

    search_context, search_ran, browser_ran = f_search.result()

    # Layer 1: working memory
    raw_history  = load_history(session_id, limit=HISTORY_LIMIT)
    api_messages = [{"role": m["role"], "content": m["content"]} for m in raw_history]
    if api_messages and api_messages[-1]["role"] == "user" and api_messages[-1]["content"] == prompt:
        api_messages = api_messages[:-1]

    MAX_CHARS = MAX_HISTORY_CHARS
    total = sum(len(m["content"]) for m in api_messages)
    while total > MAX_CHARS and len(api_messages) > 1:
        removed = api_messages.pop(0)
        total  -= len(removed["content"])

    api_messages.append({"role": "user", "content": prompt + plan_context + search_context})

    # Agentic loop — Lever 3: complex tasks get more rounds
    is_complex = _is_complex_task(prompt)
    answer, provider = agentic_loop(
        messages          = api_messages,
        system_prompt     = system_prompt,
        provider_override = provider_override,
        session_id        = session_id,
        original_prompt   = prompt,
        is_complex        = is_complex,
    )

    log_call(provider)
    # Fire-and-forget: extract core facts in background so the answer
    # is delivered immediately instead of blocking for 1-5s
    _skill_ok = False # General loop extraction doesn't have a 'skill_success' context yet
    threading.Thread(
        target=lambda: safe_extract_core_facts(prompt, answer, session_id, from_skill_success=_skill_ok),
        daemon=True,
    ).start()

    return {
        "answer":       answer,
        "provider":     provider,
        "search_label": "🌐 browser" if browser_ran else ("🔍 searched" if search_ran else "💭 no search"),
    }
