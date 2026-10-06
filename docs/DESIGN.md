# Airlock design

Airlock lets an ElevenLabs Agents voice agent take a card or eCheck payment on a live call
while the card or bank numbers never reach the language model or the merchant's servers.
This document reflects what was built and tested on 2026-10-06; every "verified" fact was
observed against the real service. For setup and reference see the
[README](../README.md#documentation) index; for PCI scope see [PCI.md](PCI.md).

## The problem, measured

* **Keypad digits are a model turn.** With keypad (DTMF) input enabled, digits arrive at the
  agent's language model as an ordinary user message. `redact_input` cleans stored
  transcripts only; ElevenLabs' docs say it does not hide digits from the agent during the
  live call. *(docs)*
* **They are re-sent on every turn.** ElevenLabs sends the whole conversation to the Custom
  LLM on each request. A spike with two agents joined by `transfer_to_agent` showed the
  payment agent's digits arriving at the main agent's model after the hand-back. A payment
  sub-agent isolates nothing. *(verified, [spike/transfer_history.py](../spike/README.md))*
* **Typed text is stored.** Digits typed into the web widget's text box are stored in the
  conversation transcript in plain text; `redact_input` applies to keypad entries. *(verified)*
* **No native secure capture** existed in the ElevenLabs changelog through 2026-09-28; the
  Stripe integration sends a payment link. *(docs)*

## Two deployment modes

| | **Payment desk** (recommended) | **Full Custom LLM** |
|---|---|---|
| Conversation model | ElevenLabs-hosted (the main agent is a normal ElevenAgents agent) | the merchant's own, behind Airlock |
| What passes the vault | only the payment agent's turns | every turn |
| Language model during payment | none: Airlock scripts every word | none: Airlock scripts every word |
| After payment | the call ends | conversation continues |
| Latency added to normal turns | none | the vault hop (~0.65 s median) |
| Agents | main agent → `transfer_to_agent` → payment desk agent (`X-Airlock-Mode: desk`) | one agent |

**Why the payment desk must not hand back.** ElevenLabs re-sends the whole conversation,
typed digits included, to whichever agent speaks next (verified for transfers between Custom
LLM agents). The main agent's hosted model is not behind the vault, so a hand-back would put
the digits in front of it; `redact_input` only cleans stored transcripts, and
`transfer_to_agent` has no option to drop history. The desk ends the call with `end_call`
(verified live: payment approved, "Thanks for calling. Goodbye.", connection closed). This
includes a caller who asks for a person during the desk's capture: the desk says no payment
was taken and ends the call. A phone deployment could extend it to transfer to a person, or
to the main agent's own number (which starts a fresh conversation); that is not implemented.

On the desk, Airlock finds the order id in the conversation (or asks for it), asks for the
account-holder name for eChecks, and handles a card number typed before it was prompted.
Provisioning: `cli/provision_elevenlabs_desk.py`.

## The fix (both modes)

Every Custom LLM request passes through a PCI DSS Level 1 vault's inbound proxy, whose
transform runs on every turn:

1. The newest user turn, if it is a keypad entry of 3+ characters, becomes a vault token
   (10-minute expiry as provisioned, matching the session). The message is replaced by a
   placeholder carrying safe metadata only:
   `[[airlock:kp id=<token> len=16 luhn=1 last4=1111 brand=visa]]`. 4- and 6-digit entries
   carry `expok`, 9-digit entries carry the ABA checksum result `aba`, 8+ digit entries carry
   `last4`. 3–7 digit entries never expose digits (they may be a security code). Full format:
   [API.md](API.md#placeholders-inbound-transform-output).
2. Earlier keypad turns (re-sent history) become `[[airlock:kp-prior]]`; no new token.
3. A card number said aloud anywhere is cut out of the sentence (`[[airlock:spoken]]`) and
   the rest of the sentence survives ("pay order A1042, my card is …").
4. In public-demo mode (`TEST_CARDS_ONLY=1`) a Luhn-valid number that is not a published
   sandbox test card is dropped and never tokenized.
5. The forwarded body is HMAC-signed (`X-Airlock-Edge-Ts`, `X-Airlock-Edge-Sig`); Airlock
   rejects anything unsigned with 401.
6. On any error the transform answers the caller itself and forwards nothing.
7. A `{"airlock_ping": true}` body is answered inside the vault (keep-warm).

Airlock (this repo) is the Custom LLM endpoint behind the proxy:

* **Tripwire:** any card-like number in a received body → refused, alert.
* **Model turns:** history is cleaned (keypad turns and placeholders become fixed markers,
  any card-like number refuses the call), Airlock's payment rules are added, the merchant's
  model is called with `start_payment` added to its tools, and every streamed token passes an
  output guard.
* **Capture turns:** once `start_payment(order_id, method)` is called, the model is not
  called again until the capture ends. A deterministic script handles every caller turn.
* **Charge:** Airlock posts token expressions (`"pan": "{{ <token> }}"`) to the gateway's
  outbound proxy. The proxy's transform detokenizes, validates every field, builds the NMI or
  Authorize.net body itself (Airlock never builds a body with card data), and reduces the
  reply to a normalized result. Tokens are deleted after the attempt.
* **Hand-back:** the model then sees one line, e.g. `start_payment result: approved, Visa
  ending 1111, amount 84.20 USD, reference ending 4471.`

## Caller handling

The capture script handles: keypad entry (card, expiry, security code, ZIP, routing, account),
`*` to redo a field, menu digits, 0 for a person, spoken yes/no, checking/savings, and
spoken intents: repeat, start over, wait/hold on, how much, is this safe, which card, where is
the security code, I already typed it, different card, cancel, send me a link, speak to a
person, no keypad. Two silences are re-prompted and a third cancels; three invalid entries
for a field cancel; two declines stop. Gateway outcomes: approved, declined (offer a second card), duplicate ("that
payment already went through"), held for review, unknown (never retried; flagged), error.

## Keys and permissions (verified on a live tenant)

| Component | Holds | Can | Cannot |
|---|---|---|---|
| ElevenLabs agent | inbound proxy key, as a workspace secret sent in `request_headers` | call the inbound proxy | reach Airlock without the vault's signature |
| Inbound transform | application `airlock-edge`: `token:create` | create tokens | read tokens (403) |
| Outbound proxies | application `airlock-outbound`: `token:use`, attached to the fixed-destination proxies only | detokenize into the gateway request | be reached at any other URL |
| Airlock | service key `airlock-service`: **`token:delete` only**; outbound proxy keys | invoke the fixed-destination outbound proxy; delete tokens | read a token (403); use a token in an ephemeral proxy (403); choose a destination (`BT-PROXY-URL` is ignored) |
| Outbound transform | gateway credentials in proxy configuration | put detokenized values into the gateway's card or bank fields | send card data in any other field (400) |

**Why `token:use` is not on Airlock's key.** In Basis Theory, a key holding `token:use` can
also drive an *ephemeral* proxy to any URL, which would let anyone holding that key send a
live token's value anywhere. So detokenization lives on a separate application attached only
to the fixed-destination outbound proxies, and Airlock's key holds `token:delete` only. Tested
on a live tenant: an ephemeral proxy call with Airlock's key returns
`403 Missing permission: token:use or proxy:invoke`. A compromised Airlock host can at worst
charge a still-live token to the merchant's own gateway account, capped by `MAX_AMOUNT` in the
transform.

The management key that edits proxies and transforms stays with the operator, never on the
Airlock host; otherwise a compromised host could rewrite the transform.

## Why a middleware at all (and the vault-only alternative)

Airlock does the jobs a vault transform cannot or should not:

1. **State across turns.** Transforms are stateless; Airlock remembers the step, token ids,
   attempts and which orders are settled (the double-charge guard).
2. **The amount comes from the merchant**, never from the model or the caller.
3. **Model routing.** Normal turns stream to the model; `start_payment` is intercepted.
4. **Small card-environment code.** Two transform files of about 260 lines each run inside
   the vault; all business logic runs outside it on tokens only, which keeps the assessed
   surface small.
5. **Observability** without card data (event feed, allowlisted logs).

*Option B, vault-only:* point the inbound proxy straight at the model provider and run the
whole capture inside the transform (transforms can answer requests themselves and call the
gateway from inside the vault). It removes the merchant container but moves all business
logic into card-environment code, rebuilds state from the re-sent history on every turn, is
bounded by the 10–30 s transform limit, and buffers every conversational turn. It is a valid
design for a small merchant; Airlock chooses the smaller assessed surface.

When ElevenLabs ships native secure capture ([SPEC-native.md](SPEC-native.md)), jobs 1–4 move into the
platform and the middleware disappears.

## Latency masking

* **Charge (~4–5 s) and cold starts:** the vault proxy buffers Airlock's streamed reply, so a
  filler from Airlock is not heard early. The agent's own **soft timeout** covers it: after
  2 s with no reply ElevenAgents speaks "One moment.", then "Still working on that, thanks for
  waiting.", then "Almost there." Static text only: `use_llm_generated_message` would hand
  recent conversation context, which may contain keypad turns, to a model to write the filler.
* **Cold runtimes:** Airlock pings the inbound and outbound proxies every 4 minutes with a body
  the transforms answer themselves (never forwarded), and pre-warms the outbound proxy when a
  caller reaches the confirm step.

## Hardening

"Test" names the test in `tests/test_hardening.py` that covers the behaviour; "code" means it
is enforced in code without a dedicated test.

| Behaviour | How | Test |
|---|---|---|
| Card numbers are detected with commas, slashes, dashes or dot-space separators, as spoken words, and in any Unicode digit script | identical detectors in Python and the vault transform (parity-tested); all decimal digits normalised to ASCII first | `test_separated_and_unicode_cards_are_caught_everywhere`, `test_vault_edge_agrees_on_separated_and_unicode_cards` |
| A charge always settles, even if the call drops mid-charge | the charge and its bookkeeping run in a shielded task; a stalled charge settles as `unknown` | `test_charge_settles_even_if_the_request_is_cancelled` |
| One order cannot be charged twice in a call | per-conversation settled-orders record (approved / unknown / held / duplicate block re-opening) | `test_settled_order_cannot_be_reopened` |
| Unclear gateway outcomes are never reported as "no charge" | only a provable pre-gateway rejection is `error`; everything else is `unknown` | `test_charge_reply_classification`, `test_unrecognised_status_is_unknown_not_no_charge` |
| Amounts only ever come from the merchant | payments are refused unless `ORDER_LOOKUP_URL` is set (or `AIRLOCK_DEMO=1` for the sandbox catalogue) | `test_no_lookup_and_no_demo_means_no_payment` |
| The edge signature cannot be forged | required settings may not be empty; the edge secret is at least 32 bytes | `test_empty_or_short_edge_secret_refuses_to_start` |
| A hedged answer never confirms a payment | confirm needs keypad 1 or a whole-utterance yes; any negation re-asks | `test_hedged_yes_at_confirm_does_not_charge` |
| Slow callers are not cut off | tokens live 10 minutes as provisioned, matching the session, and are deleted after each attempt | code |
| Callers never share a capture | no capture without ElevenLabs' `conv_` id | `test_no_conversation_id_no_capture` |
| Long numeric order ids and short account numbers work | card-data scan on every non-card field; neutral wording when no last four exists | code |
| One client cannot exhaust the live event feed | per-conversation subscriber cap and a global ceiling | code |
| Order-lookup failures fail closed | "couldn't find that order", never a broken stream | code |
| Platform tool calls cannot carry card data | refused if any argument is card-like | code |

## Security model

| # | Invariant | Enforced by | Proven by |
|---|---|---|---|
| 1 | No card digits arrive at Airlock | inbound transform | production capture of every received body: placeholders only; leak scan 0 hits |
| 2 | If they did, Airlock refuses them | tripwire + edge signature | unit tests; unsigned POST → 401 |
| 3 | Nothing card-like reaches the model | cleaner + output guard; model not called during capture | 240 generated cards × 6 formats; Python/JS parity |
| 4 | Airlock cannot read a token | vault permissions | live tenant: GET with Airlock's key → 403 |
| 5 | Card data leaves the vault only to the gateway | fixed-destination outbound proxy; transform builds the body | redirect header ignored; steering into zip/memo → 400 |
| 6 | Security code gone after authorization | token delete after each attempt; 10-minute expiry (some tokens, e.g. rejected entries and abandoned captures, are not tracked and expire on the TTL; see API.md) | delete → 204; charge with deleted token → 400 |
| 7 | Capture is deterministic | state machine; amount from the merchant system | replay test |
| 8 | Every component fails closed | error paths in both transforms and Airlock | unit tests; transform short-circuit |
| 9 | Logs and state hold no card data | allowlisted event log; ISO timestamps | leak scan of every log and capture |

## Measured latency

* Keypad turn, driver → vault → Airlock (DigitalOcean) → back: median 648 ms (13 samples).
  Airlock's own handling is under 20 ms.
* A charge: about 4–5 s end to end through the outbound proxy.
* The vault proxy **buffers** streamed replies: time-to-first-byte equalled total time, so a
  "one moment" filler is not heard early.
* A cold transform runtime added about 10.8 s to the first request. Keep it warm.
* ElevenLabs' `cascade_timeout_seconds` defaults to 4 s (2–15); set 15. If ElevenLabs retries
  during a charge, Airlock answers "still working" and never charges twice.

## Gateways

**NMI** (`/api/transact.php`, form-encoded): `type=sale`, `ccnumber`, `ccexp` (MMYY), `cvv`,
`zip`, `industry=moto`, `customer_vault=add_customer` to save; eCheck `payment=check`,
`checkname`, `checkaba`, `checkaccount`, `account_type`, `sec_code=TEL`. Reply is a query
string; `response` 1/2/3. The gateway refuses an identical card + amount inside its duplicate
window (observed "Duplicate transaction REFID…"), regardless of order id.

**Authorize.net** (`createTransactionRequest`, JSON validated against an XML schema, element
order matters; replies start with a UTF-8 byte-order mark): `creditCard` or `bankAccount`,
`profile.createProfile` + `customer.id` to save, `retail.marketType=1` (MOTO). Sandbox eCheck
rejects `echeckType=TEL` (error 246) until enabled on the merchant account.

See [ECHECK.md](ECHECK.md) for the eCheck flow and NACHA TEL authorization.

## PCI scope

* SAQ A (r1, January 2025) can cover mail- and telephone-order merchants whose account data
  handling is entirely outsourced to PCI DSS compliant third parties.
* Airlock directs the payment flow, so an assessor will likely class it as
  security-impacting; expect a reduced control set, not none.
* The transforms are merchant-authored code that runs on card data inside the vault: keep
  them small, tested and change-controlled.
* Requirement 12.8.2 needs written acknowledgement from each provider; an AOC alone is not it.
* Software is not "PCI compliant"; deployments are assessed. Not legal advice.
* Full analysis, responsibility matrix and assessor checklist: [PCI.md](PCI.md).

## Cost

| Item | Price |
|---|---|
| Airlock | free, Apache-2.0 |
| Basis Theory | $995/month incl. 20,000 tokens, $0.05/token after; test tenants free |
| Evervault | $995/month plus usage |
| VGS | from $1,000/month |
| Twilio Pay Connectors (comparison) | from $0.15/transaction; the caller leaves the agent |

Prices as listed by the vendors at the time of writing; check current pricing. A card payment
creates four tokens with the ZIP step (three without), an eCheck two, and every re-entered
field one more, so the base plan covers roughly 5,000 card payments a month.

## The better fix

A native ElevenLabs setting would make the vault unnecessary: [SPEC-native.md](SPEC-native.md).
