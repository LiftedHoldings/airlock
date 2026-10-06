"""Leak scan (invariant 9): search every file given for canary card numbers and any
Luhn-valid 13-19 digit run. Exit 1 on a single hit. Usage: python leak_scan.py <files...>"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from airlock.core.cards import find_cardlike  # noqa: E402

CANARIES = [
    "4111111111111111",
    "5424000000000015",
    "370000000000002",
    "6011000000000012",
    "4007000000027",
]
hits = 0
scanned = 0
for name in sys.argv[1:]:
    p = Path(name)
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    scanned += 1
    for c in CANARIES:
        if c in re.sub(r"[ .\-]", "", text):
            print(f"LEAK canary {c[-4:]} in {p}")
            hits += 1
    for d in find_cardlike(text):
        print(f"LEAK luhn-valid run ending {d[-4:]} in {p}")
        hits += 1
print(f"scanned {scanned} files, {hits} hits")
sys.exit(1 if hits else 0)
