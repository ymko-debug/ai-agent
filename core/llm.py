# core/llm.py
from typing import List, Dict, Tuple

import requests
import anthropic
from openai import OpenAI

from .config import (
    CLAUDE_API_KEY,
    OPENROUTER_API_KEY,
    NVIDIA_API_KEY,
    NVIDIA_MODEL,
    CLAUDE_MODEL,
    OPENROUTER_MODEL_CHEAP,
    OPENROUTER_MODEL_STRONG,
    MAX_TOKENS,
    SYSTEM_PROMPT,
)


class LLMError(Exception):
    pass


Messages = List[Dict[str, str]]


# ─────────────────────────────────────────
# LOW-LEVEL PROVIDER CALLS
# ─────────────────────────────────────────

def call_claude(messages: Messages) -> str:
    if not CLAUDE_API_KEY:
        raise LLMError("Claude API key missing")
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return resp.content[0].text


def call_openrouter(messages: Messages, strong: bool = False) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMError("OpenRouter API key missing")

    model = OPENROUTER_MODEL_STRONG if strong else OPENROUTER_MODEL_CHEAP
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    resp = client.chat.completions.create(
        model=model,
        messages=full_messages,
        max_tokens=MAX_TOKENS,
    )
    return resp.choices[0].message.content


def call_nvidia(messages: Messages) -> str:
    """
    Call NVIDIA NIM (Build API) as an OpenAI-compatible endpoint.
    Adjust base_url/model if NVIDIA changes their spec.
    """
    if not NVIDIA_API_KEY:
        raise LLMError("NVIDIA API key missing")

    # Many examples use an OpenAI-compatible format:
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY,
    )

    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    resp = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=full_messages,
        max_tokens=MAX_TOKENS,
    )
    return resp.choices[0].message.content


# ─────────────────────────────────────────
# SIMPLE PROVIDER WRAPPERS (BACKWARD COMPAT)
# ─────────────────────────────────────────

def get_llm_response(messages: Messages) -> Tuple[str, str]:
    """
    Old interface kept for compatibility.
    Prefers Claude → OpenRouter strong → NVIDIA.
    """
    # Claude
    if CLAUDE_API_KEY:
        try:
            return call_claude(messages), "Claude"
        except Exception:
            pass

    # OpenRouter strong
    if OPENROUTER_API_KEY:
        try:
            return call_openrouter(messages, strong=True), "OpenRouter-strong"
        except Exception:
            pass

    # NVIDIA
    if NVIDIA_API_KEY:
        try:
            return call_nvidia(messages), "NVIDIA"
        except Exception as e:
            return f"All providers failed. Last error: {e}", "Error"

    return "No API keys configured. Please add keys to your .env file.", "Error"


# ─────────────────────────────────────────
# ROUTER WITH TASK TYPES
# ─────────────────────────────────────────

def route_llm(
    messages: Messages,
    task_type: str = "general",
) -> Tuple[str, str]:
    """
    Router that picks provider based on task_type.

    task_type examples:
      - "planner"       → cheap lane (NVIDIA or cheap OpenRouter)
      - "executor"      → strong lane (Claude or strong OpenRouter)
      - "checker"       → cheap lane (NVIDIA or cheap OpenRouter)
      - "general"       → previous default behavior
    """

    task_type = task_type.lower()

    # PLANNER / CHECKER → cheap lane first
    if task_type in ("planner", "checker"):
        # Prefer NVIDIA (free / cheap) as planner lane
        if NVIDIA_API_KEY:
            try:
                return call_nvidia(messages), "NVIDIA"
            except Exception:
                pass

        # Fallback to cheap OpenRouter model
        if OPENROUTER_API_KEY:
            try:
                return call_openrouter(messages, strong=False), "OpenRouter-cheap"
            except Exception:
                pass

        # Last resort: Claude
        if CLAUDE_API_KEY:
            try:
                return call_claude(messages), "Claude"
            except Exception as e:
                return f"All providers failed (planner). Last error: {e}", "Error"

        return "No planner-capable providers configured.", "Error"

    # EXECUTOR → strong lane
    if task_type == "executor":
        # Prefer Claude for high-quality structured work
        if CLAUDE_API_KEY:
            try:
                return call_claude(messages), "Claude"
            except Exception:
                pass

        # Fallback to strong OpenRouter
        if OPENROUTER_API_KEY:
            try:
                return call_openrouter(messages, strong=True), "OpenRouter-strong"
            except Exception:
                pass

        # Last resort: NVIDIA
        if NVIDIA_API_KEY:
            try:
                return call_nvidia(messages), "NVIDIA"
            except Exception as e:
                return f"All providers failed (executor). Last error: {e}", "Error"

        return "No executor-capable providers configured.", "Error"

    # GENERAL → keep old behavior order
    return get_llm_response(messages)
