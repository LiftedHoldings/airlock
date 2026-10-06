"""eCheck (ACH) capture: routing + account by keypad, account type, NACHA TEL authorization."""

from airlock.core import script
from airlock.core.script import Capture, Step

ROUTING_OK = "[[airlock:kp id=11111111-2222-3333-4444-555555555555 len=9 luhn=0 aba=1 last4=3123]]"
ROUTING_BAD = "[[airlock:kp id=11111111-2222-3333-4444-555555555555 len=9 luhn=0 aba=0]]"
ACCOUNT = "[[airlock:kp id=21111111-2222-3333-4444-555555555555 len=10 luhn=0 last4=6789]]"


def chk(**kw):
    return Capture(
        conversation_id="conv_test0002",
        order_id="A2001",
        amount="12.00",
        method="check",
        holder_name="Test Payer",
        **kw,
    )


def test_echeck_happy_path_with_nacha_authorization():
    c = chk()
    assert c.step == Step.ROUTING and "account numbers" in script.opening(c)
    script.handle(c, ROUTING_OK)
    assert c.step == Step.ACCOUNT
    r = script.handle(c, ACCOUNT)
    assert c.step == Step.ACCT_TYPE and "ending in 6 7 8 9" in r.say
    script.handle(c, "checking please")
    assert c.step == Step.SAVE and c.account_type == "checking"
    r = script.handle(c, "2")
    assert c.step == Step.CONFIRM
    assert "one-time electronic debit" in r.say and "revoke" in r.say
    assert "checking account ending in 6 7 8 9" in r.say
    r = script.handle(c, "1")
    assert r.charge
    r = script.after_charge(c, {"status": "approved", "transaction_id": "1234567"})
    assert r.finished and "checking account ending 6789" in r.handback
    assert len(r.delete_tokens) == 2


def test_echeck_bad_routing_reasks_then_cancels():
    c = chk()
    r = script.handle(c, ROUTING_BAD)
    assert "routing number didn't check out" in r.say and c.step == Step.ROUTING
    script.handle(c, ROUTING_BAD)
    r = script.handle(c, ROUTING_BAD)
    assert r.finished and c.step == Step.CANCELLED


def test_echeck_account_type_by_keypad():
    c = chk()
    script.handle(c, ROUTING_OK)
    script.handle(c, ACCOUNT)
    script.handle(c, "2")
    assert c.account_type == "savings" and c.step == Step.SAVE


def test_echeck_change_account_restarts_at_routing():
    c = chk()
    script.handle(c, ROUTING_OK)
    script.handle(c, ACCOUNT)
    script.handle(c, "1")
    script.handle(c, "2")
    r = script.handle(c, "2")  # at CONFIRM: change the account
    assert c.step == Step.ROUTING and c.routing_token == "" and len(r.delete_tokens) == 2


def test_blank_merchant_name_falls_back_to_us():
    import os
    import subprocess
    import sys

    code = "from airlock.core import script; print(script.MERCHANT_NAME)"
    env = {**os.environ, "AIRLOCK_MERCHANT_NAME": "  "}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "us"


def test_echeck_error_does_not_call_it_a_card():
    c = chk()
    c.step = Step.CHARGING
    reply = script.after_charge(c, {"status": "error"})
    assert "card" not in reply.say.split(".")[0] and "payment" in reply.say
