"""Capture sessions, keyed by ElevenLabs conversation id.

In-memory with a TTL. Holds token ids, last four, brand, attempt counts and the
order id; never card data. Swap for Redis to run more than one Airlock instance.
"""

from __future__ import annotations

import time

from .core.script import Capture


class SessionStore:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl = ttl_seconds
        self._caps: dict[str, tuple[float, Capture]] = {}
        self._notes: dict[str, tuple[float, list[str]]] = {}
        self._settled: dict[str, tuple[float, dict[str, str]]] = {}

    def _sweep(self) -> None:
        cut = time.time() - self.ttl
        for d in (self._caps, self._notes, self._settled):
            for k in [k for k, (t, _) in d.items() if t < cut]:
                d.pop(k, None)

    def get(self, conv: str) -> Capture | None:
        self._sweep()
        hit = self._caps.get(conv)
        if hit:
            self._caps[conv] = (time.time(), hit[1])
            return hit[1]
        return None

    def put(self, conv: str, cap: Capture) -> None:
        self._caps[conv] = (time.time(), cap)

    def finish(self, conv: str, handback: str) -> None:
        notes = self._notes.get(conv, (0, []))[1]
        if handback:
            notes.append(handback)
        self._notes[conv] = (time.time(), notes)

    def settle(self, conv: str, order_id: str, status: str) -> None:
        """Record a charge outcome for an order. approved / unknown / held / duplicate block
        any further capture for that order in this conversation (no double charge)."""
        t, orders = self._settled.get(conv, (0, {}))
        if status in ("approved", "unknown", "held", "duplicate"):
            orders[order_id] = status
        self._settled[conv] = (time.time(), orders)

    def settled(self, conv: str) -> dict[str, str]:
        self._sweep()
        return dict(self._settled.get(conv, (0, {}))[1])

    def handbacks(self, conv: str) -> list[str]:
        self._sweep()
        return list(self._notes.get(conv, (0, []))[1])

    def open_count(self) -> int:
        return sum(1 for _, c in self._caps.values() if c.step.value not in ("done", "cancelled"))
