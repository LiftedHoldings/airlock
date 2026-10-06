"""Provision a Basis Theory tenant for Airlock (``airlock vault init``).

Creates, through the Basis Theory API:
  * application "airlock-edge"      token:create  (used by the inbound transform)
  * application "airlock-outbound"  token:use     (attached to the outbound proxies only)
  * application "airlock-service"   token:delete  (the only vault key Airlock holds)
  * inbound proxy  -> AIRLOCK_URL   request transform = vaults/basis_theory/inbound.js
  * outbound proxy -> NMI            request/response transforms = outbound.js
  * outbound proxy -> Authorize.net  request/response transforms = outbound.js

Environment:
  BT_MANAGEMENT_KEY   management key (stays on the operator's machine, never on Airlock)
  BT_API_BASE         https://api.test.basistheory.com (test) or https://api.basistheory.com
  AIRLOCK_URL         public base URL of Airlock, e.g. https://airlock.example.com/v1
  AIRLOCK_EDGE_SECRET shared HMAC secret (also configured on Airlock)
  NMI_SECURITY_KEY, NMI_URL                    (optional; skip NMI proxy if unset)
  ANET_LOGIN_ID, ANET_TRANSACTION_KEY, ANET_URL (optional; skip Authorize.net if unset)
  NMI_ECHECK_SEC, ANET_ECHECK_SEC              eCheck SEC code, default TEL (PPD for the
                                               Authorize.net sandbox)
  TOKEN_TTL_SECONDS   default 600
  TEST_CARDS_ONLY     "1" tokenizes only published sandbox test cards (public demos)
  MAX_AMOUNT          default 500.00

--update-transforms redeploys transform code and proxy configuration only (all the
variables above are still read); applications and their keys are unchanged.
See docs/CONFIGURATION.md.

Prints the resulting ids and keys as JSON on stdout. Treat that output as a secret.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SDK = {"@basis-theory/node-sdk": "6.1.1"}


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"missing {name}")
    return v


API = env("BT_API_BASE", "https://api.test.basistheory.com").rstrip("/")
H = {"BT-API-KEY": env("BT_MANAGEMENT_KEY"), "Content-Type": "application/json"}


def call(method: str, path: str, body: dict | None = None) -> dict:
    r = requests.request(method, API + path, headers=H, json=body, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
    return r.json() if r.text else {}


def find(kind: str, name: str) -> dict | None:
    page = call("GET", f"/{kind}?size=100")
    for item in page.get("data", []):
        if item.get("name") == name:
            return item
    return None


def application(name: str, permissions: list[str]) -> dict:
    existing = find("applications", name)
    if existing:
        call("DELETE", f"/applications/{existing['id']}")
    return call(
        "POST",
        "/applications",
        {"name": name, "type": "private", "permissions": permissions},
    )


def code(file: str, export: str | None = None) -> str:
    src = (ROOT / "vaults" / "basis_theory" / file).read_text(encoding="utf-8")
    if export:
        src += f"\nmodule.exports = module.exports.{export};\n"
    return src


def transform(src: str, permissions: list[str] | None = None, timeout: int = 10) -> dict:
    runtime = {"image": "node24", "dependencies": SDK, "timeout": timeout}
    if permissions:
        runtime["permissions"] = permissions
    return {"type": "code", "code": src, "options": {"runtime": runtime}}


def proxy(name: str, body: dict) -> dict:
    existing = find("proxies", name)
    body = {"name": name, **body}
    if existing:
        return call("PUT", f"/proxies/{existing['id']}", body) | {"key": existing.get("key")}
    return call("POST", "/proxies", body)


def main() -> None:
    if "--update-transforms" in sys.argv:
        # Redeploy transform code only; applications, keys and secrets are unchanged.
        edge_app = find("applications", "airlock-edge") or sys.exit("run a full provision first")
        out_app = find("applications", "airlock-outbound") or sys.exit("run a full provision first")
        svc_app = find("applications", "airlock-service") or {}
        svc_app = {"id": svc_app.get("id"), "key": None}
    else:
        edge_app = application("airlock-edge", ["token:create"])
        # token:use lives ONLY on the application attached to the fixed-destination outbound
        # proxies. A key holding token:use can also drive an EPHEMERAL proxy to any URL
        # (verified on a live tenant), so Airlock's own key gets token:delete and nothing else.
        out_app = application("airlock-outbound", ["token:use"])
        svc_app = application("airlock-service", ["token:delete"])
    out = {
        "api_base": API,
        "edge_application_id": edge_app["id"],
        "service_application_id": svc_app["id"],
        "service_api_key": svc_app.get("key"),
    }

    inbound = proxy(
        "airlock-inbound",
        {
            "destination_url": env("AIRLOCK_URL"),
            "request_transforms": [transform(code("inbound.js"), ["token:create"], timeout=10)],
            "configuration": {
                "EDGE_SECRET": env("AIRLOCK_EDGE_SECRET"),
                # Long enough for a slow caller to reach confirm; tokens are deleted after each attempt.
                "TOKEN_TTL_SECONDS": env("TOKEN_TTL_SECONDS", "600"),
                "TEST_CARDS_ONLY": env("TEST_CARDS_ONLY", "0"),
            },
            "application": {"id": edge_app["id"]},
            "require_auth": False,
        },
    )
    out["inbound_proxy_id"], out["inbound_proxy_key"] = (
        inbound["id"],
        inbound.get("key"),
    )
    out["inbound_proxy_host"] = inbound.get("proxy_host", "")

    max_amount = env("MAX_AMOUNT", "500.00")
    if os.environ.get("NMI_SECURITY_KEY"):
        p = proxy(
            "airlock-outbound-nmi",
            {
                "destination_url": env("NMI_URL", "https://secure.nmi.com/api/transact.php"),
                "request_transforms": [transform(code("outbound.js", "requestTransform"))],
                "response_transforms": [transform(code("outbound.js", "responseTransform"))],
                "configuration": {
                    "GATEWAY": "nmi",
                    "ECHECK_SEC_CODE": env("NMI_ECHECK_SEC", "TEL"),
                    "NMI_SECURITY_KEY": env("NMI_SECURITY_KEY"),
                    "MAX_AMOUNT": max_amount,
                },
                "application": {"id": out_app["id"]},
                "require_auth": False,
            },
        )
        out["nmi_proxy_id"], out["nmi_proxy_key"] = p["id"], p.get("key")
    if os.environ.get("ANET_LOGIN_ID"):
        p = proxy(
            "airlock-outbound-authorizenet",
            {
                "destination_url": env(
                    "ANET_URL", "https://apitest.authorize.net/xml/v1/request.api"
                ),
                "request_transforms": [transform(code("outbound.js", "requestTransform"))],
                "response_transforms": [transform(code("outbound.js", "responseTransform"))],
                "configuration": {
                    "GATEWAY": "authorizenet",
                    # TEL must be enabled on the merchant account by Authorize.net; sandbox
                    # accounts reject it (error 246), so sandbox runs set ANET_ECHECK_SEC=PPD.
                    "ECHECK_SEC_CODE": env("ANET_ECHECK_SEC", "TEL"),
                    "ANET_LOGIN_ID": env("ANET_LOGIN_ID"),
                    "ANET_TRANSACTION_KEY": env("ANET_TRANSACTION_KEY"),
                    "MAX_AMOUNT": max_amount,
                },
                "application": {"id": out_app["id"]},
                "require_auth": False,
            },
        )
        out["anet_proxy_id"], out["anet_proxy_key"] = p["id"], p.get("key")
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
