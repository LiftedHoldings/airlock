"""Spike: a scripted Custom LLM endpoint that records what ElevenLabs sends it.

Two routes, one per agent:
  /main/...  the main agent. First turn: hand off to the payment agent.
             After the hand-back: answer, and record what history it was given.
  /pay/...   the payment agent. Asks for the card; once digits arrive, hands back.

Every request body is appended to spike/out/requests.jsonl (the Authorization
header is dropped). Only canary test numbers are ever typed into this spike.
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
LOG = OUT / "requests.jsonl"
DIGITS = re.compile(r"\d{12,19}")


def _sse(chunks: list[dict]) -> bytes:
    out = b""
    for c in chunks:
        out += b"data: " + json.dumps(c).encode() + b"\n\n"
    return out + b"data: [DONE]\n\n"


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": f"spike-{time.time_ns()}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "airlock-spike",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _text(s: str) -> bytes:
    return _sse([_chunk({"role": "assistant", "content": s}), _chunk({}, "stop")])


def _tool_call(name: str, args: dict) -> bytes:
    call = {
        "index": 0,
        "id": f"call_{time.time_ns()}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }
    return _sse([_chunk({"role": "assistant", "tool_calls": [call]}), _chunk({}, "tool_calls")])


def _transfer_args(tools: list[dict]) -> dict:
    """Fill the transfer_to_agent parameters from the schema ElevenLabs sent."""
    for t in tools or []:
        fn = t.get("function") or {}
        if fn.get("name") == "transfer_to_agent":
            props = (fn.get("parameters") or {}).get("properties") or {}
            args = {}
            for k, v in props.items():
                typ = v.get("type")
                args[k] = 0 if typ in ("integer", "number") else "spike handoff"
            return args
    return {"agent_number": 0, "reason": "spike handoff"}


def _has_tool(tools: list[dict], name: str) -> bool:
    return any((t.get("function") or {}).get("name") == name for t in tools or [])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_unparsed": raw[:2000].decode("utf-8", "replace")}
        route = (
            "main"
            if self.path.startswith("/main")
            else "pay"
            if self.path.startswith("/pay")
            else "other"
        )
        headers = {k: v for k, v in self.headers.items() if k.lower() != "authorization"}
        msgs = body.get("messages") or []
        joined = json.dumps(msgs)
        rec = {
            "t": time.time(),
            "route": route,
            "path": self.path,
            "headers": headers,
            "had_authorization": "authorization" in {k.lower() for k in self.headers},
            "body": body,
            "canary_in_messages": bool(DIGITS.search(joined)),
        }
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        tools = body.get("tools") or []
        users = [m for m in msgs if m.get("role") == "user"]
        last_user = (users[-1].get("content") if users else "") or ""
        if isinstance(last_user, list):
            last_user = " ".join(p.get("text", "") for p in last_user if isinstance(p, dict))
        roles = [m.get("role") for m in msgs]

        if route == "pay":
            if DIGITS.search(last_user) and _has_tool(tools, "transfer_to_agent"):
                resp = _tool_call("transfer_to_agent", _transfer_args(tools))
            else:
                resp = _text("Payment desk. Please type your card number, then press pound.")
        elif route == "main":
            transferred_before = "tool" in roles or any(m.get("tool_calls") for m in msgs)
            if not transferred_before and _has_tool(tools, "transfer_to_agent"):
                resp = _tool_call("transfer_to_agent", _transfer_args(tools))
            else:
                resp = _text("Back with the main agent. Anything else?")
        else:
            resp = _text("ok")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8911
    print(f"recorder on :{port}, log {LOG}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
