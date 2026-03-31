# core/llm.py
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, Generator, List, Optional, Tuple

import anthropic
from openai import OpenAI

from .config import (
    CLAUDE_API_KEY, CLAUDE_MODEL,
    OPENROUTER_API_KEY, TASK_MODEL_MAP,
    OPENROUTER_MODEL_AGENTIC, OPENROUTER_MODEL_CHECKER,
    NVIDIA_API_KEY, NVIDIA_MODEL, NVIDIA_ROLES,
    MAX_TOKENS, SYSTEM_PROMPT,
    TEST_MODE,
)

logger = logging.getLogger(__name__)

Messages = List[Dict[str, str]]

# Injected into NVIDIA calls only — smaller models need explicit tool format reminder
TOOL_FORMAT_REMINDER = """
CRITICAL — when calling a tool output EXACTLY this format, nothing else:
<toolcall>{"tool": "TOOLNAME", "input": {"KEY": "VALUE"}}</toolcall>
Wait for Tool results before writing your final answer.
"""


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


def call_nvidia(messages: Messages) -> str:
    """
    NVIDIA NIM call — Nemotron 3 Super (free, 1M context).
    Used ONLY for text-in/text-out tasks (synthesis, summarize).
    No tool format reminder injected — these roles never produce tool calls.
    """
    if not NVIDIA_API_KEY:
        raise LLMError("NVIDIA API key missing")
    system_text, user_messages = extract_system_messages(messages)
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY,
        timeout=60,
    )
    resp = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=[{"role": "system", "content": system_text}] + user_messages,
        max_tokens=MAX_TOKENS,
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
    """
    if TEST_MODE:
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
        # ── Manual provider override (UI selector or explicit call) ──────────
        if provider_override and provider_override not in (None, "Auto Default"):
            if provider_override == "Claude" and CLAUDE_API_KEY:
                try:
                    return call_claude(messages), "Claude"
                except Exception as e:
                    logger.warning(f"Claude manual override failed: {e}")
            elif provider_override == "OpenRouter" and OPENROUTER_API_KEY:
                try:
                    return call_openrouter_model(messages, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash"
                except Exception as e:
                    logger.warning(f"OpenRouter manual override failed: {e}")
            elif provider_override == "NVIDIA" and NVIDIA_API_KEY:
                try:
                    return call_nvidia(messages), "NVIDIA"
                except Exception as e:
                    logger.warning(f"NVIDIA manual override failed: {e}")
            return f"Override '{provider_override}' failed or key missing.", "Error"

        # ── NVIDIA lane: text-only synthesis and summarization ────────────────
        if task_type in NVIDIA_ROLES:
            if NVIDIA_API_KEY:
                try:
                    return call_nvidia(messages), "NVIDIA-Nemotron"
                except Exception as e:
                    logger.warning(f"NVIDIA {task_type} failed, falling back to Gemini Flash: {e}")
            # fallback: Gemini Flash handles it fine too
            if OPENROUTER_API_KEY:
                try:
                    return call_openrouter_model(messages, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash"
                except Exception as e:
                    logger.warning(f"Gemini Flash NVIDIA fallback failed: {e}")

        # ── OpenRouter lane: all other task types ─────────────────────────────
        model = TASK_MODEL_MAP.get(task_type, OPENROUTER_MODEL_AGENTIC)
        if model and OPENROUTER_API_KEY:
            try:
                label = f"OpenRouter/{model.split('/')[-1]}"
                return call_openrouter_model(messages, model), label
            except Exception as e:
                logger.warning(f"OpenRouter {model} failed for task_type={task_type}: {e}")

                # If primary OpenRouter model fails, try the agentic model as fallback
                if model != OPENROUTER_MODEL_AGENTIC:
                    try:
                        logger.info(f"Retrying with Gemini Flash fallback for task_type={task_type}")
                        return call_openrouter_model(messages, OPENROUTER_MODEL_AGENTIC), "OpenRouter/gemini-flash-fallback"
                    except Exception as e2:
                        logger.warning(f"Gemini Flash fallback also failed: {e2}")

        # ── Claude: last resort — only when all OpenRouter models are down ────
        if CLAUDE_API_KEY:
            try:
                logger.warning(f"All primary providers failed for task_type={task_type} — falling back to Claude")
                return call_claude(messages), "Claude-fallback"
            except Exception as e:
                logger.error(f"Claude last-resort fallback also failed: {e}")

        return "All providers failed. Check API keys and logs.", "Error"

    # ── Execute and write audit trace ─────────────────────────────────────────
    response, provider = do_route()

    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/llm_traces.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp":  datetime.now().isoformat(),
                "task_type":  task_type,
                "provider":   provider,
                "test_mode":  TEST_MODE,
                "messages":   messages,
                "response":   response,
            }) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write audit trace: {e}")

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
    system_text, user_messages = extract_system_messages(messages)

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
