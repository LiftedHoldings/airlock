# Proposal: secure keypad capture in ElevenLabs Agents

Airlock works today, but it needs a PCI Level 1 vault proxy on every model request (from
about $995/month) because the platform hands keypad digits to the model. One platform setting
would make the vault, the proxy and the middleware unnecessary.

## The setting

```json
{
  "conversation_config": {
    "conversation": {
      "dtmf_input_settings": {
        "dtmf_input_timeout": 5.0,
        "hash_terminator": true,
        "redact_input": true,
        "secure_capture": {
          "enabled": true,
          "armed_by_tool": "collect_card_payment",
          "fields": ["card_number", "expiration", "security_code"],
          "model_view": "brand_and_last_four"
        }
      }
    }
  }
}
```

## Behaviour

1. The model calls a payment tool. That call arms secure capture for the listed fields.
2. While armed, keypad entries are held in memory as secret values. No user turn is created
   for the model.
3. The platform speaks the configured prompt for each field, in the agent's voice.
4. When every field is in, the platform calls the tool's webhook with the secret values
   substituted server-side, the way `secret__` dynamic variables already fill tool headers.
5. The model receives only the tool result and the masked view: approved or declined, brand,
   last four.
6. The secret values are dropped when the webhook returns. They are never written to
   transcripts, logs, analysis, or post-call webhooks.

Because the webhook can post straight to NMI, Authorize.net or another gateway, the
gateway's own vault (NMI Customer Vault, Authorize.net customer profiles) becomes the only
token store. eCheck works the same way with `routing_number` and `account_number` fields.

## A second, smaller ask: transfers without keypad turns

Today `transfer_to_agent` carries the full conversation, typed digits included, to the next
agent, so a payment agent cannot safely hand back to an agent running on a hosted model. An
option to omit (or redact in the live context) keypad turns on transfer would let a merchant
run payments on a dedicated agent and return the caller to the main agent, with no vault on
the main conversation:

```json
{ "system_tool_type": "transfer_to_agent",
  "transfers": [{ "agent_id": "...", "condition": "...", "context": { "exclude_dtmf_turns": true } }] }
```

## Why it is small

| Needed | Already in ElevenAgents |
|---|---|
| Buffer keypad digits until # or a timeout | DTMF input |
| Know a turn came from the keypad | `dtmf` source medium |
| Keep a value away from the model | `secret__` dynamic variables |
| Call an external API with stored credentials | webhook tools + secrets manager |
| Redact the entry in stored history | `redact_input` |
| Route buffered digits to a tool instead of a model turn | **missing: this is the feature** |

## Evidence that it is needed

Measured against live agents on 2026-10-06 (see the [report page](https://liftedholdings.com/airlock/)
and [spike/README.md](../spike/README.md)):

* Keypad digits reach the Custom LLM as a plain user message.
* The full history, digits included, is re-sent on every later turn, including after
  `transfer_to_agent`, so isolating payment in a sub-agent does not help.
* Digits typed in the web widget's text box are stored in the transcript in plain text.

## Test suite to hold it to

Airlock's canary approach carries over to a native implementation: send canary test cards
through a live agent (as `tests/live/live_agent_test.py` does) and run the leak scan
(`tests/e2e/leak_scan.py`) over every model request, transcript, log and webhook body to
assert that none contains them. See [TESTING.md](TESTING.md).
