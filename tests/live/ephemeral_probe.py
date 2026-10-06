"""Can Airlock's vault key send a token's value to an arbitrary URL through an EPHEMERAL proxy?

In Basis Theory, a key holding token:use can drive an ephemeral proxy (BT-PROXY-URL) to any
destination. Airlock's service key must hold token:delete only, so this must fail. The probe
creates a token holding the published test card 4111111111111111 with a throwaway
token:create application, asks the ephemeral proxy to send it to an echo service with
Airlock's key, and checks the card did not come back.

Environment:
  BT_API_BASE          default https://api.test.basistheory.com (a production tenant is
                       refused unless AIRLOCK_ALLOW_PRODUCTION_TENANT=1)
  BT_MANAGEMENT_KEY    creates and then deletes the throwaway application
  AIRLOCK_SERVICE_KEY  the service_api_key from provisioning (Airlock's VAULT_API_KEY)
  ECHO_URL             optional; default https://echo.basistheory.com/anything

Cleans up the token and the application.

Usage: python tests/live/ephemeral_probe.py
"""

from __future__ import annotations

import requests
from _common import TEST_CARD, bt_api_base, check, finish, info, need, opt

API = bt_api_base()
M = {"BT-API-KEY": need("BT_MANAGEMENT_KEY"), "Content-Type": "application/json"}
SERVICE_KEY = need("AIRLOCK_SERVICE_KEY")
ECHO = opt("ECHO_URL", "https://echo.basistheory.com/anything")

r = requests.post(
    API + "/applications",
    headers=M,
    json={
        "name": "airlock-probe-creator",
        "type": "private",
        "permissions": ["token:create"],
    },
    timeout=30,
)
if r.status_code >= 300:
    raise SystemExit(f"could not create the throwaway application: HTTP {r.status_code}")
app = r.json()
tid = ""
try:
    r = requests.post(
        API + "/tokens",
        headers={"BT-API-KEY": app["key"], "Content-Type": "application/json"},
        json={"type": "token", "data": TEST_CARD},
        timeout=30,
    )
    tid = r.json().get("id", "") if r.status_code < 300 else ""
    if not check(
        "setup",
        bool(tid),
        f"created a token holding the test card (HTTP {r.status_code})",
    ):
        raise SystemExit(1)
    r = requests.post(
        API + "/proxy",
        headers={
            "BT-API-KEY": SERVICE_KEY,
            "BT-PROXY-URL": ECHO,
            "Content-Type": "application/json",
        },
        json={"card": "{{ %s }}" % tid},
        timeout=40,
    )
    check(
        "ephemeral proxy refused for Airlock's key",
        r.status_code in (401, 403),
        f"HTTP {r.status_code}; {r.text[:120]}",
    )
    check(
        "card value not sent to the arbitrary URL",
        TEST_CARD not in r.text,
        f"test card echoed back: {TEST_CARD in r.text}",
    )
finally:
    if tid:
        d = requests.delete(f"{API}/tokens/{tid}", headers={"BT-API-KEY": SERVICE_KEY}, timeout=30)
        info(f"cleanup: token delete -> HTTP {d.status_code}")
    d = requests.delete(f"{API}/applications/{app['id']}", headers=M, timeout=30)
    info(f"cleanup: throwaway application delete -> HTTP {d.status_code}")
finish()
