"""Do the gateway sandboxes require the account-holder name on an eCheck?

Sends sandbox eChecks directly to each gateway (not through the vault), with and without a
name, using the gateways' published sandbox bank details only. Expected, as observed when
Airlock was built:

  NMI            with name: approved; without: refused, "The checkname field is required"
  Authorize.net  with name: approved (with SEC code PPD); without: refused (schema error)

Approved test transactions are voided afterwards.

USE SANDBOX CREDENTIALS ONLY. NMI uses the same URL for live and sandbox accounts; the key
decides. An Authorize.net URL other than apitest is refused unless
AIRLOCK_ALLOW_PRODUCTION_TENANT=1.

Environment (at least one gateway):
  NMI_SECURITY_KEY                       NMI sandbox security key
  NMI_URL                                optional; default https://secure.nmi.com/api/transact.php
  NMI_ECHECK_SEC                         optional; default TEL
  ANET_LOGIN_ID, ANET_TRANSACTION_KEY    Authorize.net sandbox credentials
  ANET_URL                               optional; default https://apitest.authorize.net/xml/v1/request.api
  ANET_ECHECK_SEC                        optional; default PPD (the sandbox rejects TEL, error 246)
  ANET_ROUTING, ANET_ACCOUNT             optional; default 011000015 / 123456789

Usage: python tests/live/echeck_probe.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from urllib.parse import parse_qsl

import requests
from _common import check, finish, info, opt

NMI_KEY = opt("NMI_SECURITY_KEY")
ANET_ID, ANET_KEY = opt("ANET_LOGIN_ID"), opt("ANET_TRANSACTION_KEY")
if not NMI_KEY and not (ANET_ID and ANET_KEY):
    sys.exit("set NMI_SECURITY_KEY and/or ANET_LOGIN_ID + ANET_TRANSACTION_KEY")
ANET_URL = opt("ANET_URL", "https://apitest.authorize.net/xml/v1/request.api")
if "apitest." not in ANET_URL and os.environ.get("AIRLOCK_ALLOW_PRODUCTION_TENANT") != "1":
    sys.exit(f"refusing to send test eChecks to {ANET_URL}")

# Under $100 (Authorize.net sandbox approves eChecks below that) and varied per run so the
# gateways' duplicate checks do not refuse a repeat.
AMOUNT = f"{secrets.randbelow(80) + 10}.{secrets.randbelow(90) + 10}"


def nmi(data: dict) -> dict:
    url = opt("NMI_URL", "https://secure.nmi.com/api/transact.php")
    r = requests.post(url, data={"security_key": NMI_KEY, **data}, timeout=30)
    return dict(parse_qsl(r.text))


def anet(transaction: dict) -> dict:
    body = {
        "createTransactionRequest": {
            "merchantAuthentication": {"name": ANET_ID, "transactionKey": ANET_KEY},
            "transactionRequest": transaction,
        }
    }
    r = requests.post(ANET_URL, json=body, timeout=30)
    return json.loads(r.content.decode("utf-8-sig"))


if NMI_KEY:
    base = {
        "type": "sale",
        "payment": "check",
        "checkaba": "123123123",
        "checkaccount": "123123123",
        "account_holder_type": "personal",
        "account_type": "checking",
        "sec_code": opt("NMI_ECHECK_SEC", "TEL"),
        "amount": AMOUNT,
    }
    q = nmi(
        {
            **base,
            "orderid": "ECHK" + secrets.token_hex(3).upper(),
            "checkname": "Test Payer",
        }
    )
    check(
        "NMI eCheck with a name is approved",
        q.get("response") == "1",
        f"response={q.get('response')} text={q.get('responsetext')!r}",
    )
    if q.get("response") == "1" and q.get("transactionid"):
        v = nmi({"type": "void", "payment": "check", "transactionid": q["transactionid"]})
        check(
            "cleanup: NMI test eCheck voided",
            v.get("response") == "1",
            f"{v.get('responsetext')!r}",
        )
    q = nmi({**base, "orderid": "ECHK" + secrets.token_hex(3).upper()})
    check(
        "NMI eCheck without a name is refused",
        q.get("response") != "1" and "checkname" in (q.get("responsetext") or "").lower(),
        f"response={q.get('response')} text={q.get('responsetext')!r}",
    )
    if q.get("response") == "1" and q.get("transactionid"):
        nmi({"type": "void", "payment": "check", "transactionid": q["transactionid"]})
        info("cleanup: voided the unexpected NMI approval")

if ANET_ID and ANET_KEY:
    sec = opt("ANET_ECHECK_SEC", "PPD")
    for label, name in [("with a name", "Test Payer"), ("without a name", None)]:
        bank = {
            "accountType": "checking",
            "routingNumber": opt("ANET_ROUTING", "011000015"),
            "accountNumber": opt("ANET_ACCOUNT", "123456789"),
        }
        if name:
            bank["nameOnAccount"] = name
        bank["echeckType"] = sec  # schema order: echeckType comes after nameOnAccount
        j = anet(
            {
                "transactionType": "authCaptureTransaction",
                "amount": AMOUNT,
                "payment": {"bankAccount": bank},
            }
        )
        t = j.get("transactionResponse") or {}
        errs = [f"{e.get('errorCode')}:{e.get('errorText')}" for e in t.get("errors", [])] or [
            f"{m.get('code')}:{m.get('text')}" for m in (j.get("messages") or {}).get("message", [])
        ]
        approved = t.get("responseCode") == "1"
        detail = f"resultCode={(j.get('messages') or {}).get('resultCode')} responseCode={t.get('responseCode')} {errs[:2]}"
        if name:
            check(
                f"Authorize.net eCheck {label} (SEC {sec}) is approved",
                approved,
                detail,
            )
        else:
            check(f"Authorize.net eCheck {label} is refused", not approved, detail)
        if approved and t.get("transId"):
            v = anet({"transactionType": "voidTransaction", "refTransId": t["transId"]})
            voided = (v.get("transactionResponse") or {}).get("responseCode") == "1"
            check(
                "cleanup: Authorize.net test eCheck voided",
                voided,
                f"transaction ending {t['transId'][-4:]}",
            )
finish()
