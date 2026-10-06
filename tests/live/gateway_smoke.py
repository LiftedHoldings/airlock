"""Check that gateway sandbox credentials authenticate. Sends no card or bank data and creates
no transactions.

  NMI:           a transaction query (query.php) for an order id that does not exist.
  Authorize.net: authenticateTestRequest.

Environment (at least one gateway):
  NMI_SECURITY_KEY                        NMI sandbox security key
  NMI_QUERY_URL                           optional; default https://secure.nmi.com/api/query.php
  ANET_LOGIN_ID, ANET_TRANSACTION_KEY     Authorize.net sandbox credentials
  ANET_URL                                optional; default https://apitest.authorize.net/xml/v1/request.api

Usage: python tests/live/gateway_smoke.py
"""

from __future__ import annotations

import codecs
import json
import sys

import requests
from _common import check, finish, info, opt

nmi_key = opt("NMI_SECURITY_KEY")
anet_id, anet_key = opt("ANET_LOGIN_ID"), opt("ANET_TRANSACTION_KEY")
if not nmi_key and not (anet_id and anet_key):
    sys.exit("set NMI_SECURITY_KEY and/or ANET_LOGIN_ID + ANET_TRANSACTION_KEY")

if anet_id and anet_key:
    url = opt("ANET_URL", "https://apitest.authorize.net/xml/v1/request.api")
    body = {
        "authenticateTestRequest": {
            "merchantAuthentication": {"name": anet_id, "transactionKey": anet_key}
        }
    }
    r = requests.post(url, json=body, timeout=30)
    bom = r.content.startswith(codecs.BOM_UTF8)
    info(f"Authorize.net reply starts with a UTF-8 byte-order mark: {bom}")
    try:
        msgs = json.loads(r.content.decode("utf-8-sig")).get("messages", {})
    except ValueError:
        msgs = {}
    check(
        "Authorize.net credentials authenticate",
        msgs.get("resultCode") == "Ok",
        f"HTTP {r.status_code}; {msgs.get('message')}",
    )

if nmi_key:
    url = opt("NMI_QUERY_URL", "https://secure.nmi.com/api/query.php")
    r = requests.post(
        url,
        data={
            "security_key": nmi_key,
            "report_type": "transaction",
            "order_id": "airlock-smoke-none",
        },
        timeout=30,
    )
    check(
        "NMI credentials authenticate",
        r.status_code == 200 and "error_response" not in r.text,
        f"HTTP {r.status_code}; {r.text[:120].replace(chr(10), ' ')}",
    )
finish()
