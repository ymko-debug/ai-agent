# ── PATCH FOR main.py ──────────────────────────────────────────────────────
# Changes:
#   1. API key middleware  (solo-user protection, no login UX friction)
#   2. CORS locked to your own domain
#   3. Safe file upload (path traversal fix)
#   4. Session cookie so the browser never asks for the key again

from __future__ import annotations
import asyncio, json, logging, os, shutil, uuid
from pathlib import Path, PurePosixPath
from typing import Dict, Optional

from fastapi import FastAPI, Form, UploadFile, File, WebSocket, WebSocketDisconnect, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.agent import process_user_message, request_stop
from core.db import (initdb, save_message, load_history, list_sessions,
                     delete_session, daily_call_count, save_session_name,
                     get_all_session_names, purge_expired_cache)
from core.config import (CLAUDE_API_KEY, OPENROUTER_API_KEY, TAVILY_API_KEY,
                         DAILY_CALL_LIMIT, HISTORY_LIMIT, HISTORY_DISPLAY_LIMIT)
from whatsapp import router as whatsapp_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)
Path("templates").mkdir(exist_ok=True)

# ── Auth config ──────────────────────────────────────────────────────────────
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")          # set in Render env vars
COOKIE_NAME   = "agent_session"                          # browser remembers you

def _is_authed(request: Request) -> bool:
    """Accept key via header, query param, OR session cookie."""
    if not AGENT_API_KEY:                                # key not set → open (dev mode)
        return True
    # 1. X-API-KEY header  (used by API clients / benchmark scripts)
    if request.headers.get("X-API-KEY") == AGENT_API_KEY:
        return True
    # 2. ?key=... query param  (used for first browser visit from bookmark)
    if request.query_params.get("key") == AGENT_API_KEY:
        return True
    # 3. Session cookie  (set automatically after first successful auth)
    if request.cookies.get(COOKIE_NAME) == AGENT_API_KEY:
        return True
    return False

def _auth_error():
    return JSONResponse({"error": "Unauthorized"}, status_code=403)

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="AI Assistant", version="2.1")
app.include_router(whatsapp_router)

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")       # e.g. https://agent-tikq.onrender.com
ALLOWED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
if RENDER_URL:
    ALLOWED_ORIGINS.append(RENDER_URL.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,                       # no more wildcard
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

from jinja2 import Environment, FileSystemLoader
jinja_env = Environment(loader=FileSystemLoader("templates"), cache_size=0)
templates = Jinja2Templates(env=jinja_env)

task_store: Dict[str, dict] = {}

def make_session_label(prompt: str, max_len: int = 32) -> str:
    clean = prompt.strip()
    for prefix in ["leads ", "addskill "]:
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):].strip()
    clean = " ".join(clean.split())
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(" ", 10)[0]
    return clean or "New chat"

@app.on_event("startup")
async def startup():
    initdb()
    from core.db import ensure_embedding_column
    ensure_embedding_column()
    purge_expired_cache(ttl_hours=24)
    from core.db import purge_expired_memory, get_core_memory
    purge_expired_memory()
    entries = get_core_memory()
    from collections import Counter
    ns_counts = Counter(e["namespace"] for e in entries)
    logger.info(f"Memory health on startup: {dict(ns_counts)}, total={len(entries)}")
    logger.info("Agent backend started on http://localhost:8000")

# ── UI root ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request, response: Response):
    if not _is_authed(request):
        # Show a minimal password page instead of 403
        return HTMLResponse(_login_page(), status_code=200)
    # First visit via ?key=... → bake the cookie so they never need the param again
    resp = templates.TemplateResponse(request, "index.html", {
        "sessions":      list_sessions(),
        "session_names": get_all_session_names(list_sessions()),
        "new_sid":       f"session{__import__('datetime').datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6]}",
        "claude_ok":     bool(CLAUDE_API_KEY),
        "openrouter_ok": bool(OPENROUTER_API_KEY),
        "tavily_ok":     bool(TAVILY_API_KEY),
        "calls_today":   daily_call_count(),
        "daily_limit":   DAILY_CALL_LIMIT,
    })
    if AGENT_API_KEY and request.query_params.get("key") == AGENT_API_KEY:
        resp.set_cookie(COOKIE_NAME, AGENT_API_KEY, httponly=True,
                        secure=True, samesite="lax", max_age=60*60*24*365)
    return resp

# ── Chat ─────────────────────────────────────────────────────────────────────
@app.post("/chat/{session_id}")
async def chat(request: Request, session_id: str,
               prompt: str = Form(...), use_search: bool = Form(True),
               provider_override: str = Form("Auto Default")):
    if not _is_authed(request):
        return _auth_error()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_message, session_id, "user", prompt)
    existing_names = get_all_session_names([session_id])
    if session_id not in existing_names:
        save_session_name(session_id, make_session_label(prompt))
    task_store[session_id] = {"status": "working", "answer": "", "provider": "",
                               "search_label": "", "done": False, "error": None}

    async def run():
        try:
            result = await loop.run_in_executor(
                None, lambda: process_user_message(
                    prompt=prompt, session_id=session_id,
                    use_search=use_search, provider_override=provider_override))
            await loop.run_in_executor(None, save_message, session_id, "assistant", result["answer"])
            task_store[session_id] = {"status": "done", "answer": result["answer"],
                                      "provider": result.get("provider", ""),
                                      "search_label": result.get("search_label", ""),
                                      "done": True, "error": None}
        except Exception as e:
            task_store[session_id] = {"status": "error", "answer": f"Agent error: {e}",
                                      "done": True, "error": str(e)}
    asyncio.create_task(run())
    return JSONResponse({"ok": True})

# ── External / benchmark API ──────────────────────────────────────────────────
@app.post("/api/chat")
async def api_chat(request: Request):
    if not _is_authed(request):
        return _auth_error()
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    session_id = data.get("session_id") or data.get("sessionId") or f"api-{uuid.uuid4().hex[:8]}"
    prompt = data.get("message") or data.get("content") or data.get("prompt")
    if not prompt:
        return JSONResponse({"error": "No prompt provided"}, status_code=400)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: process_user_message(prompt=prompt, session_id=session_id,
                                           use_search=True, provider_override="Auto Default"))
    await loop.run_in_executor(None, save_message, session_id, "user", prompt)
    await loop.run_in_executor(None, save_message, session_id, "assistant", result["answer"])
    return JSONResponse({"response": result["answer"], "provider": result.get("provider", ""),
                         "session_id": session_id})

# ── Stop ─────────────────────────────────────────────────────────────────────
@app.post("/stop/{session_id}")
async def stop(request: Request, session_id: str):
    if not _is_authed(request):
        return _auth_error()
    request_stop(session_id)
    if session_id in task_store:
        task_store[session_id]["status"] = "stopping"
    return JSONResponse({"ok": True})

# ── WebSocket (no auth needed — session_id is opaque enough for WS) ──────────
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    last_status = None
    try:
        while True:
            task   = task_store.get(session_id, {})
            status = task.get("status", "idle")
            if status != last_status:
                last_status = status
                if status == "working":
                    await websocket.send_text(json.dumps({"type": "working"}))
                elif status == "stopping":
                    await websocket.send_text(json.dumps({"type": "stopping"}))
                elif status in ("done", "error"):
                    await websocket.send_text(json.dumps({
                        "type":         "done",
                        "session_id":   session_id,
                        "answer":       task.get("answer"),
                        "provider":     task.get("provider"),
                        "search_label": task.get("search_label"),
                        "error":        task.get("error"),
                    }))
                    task_store.pop(session_id, None)
                    break
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        logger.info("WS disconnected for session %s", session_id)
    except Exception as e:
        logger.warning("WS error for session %s: %s", session_id, e)

# ── Sessions ─────────────────────────────────────────────────────────────────
@app.get("/sessions")
async def get_sessions(request: Request):
    if not _is_authed(request):
        return _auth_error()
    sessions = list_sessions()
    return JSONResponse({"sessions": sessions, "names": get_all_session_names(sessions)})

@app.delete("/sessions/{session_id}")
async def remove_session(request: Request, session_id: str):
    if not _is_authed(request):
        return _auth_error()
    delete_session(session_id)
    from core.agent import cleanup_session
    cleanup_session(session_id)
    task_store.pop(session_id, None)
    return HTMLResponse(content="", status_code=200)

@app.get("/history/{session_id}")
async def get_history(request: Request, session_id: str):
    if not _is_authed(request):
        return _auth_error()
    return JSONResponse({"messages": load_history(session_id, limit=HISTORY_DISPLAY_LIMIT)})

# ── File upload (path-traversal safe) ────────────────────────────────────────
@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    if not _is_authed(request):
        return _auth_error()
    safe_name = PurePosixPath(file.filename).name          # strips ../../../ attempts
    if not safe_name:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    dest = UPLOAD_DIR / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return JSONResponse({"filename": safe_name, "path": str(dest)})

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "calls_today": daily_call_count()}

# ── Login page (shown when key is wrong / missing) ────────────────────────────
def _login_page() -> str:
    return """<!doctype html><html><head><title>Agent Login</title>
<style>body{font-family:sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;background:#f7f6f2;margin:0}
form{background:#fff;padding:2rem;border-radius:12px;box-shadow:0 4px 20px #0001;
display:flex;flex-direction:column;gap:1rem;min-width:300px}
h2{margin:0;font-size:1.2rem}input{padding:.6rem;border:1px solid #ddd;
border-radius:6px;font-size:1rem}button{padding:.7rem;background:#01696f;
color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:1rem}
</style></head><body>
<form method="get" action="/">
  <h2>🎨 Artist Agent</h2>
  <input type="password" name="key" placeholder="Enter your access key" autofocus>
  <button type="submit">Enter</button>
</form></body></html>"""
