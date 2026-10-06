# Airlock

**Card and eCheck payments for ElevenLabs Agents, with the card numbers kept away from the
language model and from the merchant's servers.**

Airlock puts a PCI DSS Level 1 tokenizing vault (Basis Theory) on the one hop ElevenLabs
Agents already lets you control: the **Custom LLM** URL. Every request on that hop passes
through the vault's inbound proxy, which turns keypad entries into short-lived tokens and
strips card numbers said aloud. Airlock, a small open-source service, receives tokens only. It
runs a scripted payment capture with no language model, then charges **NMI** or
**Authorize.net** through the vault's fixed-destination outbound proxy. The card data leaves
the vault only toward the gateway.

Live report and demo: **https://liftedholdings.com/airlock/**

## The problem

* With keypad (DTMF) input on, ElevenLabs Agents delivers the digits a caller types to the
  agent's language model as an ordinary user message.
* ElevenLabs re-sends the whole conversation history on every turn, so those digits go to the
  model again and again, and to whichever agent speaks next after a `transfer_to_agent`.
* `redact_input` cleans the **stored** transcript only. It does not hide the digits from the
  model during the call.

So a voice agent that asks for a card on the keypad hands the card number to a language
model. Airlock stops that without changing the platform.

## Two deployment modes

### Payment desk (recommended)

The main agent is a normal ElevenLabs agent on an ElevenLabs-hosted model. When the caller is
ready to pay, it transfers to a payment-desk agent. Only the desk's turns go through the vault.
Airlock scripts the whole payment with no language model, then ends the call. It never hands
back to an AI agent, because ElevenLabs would re-send the typed digits to it.

```
                  ElevenLabs Agents
 Caller ----> [ main agent, hosted model ] --transfer_to_agent--> [ payment desk agent ]
                                                                         |
                                     Custom LLM request, header X-Airlock-Mode: desk
                                                                         v
                                            Vault inbound proxy (inbound.js)
                                            keypad digits -> tokens, request signed
                                                                         |
                                                                         v
                                            Airlock: scripted capture, no model
                                                                         |  token expressions
                                                                         v
                                            Vault outbound proxy (outbound.js)
                                            tokens -> card fields, fixed destination
                                                                         |
                                                                         v
                                                          NMI or Authorize.net
                                            ...then Airlock ends the call (end_call)
```

### Full Custom LLM

One agent. Every turn goes through the vault and Airlock. Normal turns go to the merchant's
own model (any OpenAI-compatible endpoint) with a cleaned history; payment turns are scripted
by Airlock. The conversation continues after payment.

```
 Caller ----> ElevenLabs agent (Custom LLM = vault inbound proxy)
                     |
                     v
              Vault inbound proxy (inbound.js): digits -> tokens, spoken cards cut, signed
                     |
                     v
              Airlock --+--> normal turn: cleaned history + start_payment tool --> your model
                        |
                        +--> payment turn: scripted capture (no model)
                                   |
                                   v
                             Vault outbound proxy (outbound.js) --> NMI or Authorize.net
```

| | Payment desk | Full Custom LLM |
|---|---|---|
| Conversation model | ElevenLabs-hosted | yours, behind Airlock |
| Turns through the vault | only the payment desk's | every turn |
| Model during payment | none (scripted) | none (scripted) |
| After payment | the call ends | the conversation continues |
| Latency added to normal turns | none | one vault hop (median ~0.65 s measured) |
| Model key needed | no | yes, any OpenAI-compatible endpoint |
| A card number said aloud before payment | reaches the hosted model of the main agent | cut out at the vault before any model |
| Provisioning | `cli/provision_elevenlabs_desk.py` | `cli/provision_elevenlabs_agent.py` |

## What is proven

Everything below was run against real services on 2026-10-06 (sandbox gateways and a test
vault tenant, test cards only). The redacted request/response evidence is in
[site/data/evidence.json](site/data/evidence.json) and on the
[report page](https://liftedholdings.com/airlock/).

| Claim | How it was tested |
|---|---|
| No card digits reach Airlock | Production capture of every body Airlock received: token placeholders only; leak scan 0 hits |
| Unsigned requests are refused | Direct POST without the vault's HMAC signature → 401 |
| Nothing card-like reaches the model | 240 generated cards in 6 formats incl. spoken words; Python/JS detector parity |
| Airlock cannot read a token | Live Basis Theory tenant: `GET /tokens/{id}` with Airlock's key → 403 |
| Airlock cannot exfiltrate a token | Ephemeral proxy to an arbitrary URL with Airlock's key → 403 (its key holds `token:delete` only) |
| Card data leaves the vault only to the gateway | Redirect via `BT-PROXY-URL` ignored (reply came from NMI); card data steered into other fields → 400 |
| Tokens are gone after the attempt | Delete → 204; charging a deleted token → 400 |
| Cards: approve, save, decline, second card | NMI and Authorize.net sandboxes through the real vault |
| eChecks (ACH, SEC code TEL) | NMI sandbox through the live ElevenAgents agent |
| Hostile callers | Card read aloud, prompt injection, "read me the number": the model never had it |

Run the offline proofs with `make prove` (140 tests, Python/JS detector parity, leak scan).
The live checks are in [tests/live/](docs/TESTING.md#live-tests).

## Features

* **Cards and eChecks.** Card number, expiry, security code (4 digits for Amex) and optional
  billing ZIP; or routing number (ABA checksum checked in the vault), account number and
  checking/savings. eChecks read a NACHA TEL authorization (amount, date, account, how to
  revoke) before the debit. See [docs/ECHECK.md](docs/ECHECK.md).
* **Save to the gateway's own vault.** An optional "save for next time" step adds the card or
  account to NMI Customer Vault or an Authorize.net customer profile. Airlock stores nothing.
* **Caller handling.** Star to redo a field, 0 for a person (Full mode hands back to the
  model; the payment desk ends the call), spoken yes/no, "repeat that",
  "start over", "hold on", "how much is it", "is this safe", "which card", "where is the
  security code", "I already typed it", "use a different card", "cancel", "send me a link",
  "I have no keypad". A hedged "yeah, no" never confirms a charge. Retries and declines are
  capped.
* **Safe outcomes.** Approved, declined (offer a second card), duplicate, held for review,
  error, unknown. An unclear outcome is never reported as "no charge" and is never retried.
  One order cannot be charged twice in one call.
* **Latency masking.** ElevenAgents soft-timeout fillers ("One moment.") cover the charge and
  any cold start; Airlock keeps the vault transforms warm with pings they answer themselves.
* **Live event feed.** `GET /events/{conversation_id}` streams allowlisted, card-free events
  for one call; the public report page draws the flow from it.
* **Fail closed everywhere.** Both vault transforms, Airlock's tripwire, the history cleaner
  and the model output guard refuse rather than partially redact.

## Quickstart

```bash
pip install -r requirements-dev.txt
export AIRLOCK_EDGE_SECRET=$(openssl rand -hex 32)
export BT_MANAGEMENT_KEY=... AIRLOCK_URL=https://airlock.example.com/v1 NMI_SECURITY_KEY=...
python cli/provision_basis_theory.py > vault.json        # treat vault.json as a secret
cp airlock.env.example airlock.env                       # fill in from vault.json
docker compose up -d --build                             # then put HTTPS in front
export ELEVENLABS_API_KEY=... VOICE_ID=... VAULT_INBOUND_KEY=<inbound_proxy_key>
export VAULT_INBOUND_URL=https://api.test.basistheory.com/proxy
python cli/provision_elevenlabs_desk.py                  # payment desk mode
```

The full walk-through, with test cards and a scripted test call, is
[docs/QUICKSTART.md](docs/QUICKSTART.md).

## Documentation

| Document | What it covers |
|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Zero to a working sandbox payment call |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable, vault setting and ElevenAgents setting |
| [docs/API.md](docs/API.md) | Airlock's HTTP surface, placeholders, vault request/response formats, events |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploying, logs, keep-warm, key rotation, scaling, troubleshooting |
| [docs/PCI.md](docs/PCI.md) | Scope analysis and what to hand an assessor |
| [docs/TESTING.md](docs/TESTING.md) | Offline proofs, the end-to-end driver, live tests |
| [docs/DESIGN.md](docs/DESIGN.md) | Why it is built this way; security model; measured latency |
| [docs/ECHECK.md](docs/ECHECK.md) | eCheck (ACH) flow and NACHA TEL authorization |
| [docs/SPEC-native.md](docs/SPEC-native.md) | Proposal for native secure keypad capture in ElevenLabs |
| [spike/README.md](spike/README.md) | The experiment that showed digits are re-sent across transfers |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and pull request checklist |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Repository

```
airlock/              FastAPI service: OpenAI-compatible /v1/chat/completions (SSE)
  app.py                edge-signature check, desk and model turns, vault calls, event feed
  core/cards.py         card/ABA primitives, spoken-digit normalisation, card-like detection
  core/sanitizer.py     tripwire, model-history cleaner, output guard (all fail closed)
  core/script.py        deterministic capture state machine (card + eCheck)
  orders.py             amount lookup (merchant endpoint, or the demo catalogue)
  sessions.py           in-memory capture sessions with a TTL
vaults/basis_theory/
  inbound.js            runs in the vault on every request: digits -> tokens, signs the request
  outbound.js           builds the NMI / Authorize.net body in the vault; reduces the reply
vaults/emulator/      local stand-in that runs the same transforms (development)
cli/                  provisioning: vault, full Custom LLM agent, payment desk agents
site/                 the public report and live demo page (served at /airlock)
spike/                the live experiment on transfer_to_agent history
tests/                unit, property, end-to-end and live tests
docs/                 the documentation above
```

## FAQ

**Why a middleware at all?** A vault transform is stateless and runs on card data. Airlock
holds what must survive across turns (the capture step, token ids, attempt counts, which
orders are settled), takes the amount from the merchant's system rather than from the model or
the caller, routes normal turns to a model, and keeps the code that runs on card data small
(two transform files). See [DESIGN.md](docs/DESIGN.md#why-a-middleware-at-all-and-the-vault-only-alternative).

**Is this PCI compliant?** Software is not PCI compliant; deployments are assessed. Airlock is
designed so that it handles tokens only and card data stays with PCI DSS Level 1 providers
(ElevenLabs in transit, the vault, the gateway). An assessor will still likely treat Airlock as
security-impacting. Read [docs/PCI.md](docs/PCI.md). It is engineering documentation, not legal
or compliance advice.

**What does it cost?** Airlock is free (Apache-2.0). Production needs a vault plan; at the time
of writing Basis Theory listed $995/month including 20,000 tokens. A card payment uses four
tokens with the ZIP step (three without), an eCheck two, plus one for every re-entered field.
Check the vendor's current pricing. You also pay ElevenLabs, your gateway, and in Full mode
your model provider.

**Why does the payment desk end the call?** ElevenLabs re-sends the whole conversation, typed
digits included, to whichever agent handles the next turn. The main agent's hosted model is
not behind the vault, so handing the caller back would show it the digits. Ending the call is
the safe close. A phone deployment could instead send the caller to a person or to a fresh
call; Airlock does not implement that today.

**Does the language model ever see card data?** Keypad entries: no, in either mode. They are
tokenized in the vault before Airlock, and no model is called during a capture. In Full mode,
a card number said aloud is also cut out at the vault before your model sees the history. In
desk mode, anything the caller says to the main agent before the transfer goes to the
ElevenLabs-hosted model unfiltered, as on any ElevenLabs agent. ElevenLabs itself hears and
transcribes speech; `redact_input` covers keypad entries only.

**Can I use VGS or Evervault instead of Basis Theory?** Not without porting. The transform
logic (`transform`, `request`, `response` in `vaults/basis_theory/`) is plain JavaScript, but
the entry points, the provisioning script, the `{{ token }}` expression syntax, the
`BT-PROXY-KEY` header and the token delete call are Basis Theory's. A port needs a new
provisioning script, an entry-point adapter, and small changes in `airlock/app.py`
(`vault_charge`, `vault_delete`, keep-warm). The key-permission split in
[DESIGN.md](docs/DESIGN.md#keys-and-permissions-verified-on-a-live-tenant) must be reproduced
on the other vault.

**What about Twilio Pay?** Twilio `<Pay>` collects the card in Twilio's own payment IVR. It
works for calls on Twilio numbers, and the caller leaves the voice agent while paying. Airlock
keeps the payment inside the ElevenLabs agent's voice and works for web calls too.

## License

Apache-2.0. Copyright 2026 Daniel Wilson Kemp / Lifted Holdings. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). Security reports: [SECURITY.md](SECURITY.md).
