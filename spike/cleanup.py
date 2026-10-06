"""Delete every agent tagged/named airlock-spike-* created by the spikes."""

import requests

from transfer_history import API, h

r = requests.get(
    f"{API}/v1/convai/agents",
    headers=h(),
    params={"search": "airlock-spike", "page_size": 100},
    timeout=30,
)
r.raise_for_status()
for a in r.json().get("agents", []):
    if a["name"].startswith("airlock-spike"):
        d = requests.delete(f"{API}/v1/convai/agents/{a['agent_id']}", headers=h(), timeout=30)
        print("deleted", a["name"], a["agent_id"], d.status_code)
