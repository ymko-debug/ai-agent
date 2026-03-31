from __future__ import annotations
import asyncio, hashlib, hmac, logging, os, re, json, threading
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
            logger.info(f"Sending WhatsApp chunk to {to} (sync): {chunk[:50]}...")
            resp = client.post(url, headers=headers, json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            })
            logger.info(f"WhatsApp API Status (sync): {resp.status_code}")
            if resp.status_code >= 400:
                logger.error(f"WhatsApp API Error Body (sync): {resp.text}")


async def _send_message(to: str, text: str) -> None:
    # Keep async version for any potential async callers
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        for chunk in _chunk(_strip_markdown(text)):
            logger.info(f"Sending WhatsApp chunk to {to} (async): {chunk[:50]}...")
            resp = await client.post(url, headers=headers, json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            })
            logger.info(f"WhatsApp API Status (async): {resp.status_code}")
            if resp.status_code >= 400:
                logger.error(f"WhatsApp API Error Body (async): {resp.text}")


def _run_agent(sender: str, session_id: str, text: str) -> None:
    """Synchronous wrapper to run in a dedicated thread."""
    try:
        logger.info(f"Background thread started for session {session_id}")
        result = process_user_message(
            prompt=text,
            session_id=session_id,
            use_search=True,
            provider_override="Auto (Default)",
        )
        answer = result["answer"]
        logger.info(f"Agent finished processing for {sender}. Saving and sending reply.")
        save_message(session_id, "assistant", answer)
        
        # Call the async sender using a dedicated event loop or just httpx sync
        _send_message_sync(sender, answer)
    except Exception as e:
        logger.exception(f"Agent error in background thread for {sender}: {e}")
        try:
            _send_message_sync(sender, "Sorry, something went wrong. Please try again.")
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
    logger.info("WhatsApp webhook hit (POST)")
    if not all([WA_TOKEN, WA_PHONE_ID, WA_APP_SECRET, WA_VERIFY_TOKEN]):
        logger.warning("WhatsApp integration missing credentials in receive_message")
        raise HTTPException(status_code=503, detail="WhatsApp integration not configured.")

    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, sig):
        logger.warning("WhatsApp signature verification failed")
        raise HTTPException(status_code=403)

    payload = await request.json()
    logger.info(f"WhatsApp payload received: {json.dumps(payload)[:500]}")
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        if "messages" not in changes:
            logger.info("No messages in WhatsApp payload changes")
            return Response(status_code=200)
        
        msg = changes["messages"][0]
        msg_id = msg.get("id", "")
        if msg_id in _seen_ids:
            logger.info(f"Already processed message {msg_id}, skipping")
            return Response(status_code=200)
        
        _seen_ids.add(msg_id)
        if len(_seen_ids) > 10_000:
            _seen_ids.clear()
            
        if msg.get("type") != "text":
            logger.info(f"Skipping non-text message type: {msg.get('type')}")
            return Response(status_code=200)
            
        sender = msg["from"]
        text = msg.get("text", {}).get("body", "")
        logger.info(f"Processing message from {sender}: {text[:50]}...")
        
        session_id = f"wa_{sender}"
        # Save user message synchronously before spawning thread
        save_message(session_id, "user", text)
        
        logger.info(f"Spawning native thread for session {session_id}")
        thread = threading.Thread(target=_run_agent, args=(sender, session_id, text), daemon=True)
        thread.start()
        
    except Exception as e:
        logger.exception(f"Error parsing WhatsApp payload: {e}")

    return Response(status_code=200)

