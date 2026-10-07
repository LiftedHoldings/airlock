"""Capture conversation: every branch the caller can take."""

import pytest

from airlock.core import script
from airlock.core.script import Capture, Step


def ph(
    n,
    luhn=1,
    last4="1111",
    brand="visa",
    expok=None,
    tok="11111111-2222-3333-4444-555555555555",
):
    extra = f" last4={last4} brand={brand}" if n >= 13 and luhn else ""
    e = f" expok={expok}" if expok is not None else ""
    return f"[[airlock:kp id={tok} len={n} luhn={luhn}{extra}{e}]]"


def cap(**kw):
    return Capture(conversation_id="conv_test0001", order_id="A1042", amount="84.20", **kw)


def walk_to(c, step):
    seq = [ph(16), ph(4, luhn=0, expok=1), ph(3, luhn=0), ph(5, luhn=0)]
    i = 0
    while c.step != step:
        script.handle(c, seq[i])
        i += 1


def test_opening_says_total_and_asks_for_keypad():
    s = script.opening(cap())
    assert "84 dollars and 20 cents" in s and "keypad" in s and "pound" in s


def test_happy_path_reaches_confirm_then_charges():
    c = cap()
    r = script.handle(c, ph(16))
    assert "Visa ending in 1 1 1 1" in r.say and c.step == Step.EXP
    script.handle(c, ph(4, luhn=0, expok=1))
    assert c.step == Step.CVV
    script.handle(c, ph(3, luhn=0))
    assert c.step == Step.ZIP
    script.handle(c, ph(5, luhn=0))
    assert c.step == Step.SAVE
    script.handle(c, "2")
    assert c.step == Step.CONFIRM and c.save_card is False
    r = script.handle(c, "1")
    assert r.charge and r.filler and c.step == Step.CHARGING
    r = script.after_charge(c, {"status": "approved", "transaction_id": "9876544471"})
    assert r.finished and "4 4 7 1" in r.say and "approved" in r.handback
    assert "1111" in r.handback and len(r.delete_tokens) == 4


def test_handback_never_contains_digits_beyond_last4_and_ref():
    c = cap()
    walk_to(c, Step.SAVE)
    script.handle(c, "yes")
    script.handle(c, "1")
    r = script.after_charge(
        c, {"status": "approved", "transaction_id": "40000123", "vault_id": "555"}
    )
    import re

    assert not re.search(r"\d{5,}", r.handback)


@pytest.mark.parametrize(
    "entry,reason",
    [
        (ph(16, luhn=0), "luhn"),
        (ph(15, brand="visa"), "length"),
        (ph(12, luhn=1), "short"),
    ],
)
def test_bad_pan_retries_then_cancels_on_third(entry, reason):
    c = cap()
    r1 = script.handle(c, entry)
    assert "didn't go through" in r1.say and c.step == Step.PAN
    script.handle(c, entry)
    r3 = script.handle(c, entry)
    assert r3.finished and c.step == Step.CANCELLED and "payment link" in r3.say


def test_expired_or_malformed_expiry_reasks():
    c = cap()
    script.handle(c, ph(16))
    r = script.handle(c, ph(4, luhn=0, expok=0))
    assert "doesn't look right" in r.say and c.step == Step.EXP
    r = script.handle(c, ph(3, luhn=0))
    assert c.step == Step.EXP


def test_amex_needs_four_digit_code():
    c = cap()
    script.handle(c, ph(15, last4="0005", brand="amex"))
    r = script.handle(c, ph(4, luhn=0, expok=1))
    assert "4-digit security code on the front" in r.say
    r = script.handle(c, ph(3, luhn=0))
    assert "should be 4 digits" in r.say


def test_star_restarts_current_field():
    c = cap()
    script.handle(c, ph(16))
    r = script.handle(c, "*")
    assert "again" in r.say and c.step == Step.EXP


@pytest.mark.parametrize(
    "utterance,expect",
    [
        ("can you repeat that", "type your card number"),
        ("hold on let me grab my wallet", "take your time"),
        ("how much is it again", "84 dollars"),
        ("is this safe?", "never heard, stored, or seen"),
        ("which card did I use", "haven't got a card number"),
        ("where's the security code", "digit code"),
        ("I already typed it", "didn't receive any digits"),
        ("what's the weather like", "once we're done"),
    ],
)
def test_spoken_intents_keep_caller_on_track(utterance, expect):
    c = cap()
    r = script.handle(c, utterance)
    assert expect.lower() in r.say.lower() and not r.finished


@pytest.mark.parametrize(
    "utterance,why",
    [
        ("let me talk to a real person", "person"),
        ("cancel, I don't want to do this", "cancelled"),
        ("can you just text me a link", "link"),
        ("I'm on a computer, I have no keypad", "no keypad"),
    ],
)
def test_exits_hand_back_without_charge(utterance, why):
    c = cap()
    script.handle(c, ph(16))
    r = script.handle(c, utterance)
    assert r.finished and "No charge" in r.handback and why in r.handback and r.delete_tokens


def test_spoken_card_number_is_refused():
    c = cap()
    r = script.handle(c, "[[airlock:spoken]]")
    assert "didn't keep that" in r.say and c.step == Step.PAN


def test_silence_twice_then_cancel():
    c = cap()
    assert "Take your time" in script.handle(c, "...").say
    assert "Take your time" in script.handle(c, "").say
    r = script.handle(c, "...")
    assert r.finished and c.step == Step.CANCELLED


def test_decline_offers_second_card_then_stops():
    c = cap()
    walk_to(c, Step.SAVE)
    script.handle(c, "2")
    script.handle(c, "1")
    r = script.after_charge(c, {"status": "declined"})
    assert "declined" in r.say and c.step == Step.PAN and not r.finished
    walk_to(c, Step.SAVE)
    script.handle(c, "2")
    script.handle(c, "1")
    r = script.after_charge(c, {"status": "declined"})
    assert r.finished and c.step == Step.CANCELLED


def test_unknown_outcome_never_retries():
    c = cap()
    walk_to(c, Step.SAVE)
    script.handle(c, "2")
    script.handle(c, "1")
    r = script.after_charge(c, {"status": "unknown"})
    assert r.finished and "Do not retry" in r.handback


def test_change_card_at_confirm():
    c = cap()
    walk_to(c, Step.SAVE)
    script.handle(c, "no")
    r = script.handle(c, "2")
    assert c.step == Step.PAN and c.pan_token == "" and len(r.delete_tokens) == 4


def test_deterministic_replay():
    turns = [
        ph(16),
        "how much",
        ph(4, luhn=0, expok=1),
        "*",
        ph(4, luhn=0, expok=1),
        ph(3, luhn=0),
        ph(5, luhn=0),
        "1",
        "1",
    ]
    outs = []
    for _ in range(2):
        c = cap()
        outs.append([script.handle(c, t).say for t in turns])
    assert outs[0] == outs[1]


import pytest
from airlock.core.script import _money


@pytest.mark.parametrize(
    ("amount", "spoken"),
    [
        ("1.00", "1 dollar"),
        ("1.01", "1 dollar and 1 cent"),
        ("0.50", "50 cents"),
        ("12.01", "12 dollars and 1 cent"),
        ("12.00", "12 dollars"),
        ("0.01", "1 cent"),
    ],
)
def test_money_pluralization(amount: str, spoken: str) -> None:
    assert _money(amount) == spoken
