import asyncio, hashlib, hmac, logging, os, re, json, threading, sys

from typing import Set
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from core.agent import process_user_message
from core.db import save_message
from core.config import WA_TOKEN, WA_PHONE_ID, WA_APP_SECRET, WA_VERIFY_TOKEN

logger = logging.getLogger(__name__)
router = APIRouter()

_seen_ids: Set[str] = set()


def _verify_signature(body: bytes, sig_header: str) -> bool:
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WA_APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header[7:])


def _strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)
    text = re.sub(r"`{3}.*?\n(.*?)`{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    return text.strip()


def _chunk(text: str, size: int = 4096) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, current = [], []
    for para in text.split("\n\n"):
        if sum(len(p) for p in current) + len(para) + 2 > size:
            chunks.append("\n\n".join(current))
            current = [para]
        else:
            current.append(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text[:size]]


def _send_message_sync(to: str, text: str) -> None:
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=15) as client:
        for chunk in _chunk(_strip_markdown(text)):
            print(f">>> Sending WhatsApp chunk to {to} (sync): {chunk[:20]}...", flush=True)
            resp = client.post(url, headers=headers, json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            })
            print(f">>> WhatsApp API Status (sync): {resp.status_code}", flush=True)
            if resp.status_code >= 400:
                print(f">>> WhatsApp API Error Body (sync): {resp.text}", flush=True)


async def _send_message(to: str, text: str) -> None:
    # Keep async version for any potential async callers
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        for chunk in _chunk(_strip_markdown(text)):
            print(f">>> Sending WhatsApp chunk to {to} (async): {chunk[:20]}...", flush=True)
            resp = await client.post(url, headers=headers, json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            })
            print(f">>> WhatsApp API Status (async): {resp.status_code}", flush=True)
            if resp.status_code >= 400:
                print(f">>> WhatsApp API Error Body (async): {resp.text}", flush=True)


def _run_agent(sender: str, session_id: str, text: str) -> None:
    """Synchronous wrapper to run in a dedicated thread."""
    try:
        print(f">>> Thread started for {sender}. Sending 'Ping'...", flush=True)
        _send_message_sync(sender, "🔄 Agent is thinking... please wait.")
        print(">>> 'Ping' sent. Calling process_user_message...", flush=True)
        
        result = process_user_message(
            prompt=text,
            session_id=session_id,
            use_search=True,
            provider_override="Auto (Default)",
        )
        answer = result["answer"]
        print(f">>> Agent result ready for {sender}", flush=True)
        save_message(session_id, "assistant", answer)
        print(">>> Assistant message saved. Sending reply...", flush=True)
        _send_message_sync(sender, answer)
        print(">>> WhatsApp reply sent successfully.", flush=True)
    except Exception as e:
        print(f">>> THREAD ERROR for {sender}: {e}", flush=True)
        logger.exception(f"Agent error in background thread for {sender}: {e}")
        try:
            _send_message_sync(sender, "❌ Sorry, something went wrong during processing.")
        except:
            pass


@router.get("/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if not all([WA_TOKEN, WA_PHONE_ID, WA_APP_SECRET, WA_VERIFY_TOKEN]):
        logger.warning("WhatsApp credentials missing. Rejecting webhook.")
        raise HTTPException(status_code=503, detail="WhatsApp integration not configured.")

    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403)


@router.post("/whatsapp")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    print(">>> WhatsApp webhook POST received", flush=True)
    if not all([WA_TOKEN, WA_PHONE_ID, WA_APP_SECRET, WA_VERIFY_TOKEN]):
        print(">>> ERROR: Missing WhatsApp credentials", flush=True)
        raise HTTPException(status_code=503, detail="WhatsApp integration not configured.")

    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, sig):
        print(">>> ERROR: Signature verification failed", flush=True)
        raise HTTPException(status_code=403)

    payload = await request.json()
    print(f">>> Payload keys: {list(payload.keys())}", flush=True)
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        if "messages" not in changes:
            print(">>> No messages in payload", flush=True)
            return Response(status_code=200)
        
        msg = changes["messages"][0]
        msg_id = msg.get("id", "")
        if msg_id in _seen_ids:
            print(f">>> Duplicate message {msg_id} skipped", flush=True)
            return Response(status_code=200)
        
        _seen_ids.add(msg_id)
        sender = msg["from"]
        text = msg.get("text", {}).get("body", "")
        print(f">>> Processing msg from {sender}: {text[:20]}", flush=True)
        
        session_id = f"wa_{sender}"
        save_message(session_id, "user", text)
        print(">>> User message saved to DB", flush=True)
        
        print(">>> Launching background thread...", flush=True)
        thread = threading.Thread(target=_run_agent, args=(sender, session_id, text), daemon=True)
        thread.start()
        print(">>> Background thread launched. Returning 200.", flush=True)
        
    except Exception as e:
        print(f">>> CRITICAL PARSE ERROR: {e}", flush=True)
        logger.exception(f"Error parsing WhatsApp payload: {e}")

    return Response(status_code=200)

