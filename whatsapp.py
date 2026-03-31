from __future__ import annotations
import asyncio, hashlib, hmac, logging, os, re
from typing import Set
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from core.agent import process_user_message
from core.db import save_message

logger = logging.getLogger(__name__)
router = APIRouter()

WA_TOKEN        = os.environ["WA_TOKEN"]
WA_PHONE_ID     = os.environ["WA_PHONE_ID"]
WA_APP_SECRET   = os.environ["WA_APP_SECRET"]
WA_VERIFY_TOKEN = os.environ["WA_VERIFY_TOKEN"]

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


async def _send_message(to: str, text: str) -> None:
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        for chunk in _chunk(_strip_markdown(text)):
            await client.post(url, headers=headers, json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            })


async def _run_agent(sender: str, session_id: str, text: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: process_user_message(
                prompt=text,
                session_id=session_id,
                use_search=True,
                provider_override="Auto (Default)",
            ),
        )
        answer = result["answer"]
        await loop.run_in_executor(None, save_message, session_id, "assistant", answer)
        await _send_message(sender, answer)
    except Exception as e:
        logger.exception("Agent error: %s", e)
        await _send_message(sender, "Sorry, something went wrong. Please try again.")


@router.get("/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403)


@router.post("/whatsapp")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, sig):
        raise HTTPException(status_code=403)

    payload = await request.json()
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        if "messages" not in changes:
            return Response(status_code=200)
        msg = changes["messages"][0]
        msg_id = msg.get("id", "")
        if msg_id in _seen_ids:
            return Response(status_code=200)
        _seen_ids.add(msg_id)
        if len(_seen_ids) > 10_000:
            _seen_ids.clear()
        if msg.get("type") != "text":
            return Response(status_code=200)
        sender = msg["from"]
        text = msg["text"]["body"]
        session_id = f"wa_{sender}"
        await asyncio.get_event_loop().run_in_executor(
            None, save_message, session_id, "user", text
        )
        background_tasks.add_task(_run_agent, sender, session_id, text)
    except (KeyError, IndexError):
        pass

    return Response(status_code=200)
