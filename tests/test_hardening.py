"""Hardening tests: each reproduces a hostile or failure input and asserts the safe behaviour."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
from airlock import orders  # noqa: E402
from airlock.core import script  # noqa: E402
from airlock.core.cards import find_cardlike  # noqa: E402
from airlock.core.sanitizer import CardDataDetected, check_model_output, clean_for_model  # noqa: E402

CARD = "4111111111111111"
FW = "".join(chr(0xFF10 + int(d)) for d in CARD)  # full-width digits
AR = "".join(chr(0x0660 + int(d)) for d in CARD)  # Arabic-Indic digits
SEPARATED = [
    "4111, 1111, 1111, 1111",
    "4111. 1111. 1111. 1111",
    "4111 - 1111 - 1111 - 1111",
    "4111/1111/1111/1111",
    "four one one one, one one one one, one one one one, one one one one",
    FW,
    AR,
    "my card is " + FW[:8] + " " + FW[8:],
]


# separators and non-ASCII digits -----------------------------------
@pytest.mark.parametrize("text", SEPARATED)
def test_separated_and_unicode_cards_are_caught_everywhere(text):
    assert CARD in find_cardlike(text)
    with pytest.raises(CardDataDetected):
        clean_for_model([{"role": "user", "content": text}])
    with pytest.raises(CardDataDetected):
        check_model_output(text)


@pytest.mark.parametrize(
    "benign",
    ["1791292673.7754595", "84.20", "orders 1042, 1043 and 1044", "call 678-953-8315"],
)
def test_benign_numbers_still_pass(benign):
    assert find_cardlike(benign) == []


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_vault_edge_agrees_on_separated_and_unicode_cards():
    js = (
        "const h=require(process.argv[1]).helpers;"
        "const xs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(xs.map(x=>h.hasCardlike(x))));"
    )
    out = subprocess.run(
        ["node", "-e", js, str(ROOT / "vaults" / "basis_theory" / "inbound.js")],
        input=json.dumps(SEPARATED + ["1791292673.7754595"]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [True] * len(SEPARATED) + [False]


# a hedged "yes" must not charge -------------------------------------------
def _at_confirm():
    c = script.Capture(
        conversation_id="conv_testconfirm1",
        order_id="A1042",
        amount="84.20",
        offer_save=False,
        ask_zip=False,
    )
    script.handle(
        c,
        "[[airlock:kp id=11111111-2222-3333-4444-555555555555 len=16 luhn=1 last4=1111 brand=visa]]",
    )
    script.handle(c, "[[airlock:kp id=21111111-2222-3333-4444-555555555555 len=4 luhn=0 expok=1]]")
    script.handle(c, "[[airlock:kp id=31111111-2222-3333-4444-555555555555 len=3 luhn=0]]")
    assert c.step == script.Step.CONFIRM
    return c


@pytest.mark.parametrize(
    "said",
    [
        "Yeah, no, that's not my card",
        "Sure, actually can I use my Mastercard instead?",
        "Okay but that's the wrong card",
        "Right, I meant the other account",
        "Okay so I am not sure",
    ],
)
def test_hedged_yes_at_confirm_does_not_charge(said):
    c = _at_confirm()
    r = script.handle(c, said)
    assert not r.charge


@pytest.mark.parametrize("said", ["yes", "Yes please.", "go ahead", "1", "confirm"])
def test_clean_yes_at_confirm_charges(said):
    c = _at_confirm()
    assert script.handle(c, said).charge


# unclear outcomes are never "no charge made" -----------------------------------
def test_unrecognised_status_is_unknown_not_no_charge():
    c = _at_confirm()
    script.handle(c, "1")
    r = script.after_charge(c, {"status": 502})
    assert "No charge" not in r.handback and "no charge" not in r.handback.lower().replace(
        "no new charge", ""
    )
    assert "unknown" in r.handback


@pytest.mark.parametrize(
    "http,body,expect",
    [
        (200, {"status": "approved", "gateway": "nmi"}, "approved"),
        (200, {"status": "error", "gateway": "nmi"}, "error"),
        (400, {"status": "error", "reason": "invalid"}, "error"),
        (400, {"proxy_error": {"status": 400}}, "error"),
        (502, {"status": 502}, "unknown"),
        (200, {"weird": True}, "unknown"),
        (504, {}, "unknown"),
    ],
)
def test_charge_reply_classification(http, body, expect):
    assert A.classify_charge_reply(http, body)["status"] == expect


# amounts only from the merchant (or explicit demo mode) ------------------------
def test_no_lookup_and_no_demo_means_no_payment(monkeypatch):
    monkeypatch.setattr(orders, "DEMO", False)
    monkeypatch.delenv("ORDER_LOOKUP_URL", raising=False)
    assert asyncio.run(orders.lookup_order("T49999")) is None
    assert asyncio.run(orders.lookup_order("A1042")) is None


# eCheck holder names: only names the gateway transform accepts, never "None" -----
@pytest.mark.parametrize(
    "raw, expect",
    [
        (None, ""),
        ("  Test Payer  ", "Test Payer"),
        ("O'Brien-Smith", "O'Brien-Smith"),
        ("José García", ""),  # outbound transform would refuse it after the caller typed everything
        ("Smith, John", ""),
        ("4111 1111 1111 1111", ""),
        ("", ""),
    ],
)
def test_holder_name_cleaning(raw, expect):
    assert orders.clean_holder_name(raw) == expect


@pytest.mark.parametrize("name", [None, "Smith & Sons"])
def test_lookup_never_returns_an_unusable_name(monkeypatch, name):
    import httpx

    def reply(request):
        return httpx.Response(200, json={"amount": "12.00", "customer_name": name})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        orders.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(reply), **kw),
    )
    monkeypatch.setenv("ORDER_LOOKUP_URL", "https://merchant.example/orders")
    assert asyncio.run(orders.lookup_order("A2001"))["customer_name"] == ""


# an empty edge secret refuses to start -----------------------------------------
@pytest.mark.parametrize("secret", ["", "short-secret"])
def test_empty_or_short_edge_secret_refuses_to_start(secret):
    env = dict(os.environ, AIRLOCK_EDGE_SECRET=secret)
    p = subprocess.run(
        [sys.executable, "-c", "import airlock.app"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and "AIRLOCK_EDGE_SECRET" in (p.stderr + p.stdout)


# no second charge for a settled order; no capture without a conversation id
def test_settled_order_cannot_be_reopened():
    conv = "conv_settledtest01"
    A.sessions.settle(conv, "A1042", "approved")
    assert "already paid" in asyncio.run(A.open_capture(conv, "1042"))
    A.sessions.settle(conv, "A2001", "unknown")
    assert "still confirming" in asyncio.run(A.open_capture(conv, "a2001"))


def test_no_conversation_id_no_capture():
    assert "can't take a payment" in asyncio.run(A.open_capture("h_deadbeef", "A1042"))


# a dropped request must not strand the charge -----------------------------------
def test_charge_settles_even_if_the_request_is_cancelled(monkeypatch):
    async def slow_charge(cap):
        await asyncio.sleep(0.2)
        return {"status": "approved", "transaction_id": "12345678"}

    monkeypatch.setattr(A, "vault_charge", slow_charge)

    async def run():
        c = _at_confirm()
        c.conversation_id = "conv_cancelledtest1"
        A.sessions.put(c.conversation_id, c)

        async def consume():
            async for _ in A.capture_turn(c, "1"):
                pass

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        t.cancel()  # ElevenLabs gave up on the request mid-charge
        await asyncio.sleep(0.4)
        return c

    c = asyncio.run(run())
    assert c.step == script.Step.DONE
    assert A.sessions.settled(c.conversation_id).get("A1042") == "approved"
    assert any("approved" in n for n in A.sessions.handbacks(c.conversation_id))
