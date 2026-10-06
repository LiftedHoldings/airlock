"""Print the shape of recorded Custom LLM requests (spike/out/requests.jsonl)."""

import json
import sys
from pathlib import Path

reqs = [
    json.loads(line)
    for line in (Path(__file__).parent / "out" / "requests.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
]
idx = [int(a) for a in sys.argv[1:]] or [0, 2, 3]
for i in idx:
    r = reqs[i]
    b = r["body"]
    print(f"===== #{i} route={r['route']} path={r['path']}")
    print(
        "headers:",
        sorted(r["headers"].keys()),
        "| authorization header present:",
        r["had_authorization"],
    )
    print("top-level keys:", sorted(b.keys()))
    for m in b.get("messages", []):
        print(
            f"  {m.get('role'):9} {json.dumps(m.get('content'))[:170]}  tool_calls={'tool_calls' in m}"
        )
    print("tools:", [t["function"]["name"] for t in b.get("tools", [])])
    for t in b.get("tools", []):
        if t["function"]["name"] == "transfer_to_agent":
            print("transfer params:", json.dumps(t["function"]["parameters"])[:500])
    print("other fields:", {k: b[k] for k in b if k not in ("messages", "tools")})
