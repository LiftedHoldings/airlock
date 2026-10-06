# PCI DSS scope

This is engineering documentation to help you and your assessor reason about scope. It is not
legal or compliance advice. **Software is not "PCI compliant"; deployments are assessed.**
Your Qualified Security Assessor (QSA), or your acquirer for a self-assessment, decides your
scope and which Self-Assessment Questionnaire (SAQ), if any, applies.

Related: [DESIGN.md](DESIGN.md#security-model) (invariants and how each is proven),
[TESTING.md](TESTING.md) (how to reproduce the evidence), [SECURITY.md](../SECURITY.md).

## Data flow

```
 Caller's phone / browser
     |  keypad tones or typed digits, speech
     v
 ElevenLabs Agents  (receives digits and audio; transcripts with redact_input for keypad entries)
     |  Custom LLM request: full conversation, keypad digits included
     v
 Vault inbound proxy + inbound.js  (Basis Theory)   <-- card data enters the vault here
     |  digits -> tokens; spoken card numbers cut; request HMAC-signed
     v
 Airlock  (tokens, last four, brand, amounts, order ids)
     |  model turns: cleaned history only            charge: token expressions only
     v                                               v
 Language model (Full mode only)        Vault outbound proxy + outbound.js  <-- detokenized here
                                                     |  card/bank fields, fixed destination
                                                     v
                                        Gateway (NMI or Authorize.net)
```

Account data (card number, expiry, security code; bank routing and account numbers for
eChecks) exists in clear only at the caller, inside ElevenLabs while it handles the turn,
inside the vault, and at the gateway.

## Scope by component

| Component | Sees account data? | Status | Notes |
|---|---|---|---|
| Caller and carrier / browser | yes | outside the merchant's control | Phone network or the caller's browser and network. |
| ElevenLabs Agents | yes, transiently | third-party service provider (TPSP); ElevenLabs states PCI DSS Level 1 | Receives keypad digits and speech, and forwards the full history on each Custom LLM request. With `redact_input`, stored transcripts show keypad entries redacted. Text typed in the web widget and card numbers said aloud are not redacted by `redact_input`. |
| Vault (Basis Theory) | yes | TPSP; Basis Theory states PCI DSS Level 1 | Stores tokens; runs the transforms. **The transforms (`vaults/basis_theory/*.js`) are merchant-authored code that runs on account data inside the vault.** |
| Airlock | no: tokens, last four, brand | merchant-operated; likely **security-impacting / connected-to** | Directs the payment flow, chooses when a token is charged, holds the outbound proxy key and a delete-only vault key. Cannot read a token (403, tested). |
| Language model (Full mode) | no | merchant's provider | Receives cleaned history only; no model is called during a capture. In desk mode, the main agent's ElevenLabs-hosted model receives whatever the caller says before the transfer, unfiltered. |
| Merchant order system (`ORDER_LOOKUP_URL`) | no | merchant | Supplies the amount; receives an order id only. |
| Gateway (NMI, Authorize.net) | yes | TPSP; Level 1 | Processes the transaction; optional card/account storage in its own vault. |

Check each provider's current Attestation of Compliance (AOC) yourself; do not rely on the
statements above.

### Why an assessor will likely treat Airlock as security-impacting

Airlock never receives account data, and its vault key cannot read or exfiltrate a token. But
it decides when a still-live token is sent to the gateway and for what amount, it holds the
key that invokes the outbound proxy, and it controls what the caller is told. A compromised
Airlock host could charge a live token to the merchant's own gateway account (capped by
`MAX_AMOUNT` in the transform) or mislead a caller. That is the profile of a system that can
impact the security of account data, so expect a reduced set of controls on the Airlock host,
not none: hardening, access control, patching, logging, change control.

Things that keep Airlock's footprint small, all in the code today:

* the edge signature: Airlock refuses any request that did not pass the inbound transform;
* the tripwire, history cleaner and output guard, all failing closed;
* allowlisted logging with no request bodies and no access log;
* a vault key holding `token:delete` only;
* no gateway credentials on the Airlock host;
* the vault management key kept off the Airlock host.

### Tokens left to expire

Tokens a capture uses are deleted after each attempt. Others are not tracked and expire on
the vault TTL (600 s as provisioned): rejected entries, a field's earlier token when the
caller redoes it, abandoned captures, keypad entries outside a capture, and duplicates when
ElevenLabs re-sends a keypad turn. Full list in [API.md](API.md#outcome-classification).
Factor this into your data-retention answers.

## Responsibility matrix (illustrative)

A high-level starting point for conversations with your providers and assessor. Each
provider's own responsibility matrix governs.

| PCI DSS requirement area | ElevenLabs | Vault (Basis Theory) | Gateway | Merchant (you) |
|---|---|---|---|---|
| 3. Protect stored account data | transcripts (keypad redaction via `redact_input`); audio off (`record_voice: false`) | token storage, encryption, expiry | gateway vault (saved cards/accounts) | no account data stored by Airlock; event log holds last four only; transform TTL setting |
| 4. Protect data in transmission | caller → platform → vault | vault → gateway over TLS | its endpoints | TLS on Airlock's endpoint (tokens only); TLS to the model and order system |
| 6. Secure systems and software | platform | vault platform and transform runtime | gateway | **the transforms (code on account data, in the vault)**, Airlock, provisioning; change control, code review, tests |
| 8. Identify users, authenticate access | platform accounts | management and application keys | gateway accounts | ElevenLabs, vault and gateway console accounts; who holds the management key; Airlock host access |
| 10. Log and monitor | platform | vault audit logs | gateway | Airlock event log, host logs, alerts on `tripwire`, `rejected`, `unknown` charges |
| 11. Test security regularly | platform | vault | gateway | Airlock host and its endpoint; re-run the live checks after changes |
| 12.8 Manage TPSPs | — | — | — | list of TPSPs, written agreements, **12.8.2 written acknowledgements**, annual AOC review, responsibility matrix |

## SAQ considerations for telephone payments (MOTO)

* SAQ A (revision 1, January 2025) can cover card-not-present merchants, including mail- and
  telephone-order merchants, whose account data functions are entirely outsourced to PCI DSS
  compliant third parties. Whether your deployment fits is your assessor's or acquirer's call.
* Arguments for: account data is captured by ElevenLabs, tokenized by the vault, and
  processed by the gateway, all third parties; the merchant's systems handle tokens only.
* Arguments that need an answer: the vault transforms are merchant code running on account
  data; Airlock directs the flow and can trigger charges; in desk mode the main agent's hosted
  model may hear a card number a caller says aloud; typed widget text is stored in
  transcripts. Expect questions on each.
* If SAQ A does not fit, a broader SAQ or a Report on Compliance may apply.
* eChecks are not card data under PCI DSS, but they are sensitive financial data under NACHA
  rules; see [ECHECK.md](ECHECK.md).

## What to hand an assessor

1. **A data-flow diagram** for your deployment (start from the one above), marking which mode
   you run (payment desk or Full Custom LLM).
2. **This document** and [DESIGN.md](DESIGN.md) (security model, keys and permissions).
3. **Test evidence:** `make prove` output, the live-test output
   ([TESTING.md](TESTING.md#live-tests)), and redacted call evidence (the public report at
   https://liftedholdings.com/airlock/ shows the format).
4. **The transform source** (`vaults/basis_theory/inbound.js`, `outbound.js`) and your change
   control for it: who can deploy it (the management key), how changes are reviewed and
   tested.
5. **AOCs** from ElevenLabs, the vault provider and the gateway.
6. **Requirement 12.8.2 written acknowledgements** from each of them: written statements that
   they are responsible for the security of the account data they handle for you. An AOC
   alone is not that acknowledgement.
7. **Your configuration**: agent settings (`redact_input`, `record_voice: false`, text input
   off), vault application permissions, `TEST_CARDS_ONLY` off in production, `MAX_AMOUNT`.

## Conservative defaults to keep

* Keep `record_voice: false` and `redact_input: true` on any agent that takes keypad input.
* Turn widget text input off in production.
* In payment desk mode the main agent's hosted model hears anything the caller says before
  the transfer. If that is not acceptable, use Full Custom LLM mode, where every turn passes
  the vault.
* Keep the management key off the Airlock host.
* Never test with real cards; keep `TEST_CARDS_ONLY=1` on any shared or public demo.
