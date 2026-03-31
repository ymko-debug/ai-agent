import asyncio, hashlib, hmac, logging, os, re, json, threading, sys, traceback

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
    # Using v18.0 as per user's proven suggestion
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30) as client:
        # Simplified payload as per user's suggestion
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }
        print(f">>> Sending WhatsApp reply to {to}: {text[:50]}...", flush=True)
        try:
            resp = client.post(url, headers=headers, json=payload)
            print(f">>> WhatsApp API Status: {resp.status_code}", flush=True)
            if resp.status_code >= 400:
                print(f">>> WhatsApp API Error: {resp.text}", flush=True)
        except Exception as e:
            print(f">>> WhatsApp API Network Error: {e}", flush=True)


async def _send_message(to: str, text: str) -> None:
    # Just a wrapper for the sync version to keep compatibility if needed elsewhere
    _send_message_sync(to, text)


def process_and_reply(sender: str, text: str) -> None:
    """Official background task pattern: runs AFTER 200 OK is sent to Meta."""
    session_id = f"wa_{sender}"
    try:
        print(f">>> Background task started for {sender}", flush=True)
        # 1. Save user message first
        save_message(session_id, "user", text)
        
        # 2. Call agent
        result = process_user_message(
            prompt=text,
            session_id=session_id,
            use_search=True,
            provider_override="Auto (Default)",
        )
        answer = result.get("answer", "Sorry, I couldn't process that.")
        print(f">>> Agent result ready for {sender}", flush=True)
        
        # 3. Save assistant reply
        save_message(session_id, "assistant", answer)
        
        # 4. Send reply back
        _send_message_sync(sender, answer)
        print(f">>> Background task SUCCESS for {sender}", flush=True)
        
    except Exception as e:
        print(f">>> Background task FAILED for {sender}: {e}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        try:
            _send_message_sync(sender, "❌ Sorry, I encountered an error processing your message.")
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
        print(f">>> WA incoming from {sender}: {text[:50]}", flush=True)
        
        # FastAPI BackgroundTask: returns 200 immediately, runs function after
        background_tasks.add_task(process_and_reply, sender, text)
        print(">>> Task queued. Returning 200 to Meta.", flush=True)
        
    except Exception as e:
        print(f">>> Webhook error: {e}", flush=True)

    return Response(status_code=200)

