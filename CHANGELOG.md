# Changelog

All notable changes to Airlock are recorded here.

## 0.1.0 — 2026-10-06 — initial public release

First public release.

* **OpenAI-compatible Custom LLM endpoint** for ElevenLabs Agents
  (`POST /v1/chat/completions`, streamed as server-sent events), reached only through a
  Basis Theory inbound proxy and verified by an HMAC edge signature.
* **Vault edge transform** (`vaults/basis_theory/inbound.js`): keypad entries become
  short-lived tokens with safe metadata (length, Luhn, brand, last four, expiry and ABA
  checks); re-sent keypad turns become `[[airlock:kp-prior]]`; card numbers said aloud are cut
  out, including spoken words and any Unicode digit script; optional test-cards-only mode for
  public demos; fails closed.
* **Outbound proxy transform** (`vaults/basis_theory/outbound.js`): builds NMI and
  Authorize.net requests inside the vault from token expressions, validates every field,
  refuses card data in any other field, and reduces the gateway reply to a normalized result.
* **Two deployment modes:** payment desk (an ElevenLabs-hosted main agent transfers to a
  scripted payment agent, which ends the call after payment) and Full Custom LLM (every turn
  through the vault, normal turns to any OpenAI-compatible model). A desk-only deployment
  needs no model configuration.
* **Card payments:** card number, expiry, security code, optional billing ZIP; NMI and
  Authorize.net; MOTO indicators.
* **eCheck (ACH) payments:** routing, account, checking/savings, and a NACHA TEL
  authorization read before the debit, naming the merchant (`AIRLOCK_MERCHANT_NAME`);
  configurable SEC code; account name from the caller or the merchant's order record.
* **Save to the gateway's vault:** NMI Customer Vault or Authorize.net customer profile.
* **Deterministic capture script** with caller-phrase handling (repeat, start over, wait, how
  much, is this safe, which card, where is the security code, different card, cancel, link,
  person, no keypad), strict confirmation, retry, silence and decline limits.
* **Safe outcomes:** approved, declined, duplicate, held, error, unknown; unclear outcomes
  never reported as "no charge" and never retried; one charge per order per conversation.
* **Safety layers in Airlock:** tripwire on every request, model-history cleaner, model
  output guard, tool-argument check; the language model is never called during a capture.
* **Least-privilege vault keys:** Airlock holds `token:delete` only; detokenization is limited
  to fixed-destination outbound proxies.
* **Latency masking:** static soft-timeout fillers on the agents, keep-warm pings answered
  inside the vault, outbound pre-warm at the confirm step.
* **Live event feed** (`GET /events/{conversation_id}`) and an allowlisted event log.
* **Provisioning scripts** for the vault, the Full Custom LLM agent and the payment-desk pair.
* **Container:** read-only, unprivileged, access log off.
* **Tests:** unit, property and Python/JavaScript parity tests (`make prove`), leak scan,
  end-to-end driver with a local vault emulator, and live checks against test tenants and
  sandboxes.
* **Documentation:** quickstart, configuration, API, operations, PCI scope, testing, design,
  eCheck, and a proposal for native secure keypad capture in ElevenLabs.
