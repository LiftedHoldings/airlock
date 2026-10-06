"""Airlock — an OpenAI-compatible Custom LLM endpoint for ElevenLabs Agents.

Request path (every turn):
    ElevenAgents -> vault inbound proxy (inbound.js) -> POST /v1/chat/completions here

1. Verify the vault edge signature; reject anything that skipped the vault.
2. Tripwire: refuse any body that still contains a card-like number.
3. If a capture is open for this conversation, answer from the script (no model).
4. Otherwise forward a cleaned history to the upstream model, with ``start_payment``
   added to its tools, and stream its reply back through an output guard.

Airlock only ever holds token ids, last four, brand, attempt counts and the order id.
Logging is allowlisted: request bodies are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .core import script
from .core.cards import find_cardlike
from .core.sanitizer import (
    PLACEHOLDER,
    CardDataDetected,
    check_model_output,
    clean_for_model,
    tripwire,
)
from .orders import clean_holder_name, lookup_order
from .orders import normalize as normalize_order
from .sessions import SessionStore

log = logging.getLogger("airlock")
if not log.handlers:
    # Allowlisted events go to stdout so `docker logs` shows them (uvicorn's default logging
    # config does not print INFO records from application loggers).
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


def _cfg(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or (default is None and not v.strip()):
        # Required settings may not be missing OR empty (an empty HMAC key is forgeable).
        raise SystemExit(f"airlock: missing required setting {name}")
    return v


EDGE_SECRET = _cfg("AIRLOCK_EDGE_SECRET")
if len(EDGE_SECRET.encode()) < 32:
    raise SystemExit("airlock: AIRLOCK_EDGE_SECRET must be at least 32 bytes")
VAULT_OUTBOUND_URL = _cfg(
    "VAULT_OUTBOUND_URL"
)  # e.g. https://<proxy-host> or http://127.0.0.1:8920/outbound
VAULT_TOKENS_URL = _cfg("VAULT_TOKENS_URL", "")  # token delete endpoint base
VAULT_API_KEY = _cfg("VAULT_API_KEY", "")
VAULT_PROXY_KEY = _cfg("VAULT_OUTBOUND_PROXY_KEY", "")
GATEWAY = _cfg("AIRLOCK_GATEWAY", "nmi")
# The merchant's language model is needed only in Full Custom LLM mode. A payment-desk-only
# deployment leaves these empty; a model turn then answers with a scripted apology.
UPSTREAM_BASE_URL = _cfg("UPSTREAM_BASE_URL", "")
UPSTREAM_API_KEY = _cfg("UPSTREAM_API_KEY", "")
UPSTREAM_MODEL = _cfg("UPSTREAM_MODEL", "")
MAX_SKEW = int(_cfg("AIRLOCK_MAX_SKEW_SECONDS", "300"))
OFFER_SAVE = _cfg("AIRLOCK_OFFER_SAVE", "1") == "1"
ASK_ZIP = _cfg("AIRLOCK_ASK_ZIP", "1") == "1"
EVENT_LOG = os.environ.get("AIRLOCK_EVENT_LOG", "")
CAPTURE_INBOUND = os.environ.get("AIRLOCK_CAPTURE_INBOUND", "")  # tests only
# Latency masking. The vault proxy buffers Airlock's streamed reply, so a filler Airlock emits
# before the charge is NOT heard early; mask the wait with the agent's own soft-timeout filler
# instead. Leave this empty unless the deployment streams end to end.
CHARGE_FILLER = os.environ.get("AIRLOCK_CHARGE_FILLER", "")
# Keep-warm: the vault runs transforms in a serverless runtime; a cold one added ~10.8 s.
VAULT_INBOUND_URL = os.environ.get("VAULT_INBOUND_URL", "")
VAULT_INBOUND_KEY = os.environ.get("VAULT_INBOUND_KEY", "")
KEEPWARM_SECONDS = int(os.environ.get("AIRLOCK_KEEPWARM_SECONDS", "240"))


@contextlib.asynccontextmanager
async def _lifespan(_app):
    task = asyncio.create_task(_keepwarm_loop()) if KEEPWARM_SECONDS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Airlock", docs_url=None, redoc_url=None, lifespan=_lifespan)

# The public report + live demo page (site/), served at /airlock when SITE_DIR is set.
SITE_DIR = os.environ.get("AIRLOCK_SITE_DIR", "")
if SITE_DIR and os.path.isdir(SITE_DIR):
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import RedirectResponse

    @app.get("/airlock", include_in_schema=False)
    async def _site_root():
        return RedirectResponse("/airlock/", status_code=301)

    app.mount("/airlock", StaticFiles(directory=SITE_DIR, html=True), name="site")
sessions = SessionStore(ttl_seconds=600)
_http = httpx.AsyncClient(
    timeout=httpx.Timeout(20.0, connect=5.0),
    http2=False,
    limits=httpx.Limits(keepalive_expiry=60),
)

START_PAYMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "start_payment",
        "description": (
            "Start a secure payment for an order, by card or by bank account (eCheck). Call this when "
            "the caller is ready to pay. Never ask for card or bank details yourself; this tool "
            "collects them securely by keypad."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order or invoice id being paid.",
                },
                "method": {
                    "type": "string",
                    "enum": ["card", "check"],
                    "description": "card (default), or check if the caller wants to pay from a bank account / eCheck / ACH.",
                },
                "account_holder_name": {
                    "type": "string",
                    "description": "For method=check only: the name on the bank account. Ask the caller for it before calling.",
                },
            },
            "required": ["order_id"],
        },
    },
}

PAYMENT_RULES = (
    "Payment rules (enforced by the payment system): To take a card payment, call start_payment "
    "with the caller's order id as soon as they want to pay; it collects the card securely by keypad. "
    "You never see card numbers: '[keypad entry captured]' and '[card number said aloud: removed for security]' "
    "mean the system removed them. If a caller says a card number aloud, tell them it was not kept and that "
    "they'll type it on the keypad when prompted, then call start_payment if you know the order id. "
    "To pay by bank account (eCheck), first ask the name on the account, then call start_payment with "
    "method='check' and account_holder_name; the routing and account numbers are typed on the keypad. "
    "Never ask for or repeat card or bank numbers. This is a phone call: answer in one or two short spoken sentences, no lists, no bracketed tags or stage directions."
)

CONV_RX = re.compile(r"\bconv_[A-Za-z0-9]{8,}\b")


def _now() -> str:
    # ISO text, not epoch floats: a float like 1791292673.7754595 is a digit run the leak scan must not see.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def event(kind: str, **fields) -> None:
    """Allowlisted structured event log: only named, non-sensitive fields."""
    allowed = {
        "conversation",
        "step",
        "intent",
        "status",
        "ms",
        "reason",
        "gateway",
        "turn",
        "mode",
        "hop",
        "brand",
        "last4",
        "len",
        "field",
        "say",
    }
    rec = {
        "t": _now(),
        "kind": kind,
        **{k: v for k, v in fields.items() if k in allowed},
    }
    if "say" in rec and find_cardlike(str(rec["say"])):
        rec.pop("say")  # belt and braces: never publish anything card-like
    log.info(json.dumps(rec))
    if EVENT_LOG:
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    conv = rec.get("conversation")
    for q in _subscribers.get(conv, []) if conv else []:
        if q.qsize() < 200:
            q.put_nowait(rec)


# Live, read-only event feed per conversation for the /airlock page's flow diagram.
# Carries only the allowlisted fields above: no tokens, no digits beyond test-card last four.
_subscribers: dict[str, list[asyncio.Queue]] = {}


# ---- SSE helpers ------------------------------------------------------------------


def _chunk(delta: dict, finish: str | None = None) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "id": "airlock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "airlock",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )
        + "\n\n"
    )


def _say(text: str) -> str:
    return _chunk({"role": "assistant", "content": text})


def _end() -> str:
    return _chunk({}, "stop") + "data: [DONE]\n\n"


def sse(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _once(text: str) -> AsyncIterator[str]:
    yield _say(text)
    yield _end()


# ---- edge verification -----------------------------------------------------------


def verify_edge(raw: bytes, ts: str | None, sig: str | None) -> bool:
    if not ts or not sig or not ts.isdigit():
        return False
    if abs(time.time() - int(ts)) > MAX_SKEW:
        return False
    mac = hmac.new(EDGE_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)


def conversation_key(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "system":
            hit = CONV_RX.search(str(m.get("content") or ""))
            if hit:
                return hit.group(0)
    # Fallback: hash of the opening of the conversation (stable across turns).
    head = json.dumps([messages[i].get("content") for i in range(min(2, len(messages)))])
    return "h_" + hashlib.sha256(head.encode()).hexdigest()[:24]


# ---- vault calls -------------------------------------------------------------------


async def vault_charge(c: script.Capture) -> dict:
    if c.method == "check":
        body = {
            "method": "check",
            "order_id": c.order_id,
            "amount": c.amount,
            "currency": c.currency,
            "routing": "{{ %s }}" % c.routing_token,
            "account": "{{ %s }}" % c.account_token,
            "account_type": c.account_type or "checking",
            "holder_name": c.holder_name,  # may be empty; see docs/ECHECK.md
            "save_card": c.save_card,
            "customer_ref": c.order_id,
        }
    else:
        body = {
            "order_id": c.order_id,
            "amount": c.amount,
            "currency": c.currency,
            "pan": "{{ %s }}" % c.pan_token,
            "exp": "{{ %s }}" % c.exp_token,
            "cvv": "{{ %s }}" % c.cvv_token,
            "save_card": c.save_card,
            "customer_ref": c.order_id,
        }
    if c.zip_token:
        body["zip"] = "{{ %s }}" % c.zip_token
    # Only the proxy key: Airlock's API key deliberately cannot use tokens (see DESIGN.md).
    headers = {"Content-Type": "application/json", "BT-PROXY-KEY": VAULT_PROXY_KEY}
    t0 = time.perf_counter()
    try:
        r = await _http.post(VAULT_OUTBOUND_URL, json=body, headers=headers, timeout=24.0)
        res = classify_charge_reply(r.status_code, r.json())
    except (httpx.TimeoutException, httpx.TransportError):
        res = {"status": "unknown", "reason": "vault-or-gateway-timeout"}
    except ValueError:
        res = {"status": "unknown", "reason": "unparseable"}
    if CAPTURE_INBOUND:
        # Test evidence only: the token-only request Airlock sent and the reduced gateway reply.
        with open(CAPTURE_INBOUND, "a", encoding="utf-8") as f:
            f.write(
                json.dumps({"t": _now(), "outbound_request": body, "outbound_response": res}) + "\n"
            )
    event(
        "charge",
        conversation=c.conversation_id,
        status=res.get("status"),
        gateway=GATEWAY,
        ms=round((time.perf_counter() - t0) * 1000),
    )
    return res


async def vault_delete(token_ids: list[str]) -> None:
    if not VAULT_TOKENS_URL or not token_ids:
        return
    headers = {"BT-API-KEY": VAULT_API_KEY} if VAULT_API_KEY else {}
    await asyncio.gather(
        *(
            _http.delete(f"{VAULT_TOKENS_URL.rstrip('/')}/{t}", headers=headers)
            for t in token_ids
            if t
        ),
        return_exceptions=True,
    )


# ---- the endpoint ----------------------------------------------------------------


KNOWN_OUTCOMES = {"approved", "declined", "duplicate", "held"}


def classify_charge_reply(http_status: int, body) -> dict:
    """Map the outbound proxy's reply to an outcome. Only a rejection that provably happened
    BEFORE the gateway (our request transform refused it, or the vault could not detokenize)
    may be reported as 'error, no charge made'. Anything else unclear is 'unknown'."""
    body = body if isinstance(body, dict) else {}
    if http_status == 200 and body.get("status") in KNOWN_OUTCOMES:
        return body
    if http_status == 200 and body.get("status") == "error" and body.get("gateway"):
        return body  # the gateway itself answered with an error (response=3): nothing charged
    if http_status == 400 and (
        body.get("reason") in ("invalid", "transform-error") or "proxy_error" in body
    ):
        return {"status": "error", "reason": "rejected-before-gateway"}
    return {"status": "unknown", "reason": f"unclear-reply-http-{http_status}"}


async def warm_outbound() -> None:
    headers = {"Content-Type": "application/json", "BT-PROXY-KEY": VAULT_PROXY_KEY}
    try:
        await _http.post(
            VAULT_OUTBOUND_URL,
            json={"airlock_ping": True},
            headers=headers,
            timeout=20.0,
        )
    except httpx.HTTPError:
        pass  # warming is best effort; the charge path has its own error handling


async def warm_inbound() -> None:
    if not (VAULT_INBOUND_URL and VAULT_INBOUND_KEY):
        return
    try:
        await _http.post(
            VAULT_INBOUND_URL.rstrip("/") + "/chat/completions",
            json={"airlock_ping": True},
            headers={
                "BT-PROXY-KEY": VAULT_INBOUND_KEY,
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
    except httpx.HTTPError:
        pass


async def _keepwarm_loop() -> None:
    while True:
        t0 = time.perf_counter()
        await asyncio.gather(warm_inbound(), warm_outbound())
        event("keepwarm", ms=round((time.perf_counter() - t0) * 1000))
        await asyncio.sleep(KEEPWARM_SECONDS)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "open_captures": sessions.open_count()}


@app.get("/events/{conv}")
async def events(conv: str, request: Request):
    """Server-sent events for one conversation (unguessable ElevenLabs conversation id)."""
    # Per-conversation cap (a page usually needs one) plus a generous global ceiling, so one
    # client opening many streams to a made-up id cannot starve real viewers.
    if (
        not CONV_RX.fullmatch(conv)
        or len(_subscribers.get(conv, [])) >= 4
        or sum(len(v) for v in _subscribers.values()) > 2000
    ):
        return JSONResponse({"error": "bad request"}, status_code=400)
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(conv, []).append(q)

    async def stream() -> AsyncIterator[str]:
        try:
            yield "retry: 2000\n\n"
            deadline = time.time() + 600
            while time.time() < deadline and not await request.is_disconnected():
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: " + json.dumps(rec) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            subs = _subscribers.get(conv, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                _subscribers.pop(conv, None)

    return sse(stream())


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(request: Request):
    raw = await request.body()
    if not verify_edge(
        raw,
        request.headers.get("x-airlock-edge-ts"),
        request.headers.get("x-airlock-edge-sig"),
    ):
        event("rejected", reason="missing-or-bad-edge-signature")
        return JSONResponse({"error": "forbidden"}, status_code=401)
    try:
        tripwire(PLACEHOLDER.sub("", raw.decode("utf-8", "replace")))
    except CardDataDetected:
        event("tripwire", reason="raw-card-data-reached-airlock")
        return sse(_once("I'm sorry, I can't take a card payment right now."))
    try:
        body = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if CAPTURE_INBOUND:
        # Test evidence only: the exact body Airlock received (already past the tripwire).
        with open(CAPTURE_INBOUND, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": _now(), "messages": body.get("messages")}) + "\n")

    messages: list[dict] = body.get("messages") or []
    conv = conversation_key(messages)
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    last_text = last_user.get("content") if isinstance(last_user.get("content"), str) else ""
    cap = sessions.get(conv)
    _announce_turn(conv, last_text or "", request.headers.get("x-airlock-edge-ts"), cap)

    desk = request.headers.get("x-airlock-mode", "").lower() == "desk"
    if cap is not None and cap.step not in (script.Step.DONE, script.Step.CANCELLED):
        return sse(capture_turn(cap, last_text or "", desk=desk, tools=body.get("tools") or []))
    if desk:
        return sse(desk_turn(conv, messages, last_text or "", body.get("tools") or []))
    return sse(model_turn(conv, body, messages))


# ---- payment desk mode -------------------------------------------------------------
# A dedicated payment agent (reached by transfer_to_agent from a main agent that runs on
# ElevenLabs' own hosted model) sends `X-Airlock-Mode: desk`. No language model is called at
# all: Airlock scripts every word, then ends the call, even when the caller asks for a person
# or a link (the desk cannot transfer without handing the history on). It must never
# hand back to an AI agent: ElevenLabs re-sends the full history, typed digits included, to
# whichever agent speaks next, and a hosted model is not behind the vault.
ORDER_IN_SPEECH = re.compile(r"\b([A-Za-z]{0,2}-?\d{3,8}(?:-[A-Za-z0-9]{2,6})?)\b")
CHECK_WORDS = re.compile(r"\b(bank|checking|savings|e-?check|ach|routing|account number)\b", re.I)
DESK_GOODBYE = "Thanks for calling. Goodbye."


def _recent_user_text(messages: list[dict], n: int = 8) -> list[str]:
    users = [
        m.get("content")
        for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]
    return [u for u in users[-n:] if "[[airlock" not in u]


def _tool_args(tool: dict, values: dict) -> str:
    """Arguments for a platform tool, filled from the JSON schema ElevenLabs sent: every
    required string gets a value (from `values` when named there), integers get 0."""
    params = (tool.get("function") or {}).get("parameters") or {}
    props = params.get("properties") or {}
    args = {}
    for k in params.get("required") or []:
        typ = (props.get(k) or {}).get("type")
        args[k] = values.get(k, 0 if typ in ("integer", "number") else "Payment complete.")
    return json.dumps(args)


def _end_call_chunks(tools: list[dict]) -> str:
    tool = next((t for t in tools if (t.get("function") or {}).get("name") == "end_call"), None)
    if tool is None:
        return _end()
    call = {
        "index": 0,
        "id": "call_end",
        "type": "function",
        "function": {
            "name": "end_call",
            "arguments": _tool_args(tool, {"reason": "Payment complete."}),
        },
    }
    return (
        _chunk({"role": "assistant", "tool_calls": [call]})
        + _chunk({}, "tool_calls")
        + "data: [DONE]\n\n"
    )


async def desk_turn(
    conv: str, messages: list[dict], last_text: str, tools: list[dict]
) -> AsyncIterator[str]:
    cap = sessions.get(conv)
    if cap is not None:  # payment finished on an earlier turn: close politely, end the call
        yield _say(DESK_GOODBYE)
        yield _end_call_chunks(tools)
        return
    recent = _recent_user_text(messages)
    method = "check" if any(CHECK_WORDS.search(u) for u in recent[-4:]) else "card"
    for text in reversed(recent):
        for m in ORDER_IN_SPEECH.finditer(text):
            if find_cardlike(m.group(1)):
                continue
            if await lookup_order(m.group(1)):
                event("desk-order-found", conversation=conv)
                opening = await open_capture(conv, m.group(1), method, ask_name=method == "check")
                cap = sessions.get(conv)
                if cap is not None and "[[airlock:kp " in last_text:
                    # The caller typed before being prompted: use the entry, don't swallow it.
                    reply = script.handle(cap, last_text)
                    total = opening.split(". ")[0] + ". "
                    yield _say(total + reply.say)
                else:
                    yield _say(opening)
                yield _end()
                return
    yield _say("I can take that payment. Which order number are you paying?")
    yield _end()


def _announce_turn(conv: str, text: str, edge_ts: str | None, cap) -> None:
    """Describe what arrived through the vault, for the live diagram. Safe metadata only."""
    from .core.sanitizer import parse_placeholder

    ph = parse_placeholder(text)
    mode = (
        "capture"
        if cap is not None and cap.step not in (script.Step.DONE, script.Step.CANCELLED)
        else "model"
    )
    if "[[airlock:not-test-card]]" in text:
        event(
            "turn",
            conversation=conv,
            hop="vault-in",
            field="refused-not-test-card",
            mode=mode,
        )
    elif "[[airlock:spoken]]" in text:
        event(
            "turn",
            conversation=conv,
            hop="vault-in",
            field="spoken-card-removed",
            mode=mode,
        )
    elif ph and not ph.prior:
        event(
            "turn",
            conversation=conv,
            hop="vault-in",
            field="keypad-token",
            len=ph.length,
            brand=ph.brand or None,
            last4=ph.last4 or None,
            mode=mode,
        )
    else:
        event("turn", conversation=conv, hop="vault-in", field="speech", mode=mode)


_charges: dict[str, asyncio.Task] = {}


async def _charge_and_settle(cap: script.Capture) -> script.Reply:
    cap.charge_started = time.time()
    result = await vault_charge(cap)
    reply = script.after_charge(cap, result)
    sessions.settle(
        cap.conversation_id,
        cap.order_id,
        cap.result.get("status") or result.get("status", "unknown"),
    )
    sessions.finish(cap.conversation_id, reply.handback)
    asyncio.create_task(vault_delete(reply.delete_tokens))
    _charges.pop(cap.conversation_id, None)
    reply.settled = True
    return reply


async def capture_turn(
    cap: script.Capture, text: str, desk: bool = False, tools: list[dict] | None = None
) -> AsyncIterator[str]:
    tools = tools or []
    t0 = time.perf_counter()
    reply = script.handle(cap, text)
    event(
        "capture",
        conversation=cap.conversation_id,
        step=cap.step.value,
        intent=script.classify(text) if "[[airlock" not in text else "keypad",
    )
    if reply.delete_tokens:
        asyncio.create_task(vault_delete(reply.delete_tokens))
    if cap.step == script.Step.CONFIRM:
        asyncio.create_task(warm_outbound())  # the charge is one keypress away: make sure it's hot
    if reply.charge:
        if CHARGE_FILLER:
            yield _say(CHARGE_FILLER + " ")
        event(
            "charge-start",
            conversation=cap.conversation_id,
            hop="vault-out",
            gateway=GATEWAY,
            brand=cap.brand,
            last4=cap.last4,
        )
        # The charge and its bookkeeping run in their own task: if ElevenLabs drops this
        # request (timeout, barge-in), the outcome is still recorded and the capture settles.
        task = asyncio.create_task(_charge_and_settle(cap))
        _charges[cap.conversation_id] = task
        reply = await asyncio.shield(task)
    elif (
        cap.step == script.Step.CHARGING
        and cap.charge_started
        and time.time() - cap.charge_started > 40
    ):
        # A charge task that outlived every timeout: settle as unknown, never retry.
        reply = script.after_charge(cap, {"status": "unknown", "reason": "charge-task-stalled"})
        sessions.settle(cap.conversation_id, cap.order_id, "unknown")
    desk_cancel = (
        desk
        and reply.finished
        and cap.step == script.Step.CANCELLED
        and cap.result.get("status") not in ("unknown", "duplicate")
    )
    # The capture's cancel lines offer a link or a person, which assumes the conversation
    # continues. On the desk the call ends, so say only that nothing was charged. Unknown and
    # duplicate outcomes keep their full wording ("our team will confirm...").
    say = (
        "Okay, I've stopped there. No payment was taken. You're welcome to call back and try again."
        if desk_cancel
        else reply.say
    )
    if say:
        yield _say(say)
    if reply.finished and not reply.settled:
        sessions.finish(cap.conversation_id, reply.handback)
    event(
        "capture-reply",
        conversation=cap.conversation_id,
        step=cap.step.value,
        ms=round((time.perf_counter() - t0) * 1000),
        say=say,  # what the caller heard
        status=cap.result.get("status"),
    )
    if desk and reply.finished:
        # Payment desk: never hand back to an AI agent. Close and end the call.
        yield _say(" " + DESK_GOODBYE)
        yield _end_call_chunks(tools)
        return
    yield _end()


async def model_turn(conv: str, body: dict, messages: list[dict]) -> AsyncIterator[str]:
    if not (UPSTREAM_BASE_URL and UPSTREAM_MODEL):
        event("refused", conversation=conv, reason="no-upstream-model-configured")
        yield _say("I'm sorry, I can only take payments on this line.")
        yield _end()
        return
    try:
        cleaned = clean_for_model(messages)
    except CardDataDetected:
        event("refused", conversation=conv, reason="card-like-number-in-history")
        yield _say("I'm sorry, I can't continue with that. Let me get you some help.")
        yield _end()
        return
    cleaned.insert(
        1 if cleaned and cleaned[0].get("role") == "system" else 0,
        {"role": "system", "content": PAYMENT_RULES},
    )
    for note in sessions.handbacks(conv):
        cleaned.append({"role": "system", "content": note})
    tools = [
        t
        for t in (body.get("tools") or [])
        if (t.get("function") or {}).get("name") != "start_payment"
    ]
    tools.append(START_PAYMENT_TOOL)
    payload = {
        "model": UPSTREAM_MODEL,
        "messages": cleaned,
        "tools": tools,
        "stream": True,
        "temperature": body.get("temperature", 0.3),
        "max_tokens": min(int(body.get("max_tokens") or 400), 600),
    }
    t0 = time.perf_counter()
    first = True
    text_so_far = ""
    tool_calls: dict[int, dict] = {}
    try:
        async with _http.stream(
            "POST",
            UPSTREAM_BASE_URL.rstrip("/") + "/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {UPSTREAM_API_KEY}"},
        ) as up:
            if up.status_code >= 400:
                raise httpx.HTTPStatusError("upstream", request=up.request, response=up)
            async for line in up.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    ch = json.loads(data)
                except ValueError:
                    continue
                for choice in ch.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        text_so_far += delta["content"]
                        check_model_output(text_so_far)  # raises on any card-like text
                        if first:
                            event(
                                "model-first-token",
                                conversation=conv,
                                ms=round((time.perf_counter() - t0) * 1000),
                            )
                            first = False
                        yield _say(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = tool_calls.setdefault(
                            tc.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
    except CardDataDetected:
        event("refused", conversation=conv, reason="card-like-number-in-model-output")
        yield _say(" Sorry, let me rephrase that.")
        yield _end()
        return
    except (httpx.HTTPError, httpx.StreamError):
        event("upstream-error", conversation=conv)
        yield _say("Sorry, I'm having trouble right now. Could you say that again?")
        yield _end()
        return

    calls = list(tool_calls.values())
    pay = next((c for c in calls if c["name"] == "start_payment"), None)
    if pay:
        try:
            args = json.loads(pay["arguments"] or "{}")
        except ValueError:
            args = {}
        order_id = str(args.get("order_id", "")).strip()
        method = "check" if str(args.get("method", "")).lower() == "check" else "card"
        given = str(args.get("account_holder_name") or "").strip()
        holder = clean_holder_name(given)
        if method == "check" and not holder and not given:
            # Both NMI (checkname) and Authorize.net (nameOnAccount) require it. Use the
            # merchant's order record when it has one; otherwise ask the caller. A name that
            # was given but cannot be sent is asked for again, never swapped for the order's
            # name: the bank account may belong to someone else.
            known = await lookup_order(order_id)
            holder = (known or {}).get("customer_name", "")
        if method == "check" and not holder and given:
            yield _say(
                "The bank needs the account name in plain letters, without accents or commas. "
                "What's the name on the bank account?"
            )
            yield _end()
            return
        if method == "check" and not holder:
            yield _say("Before we start, what's the name on the bank account?")
            yield _end()
            return
        yield _say(await open_capture(conv, order_id, method, holder))
        yield _end()
        return
    if calls:
        # Relay platform tools (end_call, transfer_to_number, ...) unchanged, but never let a
        # model put card-like data into tool arguments (e.g. a transfer note).
        if any(find_cardlike(c["arguments"] or "") for c in calls):
            event(
                "refused",
                conversation=conv,
                reason="card-like-number-in-tool-arguments",
            )
            yield _say("Sorry, let me try that another way.")
            yield _end()
            return
        yield _chunk(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": i,
                        "id": c["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for i, c in enumerate(calls)
                ],
            }
        )
        yield _chunk({}, "tool_calls") + "data: [DONE]\n\n"
        return
    yield _end()


async def open_capture(
    conv: str,
    order_id: str,
    method: str = "card",
    holder: str = "",
    ask_name: bool = False,
) -> str:
    if not conv.startswith("conv_"):
        # Without ElevenLabs' conversation id every caller would share one capture.
        event("refused", conversation=None, reason="no-conversation-id-in-prompt")
        return "I'm sorry, I can't take a payment on this line right now."
    existing = sessions.get(conv)
    if existing is not None and existing.step not in (
        script.Step.DONE,
        script.Step.CANCELLED,
    ):
        return script._ask(existing)
    oid = normalize_order(order_id)
    settled = sessions.settled(conv).get(oid)
    if settled == "approved":
        return "That order is already paid. Is there anything else I can help with?"
    if settled in ("unknown", "held", "duplicate"):
        return "We're still confirming the earlier payment for that order, so I can't take another one right now. Our team will follow up."
    order = await lookup_order(order_id)
    if order is None:
        return "I couldn't find that order number. Could you check it and tell me again?"
    cap = script.Capture(
        conversation_id=conv,
        order_id=order["order_id"],
        amount=order["amount"],
        offer_save=OFFER_SAVE,
        ask_zip=ASK_ZIP,
        method=method,
        holder_name=holder or order.get("customer_name", ""),
        # Desk mode has no model to ask for the account name, so the script asks first.
        ask_name=ask_name and not (holder or order.get("customer_name")),
    )
    sessions.put(conv, cap)
    event("capture-open", conversation=conv)
    return script.opening(cap)
