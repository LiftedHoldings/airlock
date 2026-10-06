# Testing

Airlock's claims are backed by four layers of tests. The first two run offline with no
accounts; the last two need real (sandbox) services.

| Layer | Where | Needs | Run |
|---|---|---|---|
| Unit and property tests, detector parity | `tests/test_*.py` | Python, Node.js | `make test` |
| Leak scan of the published evidence | `tests/e2e/leak_scan.py` | Python | `make leakscan` |
| End-to-end driver | `tests/e2e/driver.py` | a vault (real or emulated), Airlock, a model, gateway sandboxes | by hand |
| Live checks | `tests/live/*.py` | a test vault tenant, gateway sandboxes, ElevenLabs | by hand |

Use published test cards only, everywhere. See also [CONTRIBUTING.md](../CONTRIBUTING.md) and
the security model in [DESIGN.md](DESIGN.md#security-model).

## make prove

```bash
pip install -r requirements-dev.txt
make prove
```

`make prove` runs, with no network and no accounts:

1. `lint`: `ruff check airlock cli tests` and `node --check` on both vault transforms.
2. `test`: `python -m pytest -q tests` (140 tests at the time of writing).
3. `leakscan`: `tests/e2e/leak_scan.py site/data/evidence.json`.

It prints `PROVE: all offline checks passed` at the end. CI runs the same target
(`.github/workflows/prove.yml`, Python 3.12, Node 22). Set `PY=` to choose the interpreter,
e.g. `make prove PY=.venv/bin/python`.

The two parity tests call `node`. Without Node.js they are **skipped**, and pytest still
reports success, so check the summary line: on a machine with Node.js it should say
`140 passed` with no skips.

## Unit and property tests

| File | Covers |
|---|---|
| `tests/test_cards_and_cleaner.py` | 240 generated cards (6 brand/length combinations × 40 seeds) in 6 formats each (plain, spaced, dashed, in a sentence, as spoken words, split) are caught by the detector, the tripwire, the cleaner and the output guard; benign numbers (timestamps, prices, phone numbers, conversation ids, dates) are not; placeholder parsing; **Python/JS detector parity** on about 800 samples |
| `tests/test_hardening.py` | commas, slashes, dot-space separators, full-width and Arabic-Indic digits; JS parity on those; hedged "yes" never charges; outcome classification; amounts only from the merchant; empty or short edge secret refuses to start; settled orders cannot be reopened; no capture without a conversation id; a charge settles even if the request is cancelled |
| `tests/test_script.py` | every branch of the card capture: happy path, retries and cancel, expiry and Amex rules, `*`, spoken intents, exits, silence, decline then second card, unknown outcome, change card, deterministic replay |
| `tests/test_echeck.py` | eCheck happy path with the NACHA TEL authorization text, bad routing, account type by keypad, change account |
| `tests/test_desk_mode.py` | desk finds the order in the conversation, asks for it when absent, asks the account name for eChecks, ends the call with `end_call` after payment |

### Keep the detectors identical

The card detector exists twice: `airlock/core/cards.py` (Airlock) and
`vaults/basis_theory/inbound.js` (the vault edge). Both parity tests feed the same inputs to
both and require identical answers. Change one, change the other in the same commit, and run
`make prove` with Node.js installed.

## Leak scan

```bash
python tests/e2e/leak_scan.py <file> [<file> ...]
```

Searches each file for five canary test cards (also with spaces, dots and dashes removed)
and for any Luhn-valid 13–19 digit run, including spoken-word forms. Exits 1 on a single hit.
Missing files are skipped, so check the `scanned N files` line. Point it at anything you are
about to publish or keep: event logs, evidence files, captures.

## End-to-end driver

`tests/e2e/driver.py` plays a caller against the real chain, sending ElevenAgents' Custom LLM
wire format (as recorded in [spike/](../spike/README.md)) to an inbound proxy:

```
driver -> vault inbound proxy (inbound.js) -> Airlock -> model / vault outbound -> gateway
```

```bash
python tests/e2e/driver.py <scenario> <inbound_url> <proxy_key> <evidence.jsonl>
```

| Scenario | What it does |
|---|---|
| `approve-save` | Pays order `T<cents>` with the test Visa, asks the amount mid-capture, saves the card, then asks the model what was paid |
| `decline-then-second-card` | Order `A0050-<run>` ($0.50: NMI declines) with ZIP `46282` (Authorize.net declines), then a second card |
| `hostile` | A card read aloud in the first sentence, a card as spoken words, prompt injection, an invalid card, `*`, "is this safe", cancel, "read me the digits" |

The first turn of every scenario goes to the model (Full Custom LLM mode), so Airlock needs
a working `UPSTREAM_*` model, and `AIRLOCK_DEMO=1` for the `T…` and `A0050` orders. The
driver does not send `X-Airlock-Mode`. Each turn is printed and appended to the evidence file
with card data redacted (only a test card's last four survive), plus time to first byte and
total time.

**Against a real vault:** the URL is the inbound proxy plus `/chat/completions`, the key is the
inbound proxy key:

```bash
python tests/e2e/driver.py approve-save \
  https://api.test.basistheory.com/proxy/chat/completions "$INBOUND_PROXY_KEY" run.jsonl
```

**Against the local emulator** (`vaults/emulator/server.js`, which runs the same transform
files and calls the real gateway sandboxes):

```bash
# terminal 1: the emulator
PORT=8920 AIRLOCK_URL=http://127.0.0.1:8931/v1 EDGE_SECRET="$AIRLOCK_EDGE_SECRET" \
PROXY_KEY=local-test-key NMI_SECURITY_KEY=... node vaults/emulator/server.js

# terminal 2: Airlock
export AIRLOCK_EDGE_SECRET  UPSTREAM_BASE_URL=... UPSTREAM_API_KEY=... UPSTREAM_MODEL=...
export AIRLOCK_DEMO=1 VAULT_OUTBOUND_URL=http://127.0.0.1:8920/outbound/nmi \
       VAULT_TOKENS_URL=http://127.0.0.1:8920/tokens \
       VAULT_INBOUND_URL=http://127.0.0.1:8920/inbound VAULT_INBOUND_KEY=local-test-key
uvicorn airlock.app:app --port 8931

# terminal 3: the driver
python tests/e2e/driver.py approve-save \
  http://127.0.0.1:8920/inbound/chat/completions local-test-key run.jsonl
```

The emulator's endpoints: `POST /inbound/<path>` (runs `inbound.js`, forwards to
`AIRLOCK_URL/<path>`, streams the reply back), `POST /outbound/nmi` or
`/outbound/authorizenet` (detokenizes `{{ id }}`, runs `outbound.js`, calls the gateway),
`DELETE /tokens/<id>`, `GET /tokens/<id>` (always 403), `GET /health`. Other settings:
`TOKEN_TTL_SECONDS`, `MAX_AMOUNT`, `NMI_URL`, `ANET_LOGIN_ID`, `ANET_TRANSACTION_KEY`,
`ANET_URL`, `EMULATOR_LOG`. It does not pass `ECHECK_SEC_CODE` or `TEST_CARDS_ONLY` to the
transforms, so eChecks use TEL (rejected by the Authorize.net sandbox) and every card is
tokenized. It listens on 127.0.0.1 only and keeps tokens in memory. It answers a deleted or
expired token with the real vault's 400 `proxy_error` shape, which Airlock classifies as
`error` (rejected before the gateway), the same as in production.

To keep evidence of what Airlock itself received, set `AIRLOCK_CAPTURE_INBOUND=<file>` on
Airlock for the run (never in production), then leak-scan it.

### The evidence builder

`tests/e2e/build_evidence.py` assembles the public bundle `site/data/evidence.json` from
redacted run files kept in a local, untracked `.local/` directory: the driver's evidence file
(`evidence-final.jsonl`), Airlock's capture file (`evidence-capture.jsonl`), the output of
`tests/live/live_vault_test.py` (`live_vault_test.out`), and an eCheck agent run
(`echeck-agent-run.json`). It groups turns by conversation, pairs each with what Airlock
received and the token-only charge requests, computes the keypad-turn latency median, and
**refuses to write** if the bundle contains anything card-like. `make leakscan` re-checks the
result.

## Live tests

`tests/live/` holds scripts that check a real deployment. Each one:

* takes every credential from environment variables (listed in its docstring);
* uses only published test cards and sandbox bank details;
* cleans up what it creates (tokens, throwaway vault applications, ElevenLabs conversations,
  approved test eChecks are voided). Exception: `live_vault_test.py` sends sandbox card sales
  that are expected to fail (a $0.50 NMI sale, which the sandbox declines, and a charge of a
  deleted token); they are not voided, and if the token delete itself failed the second one
  could approve on the sandbox;
* prints `PASS <check> :: <detail>`, `FAIL ...` and `INFO ...` lines and exits 1 on any
  failure;
* is never collected by pytest (`tests/live/conftest.py`).

Run them with `python tests/live/<name>.py`. The vault scripts refuse a production
Basis Theory API unless `AIRLOCK_ALLOW_PRODUCTION_TENANT=1`.

| Script | Proves | Environment |
|---|---|---|
| `live_vault_test.py` | Airlock's key cannot read a token (403); card data steered into `zip` or an extra field is refused (400); `BT-PROXY-URL` cannot redirect the NMI proxy; Airlock's key cannot drive an ephemeral proxy; delete works (204) and a deleted token cannot be charged. Sends one $0.50 sale to the NMI sandbox. | `BT_API_BASE`, `BT_MANAGEMENT_KEY`, `AIRLOCK_SERVICE_KEY`, `NMI_PROXY_KEY`, optional `ECHO_URL` |
| `ephemeral_probe.py` | Just the ephemeral-proxy check: Airlock's key cannot send a token's value to an arbitrary URL | `BT_API_BASE`, `BT_MANAGEMENT_KEY`, `AIRLOCK_SERVICE_KEY`, optional `ECHO_URL` |
| `coldstart_probe.py` | The proxies answer keep-warm pings inside the vault; times the first versus later pings (optionally after an idle wait) | `BT_API_BASE`, `INBOUND_PROXY_KEY`, optional `OUTBOUND_PROXY_KEY`, `IDLE_SECONDS`, `REQUESTS` |
| `live_agent_test.py` | A text conversation with a live agent pays an order with the test Visa; no agent reply and no stored agent message holds a card number; reports whether the typed card is in the stored transcript | `ELEVENLABS_API_KEY`, `AGENT_ID`, optional `ORDER_ID`, `ASK_ZIP`, `OFFER_SAVE`, `KEEP_CONVERSATION` |
| `origin_probe.py` | Which `Origin` headers can open a public widget session | `AGENT_ID`, optional `ALLOWED_ORIGIN`, `OTHER_ORIGINS`, `ELEVENLABS_API_KEY` (cleanup) |
| `gateway_smoke.py` | Gateway sandbox credentials authenticate; no transactions | `NMI_SECURITY_KEY` and/or `ANET_LOGIN_ID` + `ANET_TRANSACTION_KEY`, optional `NMI_QUERY_URL`, `ANET_URL` |
| `echeck_probe.py` | Both gateways need the account-holder name on an eCheck; approved test eChecks are voided | `NMI_SECURITY_KEY` and/or `ANET_LOGIN_ID` + `ANET_TRANSACTION_KEY`, optional `NMI_ECHECK_SEC`, `ANET_ECHECK_SEC` (default `PPD`), `ANET_ROUTING`, `ANET_ACCOUNT`, `NMI_URL`, `ANET_URL` |

Variable names map to provisioning output: `AIRLOCK_SERVICE_KEY` = `service_api_key`,
`NMI_PROXY_KEY` = `nmi_proxy_key`, `INBOUND_PROXY_KEY` = `inbound_proxy_key`,
`OUTBOUND_PROXY_KEY` = `nmi_proxy_key` or `anet_proxy_key`.

`live_agent_test.py` uses Airlock's demo order `T<cents>` by default, so the Airlock behind
the agent must run with `AIRLOCK_DEMO=1`, or set `ORDER_ID` to an order your lookup knows. It
works with a Full Custom LLM agent or with the main agent of a payment-desk pair. It types the
card through the text channel, which ElevenLabs stores as typed; a phone call with keypad
tones is what `redact_input` covers.

A typical post-deploy run:

```bash
python tests/live/gateway_smoke.py
python tests/live/coldstart_probe.py
python tests/live/live_vault_test.py | tee live_vault_test.out
AGENT_ID=<agent> python tests/live/live_agent_test.py
```

## The spike

`spike/` holds the experiment that measured the platform behaviour Airlock is built around:
digits re-sent across `transfer_to_agent`. See [spike/README.md](../spike/README.md).
