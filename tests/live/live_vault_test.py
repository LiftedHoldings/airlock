"""Live vault checks: Airlock's key permissions and the outbound proxy's guards.

Run against a Basis Theory TEST tenant provisioned by cli/provision_basis_theory.py with an
NMI sandbox outbound proxy. Uses only the published test card 4111111111111111.

Checks (numbers refer to the security model in docs/DESIGN.md):
  4  Airlock's service key cannot read a token; the token:create key cannot either.
  5  The NMI outbound proxy refuses card data steered into another field (zip, an extra field);
     a BT-PROXY-URL header cannot redirect it; Airlock's key cannot drive an ephemeral proxy.
  6  Airlock's key can delete a token, and a deleted token can no longer be charged.

One check sends a $0.50 sale to the NMI sandbox (which declines amounts under $1.00).

Environment:
  BT_API_BASE          default https://api.test.basistheory.com (a production tenant is
                       refused unless AIRLOCK_ALLOW_PRODUCTION_TENANT=1)
  BT_MANAGEMENT_KEY    creates and then deletes a throwaway token:create application
  AIRLOCK_SERVICE_KEY  the service_api_key from provisioning (Airlock's VAULT_API_KEY)
  NMI_PROXY_KEY        the nmi_proxy_key from provisioning
  ECHO_URL             optional; default https://echo.basistheory.com/anything

Cleans up the token and the throwaway application. Output lines (PASS/FAIL <check> ::
<detail>) are the format tests/e2e/build_evidence.py reads.

Usage: python tests/live/live_vault_test.py
"""

from __future__ import annotations

import secrets

import requests
from _common import TEST_CARD, bt_api_base, check, finish, info, need, opt

API = bt_api_base()
M = {"BT-API-KEY": need("BT_MANAGEMENT_KEY"), "Content-Type": "application/json"}
S = {"BT-API-KEY": need("AIRLOCK_SERVICE_KEY"), "Content-Type": "application/json"}
P = {"BT-PROXY-KEY": need("NMI_PROXY_KEY"), "Content-Type": "application/json"}
ECHO = opt("ECHO_URL", "https://echo.basistheory.com/anything")


def charge_body(tid: str, **extra) -> dict:
    return {
        "order_id": "LIVE" + secrets.token_hex(3).upper(),
        "amount": "1.00",
        "currency": "USD",
        "pan": "{{ %s }}" % tid,
        "exp": "1229",
        "cvv": "999",
        **extra,
    }


r = requests.post(
    API + "/applications",
    headers=M,
    json={
        "name": "airlock-livetest-creator",
        "type": "private",
        "permissions": ["token:create"],
    },
    timeout=30,
)
if r.status_code >= 300:
    raise SystemExit(f"could not create the throwaway application: HTTP {r.status_code}")
app = r.json()
C = {"BT-API-KEY": app["key"], "Content-Type": "application/json"}
tid = ""
deleted = False
try:
    r = requests.post(
        API + "/tokens",
        headers=C,
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

    r = requests.get(f"{API}/tokens/{tid}", headers=S, timeout=30)
    check(
        "4 Airlock's key cannot read a token",
        r.status_code in (401, 403),
        f"GET /tokens/<id> with Airlock's key -> HTTP {r.status_code}",
    )
    check(
        "4 response body carries no card data",
        TEST_CARD not in r.text,
        "searched the response body for the card number",
    )
    r = requests.get(f"{API}/tokens/{tid}", headers=C, timeout=30)
    check(
        "4 the creator key cannot read it back either",
        r.status_code in (401, 403),
        f"GET with the token:create-only key -> HTTP {r.status_code}",
    )

    r = requests.post(
        API + "/proxy",
        headers=P,
        json=charge_body(tid, zip="{{ %s }}" % tid),
        timeout=40,
    )
    check(
        "5 card data steered into another field is refused",
        r.status_code == 400 and TEST_CARD not in r.text,
        f"zip={{{{token}}}} -> HTTP {r.status_code} {r.text[:90]}",
    )
    r = requests.post(
        API + "/proxy",
        headers=P,
        json=charge_body(tid, memo="{{ %s }}" % tid),
        timeout=40,
    )
    check(
        "5 card data in an echo field is refused",
        r.status_code == 400 and TEST_CARD not in r.text,
        f"memo={{{{token}}}} -> HTTP {r.status_code} {r.text[:90]}",
    )

    r = requests.post(
        API + "/proxy",
        headers={**P, "BT-PROXY-URL": ECHO},
        json=charge_body(tid, amount="0.50"),
        timeout=40,
    )
    went_to_nmi = '"gateway":"nmi"' in r.text  # only the NMI response transform emits this
    check(
        "5 caller cannot redirect the fixed-destination proxy",
        went_to_nmi and TEST_CARD not in r.text,
        f"BT-PROXY-URL={ECHO} -> HTTP {r.status_code}; reply came from NMI: {went_to_nmi}",
    )

    r = requests.post(
        API + "/proxy",
        headers={**S, "BT-PROXY-URL": ECHO},
        json={"card": "{{ %s }}" % tid},
        timeout=40,
    )
    check(
        "5 Airlock's key cannot send card data to an arbitrary URL (ephemeral proxy)",
        r.status_code in (401, 403) and TEST_CARD not in r.text,
        f"ephemeral proxy with Airlock's key -> HTTP {r.status_code}; card in reply: {TEST_CARD in r.text}",
    )

    r = requests.delete(f"{API}/tokens/{tid}", headers=S, timeout=30)
    deleted = r.status_code == 204
    check(
        "6 Airlock's key can delete the token",
        deleted,
        f"DELETE -> HTTP {r.status_code}",
    )
    r = requests.post(API + "/proxy", headers=P, json=charge_body(tid), timeout=40)
    check(
        "6 a deleted token can no longer be charged",
        r.status_code >= 400,
        f"charge with deleted token -> HTTP {r.status_code} {r.text[:90]}",
    )
finally:
    if tid and not deleted:
        d = requests.delete(f"{API}/tokens/{tid}", headers=S, timeout=30)
        info(f"cleanup: token delete -> HTTP {d.status_code}")
    d = requests.delete(f"{API}/applications/{app['id']}", headers=M, timeout=30)
    info(f"cleanup: throwaway application delete -> HTTP {d.status_code}")
finish()
