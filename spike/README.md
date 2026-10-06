# Spike: does `transfer_to_agent` isolate a payment sub-agent?

The first design moved card capture into a dedicated payment agent, so only those turns
would cross the vault. This spike tested that assumption against live ElevenLabs agents.

* `recorder.py` is a scripted Custom LLM that records every request ElevenLabs sends.
* `transfer_history.py` creates two throwaway agents joined by `transfer_to_agent`, holds a
  text conversation that types a test card into the payment agent, then reports whether the
  main agent's model received the digits after the hand-back.
* `hosted_leak.py` is the follow-up for the payment-desk design: the main agent runs on an
  ElevenLabs-hosted model and the payment agent (the recorder) hands back after digits
  arrive; the caller then asks the main agent to read the number back.
* `inspect_requests.py` prints the shape of the recorded requests.
* `cleanup.py` deletes the throwaway agents (`airlock-spike-*`).

**Result (2026-10-06): no, it does not isolate.** ElevenLabs re-sends the full conversation, digits included,
to whichever agent's Custom LLM handles the next turn. Isolation by sub-agent does not work.
Airlock therefore either routes every model request through the vault (Full Custom LLM mode)
or runs a payment desk that never hands back to an AI agent (see
[docs/DESIGN.md](../docs/DESIGN.md#two-deployment-modes)).

Other facts recorded from the real requests: body fields are `messages`, `model`,
`max_tokens` (8192), `stream`, `stream_options`, `temperature` (0.0) and `tools`; there is no
conversation-id field and tool calls are not kept in the history; `{{system__conversation_id}}`
in the agent prompt arrives filled in (`conv_…`); `transfer_to_agent` takes
`{"agent_number": <int>}`.

Run: `python recorder.py 8911`, expose it publicly (e.g. `ngrok http 8911`), then
`ELEVENLABS_API_KEY=... python transfer_history.py https://<public-url>` (or
`hosted_leak.py`). Only the public test card `4111111111111111` is ever typed. Recordings go
to `spike/out/`, which is not committed.
