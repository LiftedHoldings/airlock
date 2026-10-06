"""Assemble the public evidence bundle (site/data/evidence.json) from real test runs.

Inputs (all already redacted at source; this script refuses to write if a leak scan hits):
  .local/evidence-final.jsonl     caller-side turns through the production chain (redacted)
  .local/evidence-capture.jsonl   what Airlock received + its token-only outbound calls
  .local/live_vault_test.out      live Basis Theory permission / lifecycle results
  .local/echeck-agent-run.json    eCheck conversation through the live ElevenAgents agent
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from airlock.core.cards import find_cardlike  # noqa: E402

L = ROOT / ".local"
OUT = ROOT / "site" / "data" / "evidence.json"
CONV = re.compile(r"conv_[A-Za-z0-9]+")


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


turns = jl(L / "evidence-final.jsonl")
capture = jl(L / "evidence-capture.jsonl")

# Group what Airlock received by conversation id (found in the system prompt).
received: dict[str, list] = {}
outbound: dict[str, list] = {}
last_conv = None
for rec in capture:
    if "messages" in rec:
        sysmsg = next(
            (m.get("content", "") for m in rec["messages"] if m.get("role") == "system"),
            "",
        )
        m = CONV.search(sysmsg)
        conv = m.group(0) if m else "?"
        last_conv = conv
        users = [m for m in rec["messages"] if m.get("role") == "user"]
        received.setdefault(conv, []).append(
            {
                "last_user_message_as_received_by_airlock": users[-1]["content"] if users else "",
                "earlier_user_messages_as_received": [u["content"] for u in users[:-1]][-4:],
            }
        )
    elif "outbound_request" in rec and last_conv:
        outbound.setdefault(last_conv, []).append(
            {
                "airlock_to_vault_request": rec["outbound_request"],
                "vault_reply_to_airlock": rec["outbound_response"],
            }
        )

runs = []
for conv in dict.fromkeys(t["conversation"] for t in turns):
    ts = [t for t in turns if t["conversation"] == conv]
    rcv = received.get(conv, [])
    steps = []
    for i, t in enumerate(ts):
        steps.append(
            {
                "caller_sent_redacted": t["caller_sent"],
                "airlock_received": rcv[i]["last_user_message_as_received_by_airlock"]
                if i < len(rcv)
                else None,
                "agent_said": t["agent_said"],
                "ttfb_ms": t["ttfb_ms"],
                "total_ms": t["total_ms"],
            }
        )
    runs.append(
        {
            "scenario": ts[0]["scenario"],
            "conversation": conv,
            "steps": steps,
            "charges": outbound.get(conv, []),
        }
    )

vault_tests = []
for line in (L / "live_vault_test.out").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^(PASS|FAIL) (.+?) :: (.+)$", line)
    if m:
        vault_tests.append({"result": m.group(1), "check": m.group(2), "evidence": m.group(3)})

scripted = [
    s["ttfb_ms"]
    for r in runs
    for s in r["steps"][1:]
    if s["airlock_received"] and "[[airlock:kp" in s["airlock_received"]
]
bundle = {
    "generated": "2026-10-06",
    "environment": "Basis Theory TEST tenant + NMI sandbox + Authorize.net sandbox + ElevenLabs Agents. Test cards only; no real money.",
    "runs": runs,
    "live_vault_tests": vault_tests,
    "echeck_agent_run": json.loads((L / "echeck-agent-run.json").read_text(encoding="utf-8")),
    "latency": {
        "keypad_turn_through_vault_median_ms": round(statistics.median(scripted))
        if scripted
        else None,
        "keypad_turn_samples": len(scripted),
        "note": "Driver -> Basis Theory inbound proxy -> Airlock (DigitalOcean NYC) and back. Airlock's own handling is under 20 ms; the rest is the vault hop.",
    },
}
text = json.dumps(bundle, indent=1, ensure_ascii=False)
hits = find_cardlike(text)
if hits:
    sys.exit(f"REFUSING to write: {len(hits)} card-like numbers found")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(text, encoding="utf-8")
print(
    f"wrote {OUT} | runs={len(runs)} steps={sum(len(r['steps']) for r in runs)} charges={sum(len(r['charges']) for r in runs)} vault_tests={len(vault_tests)} | leak scan: 0 hits"
)
