"""Which browser origins can open a session with a public agent?

Opens an unauthenticated ElevenAgents WebSocket session (no API key, as a web widget would)
with different Origin headers and reports whether each one starts a conversation. The agent's
allowlist decides this when enforced. The provisioning scripts keep
require_origin_header=false, because voice (WebRTC) sessions dropped with it on, so treat the
allowlist as a convenience, not a security boundary; this probe shows what it actually does.

Checks: ALLOWED_ORIGIN, if given, starts a session. Every other origin is reported as INFO.
Each session is closed at once; with ELEVENLABS_API_KEY set, the conversations it created are
deleted.

Environment:
  AGENT_ID             the agent to probe
  ALLOWED_ORIGIN       optional: an origin on the agent's allowlist, e.g. https://www.example.com
  OTHER_ORIGINS        optional: comma-separated; default "https://evil.example,none"
                       ("none" sends no Origin header)
  ELEVENLABS_API_KEY   optional: deletes the conversations the probe opened

Usage: python tests/live/origin_probe.py
"""

from __future__ import annotations

import asyncio
import json

import requests
import websockets
from _common import check, finish, info, need, opt

AGENT = need("AGENT_ID")
URL = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT}"
ALLOWED = opt("ALLOWED_ORIGIN")
OTHERS = [
    o.strip() for o in opt("OTHER_ORIGINS", "https://evil.example,none").split(",") if o.strip()
]
created: list[str] = []


async def probe(origin: str | None) -> tuple[bool, str]:
    try:
        async with websockets.connect(
            URL,
            additional_headers={"Origin": origin} if origin else {},
            open_timeout=15,
        ) as ws:
            await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
            for _ in range(5):
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if ev.get("type") == "conversation_initiation_metadata":
                    created.append(ev["conversation_initiation_metadata_event"]["conversation_id"])
                    return True, "session started"
            return False, "connected, no session metadata"
    except Exception as e:  # report every refusal reason, whatever its type
        return False, f"refused: {type(e).__name__}: {str(e)[:120]}"


async def main() -> None:
    if ALLOWED:
        ok, detail = await probe(ALLOWED)
        check(f"allowlisted origin {ALLOWED} can start a session", ok, detail)
    for o in OTHERS:
        ok, detail = await probe(None if o == "none" else o)
        info(f"origin {o}: {'ACCEPTED' if ok else 'not accepted'} ({detail})")


asyncio.run(main())
key = opt("ELEVENLABS_API_KEY")
for conv in created:
    if key:
        d = requests.delete(
            f"https://api.elevenlabs.io/v1/convai/conversations/{conv}",
            headers={"xi-api-key": key},
            timeout=30,
        )
        info(f"cleanup: conversation delete -> HTTP {d.status_code}")
    else:
        info("cleanup skipped: set ELEVENLABS_API_KEY to delete the probe's conversations")
if not ALLOWED:
    info("no ALLOWED_ORIGIN given: informational run only")
    raise SystemExit(0)
finish()
