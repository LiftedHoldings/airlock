"""Property tests for the card detector and the cleaner (invariant 3), and parity
between the Python detector and the JavaScript vault transform."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from airlock.core.cards import brand, find_cardlike, luhn_ok, pan_valid, words_to_digits
from airlock.core.sanitizer import (
    MASKED_TURN,
    CardDataDetected,
    check_model_output,
    clean_for_model,
    parse_placeholder,
    tripwire,
)

ROOT = Path(__file__).resolve().parents[1]
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def make_pan(prefix: str, length: int, rng: random.Random) -> str:
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(length - len(prefix) - 1))
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError


def variants(pan: str, rng: random.Random) -> list[str]:
    groups = [pan[i : i + 4] for i in range(0, len(pan), 4)]
    return [
        pan,
        " ".join(groups),
        "-".join(groups),
        f"my card is {' '.join(groups)} thanks",
        " ".join(WORDS[int(d)] for d in pan),
        f"it's {pan[:8]} {pan[8:]} ok",
    ]


@pytest.mark.parametrize("seed", range(40))
def test_generated_cards_are_always_caught(seed):
    rng = random.Random(seed)
    for prefix, length in [
        ("4", 16),
        ("51", 16),
        ("37", 15),
        ("6011", 16),
        ("4", 13),
        ("4", 19),
    ]:
        pan = make_pan(prefix, length, rng)
        for v in variants(pan, rng):
            assert pan in find_cardlike(v), (v, pan)
            with pytest.raises(CardDataDetected):
                tripwire(json.dumps({"messages": [{"role": "user", "content": v}]}))
            with pytest.raises(CardDataDetected):
                clean_for_model([{"role": "user", "content": v}])
            with pytest.raises(CardDataDetected):
                check_model_output(v)


@pytest.mark.parametrize(
    "benign",
    [
        "1791292673.7754595",
        '{"t": 1791292598.824, "ms": 1234}',
        "call me at 678-953-8315",
        "order A1042 for 84.20",
        "conv_5101m48mvwzkfbrrs31kk8nxer2s",
        "the zip is 37306 and the date 2026-10-06",
        "I want to pay for two coffees",
    ],
)
def test_benign_text_is_not_flagged(benign):
    assert find_cardlike(benign) == []
    tripwire(benign)


def test_words_to_digits_handles_double_and_triple():
    assert words_to_digits("four one double one triple two") == "4111222"


def test_brand_and_validity():
    assert brand("4111111111111111") == "visa" and pan_valid("4111111111111111")
    assert brand("378282246310005") == "amex" and pan_valid("378282246310005")
    assert not pan_valid("4111111111111112")


def test_cleaner_masks_keypad_and_placeholders_and_keeps_conversation():
    msgs = [
        {
            "role": "system",
            "content": "You are Ava. Conversation id: conv_abc12345678.",
        },
        {"role": "user", "content": "I want to pay order A1042"},
        {"role": "user", "content": "[[airlock:kp-prior]]"},
        {"role": "user", "content": "1229#"},
        {"role": "assistant", "content": "Thanks."},
    ]
    out = clean_for_model(msgs)
    assert out[1]["content"] == "I want to pay order A1042"
    assert out[2]["content"] == MASKED_TURN and out[3]["content"] == MASKED_TURN
    assert out[4]["content"] == "Thanks."


def test_model_output_with_placeholder_or_token_id_is_refused():
    for bad in [
        "your token is 7c2d1d0e-3f2a-4b1e-9c3f-0a1b2c3d4e5f",
        "[[airlock:kp id=x len=16]]",
    ]:
        with pytest.raises(CardDataDetected):
            check_model_output(bad)


def test_placeholder_parse():
    p = parse_placeholder(
        "[[airlock:kp id=7c2d1d0e-3f2a-4b1e-9c3f-0a1b2c3d4e5f len=4 luhn=0 expok=1]]"
    )
    assert p.length == 4 and p.expok is True and not p.luhn


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_python_and_javascript_detectors_agree():
    rng = random.Random(7)
    samples = []
    for _ in range(200):
        pan = make_pan(rng.choice(["4", "51", "37", "6011"]), rng.choice([15, 16]), rng)
        samples += variants(pan, rng)[:4]
    samples += [
        "1791292673.7754595",
        "order A1042",
        "4111111111111112",
        "call 678-953-8315",
    ]
    js = (
        "const h=require(process.argv[1]).helpers;"
        "const xs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(xs.map(x=>h.hasCardlike(x))));"
    )
    out = subprocess.run(
        ["node", "-e", js, str(ROOT / "vaults" / "basis_theory" / "inbound.js")],
        input=json.dumps(samples),
        capture_output=True,
        text=True,
        check=True,
    )
    js_says = json.loads(out.stdout)
    py_says = [bool(find_cardlike(s)) for s in samples]
    assert js_says == py_says
