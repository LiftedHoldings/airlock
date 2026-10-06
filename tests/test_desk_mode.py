"""Payment desk mode: a dedicated payment agent with no language model. Airlock scripts every
word, finds the order in the conversation, and ends the call after paying."""

import asyncio
import json
import os

os.environ.update(
    AIRLOCK_EDGE_SECRET="x" * 40,
    VAULT_OUTBOUND_URL="http://127.0.0.1:9/outbound",
    UPSTREAM_BASE_URL="http://127.0.0.1:9",
    UPSTREAM_API_KEY="k",
    UPSTREAM_MODEL="m",
    AIRLOCK_DEMO="1",
    AIRLOCK_KEEPWARM_SECONDS="0",
)

from airlock import app as A  # noqa: E402
from airlock.core import script  # noqa: E402

END_CALL = [
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def collect(gen) -> tuple[str, list[str]]:
    async def run():
        return [c async for c in gen]

    chunks = asyncio.run(run())
    said, tools = [], []
    for c in chunks:
        for line in c.splitlines():
            if line.startswith("data: {"):
                d = json.loads(line[6:])["choices"][0]["delta"]
                if d.get("content"):
                    said.append(d["content"])
                for t in d.get("tool_calls") or []:
                    tools.append(t["function"]["name"])
    return "".join(said), tools


def msgs(*user):
    out = [{"role": "system", "content": "Payment desk. Conversation id: conv_desk000001."}]
    for u in user:
        out.append({"role": "user", "content": u})
    return out


def test_desk_finds_the_order_in_the_conversation_and_opens_capture():
    conv = "conv_deskfind0001"
    said, tools = collect(
        A.desk_turn(conv, msgs("I'd like to pay for order A1042 please"), "", END_CALL)
    )
    assert "84 dollars and 20 cents" in said and "keypad" in said and not tools
    assert A.sessions.get(conv).step == script.Step.PAN


def test_desk_asks_for_the_order_when_none_was_said():
    said, _ = collect(A.desk_turn("conv_desknoorder01", msgs("I want to pay"), "", END_CALL))
    assert "Which order number" in said


def test_desk_echeck_asks_the_account_name_first():
    conv = "conv_deskcheck0001"
    said, _ = collect(A.desk_turn(conv, msgs("pay order A2001 from my bank account"), "", END_CALL))
    cap = A.sessions.get(conv)
    assert (
        cap.method == "check"
        and cap.step == script.Step.NAME
        and "name on the bank account" in said
    )


def test_desk_ends_the_call_after_payment(monkeypatch):
    async def ok(cap):
        return {"status": "approved", "transaction_id": "99887766"}

    monkeypatch.setattr(A, "vault_charge", ok)
    conv = "conv_deskend00001"
    collect(A.desk_turn(conv, msgs("pay order A1042"), "", END_CALL))
    cap = A.sessions.get(conv)
    cap.offer_save = cap.ask_zip = False
    for ph in [
        "[[airlock:kp id=11111111-2222-3333-4444-555555555555 len=16 luhn=1 last4=1111 brand=visa]]",
        "[[airlock:kp id=21111111-2222-3333-4444-555555555555 len=4 luhn=0 expok=1]]",
        "[[airlock:kp id=31111111-2222-3333-4444-555555555555 len=3 luhn=0]]",
    ]:
        collect(A.capture_turn(cap, ph, desk=True, tools=END_CALL))
    said, tools = collect(A.capture_turn(cap, "1", desk=True, tools=END_CALL))
    assert "went through" in said and "Goodbye" in said and tools == ["end_call"]


def test_desk_cancel_promises_no_link_or_person():
    conv = "conv_deskcancel001"
    collect(A.desk_turn(conv, msgs("pay order A1042"), "", END_CALL))
    cap = A.sessions.get(conv)
    said, tools = collect(A.capture_turn(cap, "cancel", desk=True, tools=END_CALL))
    low = said.lower()
    assert "link" not in low and "person" not in low and "connect" not in low
    assert "No payment was taken" in said and tools == ["end_call"]


def test_desk_unknown_outcome_does_not_claim_nothing_was_taken(monkeypatch):
    async def lost(cap):
        return {"status": "unknown"}

    monkeypatch.setattr(A, "vault_charge", lost)
    conv = "conv_deskunknown01"
    collect(A.desk_turn(conv, msgs("pay order A2001"), "", END_CALL))
    cap = A.sessions.get(conv)
    cap.offer_save = cap.ask_zip = False
    for ph in [
        "[[airlock:kp id=11111111-2222-3333-4444-555555555555 len=16 luhn=1 last4=1111 brand=visa]]",
        "[[airlock:kp id=21111111-2222-3333-4444-555555555555 len=4 luhn=0 expok=1]]",
        "[[airlock:kp id=31111111-2222-3333-4444-555555555555 len=3 luhn=0]]",
    ]:
        collect(A.capture_turn(cap, ph, desk=True, tools=END_CALL))
    said, tools = collect(A.capture_turn(cap, "1", desk=True, tools=END_CALL))
    assert "No payment was taken" not in said and tools == ["end_call"]


def test_model_turn_without_upstream_is_a_scripted_refusal(monkeypatch):
    monkeypatch.setattr(A, "UPSTREAM_MODEL", "")
    said, tools = collect(A.model_turn("conv_noupstream01", {}, msgs("hello")))
    assert "only take payments" in said and not tools


def test_desk_cancel_event_logs_what_the_caller_heard(monkeypatch):
    seen = []
    monkeypatch.setattr(A, "event", lambda kind, **f: seen.append((kind, f)))
    conv = "conv_deskcancel002"
    collect(A.desk_turn(conv, msgs("pay order A1042"), "", END_CALL))
    collect(A.capture_turn(A.sessions.get(conv), "cancel", desk=True, tools=END_CALL))
    said = [f["say"] for k, f in seen if k == "capture-reply"][-1]
    assert said.startswith("Okay, I've stopped there.") and "link" not in said
