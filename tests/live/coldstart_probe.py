"""Time keep-warm pings through the vault proxies, optionally after an idle wait.

Each request is the body Airlock's keep-warm loop sends, {"airlock_ping": true}. The vault
transforms answer it themselves ({"pong": true}); it never reaches Airlock or a gateway, and
creates nothing. A cold transform runtime shows up as a slow first request (about 10.8 s was
measured on a test tenant; a warm one answers in well under a second).

Environment:
  BT_API_BASE          default https://api.test.basistheory.com (a production tenant is
                       refused unless AIRLOCK_ALLOW_PRODUCTION_TENANT=1)
  INBOUND_PROXY_KEY    the inbound_proxy_key from provisioning
  OUTBOUND_PROXY_KEY   optional: nmi_proxy_key or anet_proxy_key, to ping the outbound proxy too
  IDLE_SECONDS         optional: wait this long first (default 0); e.g. 900 to see a cold start
  REQUESTS             optional: pings per proxy (default 3)

Usage: python tests/live/coldstart_probe.py
"""

from __future__ import annotations

import time

import requests
from _common import bt_api_base, check, finish, info, need, opt

API = bt_api_base()
targets = [("inbound", API + "/proxy/chat/completions", need("INBOUND_PROXY_KEY"))]
if opt("OUTBOUND_PROXY_KEY"):
    targets.append(("outbound", API + "/proxy", opt("OUTBOUND_PROXY_KEY")))
idle = int(opt("IDLE_SECONDS", "0"))
count = int(opt("REQUESTS", "3"))

if idle:
    info(f"waiting {idle} s before the first ping")
    time.sleep(idle)
for name, url, key in targets:
    times = []
    for i in range(count):
        t0 = time.perf_counter()
        try:
            r = requests.post(
                url,
                headers={"BT-PROXY-KEY": key, "Content-Type": "application/json"},
                json={"airlock_ping": True},
                timeout=60,
            )
            ms = (time.perf_counter() - t0) * 1000
            pong = r.status_code == 200 and '"pong":true' in r.text.replace(" ", "")
            detail = f"HTTP {r.status_code} {r.text[:30]!r} in {ms:.0f} ms"
        except requests.RequestException as e:
            ms, pong, detail = (
                (time.perf_counter() - t0) * 1000,
                False,
                type(e).__name__,
            )
        times.append(ms)
        check(f"{name} proxy answered ping {i + 1} inside the vault", pong, detail)
    if len(times) > 1:
        rest = sorted(times[1:])[len(times[1:]) // 2]
        info(f"{name}: first ping {times[0]:.0f} ms, median of the rest {rest:.0f} ms")
finish()
