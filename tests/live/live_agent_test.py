"""Hold a text conversation with a live ElevenAgents agent wired to Airlock, pay an order with
the published test card 4111111111111111, then read what ElevenLabs stored.

Works for both modes: pass the full Custom LLM agent, or the MAIN agent of a payment desk
pair (it transfers to the desk). The default order id, T<cents>, is priced by Airlock's demo
catalogue, so the Airlock under test must run with AIRLOCK_DEMO=1, or set ORDER_ID to an order
your ORDER_LOOKUP_URL knows. The amount varies per run so the gateway's duplicate check does
not refuse a repeat.

Checks: a conversation starts; the payment goes through ("went through"); no agent reply
contains a card-like number; no agent message in the stored transcript contains one. It
also reports (INFO) whether the stored transcript holds the typed card: text typed through
the text channel is stored as typed, and redact_input applies to keypad (DTMF) entries.

Environment:
  ELEVENLABS_API_KEY   ElevenLabs API key (signed URL, transcript read, cleanup)
  AGENT_ID             the agent to call
  ORDER_ID             optional; default T<random cents> (needs AIRLOCK_DEMO=1 on Airlock)
  ASK_ZIP              optional; "1" (default) if Airlock asks for the ZIP, else "0"
  OFFER_SAVE           optional; "1" (default) if Airlock offers to save the card, else "0"
  KEEP_CONVERSATION    optional; "1" keeps the conversation in ElevenLabs (default: deleted)

Usage: python tests/live/live_agent_test.py
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import requests
import websockets
from _common import TEST_CARD, check, finish, info, mask, need, opt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from airlock.core.cards import find_cardlike  # noqa: E402

API = "https://api.elevenlabs.io"
H = {"xi-api-key": need("ELEVENLABS_API_KEY")}
AGENT = need("AGENT_ID")
ORDER = opt("ORDER_ID", f"T{1000 + secrets.randbelow(8999)}")
TURNS = [f"I'd like to pay for order {ORDER}.", TEST_CARD + "#", "1229#", "999#"]
if opt("ASK_ZIP", "1") == "1":
    TURNS.append("12345#")
if opt("OFFER_SAVE", "1") == "1":
    TURNS.append("2")
TURNS.append("1")
QUIET, CAP = 4.0, 30.0  # a turn ends after 4 s without a reply, or 30 s at most


async def converse() -> tuple[str | None, list[str]]:
    r = requests.get(
        f"{API}/v1/convai/conversation/get-signed-url",
        headers=H,
        params={"agent_id": AGENT},
        timeout=30,
    )
    r.raise_for_status()
    conv, said_all = None, []
    async with websockets.connect(r.json()["signed_url"], max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))

        async def drain() -> str:
            nonlocal conv
            said, start, last = [], time.time(), None
            while True:
                now = time.time()
                limit = min(start + CAP, (last + QUIET) if last else start + CAP)
                if now >= limit:
                    break
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=limit - now))
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    break
                kind = ev.get("type")
                if kind == "ping":
                    await ws.send(
                        json.dumps({"type": "pong", "event_id": ev["ping_event"]["event_id"]})
                    )
                elif kind == "conversation_initiation_metadata":
                    conv = ev["conversation_initiation_metadata_event"]["conversation_id"]
                elif kind == "agent_response":
                    said.append(ev["agent_response_event"]["agent_response"])
                    last = time.time()
            return " ".join(said)

        said_all.append(await drain())
        info(f"agent: {said_all[-1]}")
        for turn in TURNS:
            try:
                await ws.send(json.dumps({"type": "user_message", "text": turn}))
            except websockets.ConnectionClosed:
                info("the agent ended the call")
                break
            said_all.append(await drain())
            digits = turn.rstrip("#")
            if digits.isdigit() and len(digits) > 2:  # keypad entry: show only a card's last four
                shown = (
                    "•" * (len(digits) - 4) + digits[-4:] if len(digits) > 6 else "•" * len(digits)
                )
            else:
                shown = turn
            info(f"caller: {shown}")
            info(f"agent:  {said_all[-1]}")
    return conv, said_all


conv, replies = asyncio.run(converse())
check("conversation started", bool(conv), f"conversation id present: {bool(conv)}")
check(
    "payment went through",
    any("went through" in s for s in replies),
    "an agent reply contained 'went through'",
)
check(
    "no agent reply contains a card-like number",
    not any(find_cardlike(s) for s in replies),
    f"scanned {len(replies)} replies",
)
if conv:
    time.sleep(6)  # the transcript is stored shortly after the conversation ends
    r = requests.get(f"{API}/v1/convai/conversations/{conv}", headers=H, timeout=30)
    transcript = r.json().get("transcript", []) if r.status_code == 200 else []
    agent_text = " ".join(t.get("message") or "" for t in transcript if t.get("role") == "agent")
    check(
        "stored transcript: no agent message contains a card-like number",
        r.status_code == 200 and not find_cardlike(agent_text),
        f"HTTP {r.status_code}; {len(transcript)} stored turns",
    )
    info(
        "stored transcript holds the typed test card: "
        f"{TEST_CARD in json.dumps(transcript)} (typed text is stored as typed)"
    )
    for t in transcript[:4]:
        info(f"stored: {t.get('role')} {mask(t.get('message') or '')[:70]!r}")
    if opt("KEEP_CONVERSATION") != "1":
        d = requests.delete(f"{API}/v1/convai/conversations/{conv}", headers=H, timeout=30)
        info(f"cleanup: conversation delete -> HTTP {d.status_code}")
finish()
