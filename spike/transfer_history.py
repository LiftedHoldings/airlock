"""Spike test 1: does transfer_to_agent carry the payment agent's keypad turns
back into the main agent's model context?

Creates two throwaway agents (airlock-spike-main / airlock-spike-pay) whose Custom
LLM is the local recorder behind a public tunnel, runs one text conversation over the
ElevenAgents WebSocket, then reads spike/out/requests.jsonl.

Usage: python transfer_history.py <public_base_url>   (e.g. https://xxxx.ngrok-free.app)
The ElevenLabs key comes from the environment (ELEVENLABS_API_KEY).
Only the canary number 4111111111111111 (a public test card) is ever typed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import requests
import websockets

API = "https://api.elevenlabs.io"
CANARY = "4111111111111111"
OUT = Path(__file__).parent / "out"


def _key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("set ELEVENLABS_API_KEY")
    return key


def h() -> dict:
    return {"xi-api-key": _key(), "Content-Type": "application/json"}


def create_agent(
    name: str, llm_url: str, prompt: str, first_message: str, transfers: list[dict]
) -> str:
    agent = {
        "first_message": first_message,
        "language": "en",
        "prompt": {
            "prompt": prompt,
            "llm": "custom-llm",
            "custom_llm": {"url": llm_url, "model_id": "airlock-spike"},
        },
    }
    if transfers:
        agent["prompt"]["built_in_tools"] = {
            "transfer_to_agent": {
                "name": "transfer_to_agent",
                "description": "",
                "params": {
                    "system_tool_type": "transfer_to_agent",
                    "transfers": transfers,
                },
            }
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


def patch_transfers(agent_id: str, transfers: list[dict]) -> None:
    body = {
        "conversation_config": {
            "agent": {
                "prompt": {
                    "built_in_tools": {
                        "transfer_to_agent": {
                            "name": "transfer_to_agent",
                            "description": "",
                            "params": {
                                "system_tool_type": "transfer_to_agent",
                                "transfers": transfers,
                            },
                        }
                    }
                }
            }
        }
    }
    r = requests.patch(f"{API}/v1/convai/agents/{agent_id}", headers=h(), json=body, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"patch {agent_id}: {r.status_code} {r.text[:600]}")


async def converse(agent_id: str, turns: list[str], transcript: list) -> None:
    r = requests.get(
        f"{API}/v1/convai/conversation/get-signed-url",
        headers=h(),
        params={"agent_id": agent_id},
        timeout=30,
    )
    r.raise_for_status()
    url = r.json()["signed_url"]
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))

        async def drain(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    return
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "ping":
                    await ws.send(
                        json.dumps({"type": "pong", "event_id": ev["ping_event"]["event_id"]})
                    )
                    continue
                if t in ("audio",):
                    continue
                transcript.append(ev)

        await drain(8)
        for text in turns:
            await ws.send(json.dumps({"type": "user_message", "text": text}))
            transcript.append({"type": "_sent", "text": text})
            await drain(14)


def main() -> None:
    base = sys.argv[1].rstrip("/")
    OUT.mkdir(exist_ok=True)
    log = OUT / "requests.jsonl"
    if log.exists():
        log.unlink()
    pay = create_agent(
        "airlock-spike-pay",
        f"{base}/pay/v1",
        "Payment desk. Conversation id: {{system__conversation_id}}.",
        "",
        [],
    )
    main_id = create_agent(
        "airlock-spike-main",
        f"{base}/main/v1",
        "Main desk. Conversation id: {{system__conversation_id}}.",
        "Hi, this is the Airlock spike.",
        [{"agent_id": pay, "condition": "The caller wants to pay."}],
    )
    patch_transfers(pay, [{"agent_id": main_id, "condition": "Payment step finished."}])
    print("agents:", main_id, pay)
    transcript: list = []
    asyncio.run(
        converse(
            main_id,
            ["I'd like to pay for order 1042.", CANARY, "What happens next?"],
            transcript,
        )
    )
    (OUT / "ws_transcript.json").write_text(json.dumps(transcript, indent=1), encoding="utf-8")

    reqs = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    print(f"recorded {len(reqs)} Custom LLM requests")
    seen_canary_main_after = False
    for i, rq in enumerate(reqs):
        msgs = rq["body"].get("messages") or []
        has = CANARY in json.dumps(msgs)
        print(
            f"#{i} route={rq['route']} path={rq['path']} msgs={len(msgs)} roles={[m.get('role') for m in msgs]} canary={has}"
        )
        if rq["route"] == "main" and has:
            seen_canary_main_after = True
    print(
        "RESULT: main agent model received the payment-agent digits after hand-back:",
        seen_canary_main_after,
    )
    json.dump({"main": main_id, "pay": pay}, open(OUT / "agents.json", "w"))


if __name__ == "__main__":
    main()
