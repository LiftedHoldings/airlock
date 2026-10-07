"""The capture conversation: a deterministic state machine.

While a capture is open the model is not called. Every caller turn is classified
(keypad placeholder, keypad menu digit, spoken intent, silence, or something else)
and answered from a fixed script. The same inputs always give the same outputs.

The machine never sees card digits. It sees placeholders minted by the vault edge:
    [[airlock:kp id=<token> len=16 luhn=1 last4=4242 brand=visa expok=0]]
    [[airlock:spoken]]          (the caller read a card number aloud; discarded)
    [[airlock:kp-prior]]        (an earlier keypad turn, already handled)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum

from .cards import cvv_length, expected_lengths
from .sanitizer import parse_placeholder


class Step(str, Enum):
    PAN = "pan"
    ROUTING = "routing"
    ACCOUNT = "account"
    ACCT_TYPE = "acct_type"
    NAME = "name"
    EXP = "exp"
    CVV = "cvv"
    ZIP = "zip"
    SAVE = "save"
    CONFIRM = "confirm"
    CHARGING = "charging"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Capture:
    conversation_id: str
    order_id: str
    amount: str  # "84.20", from the merchant system, never from the model
    currency: str = "USD"
    method: str = "card"  # "card" or "check" (eCheck / ACH)
    step: Step = Step.PAN
    pan_token: str = ""
    exp_token: str = ""
    cvv_token: str = ""
    zip_token: str = ""
    brand: str = ""
    last4: str = ""
    save_card: bool = False
    routing_token: str = ""
    account_token: str = ""
    account_type: str = ""  # checking | savings
    holder_name: str = ""
    tries: dict = field(
        default_factory=lambda: {
            "pan": 0,
            "exp": 0,
            "cvv": 0,
            "zip": 0,
            "routing": 0,
            "account": 0,
        }
    )
    silences: int = 0
    cards_tried: int = 0
    offer_save: bool = True
    ask_zip: bool = True
    result: dict = field(default_factory=dict)
    charge_started: float = 0.0  # epoch seconds when the charge task began
    ask_name: bool = False  # payment-desk eCheck: no model to ask, so the script asks first

    def tokens(self) -> list[str]:
        return [
            t
            for t in (
                self.pan_token,
                self.exp_token,
                self.cvv_token,
                self.zip_token,
                self.routing_token,
                self.account_token,
            )
            if t
        ]

    def __post_init__(self):
        if self.method == "check" and self.step == Step.PAN:
            self.step = Step.NAME if self.ask_name else Step.ROUTING

    def payment_label(self, spoken: bool = True) -> str:
        """'Visa ending in 1 1 1 1' for speech; 'Visa ending 1111' for the model hand-back."""
        if not self.last4:
            # Short account numbers (4-7 digits) carry no last four from the edge.
            # Reads naturally after "your": "...debit from your bank account you just entered".
            return (
                "bank account you just entered"
                if self.method == "check"
                else "card you just entered"
            )
        tail = f"ending in {_spell(self.last4)}" if spoken else f"ending {self.last4}"
        if self.method == "check":
            return f"{self.account_type or 'bank'} account {tail}"
        return f"{_brand_name(self.brand)} {tail}"


@dataclass
class Reply:
    say: str
    charge: bool = False  # caller confirmed: run the charge, then call after_charge()
    finished: bool = False  # capture is over; hand control back to the model
    handback: str = ""  # the only text the model will ever see about this payment
    delete_tokens: list[str] = field(default_factory=list)
    filler: str = ""  # spoken immediately, before a slow step (latency)
    settled: bool = False  # bookkeeping (finish/settle) already done by the charge task


# Spoken in the eCheck (NACHA TEL) authorization: the business the caller is paying.
MERCHANT_NAME = os.environ.get("AIRLOCK_MERCHANT_NAME", "").strip() or "us"

MAX_TRIES = 3
MAX_SILENCES = 2
MAX_CARDS = 2


def _money(amount: str) -> str:
    dollars, _, cents = amount.partition(".")
    cents = (cents + "00")[:2]
    d = int(dollars or 0)
    c = int(cents)
    dollar_word = "dollar" if d == 1 else "dollars"
    cent_word = "cent" if c == 1 else "cents"
    if c == 0:
        return f"{d} {dollar_word}"
    if d == 0:
        return f"{c} {cent_word}"
    return f"{d} {dollar_word} and {c} {cent_word}"


def _brand_name(b: str) -> str:
    return {
        "visa": "Visa",
        "mastercard": "Mastercard",
        "amex": "American Express",
        "discover": "Discover",
        "jcb": "JCB",
        "diners": "Diners Club",
    }.get(b, "card")


def _spell(last4: str) -> str:
    return " ".join(last4)


# ---- spoken-intent classification ------------------------------------------------

_INTENTS: list[tuple[str, re.Pattern]] = [
    (
        "human",
        re.compile(
            r"\b(human|real person|agent|representative|operator|someone else|manager|supervisor)\b",
            re.I,
        ),
    ),
    (
        "cancel",
        re.compile(
            r"\b(cancel|stop|never ?mind|forget it|don'?t want to (pay|do this)|not now|hang ?up|quit|exit)\b",
            re.I,
        ),
    ),
    (
        "link",
        re.compile(
            r"\b(link|text me|email me|send (me )?(a|the) (link|invoice)|pay online|website)\b",
            re.I,
        ),
    ),
    (
        "other_card",
        re.compile(
            r"\b(different|another|other|new) (card|one)\b|\buse (my|a) (other|different)\b|\b(try|use) another\b",
            re.I,
        ),
    ),
    (
        "restart",
        re.compile(
            r"\b(start (over|again)|restart|from the (top|beginning)|redo|messed up|made a mistake|wrong (number|digits?)|typo)\b",
            re.I,
        ),
    ),
    (
        "repeat",
        re.compile(
            r"\b(repeat|say (that|it) again|come again|pardon|what\??$|sorry\??$|didn'?t (catch|hear|get) (that|you)|one more time)\b",
            re.I,
        ),
    ),
    (
        "wait",
        re.compile(
            r"\b(wait|hold on|hang on|one (sec|second|moment|minute)|give me a (sec|second|moment|minute)|just a (sec|second|moment|minute)|getting (it|my (card|wallet))|let me (find|get|grab))\b",
            re.I,
        ),
    ),
    (
        "amount",
        re.compile(r"\b(how much|total|amount|what do i owe|price|cost|balance)\b", re.I),
    ),
    (
        "safety",
        re.compile(
            r"\b(safe|secure|security(?! code)|scam|trust|why (do you need|the keypad|type)|who (sees|can see)|record(ed|ing)?|privacy)\b",
            re.I,
        ),
    ),
    (
        "which_card",
        re.compile(
            r"\b(which card|what card|last (four|4)|ending in|what did i (type|enter))\b",
            re.I,
        ),
    ),
    (
        "where_cvv",
        re.compile(
            r"\b(where is|where'?s|what is|what'?s|find) (the )?(security code|cvv|cvc|csc|code)\b|\bback of the card\b",
            re.I,
        ),
    ),
    (
        "no_keypad",
        re.compile(
            r"\b(no (keypad|keys|buttons)|can'?t (type|press|use the keypad)|on (a|my) (computer|headset|speaker)|rotary|no dial ?pad)\b",
            re.I,
        ),
    ),
    (
        "done_typing",
        re.compile(
            r"\b(i (did|already|just) (type|typed|enter|entered|press|pressed)|done|finished|i pressed pound)\b",
            re.I,
        ),
    ),
    (
        "yes",
        re.compile(
            r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|correct|right|please do|go ahead|confirm|that'?s (right|correct)|absolutely|of course)\b",
            re.I,
        ),
    ),
    (
        "no",
        re.compile(r"^\s*(no|nope|nah|don'?t|do not|no thanks|not (really|this time))\b", re.I),
    ),
]


STRICT_YES = re.compile(
    r"(yes|yeah|yep|yup|correct|confirm|confirmed|go ahead|please do|do it|authorize|authorise|"
    r"yes please|yes go ahead|that'?s (right|correct)|pay it|charge it)[\s.!,]*(please|thanks|thank you)?[\s.!]*",
    re.I,
)
NEGATION = re.compile(
    r"\b(no|not|don'?t|wrong|instead|actually|other|different|wait|hold|stop|cancel)\b",
    re.I,
)


def classify(text: str) -> str:
    t = (text or "").strip()
    if not t or t in {"...", "…"} or re.fullmatch(r"[\s.…,-]*", t):
        return "silence"
    for name, rx in _INTENTS:
        if rx.search(t):
            return name
    return "other"


# ---- prompts --------------------------------------------------------------------


def _ask(c: Capture) -> str:
    if c.step == Step.PAN:
        return "Please type your card number on your phone's keypad, then press the pound key."
    if c.step == Step.EXP:
        return "Now type the expiration date as four digits, month then year, then press pound."
    if c.step == Step.CVV:
        n = cvv_length(c.brand)
        where = "on the front of the card" if n == 4 else "on the back of the card"
        return f"And the {n}-digit security code {where}, then pound."
    if c.step == Step.ZIP:
        return "Last one: type the billing ZIP code, then pound."
    if c.step == Step.ROUTING:
        return "Please type your bank's nine-digit routing number from the bottom left of a check, then press pound."
    if c.step == Step.ACCOUNT:
        return "Now type your account number, then press pound."
    if c.step == Step.ACCT_TYPE:
        return "Is that a checking or a savings account? Press 1 for checking, or 2 for savings."
    if c.step == Step.NAME:
        return "First, please say the name on the bank account."
    if c.step == Step.SAVE:
        what = "this account" if c.method == "check" else "this card"
        return f"Would you like us to save {what} for next time? Press 1 for yes, or 2 for no."
    if c.step == Step.CONFIRM:
        if c.method == "check":
            # NACHA TEL: the authorization must state amount, date, account and how to revoke.
            return (
                f"To authorize: you're allowing {MERCHANT_NAME} to make a one-time electronic debit of "
                f"{_money(c.amount)} from your {c.payment_label()}, today. "
                "You can revoke this by calling us before it settles. Press 1 to authorize, or 2 to change the account."
            )
        return (
            f"To confirm: {_money(c.amount)} on your {c.payment_label()}. "
            "Press 1 to pay, or 2 to change the card."
        )
    return ""


def opening(c: Capture) -> str:
    what = "account numbers" if c.method == "check" else "card number"
    return (
        f"Your total is {_money(c.amount)}. For your security, please don't say your {what} out loud. "
        + _ask(c)
    )


def _field_key(step: Step) -> str:
    return {
        Step.PAN: "pan",
        Step.EXP: "exp",
        Step.CVV: "cvv",
        Step.ZIP: "zip",
        Step.ROUTING: "routing",
        Step.ACCOUNT: "account",
    }.get(step, "")


def _cancel(c: Capture, why: str, say: str) -> Reply:
    c.step = Step.CANCELLED
    return Reply(
        say=say,
        finished=True,
        handback=f"start_payment result: cancelled ({why}). No charge was made.",
        delete_tokens=c.tokens(),
    )


def _fail_field(c: Capture, retry_line: str) -> Reply:
    key = _field_key(c.step)
    c.tries[key] += 1
    if c.tries[key] >= MAX_TRIES:
        return _cancel(
            c,
            f"too many invalid {key} entries",
            "I'm sorry, I wasn't able to take that card. I can send you a secure payment link instead, or connect you with someone. Which would you prefer?",
        )
    return Reply(say=f"{retry_line} {_ask(c)}")


def _restart_field(c: Capture) -> Reply:
    if c.step == Step.PAN:
        c.pan_token = ""
    elif c.step == Step.EXP:
        c.exp_token = ""
    elif c.step == Step.CVV:
        c.cvv_token = ""
    elif c.step == Step.ZIP:
        c.zip_token = ""
    elif c.step == Step.ROUTING:
        c.routing_token = ""
    elif c.step == Step.ACCOUNT:
        c.account_token = ""
    return Reply(say=f"No problem, let's do that one again. {_ask(c)}")


def _advance(c: Capture) -> None:
    if c.method == "check":
        # The account holder name comes from the merchant's order record, not the caller.
        order = ([Step.NAME] if c.ask_name else []) + [
            Step.ROUTING,
            Step.ACCOUNT,
            Step.ACCT_TYPE,
        ]
    else:
        order = [Step.PAN, Step.EXP, Step.CVV]
    if c.ask_zip and c.method != "check":
        order.append(Step.ZIP)
    if c.offer_save:
        order.append(Step.SAVE)
    order.append(Step.CONFIRM)
    i = order.index(c.step)
    c.step = order[i + 1]


# ---- the machine ------------------------------------------------------------------


def handle(c: Capture, user_text: str) -> Reply:
    """One caller turn while the capture is open."""
    text = user_text or ""
    ph = parse_placeholder(text)

    if c.step in (Step.DONE, Step.CANCELLED):
        return Reply(say="", finished=True, handback="start_payment result: already finished.")
    if c.step == Step.CHARGING:
        return Reply(say="Still working on that, just a moment.")

    # The caller read a card number aloud. The vault edge already discarded it.
    if "[[airlock:spoken" in text:
        return Reply(
            say="For your security I didn't keep that. Please type the numbers on your keypad instead. "
            + _ask(c)
        )

    if "[[airlock:not-test-card]]" in text:
        return Reply(
            say="This is a demo, so it only accepts the test card numbers shown on the page. Please never use a real card here. "
            + _ask(c)
        )

    # Keypad entries of three or more digits arrive as placeholders.
    if ph and not ph.prior:
        c.silences = 0
        return _keypad_field(c, ph)

    stripped = text.replace(" ", "")
    # Short keypad entries (menu choices, star) pass through the vault untouched.
    if stripped in ("*", "**", "*#"):
        return _restart_field(c)
    if stripped in ("1", "1#", "2", "2#") and c.step in (
        Step.SAVE,
        Step.CONFIRM,
        Step.ACCT_TYPE,
    ):
        return _menu(c, stripped[0])
    if stripped in ("0", "0#"):
        return _cancel(
            c,
            "caller asked for a person",
            "Okay, let me get you to someone who can help.",
        )
    if stripped.rstrip("#").isdigit() and len(stripped.rstrip("#")) <= 2:
        return Reply(say=f"Sorry, that was too short. {_ask(c)}")
    if stripped == "#":
        return Reply(say=f"I didn't get any digits there. {_ask(c)}")

    intent = classify(text)
    if intent == "silence":
        c.silences += 1
        if c.silences > MAX_SILENCES:
            return _cancel(
                c,
                "no input",
                "I haven't heard anything, so I'll stop here. No payment was taken.",
            )
        return Reply(say=f"Take your time. {_ask(c)}")
    c.silences = 0

    if c.step == Step.ACCT_TYPE and re.search(r"\b(checking|savings)\b", text, re.I):
        return _menu(c, "1" if re.search(r"checking", text, re.I) else "2")
    if c.step == Step.NAME and intent in ("other", "yes"):
        name = re.sub(
            r"(?i)^(it'?s|my name is|the name is|name on (it|the account) is)\s+",
            "",
            text,
        ).strip(" .")
        if re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{1,59}", name):
            c.holder_name = name
            _advance(c)
            return Reply(say=f"Thanks, {name.split()[0].title()}. {_ask(c)}")
        return Reply(say=f"Sorry, I didn't catch that. {_ask(c)}")

    if c.step == Step.CONFIRM and intent in ("yes", "no"):
        # Money moves on this answer: only a clean, whole-utterance yes counts. "Yeah, no,
        # that's not my card" or "Sure, actually use my other card" must not charge.
        if intent == "yes" and STRICT_YES.fullmatch(text.strip()):
            return _menu(c, "1")
        if intent == "no" or NEGATION.search(text):
            return _menu(c, "2")
        return Reply(say=f"Sorry, just to be sure. {_ask(c)}")
    if intent in ("yes", "no") and c.step == Step.SAVE:
        return _menu(c, "1" if intent == "yes" else "2")
    if intent == "yes":
        return Reply(say=f"Great. {_ask(c)}")
    if intent == "no" and c.cards_tried and c.step == Step.PAN and not c.pan_token:
        return _cancel(
            c,
            "caller declined to try another card",
            "No problem. I can send you a secure payment link instead, or connect you with someone.",
        )
    if intent == "human":
        return _cancel(
            c,
            "caller asked for a person",
            "Of course. Let me connect you with someone.",
        )
    if intent == "cancel":
        return _cancel(
            c,
            "caller cancelled",
            "No problem, I've cancelled that and nothing was charged.",
        )
    if intent == "link":
        return _cancel(
            c,
            "caller prefers a payment link",
            "Sure, I can send you a secure payment link instead.",
        )
    if intent == "other_card":
        if c.step == Step.PAN and not c.pan_token:
            return Reply(say=f"Sure. {_ask(c)}")
        return _new_card(c, "Sure, let's use a different card.")
    if intent == "restart":
        return _restart_field(c)
    if intent == "repeat":
        return Reply(say=_ask(c))
    if intent == "wait":
        if c.step in (Step.CONFIRM, Step.SAVE, Step.ACCT_TYPE):
            return Reply(say=f"Sure, take your time. {_ask(c)}")
        return Reply(
            say="Sure, take your time. Type it in whenever you're ready, then press pound."
        )
    if intent == "amount":
        return Reply(say=f"The total is {_money(c.amount)}. {_ask(c)}")
    if intent == "safety":
        return Reply(
            say=(
                "Good question. When you type on the keypad, the numbers go to our certified payment vault "
                "and are never heard, stored, or seen by the assistant. " + _ask(c)
            )
        )
    if intent == "which_card":
        if c.last4:
            return Reply(
                say=f"You're using your {_brand_name(c.brand)} ending in {_spell(c.last4)}. {_ask(c)}"
            )
        return Reply(say=f"We haven't got a card number yet. {_ask(c)}")
    if intent == "where_cvv":
        n = cvv_length(c.brand)
        where = (
            "printed on the front, above the card number"
            if n == 4
            else "printed on the back, near the signature strip"
        )
        return Reply(say=f"It's the {n}-digit code {where}. {_ask(c)}")
    if intent == "no_keypad":
        return _cancel(
            c,
            "caller has no keypad",
            "No problem. Since you can't use a keypad, I'll send you a secure payment link instead.",
        )
    if intent == "done_typing":
        return Reply(
            say="I didn't receive any digits that time. Please type them again and press pound at the end. "
            + _ask(c)
        )
    # Anything else: answer briefly and keep the caller on track.
    return Reply(say=f"I can help with that once we're done here. {_ask(c)}")


def _keypad_field(c: Capture, ph) -> Reply:
    if c.step == Step.PAN:
        if not ph.luhn or ph.length not in expected_lengths(ph.brand or "unknown"):
            return _fail_field(c, "That number didn't go through.")
        c.pan_token, c.brand, c.last4 = ph.token, ph.brand, ph.last4
        _advance(c)
        return Reply(say=f"Got it, {_brand_name(c.brand)} ending in {_spell(c.last4)}. {_ask(c)}")
    if c.step == Step.EXP:
        if ph.length not in (4, 6) or ph.expok is not True:
            return _fail_field(c, "That date doesn't look right.")
        c.exp_token = ph.token
        _advance(c)
        return Reply(say=f"Thanks. {_ask(c)}")
    if c.step == Step.CVV:
        if ph.length != cvv_length(c.brand):
            return _fail_field(c, f"The security code should be {cvv_length(c.brand)} digits.")
        c.cvv_token = ph.token
        _advance(c)
        return Reply(say=f"Thanks. {_ask(c)}")
    if c.step == Step.ZIP:
        if ph.length not in (5, 9):
            return _fail_field(c, "A ZIP code is five digits.")
        c.zip_token = ph.token
        _advance(c)
        return Reply(say=_ask(c))
    if c.step == Step.ROUTING:
        if ph.length != 9 or ph.aba is not True:
            return _fail_field(c, "That routing number didn't check out.")
        c.routing_token = ph.token
        _advance(c)
        return Reply(say=f"Thanks. {_ask(c)}")
    if c.step == Step.ACCOUNT:
        if not 4 <= ph.length <= 17:
            return _fail_field(c, "That account number doesn't look right.")
        c.account_token, c.last4 = ph.token, ph.last4 or ""
        _advance(c)
        tail = f"Account ending in {_spell(c.last4)}. " if c.last4 else ""
        return Reply(say=f"Got it. {tail}{_ask(c)}")
    # Long digits at a menu step: treat as a mistake.
    return Reply(say=f"Sorry, I only need one digit here. {_ask(c)}", delete_tokens=[ph.token])


def _menu(c: Capture, choice: str) -> Reply:
    if c.step == Step.ACCT_TYPE:
        c.account_type = "checking" if choice == "1" else "savings"
        _advance(c)
        return Reply(say=f"{c.account_type.title()}, got it. {_ask(c)}")
    if c.step == Step.SAVE:
        c.save_card = choice == "1"
        _advance(c)
        pre = "Okay, I'll save it. " if c.save_card else "Okay, I won't save it. "
        return Reply(say=pre + _ask(c))
    if c.step == Step.CONFIRM:
        if choice == "1":
            c.step = Step.CHARGING
            return Reply(say="", filler="One moment while I process that.", charge=True)
        return _new_card(
            c,
            "Okay, let's change the account."
            if c.method == "check"
            else "Okay, let's change the card.",
        )
    return Reply(say=_ask(c))


def _new_card(c: Capture, lead: str) -> Reply:
    old = c.tokens()
    c.pan_token = c.exp_token = c.cvv_token = c.zip_token = ""
    c.routing_token = c.account_token = c.account_type = ""
    c.brand = c.last4 = ""
    c.tries = {k: 0 for k in c.tries}
    c.step = Step.ROUTING if c.method == "check" else Step.PAN
    return Reply(say=f"{lead} {_ask(c)}", delete_tokens=old)


def after_charge(c: Capture, result: dict) -> Reply:
    """result = normalized gateway result: {status: approved|declined|error|unknown, transaction_id, vault_id}"""
    used = c.tokens()
    status = result.get("status")
    c.result = {
        k: v
        for k, v in result.items()
        if k in ("status", "transaction_id", "auth_code", "vault_id")
    }
    if status == "approved":
        c.step = Step.DONE
        ref = (result.get("transaction_id") or "")[-4:]
        what = "account" if c.method == "check" else "card"
        saved = f" I've saved the {what} for next time." if result.get("vault_id") else ""
        return Reply(
            say=f"You're all set. Your payment of {_money(c.amount)} went through.{saved} Your confirmation number ends in {_spell(ref)}.",
            finished=True,
            handback=(
                f"start_payment result: approved, {c.payment_label(spoken=False)}, "
                f"amount {c.amount} {c.currency}, reference ending {ref}"
                + (f", {what} saved on file" if result.get("vault_id") else "")
                + "."
            ),
            delete_tokens=used,
        )
    if status == "declined":
        c.cards_tried += 1
        if c.cards_tried >= MAX_CARDS:
            return _cancel(
                c,
                "card declined twice",
                "I'm sorry, that card was declined too. I can send you a secure payment link instead, or connect you with someone.",
            )
        r = _new_card(
            c,
            "I'm sorry, the bank declined that card. Would you like to try a different card?",
        )
        r.delete_tokens = used
        return r
    if status == "duplicate":
        c.step = Step.CANCELLED
        return Reply(
            say="It looks like this exact payment already went through a few minutes ago, so I haven't charged you again.",
            finished=True,
            handback="start_payment result: duplicate; the gateway refused an identical charge made minutes earlier. No new charge.",
            delete_tokens=used,
        )
    if status == "held":
        c.step = Step.DONE
        return Reply(
            say="Thanks, your payment has been received and is being reviewed. You'll get a confirmation shortly.",
            finished=True,
            handback="start_payment result: held for review by the gateway; not yet approved.",
            delete_tokens=used,
        )
    if status != "error":
        # unknown, or anything unrecognised: the money may have moved. Never say "no charge".
        c.step = Step.CANCELLED
        return Reply(
            say="That's taking longer than expected. To be safe I won't charge you again; our team will confirm the payment and follow up.",
            finished=True,
            handback="start_payment result: outcome unknown (gateway timeout); flagged for manual review. Do not retry.",
            delete_tokens=used,
        )
    c.step = Step.CANCELLED
    return Reply(
        say=(
            f"I couldn't process that {'payment' if c.method == 'check' else 'card'} just now. "
            "I can send you a secure payment link instead, or connect you with someone."
        ),
        finished=True,
        handback="start_payment result: error, no charge made.",
        delete_tokens=used,
    )
