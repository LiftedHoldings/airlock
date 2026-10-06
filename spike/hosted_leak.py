"""Spike 2: after a hand-back, does an agent on ElevenLabs' OWN hosted model see the digits
typed during the payment agent's turns?

Main agent: ElevenLabs-hosted LLM. Payment agent: Custom LLM = recorder.py (scripted; hands
back once digits arrive). After the hand-back the caller asks the main agent to read the
number back. Test card only (4111111111111111).

Usage: python hosted_leak.py <public_recorder_base_url>   (ELEVENLABS_API_KEY in env)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import requests
import websockets
from transfer_history import API, create_agent, h, patch_transfers

CANARY = "4111111111111111"


def create_hosted(name: str, prompt: str, first: str, transfers: list[dict]) -> str:
    agent = {
        "first_message": first,
        "language": "en",
        "prompt": {
            "prompt": prompt,
            "llm": "gemini-2.5-flash",
            "built_in_tools": {
                "transfer_to_agent": {
                    "name": "transfer_to_agent",
                    "description": "",
                    "params": {
                        "system_tool_type": "transfer_to_agent",
                        "transfers": transfers,
                    },
                }
            },
        },
    }
    body = {
        "name": name,
        "conversation_config": {"agent": agent, "conversation": {"text_only": True}},
        "tags": ["airlock-spike"],
    }
    r = requests.post(f"{API}/v1/convai/agents/create", headers=h(), json=body, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"create {name}: {r.status_code} {r.text[:600]}")
    return r.json()["agent_id"]


async def talk(agent_id: str, turns: list[str]) -> list[tuple[str, str]]:
    url = requests.get(
        f"{API}/v1/convai/conversation/get-signed-url",
        headers=h(),
        params={"agent_id": agent_id},
        timeout=30,
    ).json()["signed_url"]
    log = []
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))

        async def drain(sec):
            said, end = [], time.time() + sec
            while time.time() < end:
                try:
                    ev = json.loads(
                        await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                    )
                except asyncio.TimeoutError:
                    break
                if ev.get("type") == "ping":
                    await ws.send(
                        json.dumps({"type": "pong", "event_id": ev["ping_event"]["event_id"]})
                    )
                elif ev.get("type") == "agent_response":
                    said.append(ev["agent_response_event"]["agent_response"])
            return " ".join(said)

        log.append(("(start)", await drain(8)))
        for t in turns:
            await ws.send(json.dumps({"type": "user_message", "text": t}))
            log.append((t, await drain(18)))
    return log


def main() -> None:
    base = sys.argv[1].rstrip("/")
    pay = create_agent(
        "airlock-spike-pay2",
        f"{base}/pay/v1",
        "Payment desk. Conversation id: {{system__conversation_id}}.",
        "",
        [],
    )
    main_id = create_hosted(
        "airlock-spike-hosted-main",
        "You are a helpful store assistant. If the caller wants to pay, transfer them to the payment agent. "
        "When the caller asks you to read back what they typed, do so exactly.",
        "Hi, this is the store assistant.",
        [{"agent_id": pay, "condition": "The caller wants to pay."}],
    )
    patch_transfers(pay, [{"agent_id": main_id, "condition": "Payment step finished."}])
    log = asyncio.run(
        talk(
            main_id,
            [
                "I'd like to pay for my order.",
                CANARY,
                "Thanks. What number did I just type on the keypad? Read every digit.",
            ],
        )
    )
    for said, heard in log:
        shown = "•" * 12 + said[-4:] if said == CANARY else said
        print(f"caller: {shown}\nagent:  {heard}\n")
    final = log[-1][1]
    leaked = CANARY in final.replace(" ", "").replace("-", "") or "4111" in final
    print("RESULT: hosted model read the typed card back after the hand-back:", leaked)


if __name__ == "__main__":
    main()
