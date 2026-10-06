"""Shared helpers for the live checks in tests/live/.

These scripts talk to real services (a Basis Theory tenant, gateway sandboxes, ElevenLabs).
They take every credential from the environment, use only published test cards, and print one
line per check:

    PASS <check> :: <detail>
    FAIL <check> :: <detail>
    INFO <message>

The exit code is 1 if any check failed (or none ran). pytest never collects this directory
(see conftest.py).
"""

from __future__ import annotations

import os
import sys

TEST_CARD = "4111111111111111"  # published sandbox test card (Visa)
TEST_BT_API = "https://api.test.basistheory.com"

_results: list[bool] = []


def need(name: str) -> str:
    """A required environment variable; exits with a clear message if unset or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing environment variable {name} (see this script's docstring)")
    return value


def opt(name: str, default: str = "") -> str:
    """An optional environment variable."""
    return os.environ.get(name, "").strip() or default


def bt_api_base() -> str:
    """BT_API_BASE, defaulting to the test API. Refuses a production tenant unless
    AIRLOCK_ALLOW_PRODUCTION_TENANT=1, because some checks send sandbox-style charges."""
    api = opt("BT_API_BASE", TEST_BT_API).rstrip("/")
    if ".test." not in api and os.environ.get("AIRLOCK_ALLOW_PRODUCTION_TENANT") != "1":
        sys.exit(
            f"refusing to run against {api}: use a test tenant, or set "
            "AIRLOCK_ALLOW_PRODUCTION_TENANT=1 if you really mean it"
        )
    return api


def mask(text: str) -> str:
    """Never print the test card in full, even though it is public."""
    return (text or "").replace(TEST_CARD, "<test card>")


def check(name: str, ok: bool, detail: str) -> bool:
    _results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} :: {mask(detail)}", flush=True)
    return bool(ok)


def info(message: str) -> None:
    print(f"INFO {mask(message)}", flush=True)


def finish() -> None:
    failed = _results.count(False)
    print(f"{len(_results) - failed} passed, {failed} failed", flush=True)
    sys.exit(1 if failed or not _results else 0)
