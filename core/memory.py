# memory.py — BUSINESS LOGIC LAYER
# ─────────────────────────────────────────────────────────────────
# This module owns:
#   - format_memory_by_namespace()   prompt injection formatting
#   - extract_core_facts()           LLM-based fact extraction prompt
#   - safe_extract_core_facts()      → LIVES IN agent.py (needs routellm)
#   - get_session_summary()          read Layer 2 summary
#   - save_session_summary()         write Layer 2 summary
#   - maybe_summarize_session()      Layer 2 fold-and-replace logic
#   - delete_core_memory()           business-logic delete (calls db.py)
#
# This module does NOT own:
#   - update_core_memory()           → lives in db.py
#   - get_core_memory()              → lives in db.py
#   - purge_expired_memory()         → lives in db.py
#   - init_db()                      → lives in db.py
# ─────────────────────────────────────────────────────────────────

import logging
from datetime import datetime
from typing import Optional, Dict, List

from .config import DB_PATH

logger = logging.getLogger("memory")


# ── Layer 3: Core Persistent Memory ────────────────────────────────────────────

# Note: Table initialization and DB operations for Core Memory have been explicitly moved to core/db.py


def delete_core_memory(namespace: str, key: str) -> bool:
    """
    Delete a single fact by (namespace, key).
    Returns True if a row was deleted, False if not found.
    """
    from core.db import delete_core_memory as delete_from_db
    deleted = delete_from_db(namespace, key)
    if deleted:
        logger.info(f"Core memory deleted: [{namespace}] {key}")
    return deleted


def format_memory_by_namespace(namespaces: list[str], exclude_expired: bool = True) -> str:
    from core.db import get_core_memory
    from datetime import datetime
    entries = get_core_memory()
    lines = []
    for ns in namespaces:
        ns_entries = [
            e for e in entries
            if e["namespace"] == ns
            and (not exclude_expired or not e["expires_at"]
                 or e["expires_at"] > datetime.now().isoformat())
        ]
        if ns_entries:
            lines.append(f"[{ns}]")
            for e in ns_entries:
                lines.append(f"  {e['key']} = {e['value']}  (conf={e['confidence']:.2f})")
    return "\n".join(lines) if lines else ""


# ── Layer 2: Rolling Session Summary (Fold & Replace) ─────────────────────────

def get_session_summary(session_id: str) -> str:
    """Retrieve the rolling summary for a session."""
    from core.db import get_session_summary as get_from_db
    return get_from_db(session_id)


def save_session_summary(session_id: str, summary_text: str):
    """Upsert the session summary (fold-and-replace, never append)."""
    from core.db import save_session_summary as save_to_db
    save_to_db(session_id, summary_text)


def estimate_tokens(messages: List[Dict[str, str]]) -> int:
    """Rough token estimate: ~4 chars per token (conservative for English text)."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return total_chars // 4


def maybe_summarize_session(session_id: str, route_llm_fn, token_threshold: int = 3000, keep_recent: int = 6):
    """
    Check if history exceeds token_threshold. If so, summarize the oldest
    messages, fold into the existing summary, and delete the old messages.
    
    This is SYNCHRONOUS — it runs before the next LLM call to prevent race conditions.
    """
    from .db import load_history, _delete_oldest_messages

    all_messages = load_history(session_id, limit=100)  # grab everything

    if len(all_messages) <= keep_recent:
        return  # not enough messages to summarize

    estimated = estimate_tokens(all_messages)
    if estimated <= token_threshold:
        return  # under budget

    # Split: older messages to summarize vs recent messages to keep
    messages_to_summarize = all_messages[:-keep_recent]
    
    if len(messages_to_summarize) < 3:
        return  # too few to be worth a summarization call

    # Build the text to summarize
    existing_summary = get_session_summary(session_id)
    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}" for m in messages_to_summarize
    )

    summarize_prompt = f"""Condense the following into a single brief paragraph (max 150 words).
Capture: user goals, key facts shared, decisions made, and current task status.
Drop: greetings, filler, raw data dumps, tool outputs.

{"PREVIOUS SUMMARY (fold this in):" + chr(10) + existing_summary + chr(10) if existing_summary else ""}
CONVERSATION TO SUMMARIZE:
{conversation_text}"""

    summary_messages = [{"role": "user", "content": summarize_prompt}]
    
    try:
        new_summary, _ = route_llm_fn(summary_messages, task_type="planner")
        save_session_summary(session_id, new_summary)
        
        # Delete the old messages from the DB
        count = len(messages_to_summarize)
        _delete_oldest_messages(session_id, count)
        
        logger.info(f"Summarized {count} messages for session {session_id}. Token est: {estimated} -> keeping {keep_recent} recent.")
    except Exception as e:
        logger.error(f"Summarization failed for session {session_id}: {e}")


# ── Automated Core Memory Extraction ──────────────────────────────────────────

EXTRACTION_PROMPT = """\
You are a memory writer for an AI agent. Analyze the turn below and extract \
facts worth storing permanently.

USER MESSAGE: {prompt}
AGENT ANSWER: {answer}

For each fact, output exactly one JSON object per line:
{{"namespace": "...", "key": "...", "value": "...", \
"confidence": 0.0-1.0, "source": "...", "expires_days": null}}

NAMESPACE RULES — read carefully:
- "user"     → facts about the PERSON WRITING the messages (the agent's owner/operator)
               ONLY write here if the user EXPLICITLY stated something about themselves.
               NEVER infer user identity from research context.
- "task"     → project-specific working facts (flight dates, budgets, target names).
               Always set expires_days: 7.
- "research" → facts about people or companies BEING RESEARCHED.
               A person mentioned IN a user's question is research, NOT the user.
- "agent"    → things the agent learned about how to do its job better.

SOURCE VALUES: "user_stated" | "agent_inferred" | "web_scraped"
CONFIDENCE: user_stated=0.9-1.0, agent_inferred=0.4-0.7, web_scraped=0.6-0.8

HARD RULES:
1. If the user asks "find info about John" → John is "research", NOT "user"
2. If unsure of namespace → output nothing, skip the fact
3. Never write namespace "user" with source "agent_inferred"
4. Output ONLY JSON lines. No explanation. No prose.

Example output:
{{"namespace": "user", "key": "preferred_language", "value": "Polish", \
"confidence": 0.95, "source": "user_stated", "expires_days": null}}
{{"namespace": "research", "key": "skyler_peake_company", \
"value": "Peake Management LLC", "confidence": 0.9, \
"source": "web_scraped", "expires_days": 7}}
"""

def extract_core_facts(prompt: str, answer: str, route_llm_fn) -> str:
    """
    Returns purely the raw JSON string generated by the LLM based on the new extraction prompt.
    The parsing and saving is handled securely by safe_extract_core_facts.
    """
    messages = [{"role": "user", "content": EXTRACTION_PROMPT.format(prompt=prompt, answer=answer)}]
    try:
        # executor = strong model — namespace safety is critical here
        # checker (GLM-4.5 free tier) is NOT acceptable for this decision
        response, _ = route_llm_fn(messages, task_type="executor")
        return response
    except Exception as e:
        logger.debug(f"Core fact extraction LLM call failed: {e}")
        return ""
