# core/llm.py
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Generator, List, Optional, Tuple

import anthropic
from openai import OpenAI

from .db import DbConn
from .config import (
    CLAUDE_API_KEY, CLAUDE_MODEL,
    OPENROUTER_API_KEY, TASK_MODEL_MAP,
    OPENROUTER_MODEL_AGENTIC, OPENROUTER_MODEL_CHECKER,
    NVIDIA_API_KEY, NVIDIA_MODEL, NVIDIA_ROLES,
    NVIDIA_MAX_TOKENS, NVIDIA_TEMPS,
    MAX_TOKENS, SYSTEM_PROMPT,
    TEST_MODE,
)

logger = logging.getLogger(__name__)

Messages = List[Dict[str, str]]

_trace_lock = threading.Lock()   # module-level lock for thread-safe audit logging

# Injected into NVIDIA calls only — smaller models need explicit tool format reminder
TOOL_FORMAT_REMINDER = """
CRITICAL — when calling a tool output EXACTLY this format, nothing else:
<toolcall>{"tool": "TOOLNAME", "input": {"KEY": "VALUE"}}</toolcall>
Wait for Tool results before writing your final answer.
"""

ROLE_SYSTEM_PROMPTS = {
    "checker": (
        "You are a classifier. Respond with YES, NO, or a single short fact. "
        "Never produce <toolcall> blocks. Never explain. Never ask questions."
    ),
    "planner": (
        "You are a task planner. Output ONLY a numbered list of concrete steps (max 5). "
        "Each step MUST name a specific tool: web_search, browse, run_skill, create_skill, "
        "updatecorememory. Do NOT ask for clarification. Do NOT say 'I need more info'. "
        "If the task is vague, plan around what you can infer and proceed."
    ),
    "deep_plan": (
        "You are a strategic execution planner with full session context. "
        "Output ONLY a numbered plan (max 7 steps) using the available tools listed below. "
        "Do NOT ask for clarification. Always produce a plan."
    ),
    "synthesis": (
        "You are a synthesis engine. Condense the provided content into a clear, "
        "factual answer. Never produce <toolcall> blocks. Be concise."
    ),
    "summarize": (
        "You are a summarizer. Compress the conversation into 3-5 bullet points "
        "preserving key facts, decisions, and pending tasks. Never produce <toolcall> blocks."
    ),
    "executor": (
        "You are a JSON output engine. Respond ONLY with valid JSON. No prose. No explanation."
    ),
}


def _scope_messages(messages: Messages, task_type: str) -> Messages:
    """
    Replace the system message with a minimal role-specific prompt for
    non-agentic roles. Prevents the full 4k-token agent system prompt from
    leaking into checker, planner, synthesis, and summarize calls.
    """
    role_prompt = ROLE_SYSTEM_PROMPTS.get(task_type)
    if not role_prompt:
        return messages  # general/critical/fallback — keep full agent system prompt
    stripped = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": role_prompt}] + stripped


class LLMError(Exception):
    pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_system_messages(messages: Messages) -> Tuple[str, Messages]:
    """Split system message from the rest. Returns (system_text, user_messages)."""
    if messages and messages[0].get("role") == "system":
        return messages[0]["content"], messages[1:]
    return SYSTEM_PROMPT, messages


def safe_content(text: str | None, provider: str) -> str:
    """Guard against None content — some free models return null on rate limits."""
    if text is None:
        logger.warning(f"{provider} returned None content, treating as empty string")
        return ""
    return text


# ── Provider call functions ───────────────────────────────────────────────────

def call_claude(messages: Messages) -> str:
    if not CLAUDE_API_KEY:
        raise LLMError("Claude API key missing")
    system_text, user_messages = extract_system_messages(messages)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},  # prompt caching — 10x cheaper from round 2
            }
        ],
        messages=user_messages,
        timeout=120,
    )
    return safe_content(resp.content[0].text, "Claude")


def call_openrouter_model(messages: Messages, model: str) -> str:
    """
    Generic OpenRouter call. Accepts any model string from TASK_MODEL_MAP.
    Replaces the old call_openrouter(strong=True/False) boolean API.
    """
    if not OPENROUTER_API_KEY:
        raise LLMError("OpenRouter API key missing")
    system_text, user_messages = extract_system_messages(messages)
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        timeout=60,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_text}] + user_messages,
        max_tokens=MAX_TOKENS,
    )
    content = safe_content(resp.choices[0].message.content, f"OpenRouter/{model}")
    if not content:
        raise LLMError(f"OpenRouter returned empty content for model {model}")
    return content


def call_nvidia(messages: Messages, task_type: str = "synthesis") -> str:
    """
    NVIDIA NIM call — Nemotron 3 Super (free, 1M context).
    Applies role-specific temperature and token cap to prevent verbosity.
    TOOL_FORMAT_REMINDER injected only when task_type is a tool-calling role.
    """
    if not NVIDIA_API_KEY:
        raise LLMError("NVIDIA API key missing")

    system_text, user_messages = extract_system_messages(messages)

    # Inject tool format reminder only if this role needs tool calls
    TOOL_CALLING_ROLES = {"research"}   # extend if needed
    if task_type in TOOL_CALLING_ROLES:
        system_text = system_text + "\n" + TOOL_FORMAT_REMINDER

    # Speed-first roles like 'planner' get 90s, high-context roles get 150s
    timeout = 150 if task_type in {"synthesis", "research", "deep_plan"} else 90

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY,
        timeout=timeout,
    )
    resp = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=[{"role": "system", "content": system_text}] + user_messages,
        max_tokens=NVIDIA_MAX_TOKENS.get(task_type, MAX_TOKENS),
        temperature=NVIDIA_TEMPS.get(task_type, 0.7),
    )
    content = safe_content(resp.choices[0].message.content, "NVIDIA")
    if not content:
        raise LLMError("NVIDIA returned empty content")
    return content


# ── Backward-compat wrappers ─────────────────────────────────────────────────

def call_openrouter(messages: Messages, strong: bool = False) -> str:
    """Legacy wrapper — maps strong=True/False to the new model-string API."""
    model = OPENROUTER_MODEL_AGENTIC if strong else OPENROUTER_MODEL_CHECKER
    return call_openrouter_model(messages, model)


# ── Test mode override ────────────────────────────────────────────────────────

def _apply_test_mode(task_type: str, provider_override: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    In TEST_MODE, force all calls to the free checker model.
    Ignores any provider override — zero Claude spend during development.
    Exempts NVIDIA roles because they are already free.
    """
    if TEST_MODE and task_type not in NVIDIA_ROLES:
        logger.debug(f"TEST_MODE active: rerouting task_type='{task_type}' → 'checker' (free tier)")
        return "checker", None
    return task_type, provider_override


# ── Main router ───────────────────────────────────────────────────────────────

def route_llm(
    messages: Messages,
    task_type: str = "general",
    provider_override: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Routes to the right model based on task_type.

    Task type → Model mapping (see config.py TASK_MODEL_MAP):
      general   → Gemini 2.5 Flash        (live, tool-calling, user waits)
      executor  → MiniMax M2.5            (background, 100% JSON compliance)
      planner   → Gemini 2.5 Flash-Lite   (background, decomposition)
      checker   → GLM-4.5-Air (free)      (classification, near-zero cost)
      synthesis → NVIDIA Nemotron         (long-context synthesis, free)
      summarize → NVIDIA Nemotron         (session rolling summary, free)

    Claude is kept only as:
      - manual override via provider_override="Claude"
      - last-resort fallback when all other providers fail
    """
    task_type, provider_override = _apply_test_mode(task_type, provider_override)
    task_type = task_type.lower()

    def do_route() -> Tuple[str, str]:
        scoped = _scope_messages(messages, task_type)

        # ── Manual provider override (UI selector or explicit call) ──────────
        if provider_override and provider_override not in (None, "Auto Default"):
            if provider_override == "Claude" and CLAUDE_API_KEY:
                try:
                    return call_claude(scoped), "Claude"
                except Exception as e:
                    logger.warning(f"Claude manual override failed: {e}")
            elif provider_override == "OpenRouter" and OPENROUTER_API_KEY:
                try:
                    return call_openrouter_model(scoped, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash"
                except Exception as e:
                    logger.warning(f"OpenRouter manual override failed: {e}")
            elif provider_override == "NVIDIA" and NVIDIA_API_KEY:
                try:
                    return call_nvidia(scoped, task_type=task_type), "NVIDIA"
                except Exception as e:
                    logger.warning(f"NVIDIA manual override failed: {e}")
            return f"Override '{provider_override}' failed or key missing.", "Error"

        # ── NVIDIA lane: text-only synthesis and summarization ────────────────
        if task_type in NVIDIA_ROLES:
            if NVIDIA_API_KEY:
                try:
                    return call_nvidia(scoped, task_type=task_type), "NVIDIA-Nemotron"
                except Exception as e:
                    logger.warning(f"NVIDIA {task_type} failed, falling back to Gemini Flash: {e}")
            # fallback: Gemini Flash handles it fine too
            if OPENROUTER_API_KEY:
                try:
                    return call_openrouter_model(scoped, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash"
                except Exception as e:
                    logger.warning(f"Gemini Flash NVIDIA fallback failed: {e}")

        # ── OpenRouter lane: all other task types ─────────────────────────────
        model = TASK_MODEL_MAP.get(task_type, OPENROUTER_MODEL_AGENTIC)
        if model and OPENROUTER_API_KEY:
            try:
                label = f"OpenRouter/{model.split('/')[-1]}"
                return call_openrouter_model(scoped, model), label
            except Exception as e:
                logger.warning(f"OpenRouter {model} failed for task_type={task_type}: {e}")

                # If primary OpenRouter model fails, try the agentic model as fallback
                if model != OPENROUTER_MODEL_AGENTIC:
                    try:
                        logger.info(f"Retrying with Gemini Flash fallback for task_type={task_type}")
                        return call_openrouter_model(scoped, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash-fallback"
                    except Exception as e2:
                        logger.warning(f"Gemini Flash fallback also failed: {e2}")

        # ── Claude: last resort — only when all OpenRouter models are down ────
        if CLAUDE_API_KEY:
            try:
                logger.warning(f"All primary providers failed for task_type={task_type} — falling back to Claude")
                return call_claude(scoped), "Claude-fallback"
            except Exception as e:
                logger.error(f"Claude last-resort fallback also failed: {e}")

        return "All providers failed. Check API keys and logs.", "Error"

    # ── Execute and write audit trace ─────────────────────────────────────────
    t0 = time.perf_counter()
    response, provider = do_route()
    latency = time.perf_counter() - t0

    def _write_trace(lat_s: float):
        try:
            # Sanitise messages: strip system prompt, truncate long content
            def _sanitise_messages(msgs: Messages) -> list:
                out = []
                for m in msgs:
                    if m.get("role") == "system":
                        continue                       # never log the full 4k system prompt
                    content = m.get("content", "") or ""
                    out.append({
                        "role": m["role"],
                        "content": content[:400] + "…" if len(content) > 400 else content,
                    })
                return out

            record = {
                "ts":       datetime.now(timezone.utc),
                "task":     task_type,
                "provider": provider,
                "test":     TEST_MODE,
                "msgs":     _sanitise_messages(messages),
                "resp":     (response or "")[:800],    # cap response at 800 chars in log
                "lat":      lat_s,
            }

            with DbConn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO llm_traces (ts, task_type, provider, test_mode, input_msgs, response, latency_s)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (record["ts"], record["task"], record["provider"], 
                          record["test"], json.dumps(record["msgs"]), record["resp"], record["lat"]))
        except Exception as e:
            logger.warning(f"Trace write failed: {e}")

    # Fire-and-forget trace writing to avoid blocking the user
    threading.Thread(target=_write_trace, args=(latency,), daemon=True).start()

    return response, provider


# ── Streaming (WebSocket live output) ────────────────────────────────────────

def route_llm_stream(
    messages: Messages,
    provider_override: Optional[str] = None,
) -> Generator[Tuple[str, str], None, None]:
    """
    Streaming waterfall: Gemini Flash → Claude.
    Yields (text_chunk, provider) tuples as the LLM produces tokens.
    Streaming is only used for the live agentic loop — always uses the
    agentic model (Gemini Flash), not the role-specific map.

    Usage:
        full_text = ""
        for chunk, provider in route_llm_stream(messages):
            full_text += chunk
            send_to_client(chunk)
    """
    _, provider_override = _apply_test_mode("general", provider_override)
    scoped = _scope_messages(messages, "general")
    system_text, user_messages = extract_system_messages(scoped)

    providers = (
        [provider_override]
        if provider_override and provider_override not in (None, "Auto Default")
        else ["OpenRouter", "Claude"]
    )

    for provider in providers:
        # ── OpenRouter streaming (Gemini Flash) ───────────────────────────────
        if provider == "OpenRouter" and OPENROUTER_API_KEY:
            try:
                client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=OPENROUTER_API_KEY,
                    timeout=60,
                )
                resp = client.chat.completions.create(
                    model=OPENROUTER_MODEL_AGENTIC,
                    messages=[{"role": "system", "content": system_text}] + user_messages,
                    max_tokens=MAX_TOKENS,
                    stream=True,
                )
                for chunk in resp:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta, "OpenRouter/gemini-flash"
                return  # success — stop waterfall
            except Exception as e:
                logger.warning(f"OpenRouter stream failed: {e}")
                continue

        # ── Claude streaming (last resort) ────────────────────────────────────
        if provider == "Claude" and CLAUDE_API_KEY:
            try:
                client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
                with client.messages.stream(
                    model=CLAUDE_MODEL,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=user_messages,
                ) as stream:
                    for text in stream.text_stream:
                        yield text, "Claude"
                return
            except Exception as e:
                logger.warning(f"Claude stream failed: {e}")
                continue

    yield "All providers failed. Check API keys and logs.", "Error"
