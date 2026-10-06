"""The cleaner and the tripwire.

* ``tripwire`` runs on every inbound body before it is parsed or logged. If a raw
  card-like number is present the vault edge has failed: the request is refused.
* ``clean_for_model`` runs on every message list Airlock forwards to a model. It
  drops keypad turns and placeholders, and refuses (raises) on any card-like number,
  so a model is never called with one.
* ``check_model_output`` runs on every model reply before it is spoken.

All three fail closed: on a hit they raise; nothing is "partially redacted".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import find_cardlike, is_keypad_entry

PLACEHOLDER = re.compile(r"\[\[airlock:[^\]]*\]\]")
TOKEN_ID = re.compile(
    r"\b(?:tok|token)_[A-Za-z0-9_-]{6,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
MASKED_TURN = "[keypad entry captured]"
SPOKEN = "[[airlock:spoken]]"
SPOKEN_NOTE = "[card number said aloud: removed for security]"


class CardDataDetected(Exception):
    """Raised when a raw card-like number is found where none may exist."""

    def __init__(self, where: str):
        super().__init__(f"card-like number detected in {where}")
        self.where = where


@dataclass(frozen=True)
class Placeholder:
    token: str
    length: int
    luhn: bool
    last4: str
    brand: str
    prior: bool = False
    expok: bool | None = None  # set by the edge for 4- or 6-digit entries: a valid future MMYY(YY)
    aba: bool | None = None  # set by the edge for 9-digit entries: ABA routing checksum passes


_KV = re.compile(r"(\w+)=([^\s\]]+)")


def parse_placeholder(text: str) -> Placeholder | None:
    m = PLACEHOLDER.search(text or "")
    if not m:
        return None
    body = m.group(0)
    kv = dict(_KV.findall(body))
    if "kp-prior" in body:
        return Placeholder(token="", length=0, luhn=False, last4="", brand="", prior=True)
    return Placeholder(
        token=kv.get("id", ""),
        length=int(kv.get("len", "0") or 0),
        luhn=kv.get("luhn") == "1",
        last4=kv.get("last4", ""),
        brand=kv.get("brand", ""),
        expok=None if "expok" not in kv else kv["expok"] == "1",
        aba=None if "aba" not in kv else kv["aba"] == "1",
    )


def tripwire(raw_body: str) -> None:
    if find_cardlike(raw_body or ""):
        raise CardDataDetected("inbound request")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def clean_for_model(messages: list[dict]) -> list[dict]:
    """Return a copy safe to send to any model. Keypad turns and placeholders become
    a fixed marker; any card-like number raises."""
    out: list[dict] = []
    for m in messages:
        text = _text_of(m.get("content"))
        if find_cardlike(text):
            raise CardDataDetected(f"{m.get('role')} message")
        if m.get("role") == "user" and SPOKEN in text:
            # A card number said aloud was cut out at the vault edge; keep the rest.
            rest = PLACEHOLDER.sub("", text.replace(SPOKEN, SPOKEN_NOTE)).strip()
            out.append({"role": "user", "content": rest or SPOKEN_NOTE})
            continue
        if m.get("role") == "user" and (is_keypad_entry(text) or PLACEHOLDER.search(text)):
            out.append({"role": "user", "content": MASKED_TURN})
            continue
        if PLACEHOLDER.search(text) or TOKEN_ID.search(text):
            text = TOKEN_ID.sub("[ref]", PLACEHOLDER.sub(MASKED_TURN, text))
            out.append({**m, "content": text})
            continue
        out.append(m)
    return out


def check_model_output(text: str) -> None:
    if find_cardlike(text or "") or PLACEHOLDER.search(text or "") or TOKEN_ID.search(text or ""):
        raise CardDataDetected("model output")
