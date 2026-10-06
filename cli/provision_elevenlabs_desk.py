"""Create (or update) the PAYMENT DESK pair of ElevenLabs agents.

* Main agent: runs on an ElevenLabs-hosted language model (no Custom LLM). Handles the
  conversation and, when the caller is ready to pay, transfers to the payment desk.
* Payment desk agent: Custom LLM = the vault's inbound proxy, header X-Airlock-Mode: desk.
  Airlock scripts every word (no language model), charges through the vault, then ends the
  call. It never transfers back to an AI agent: ElevenLabs re-sends the full history, typed
  digits included, to whichever agent speaks next.

Environment: ELEVENLABS_API_KEY, VAULT_INBOUND_URL, VAULT_INBOUND_KEY, VOICE_ID,
MAIN_LLM (default gemini-2.5-flash), ALLOWED_HOSTS, NAME_PREFIX.
Prints {"main_agent_id": ..., "desk_agent_id": ...}.
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
PREFIX = env("NAME_PREFIX", "Airlock demo")
HOSTS = [x for x in env("ALLOWED_HOSTS", "").split(",") if x]
TTS = {"voice_id": env("VOICE_ID"), "model_id": env("TTS_MODEL", "eleven_v4_turbo")}
SOFT = {
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
}
PLATFORM = {
    "privacy": {"record_voice": False},
    "auth": {
        "enable_auth": False,
        "allowlist": [{"hostname": h} for h in HOSTS],
        # Not required: voice (WebRTC) sessions dropped when it was on. The demo's real safety
        # net is the vault edge's test-cards-only rule plus the call limits below.
        "require_origin_header": False,
    },
    "widget": {"text_input_enabled": True},
    "call_limits": {"agent_concurrency_limit": 3, "daily_limit": 150},
}
END_CALL = {
    "name": "end_call",
    "description": "",
    "params": {"system_tool_type": "end_call"},
}


def secret(name: str, value: str) -> str:
    for s in (
        requests.get(API + "/v1/convai/secrets", headers=H, timeout=30).json().get("secrets", [])
    ):
        if s.get("name") == name:
            return s["secret_id"]
    r = requests.post(
        API + "/v1/convai/secrets",
        headers=H,
        json={"type": "new", "name": name, "value": value},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["secret_id"]


def upsert(conf: dict) -> str:
    found = requests.get(
        API + "/v1/convai/agents",
        headers=H,
        params={"search": conf["name"]},
        timeout=30,
    ).json()
    match = next((a for a in found.get("agents", []) if a["name"] == conf["name"]), None)
    if match:
        r = requests.patch(
            API + f"/v1/convai/agents/{match['agent_id']}",
            headers=H,
            json=conf,
            timeout=60,
        )
        aid = match["agent_id"]
    else:
        r = requests.post(API + "/v1/convai/agents/create", headers=H, json=conf, timeout=60)
        aid = r.json().get("agent_id") if r.status_code < 300 else None
    if r.status_code >= 300:
        sys.exit(f"{conf['name']}: {r.status_code} {r.text[:600]}")
    return aid


def main() -> None:
    sid = secret("airlock-vault-inbound-key", env("VAULT_INBOUND_KEY"))
    desk = upsert(
        {
            "name": f"{PREFIX} - payment desk",
            "tags": ["airlock"],
            "conversation_config": {
                "agent": {
                    "first_message": "You're on the secure payment line. Say ready when you have your card or bank details.",
                    "language": "en",
                    "prompt": {
                        "prompt": "Secure payment desk (scripted by Airlock). Conversation id: {{system__conversation_id}}.",
                        "llm": "custom-llm",
                        "custom_llm": {
                            "url": env("VAULT_INBOUND_URL"),
                            "model_id": "airlock-desk",
                            "request_headers": {
                                "BT-PROXY-KEY": {"secret_id": sid},
                                "X-Airlock-Mode": "desk",
                            },
                        },
                        "cascade_timeout_seconds": 15,
                        "built_in_tools": {"end_call": END_CALL},
                    },
                },
                "tts": TTS,
                "turn": SOFT,
                "conversation": {
                    "max_duration_seconds": 300,
                    "dtmf_input_settings": {
                        "hash_terminator": True,
                        "dtmf_input_timeout": 5.0,
                        "redact_input": True,
                    },
                },
            },
            "platform_settings": PLATFORM,
        }
    )
    main_id = upsert(
        {
            "name": f"{PREFIX} - hosted model + payment desk",
            "tags": ["airlock"],
            "conversation_config": {
                "agent": {
                    "first_message": (
                        "Hi, this is Scarlett at Lifted Coffee Roasters. I can help with your order, and take a "
                        "secure payment. Try saying: I'd like to pay for order A1042."
                    ),
                    "language": "en",
                    "prompt": {
                        "prompt": (
                            "You are Scarlett, the phone assistant for Lifted Coffee Roasters, a demo store. "
                            "Demo orders: A1042 is a bag of house espresso plus shipping, 84 dollars and 20 cents; "
                            "A2001 is a 12 dollar gift card; A3003 is a 250 dollar grinder; A0050 is a 50 cent "
                            "decline test. Answer questions in one or two short sentences. When the caller is ready "
                            "to pay, make sure you know the order number, tell them you are connecting them to the "
                            "secure payment line and that the call will end once payment is complete, then transfer "
                            "to the payment desk. Never ask for card or bank numbers yourself."
                        ),
                        "llm": env("MAIN_LLM", "gemini-2.5-flash"),
                        "built_in_tools": {
                            "transfer_to_agent": {
                                "name": "transfer_to_agent",
                                "description": "",
                                "params": {
                                    "system_tool_type": "transfer_to_agent",
                                    "transfers": [
                                        {
                                            "agent_id": desk,
                                            "condition": "The caller is ready to pay and the order number is known.",
                                            "transfer_message": "Connecting you to our secure payment line.",
                                            "enable_transferred_agent_first_message": True,
                                        }
                                    ],
                                },
                            },
                            "end_call": END_CALL,
                        },
                    },
                },
                "tts": TTS,
                "turn": SOFT,
                "conversation": {"max_duration_seconds": 300},
            },
            "platform_settings": PLATFORM,
        }
    )
    print(json.dumps({"main_agent_id": main_id, "desk_agent_id": desk}))


if __name__ == "__main__":
    main()
