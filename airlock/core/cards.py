"""Card-number primitives shared by the vault edge, the cleaner and the tests.

Pure functions, no I/O. Nothing here logs or stores a number; callers decide what
to keep (only last four and brand ever leave the vault edge).
"""

from __future__ import annotations

import re
import unicodedata

KEYPAD_CHARS = set("0123456789*#")


def ascii_digits(text: str) -> str:
    """Map every Unicode decimal digit (full-width, Arabic-Indic, Devanagari, ...) to ASCII
    so no detector can be bypassed by typing a card number in another digit script."""
    if not text or text.isascii():
        return text or ""
    return "".join(
        str(unicodedata.decimal(ch)) if not ch.isascii() and ch.isdecimal() else ch for ch in text
    )


_WORD_DIGITS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "nought": "0",
    "one": "1",
    "won": "1",
    "two": "2",
    "to": "2",
    "too": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
    "niner": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}

# A run of 13-19 digits, optionally broken by short separators: spaces, commas, slashes,
# dashes ("4111, 1111, 1111, 1111" from speech-to-text), or a dot followed by a space
# ("4111. 1111"). A dot directly between digits is NOT a separator, so decimals and
# timestamps ("1791292673.7754595") never read as cards.
_SEP = r"(?:[ ,/\-]{1,3}|\.[ ]{1,2})"
_CARDLIKE = re.compile(rf"(?<![\d.])(?:\d{_SEP}?){{12,18}}\d(?![\d.]\d)")


def digits_only(s: str) -> str:
    return "".join(ch for ch in ascii_digits(s) if "0" <= ch <= "9")


def luhn_ok(number: str) -> bool:
    d = digits_only(number)
    if not d:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def brand(number: str) -> str:
    """Best-effort brand from the leading digits (IIN ranges)."""
    d = digits_only(number)
    if d.startswith(("34", "37")):
        return "amex"
    if d.startswith("4"):
        return "visa"
    if d[:2].isdigit() and 51 <= int(d[:2] or 0) <= 55:
        return "mastercard"
    if d[:4].isdigit() and 2221 <= int(d[:4] or 0) <= 2720:
        return "mastercard"
    if d.startswith(("6011", "65")) or (d[:3].isdigit() and 644 <= int(d[:3]) <= 649):
        return "discover"
    if d[:4].isdigit() and 3528 <= int(d[:4]) <= 3589:
        return "jcb"
    if d.startswith(("36", "300", "301", "302", "303", "304", "305", "38")):
        return "diners"
    return "unknown"


def expected_lengths(card_brand: str) -> tuple[int, ...]:
    return {
        "amex": (15,),
        "visa": (13, 16, 19),
        "mastercard": (16,),
        "discover": (16, 19),
        "jcb": (16, 17, 18, 19),
        "diners": (14, 16),
    }.get(card_brand, (13, 14, 15, 16, 17, 18, 19))


def cvv_length(card_brand: str) -> int:
    return 4 if card_brand == "amex" else 3


def pan_valid(number: str) -> bool:
    d = digits_only(number)
    return 13 <= len(d) <= 19 and luhn_ok(d) and len(d) in expected_lengths(brand(d))


def is_keypad_entry(text: str, min_len: int = 3) -> bool:
    """A user turn made only of keypad characters (digits, * and #), ignoring spaces."""
    t = ascii_digits(text or "").replace(" ", "").strip()
    return len(t) >= min_len and all(ch in KEYPAD_CHARS for ch in t)


def words_to_digits(text: str) -> str:
    """Turn spoken digit words into digits: 'four one double one' -> '4111'.
    Non-digit words are kept so callers can still find runs inside prose."""
    out: list[str] = []
    pending = 1
    # Split on whitespace and punctuation, including JSON quotes/brackets, so a spoken
    # number at the edge of a JSON string ('"four one ... one"') is still converted.
    for raw in re.split(r"(\s+|[,.;:!?\-\"'(){}\[\]])", text or ""):
        w = raw.lower().strip()
        if not w:
            out.append(raw)
            continue
        if w in _MULTIPLIERS:
            pending = _MULTIPLIERS[w]
            continue
        if w in _WORD_DIGITS:
            out.append(_WORD_DIGITS[w] * pending)
            pending = 1
            continue
        if w.isdigit():
            out.append(w[0] * (pending - 1) + w)
            pending = 1
            continue
        pending = 1
        out.append(raw)
    # Join digit tokens that were separated only by whitespace.
    joined = "".join(out)
    return re.sub(r"(?<=\d)\s+(?=\d)", "", joined)


def find_cardlike(text: str, require_luhn: bool = True) -> list[str]:
    """Card-like runs in free text (13-19 digits, separators allowed), also after
    converting spoken digit words. Returns the digit strings."""
    found: list[str] = []
    text = ascii_digits(text or "")
    for candidate_text in (text, words_to_digits(text)):
        for m in _CARDLIKE.finditer(candidate_text):
            d = digits_only(m.group(0))
            if 13 <= len(d) <= 19 and (not require_luhn or luhn_ok(d)) and d not in found:
                found.append(d)
    return found


def parse_expiry(entry: str, today_year: int, today_month: int) -> tuple[int, int] | None:
    """MMYY or MMYYYY keypad entry -> (month, 4-digit year), or None if malformed or past."""
    d = digits_only(entry)
    if len(d) == 4:
        mm, yy = int(d[:2]), 2000 + int(d[2:])
    elif len(d) == 6:
        mm, yy = int(d[:2]), int(d[2:])
    else:
        return None
    if not 1 <= mm <= 12:
        return None
    if (yy, mm) < (today_year, today_month) or yy > today_year + 20:
        return None
    return mm, yy
