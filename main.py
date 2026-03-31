"""
main.py — FastAPI backend for the autonomous agent.
Replaces Streamlit entirely. Serves HTMX UI + WebSocket live updates.
core/ folder: zero changes required.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Form, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from core.agent import process_user_message, request_stop
from core.db import (
    init_db,
    save_message,
    load_history,
    list_sessions,
    delete_session,
    daily_call_count,
    save_session_name,
    get_all_session_names,
    purge_expired_cache,
)
from core.config import (
    CLAUDE_API_KEY,
    OPENROUTER_API_KEY,
    TAVILY_API_KEY,
    DAILY_CALL_LIMIT,
    HISTORY_LIMIT,
    HISTORY_DISPLAY_LIMIT,
)
from whatsapp import router as whatsapp_router


# ── Setup ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

Path("static").mkdir(exist_ok=True)
Path("templates").mkdir(exist_ok=True)

app = FastAPI(title="AI Assistant", version="2.0")
app.include_router(whatsapp_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Global task store ──────────────────────────────────────────────────────────
# {session_id: {status, answer, provider, search_label, done, error}}
task_store: Dict[str, dict] = {}


def _make_session_label(prompt: str, max_len: int = 32) -> str:
    clean = prompt.strip()
    for prefix in ("/leads", "/add_skill", "/"):
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):].strip()
    clean = " ".join(clean.split())
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(" ", 1)[0] + "…"
    return clean or "New chat"


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    purge_expired_cache(ttl_hours=24)
    from core.db import purge_expired_memory, get_core_memory
    purge_expired_memory()               # NEW — runs Task 4's purge function

    # Health log
    entries = get_core_memory()
    from collections import Counter
    ns_counts = Counter(e["namespace"] for e in entries)
    logger.info(f"Memory health on startup: {dict(ns_counts)} | total={len(entries)}")
    logger.info("Agent backend started on http://localhost:8000")


# ── Serve UI ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    import datetime
    sessions     = list_sessions()
    session_names = get_all_session_names(sessions)
    new_sid      = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return templates.TemplateResponse("index.html", {
        "request":      request,
        "sessions":     sessions,
        "session_names": session_names,
        "new_sid":      new_sid,
        "claude_ok":    bool(CLAUDE_API_KEY),
        "openrouter_ok": bool(OPENROUTER_API_KEY),
        "tavily_ok":    bool(TAVILY_API_KEY),
        "calls_today":  daily_call_count(),
        "daily_limit":  DAILY_CALL_LIMIT,
    })


# ── Chat ───────────────────────────────────────────────────────────────────────
@app.post("/chat/{session_id}")
async def chat(
    session_id:        str,
    prompt:            str  = Form(...),
    use_search:        bool = Form(True),
    provider_override: str  = Form("Auto (Default)"),
):
    """
    1. Save user message to DB immediately.
    2. Name the session from first message.
    3. Launch agent in thread pool (non-blocking).
    4. Return {"ok": true} immediately — WS delivers the answer.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_message, session_id, "user", prompt)

    # Name session from first user message if unnamed
    existing_names = get_all_session_names([session_id])
    if session_id not in existing_names:
        save_session_name(session_id, _make_session_label(prompt))

    # Mark task as running
    task_store[session_id] = {
        "status":       "working",
        "answer":       "",
        "provider":     "",
        "search_label": "",
        "done":         False,
        "error":        None,
    }

    # Run agent in thread pool — never blocks the event loop
    loop = asyncio.get_event_loop()

    async def _run():
        try:
            result = await loop.run_in_executor(
                None,
                lambda: process_user_message(
                    prompt            = prompt,
                    session_id        = session_id,
                    use_search        = use_search,
                    provider_override = provider_override,
                ),
            )
            await loop.run_in_executor(None, save_message, session_id, "assistant", result["answer"])
            task_store[session_id] = {
                "status":       "done",
                "answer":       result["answer"],
                "provider":     result.get("provider", ""),
                "search_label": result.get("search_label", ""),
                "done":         True,
                "error":        None,
            }
        except Exception as e:
            task_store[session_id] = {
                "status": "error",
                "answer": f"Agent error: {e}",
                "done":   True,
                "error":  str(e),
            }

    asyncio.create_task(_run())
    return JSONResponse({"ok": True})


# ── Stop ───────────────────────────────────────────────────────────────────────
@app.post("/stop/{session_id}")
async def stop(session_id: str):
    request_stop(session_id)
    if session_id in task_store:
        task_store[session_id]["status"] = "stopping"
    return JSONResponse({"ok": True})


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """"""
    print(f"!!! WEBSOCKET ENDPOINT HIT for {session_id}")
    await websocket.accept()
    print(f"!!! WEBSOCKET ACCEPTED for {session_id}")
    last_status = None
    try:
        while True:
            task = task_store.get(session_id, {})
            status = task.get("status", "idle")

            if status != last_status:
                last_status = status

                if status == "working":
                    import json as json_module
                    await websocket.send_text(json_module.dumps({"type": "working"}))

                elif status == "stopping":
                    import json as json_module
                    await websocket.send_text(json_module.dumps({"type": "stopping"}))

                elif status in ("done", "error"):
                    answer       = task.get("answer", "")
                    provider     = task.get("provider", "")
                    search_label = task.get("search_label", "")
                    error        = task.get("error", None)

                    import json as json_module
                    payload = json_module.dumps({
                        "type": "done",
                        "session_id": session_id,
                        "answer": answer,
                        "provider": provider,
                        "search_label": search_label,
                        "error": error
                    })
                    await websocket.send_text(payload)
                    # Clean up after delivery
                    task_store.pop(session_id, None)
                    break

            await asyncio.sleep(0.2)  # 200ms polling for snappy UI updates
    except WebSocketDisconnect:
        logger.info("WS disconnected for session %s", session_id)
    except Exception as e:
        logger.warning("WS error for session %s: %s", session_id, e)


# ── Sessions ───────────────────────────────────────────────────────────────────
@app.get("/sessions")
async def get_sessions():
    sessions      = list_sessions()
    session_names = get_all_session_names(sessions)
    return JSONResponse({"sessions": sessions, "names": session_names})


@app.delete("/sessions/{session_id}")
async def remove_session(session_id: str):
    delete_session(session_id)
    from core.agent import cleanup_session
    cleanup_session(session_id)        # evict _stop_flags entry — prevents memory leak
    task_store.pop(session_id, None)
    return HTMLResponse(content="", status_code=200)


@app.get("/history/{session_id}")
async def get_history(session_id: str):
    msgs = load_history(session_id, limit=HISTORY_DISPLAY_LIMIT)
    return JSONResponse({"messages": msgs})


# ── File upload ────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    dest = UPLOAD_DIR / file.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return JSONResponse({"filename": file.filename, "path": str(dest)})


# ── Status ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "calls_today": daily_call_count()}
