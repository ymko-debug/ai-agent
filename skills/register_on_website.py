"""
Generic website registration skill using browser-use.
Handles overlays, CAPTCHA detection, form discovery automatically.
"""
import asyncio
import os
from browser_use import Agent
from browser_use.llm import ChatOpenAI


async def _run_agent(url: str, user_data: dict) -> str:
    fields = ", ".join(f"{k}: {v}" for k, v in user_data.items() if v)
    task = (
        f"Go to {url} and register a new account using these details: {fields}. "
        f"Close any popups or overlays first. "
        f"If a CAPTCHA appears, stop and report it. "
        f"Return whether registration succeeded and the final URL."
    )
    # Canonical LLM initialization using browser-use's own ChatOpenAI wrapper
    llm = ChatOpenAI(
        model="google/gemini-2.5-flash",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    agent  = Agent(task=task, llm=llm)
    result = await agent.run()
    return str(result)


def run(input_data: dict) -> dict:
    url       = input_data.get("url", "")
    if not url:
        return {"success": False, "error": "No URL provided."}

    user_data = {k: v for k, v in input_data.items() if k != "url" and v}

    try:
        # Since uvicorn runs in an event loop, we need to run browser-use in a separate loop or thread
        # Actually, if we're inside a thread (from run_in_executor), asyncio.run should work.
        result = asyncio.run(_run_agent(url, user_data))
        success = any(w in result.lower() for w in
                      ["success", "registered", "account created",
                       "welcome", "verify your email", "check your email", "check inbox"])
        return {
            "success":   success,
            "result":    result,
            "final_url": "",
            "evidence":  [result[:200]],
        }
    except Exception as e:
        return {
            "success": False,
            "error":   f"Runtime error: {str(e)}",
            "retry_hint": "Check GOOGLE_API_KEY in .env and browser-use installation.",
        }
