"""Amount lookup. The model supplies an order id, never an amount.

Production: set ORDER_LOOKUP_URL to a merchant endpoint that answers
GET <url>?order_id=<id> with {"amount": "84.20"} (optionally "customer_name", "paid": true).
Demo: set AIRLOCK_DEMO=1 to use the built-in sandbox catalogue instead. With neither,
Airlock refuses to start a payment: nothing may let a caller or the model choose an amount.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation

import httpx

from .core.cards import find_cardlike

DEMO = os.environ.get("AIRLOCK_DEMO") == "1"
DEMO_ORDERS = {
    "A1042": "84.20",  # approves in both sandboxes
    "A2001": "12.00",
    "A3003": "250.00",
    "A0050": "0.50",  # NMI test mode declines amounts under 1.00
}
# Letters, digits and dashes, up to 20 (Authorize.net invoiceNumber limit). Card-like ids are
# refused separately by the outbound transform.
ORDER_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,19}$")
MAX_AMOUNT = Decimal(os.environ.get("MAX_AMOUNT", "500.00"))


# The eCheck account-holder names the outbound transform accepts (vaults/basis_theory/outbound.js).
HOLDER_RX = re.compile(r"[A-Za-z][A-Za-z .'\-]{0,59}")


def clean_holder_name(value) -> str:
    """A name the gateway transform will accept, or "" (so the caller is asked instead)."""
    if value is None:
        return ""
    name = str(value).strip()[:60]
    if find_cardlike(name) or not HOLDER_RX.fullmatch(name):
        return ""
    return name


def normalize(order_id: str) -> str:
    s = re.sub(r"[^A-Za-z0-9-]", "", order_id or "").upper()
    if DEMO and s and s[0].isdigit():
        s = "A" + s  # demo callers often say just the number ("1042")
    return s


async def lookup_order(order_id: str) -> dict | None:
    oid = normalize(order_id)
    if not ORDER_RX.match(oid):
        return None
    url = os.environ.get("ORDER_LOOKUP_URL", "").strip()
    customer_name = ""
    if url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(url, params={"order_id": oid})
            if r.status_code != 200:
                return None
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return None  # fail closed: no amount, no payment
        if not isinstance(data, dict) or data.get("paid") is True:
            return None
        amount = str(data.get("amount", ""))
        customer_name = clean_holder_name(data.get("customer_name"))
    elif DEMO:
        # Demo catalogue: "A1042" or a run-unique "A1042-7F3K" both price as A1042.
        amount = DEMO_ORDERS.get(oid.split("-")[0], "")
        # Sandbox test orders: "T" + cents, e.g. T8437 = 84.37 (varies the amount between test
        # runs so the gateway's duplicate check does not refuse a repeat of the same test).
        m = re.fullmatch(r"T(\d{3,5})(?:-[A-Z0-9]+)?", oid)
        if not amount and m:
            amount = f"{int(m.group(1)) / 100:.2f}"
    else:
        return None
    try:
        amt = Decimal(amount).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if amt <= 0 or amt > MAX_AMOUNT:
        return None
    return {
        "order_id": oid,
        "amount": f"{amt:.2f}",
        "customer_name": customer_name[:60],
    }
