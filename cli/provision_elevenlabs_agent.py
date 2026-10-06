"""Create (or update) an ElevenLabs Agents agent wired to Airlock through the vault.

The agent's Custom LLM URL is the vault's inbound proxy, and the proxy key travels in a
workspace secret (never inline in the agent config). Keypad input is enabled with pound
as the terminator and redaction on; audio recording is off.

Environment:
  ELEVENLABS_API_KEY
  VAULT_INBOUND_URL     e.g. https://api.basistheory.com/proxy
  VAULT_INBOUND_KEY     the inbound proxy key (stored as an ElevenLabs workspace secret)
  AGENT_NAME            default "Airlock demo"
  VOICE_ID              ElevenLabs voice id
  ALLOWED_HOSTS         comma-separated hostnames allowed to embed the agent (optional)
Prints {"agent_id": ...}.
"""

from __future__ import annotations

import json
import os
import sys

import requests

API = "https://api.elevenlabs.io"


def env(k: str, d: str | None = None) -> str:
    v = os.environ.get(k, d)
    if v is None:
        sys.exit(f"missing {k}")
    return v


H = {"xi-api-key": env("ELEVENLABS_API_KEY"), "Content-Type": "application/json"}

PROMPT = """You are Scarlett, the phone assistant for Lifted Coffee Roasters, a demo store.
You help callers pay for an order. Demo orders: A1042 is a 12oz bag of house espresso plus shipping, 84 dollars and 20 cents.
A2001 is a 12 dollar gift card. A3003 is a 250 dollar espresso grinder.
When the caller wants to pay, call start_payment with the order id. You never collect card details yourself;
start_payment collects them securely on the keypad, and you will never see the digits.
Keep every reply to one or two short spoken sentences.
Conversation id: {{system__conversation_id}}."""

FIRST = (
    "Hi, this is Scarlett at Lifted Coffee Roasters. I can take a secure payment for an order. "
    "Try saying: I'd like to pay for order A1042."
)


def secret(name: str, value: str) -> str:
    r = requests.get(API + "/v1/convai/secrets", headers=H, timeout=30)
    r.raise_for_status()
    for s in r.json().get("secrets", []):
        if s.get("name") == name:
            requests.delete(API + f"/v1/convai/secrets/{s['secret_id']}", headers=H, timeout=30)
    r = requests.post(
        API + "/v1/convai/secrets",
        headers=H,
        json={"type": "new", "name": name, "value": value},
        timeout=30,
    )
    if r.status_code >= 300:
        sys.exit(f"secret create: {r.status_code} {r.text[:400]}")
    return r.json()["secret_id"]


def main() -> None:
    sid = secret("airlock-vault-inbound-key", env("VAULT_INBOUND_KEY"))
    hosts = [h for h in env("ALLOWED_HOSTS", "").split(",") if h]
    conf = {
        "name": env("AGENT_NAME", "Airlock demo"),
        "tags": ["airlock"],
        "conversation_config": {
            "agent": {
                "first_message": FIRST,
                "language": "en",
                "prompt": {
                    "prompt": PROMPT,
                    "llm": "custom-llm",
                    "custom_llm": {
                        "url": env("VAULT_INBOUND_URL"),
                        "model_id": "airlock",
                        "request_headers": {"BT-PROXY-KEY": {"secret_id": sid}},
                    },
                    "cascade_timeout_seconds": 15,
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": "",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            # Eleven v4 Turbo: the low-latency v4 model for live calls.
            "tts": {
                "voice_id": env("VOICE_ID"),
                "model_id": env("TTS_MODEL", "eleven_v4_turbo"),
            },
            # Latency masking on the platform side. The vault proxy buffers Airlock's streamed
            # reply, so Airlock cannot speak early during a charge; ElevenAgents can.
            # Static fillers only: use_llm_generated_message would hand recent conversation
            # context (which may hold keypad turns) to a model to write the filler.
            "turn": {
                "soft_timeout_config": {
                    "timeout_seconds": 2.0,
                    "message": "One moment.",
                    "additional_soft_timeout_messages": [
                        "Still working on that, thanks for waiting.",
                        "Almost there.",
                    ],
                    "use_llm_generated_message": False,
                    "randomize_fillers": False,
                    "max_soft_timeouts_per_generation": 3,
                    "disable_until_first_user_message": True,
                }
            },
            "conversation": {
                "max_duration_seconds": 300,
                "dtmf_input_settings": {
                    "hash_terminator": True,
                    "dtmf_input_timeout": 5.0,
                    "redact_input": True,
                },
            },
        },
        "platform_settings": {
            "privacy": {"record_voice": False},
            "auth": {
                "enable_auth": False,
                "allowlist": [{"hostname": h} for h in hosts],
                "require_origin_header": False,
            },
            "widget": {"text_input_enabled": True},
            "call_limits": {"agent_concurrency_limit": 3, "daily_limit": 150},
        },
    }
    existing = requests.get(
        API + "/v1/convai/agents",
        headers=H,
        params={"search": conf["name"]},
        timeout=30,
    ).json()
    match = next((a for a in existing.get("agents", []) if a["name"] == conf["name"]), None)
    if match:
        r = requests.patch(
            API + f"/v1/convai/agents/{match['agent_id']}",
            headers=H,
            json=conf,
            timeout=60,
        )
        agent_id = match["agent_id"]
    else:
        r = requests.post(API + "/v1/convai/agents/create", headers=H, json=conf, timeout=60)
        agent_id = r.json().get("agent_id") if r.status_code < 300 else None
    if r.status_code >= 300:
        sys.exit(f"agent: {r.status_code} {r.text[:800]}")
    print(json.dumps({"agent_id": agent_id}))


if __name__ == "__main__":
    main()
