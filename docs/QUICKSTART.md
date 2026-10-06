# Quickstart: a sandbox payment call

This takes you from nothing to a test call that pays an order with a sandbox test card. Use
test credentials and published test cards only. Never type a real card into a sandbox setup.

Related: [CONFIGURATION.md](CONFIGURATION.md) lists every setting used here;
[OPERATIONS.md](OPERATIONS.md) covers running it for real.

## Prerequisites

| You need | Notes |
|---|---|
| An ElevenLabs account | API key with Agents access; a voice id for the agent |
| A Basis Theory test tenant | Free. Create a **management** key that can manage applications and proxies |
| An NMI and/or Authorize.net sandbox | NMI: a sandbox security key. Authorize.net: sandbox API login id + transaction key |
| An OpenAI-compatible model endpoint | **Full Custom LLM mode only.** Base URL, API key, model name |
| A host with Docker and HTTPS | Airlock must be reachable from the vault over HTTPS, with a correct clock (NTP) |
| Python 3.12 on your workstation | For the provisioning scripts |

## 1. Get the code and tools

```bash
git clone https://github.com/LiftedHoldings/airlock.git
cd airlock
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

## 2. Pick Airlock's public URL and an edge secret

Airlock will be served at `https://airlock.example.com` (replace with your hostname). The vault
forwards to `<AIRLOCK_URL>/chat/completions`, so `AIRLOCK_URL` ends in `/v1`.

```bash
export AIRLOCK_URL=https://airlock.example.com/v1
export AIRLOCK_EDGE_SECRET=$(openssl rand -hex 32)    # Airlock requires at least 32 bytes
```

Keep `AIRLOCK_EDGE_SECRET`: the vault uses it to sign every request and Airlock uses it to
check them.

## 3. Provision the vault

```bash
export BT_API_BASE=https://api.test.basistheory.com
export BT_MANAGEMENT_KEY=...                  # stays on your workstation, never on the Airlock host

# One or both gateways:
export NMI_SECURITY_KEY=...                   # NMI sandbox key
export ANET_LOGIN_ID=... ANET_TRANSACTION_KEY=...
export ANET_ECHECK_SEC=PPD                    # Authorize.net sandbox rejects TEL (error 246)

# Public or shared demos: tokenize only published test cards.
export TEST_CARDS_ONLY=1

python cli/provision_basis_theory.py > vault.json
chmod 600 vault.json
```

The script creates three applications (`airlock-edge`, `airlock-outbound`,
`airlock-service`), the inbound proxy to `AIRLOCK_URL`, and one outbound proxy per gateway you
configured. `vault.json` holds keys: treat it as a secret and delete it once the values are in
place. Fields you need next:

| `vault.json` field | Goes to |
|---|---|
| `service_api_key` | `VAULT_API_KEY` in `airlock.env` |
| `nmi_proxy_key` or `anet_proxy_key` | `VAULT_OUTBOUND_PROXY_KEY` in `airlock.env` (one gateway per Airlock instance) |
| `inbound_proxy_key` | `VAULT_INBOUND_KEY` in `airlock.env`, and the agent provisioning in step 6 |

## 4. Configure Airlock

```bash
cp airlock.env.example airlock.env
chmod 600 airlock.env
```

Edit `airlock.env`:

```ini
AIRLOCK_EDGE_SECRET=<the value from step 2>
AIRLOCK_GATEWAY=nmi                                  # label for events: nmi or authorizenet
VAULT_OUTBOUND_URL=https://api.test.basistheory.com/proxy
VAULT_OUTBOUND_PROXY_KEY=<nmi_proxy_key or anet_proxy_key>
VAULT_API_KEY=<service_api_key>
VAULT_TOKENS_URL=https://api.test.basistheory.com/tokens
VAULT_INBOUND_URL=https://api.test.basistheory.com/proxy
VAULT_INBOUND_KEY=<inbound_proxy_key>
UPSTREAM_BASE_URL=https://llm.example.com/v1         # Full mode: your model endpoint
UPSTREAM_API_KEY=...
UPSTREAM_MODEL=...
MAX_AMOUNT=500.00
AIRLOCK_OFFER_SAVE=1
AIRLOCK_ASK_ZIP=1
AIRLOCK_DEMO=1                                       # sandbox catalogue; see below
ORDER_LOOKUP_URL=
```

* **Payment desk only?** Leave `UPSTREAM_*` empty; the model is never called in desk mode.
* **eChecks:** set `AIRLOCK_MERCHANT_NAME` to your business name; the authorization reads it.
* **`AIRLOCK_DEMO=1` is for the sandbox catalogue only.** It prices orders `A1042` ($84.20),
  `A2001` ($12.00), `A3003` ($250.00), `A0050` ($0.50) and `T<cents>` (e.g. `T1234` = $12.34).
  In production set `AIRLOCK_DEMO=0` and point `ORDER_LOOKUP_URL` at your order system
  ([API.md](API.md#order-lookup-merchant-endpoint)). With neither, Airlock refuses every
  payment.

## 5. Run Airlock behind HTTPS

```bash
docker compose up -d --build
curl -s http://127.0.0.1:8931/health          # {"ok": true, "open_captures": 0}
```

The container listens on `127.0.0.1:8931`. Put a TLS reverse proxy in front. Streaming routes
must not be buffered. An nginx example:

```nginx
server {
    listen 443 ssl;
    server_name airlock.example.com;
    # ssl_certificate / ssl_certificate_key: your certificate (e.g. from certbot)

    location ~ ^/(v1/)?chat/completions$ {
        proxy_pass http://127.0.0.1:8931;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 60s;
    }
    location /events/ {
        proxy_pass http://127.0.0.1:8931;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 120s;
    }
    location = /health  { proxy_pass http://127.0.0.1:8931; }
    location /airlock   { proxy_pass http://127.0.0.1:8931; }   # optional: report page
    location /          { return 404; }
}
```

Check from outside:

```bash
# An unsigned request must be refused: expect 401.
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://airlock.example.com/v1/chat/completions \
     -H "Content-Type: application/json" -d '{"messages":[]}'

# The inbound proxy answers a keep-warm ping itself: expect {"pong":true}.
curl -s -X POST https://api.test.basistheory.com/proxy/chat/completions \
     -H "BT-PROXY-KEY: <inbound_proxy_key>" -H "Content-Type: application/json" \
     -d '{"airlock_ping":true}'
```

## 6. Create the agents

Both scripts read the inbound proxy key and store it as an ElevenLabs **workspace secret**
named `airlock-vault-inbound-key`; the agent config only references it.

```bash
export ELEVENLABS_API_KEY=...
export VOICE_ID=...                                    # any ElevenLabs voice id
export VAULT_INBOUND_URL=https://api.test.basistheory.com/proxy
export VAULT_INBOUND_KEY=<inbound_proxy_key>
export ALLOWED_HOSTS=www.example.com                   # optional: hosts allowed to embed the widget
```

**Payment desk (recommended):**

```bash
export NAME_PREFIX="Airlock sandbox"
python cli/provision_elevenlabs_desk.py
# {"main_agent_id": "...", "desk_agent_id": "..."}
```

This creates `<NAME_PREFIX> - payment desk` (Custom LLM through the vault, header
`X-Airlock-Mode: desk`) and `<NAME_PREFIX> - hosted model + payment desk` (ElevenLabs-hosted
model, `transfer_to_agent` to the desk). Callers talk to the **main** agent.

**Full Custom LLM:**

```bash
export AGENT_NAME="Airlock sandbox"
python cli/provision_elevenlabs_agent.py
# {"agent_id": "..."}
```

Both scripts install demo prompts for a store called Lifted Coffee Roasters with the demo
order numbers. Edit `PROMPT`, `FIRST` and the desk/main prompts in the scripts for your own
business. Keep `Conversation id: {{system__conversation_id}}` in every prompt that reaches
Airlock: without it Airlock will not open a capture.

## 7. Make a test call

Call the agent: attach a phone number to it in ElevenLabs for keypad tones, or use the web
widget and type the digits in its text box (text input is enabled by the scripts). Text typed
in the widget is stored in the ElevenLabs transcript as typed, so use test cards only.

Sandbox values:

| What | Value |
|---|---|
| Visa | `4111 1111 1111 1111`, security code any 3 digits |
| Mastercard | `5424 0000 0000 0015`, security code any 3 digits |
| American Express | `3700 000000 00002`, security code any **4** digits |
| Expiry | `1229` (12/29) |
| ZIP | any 5 digits, e.g. `12345` |
| Force a decline on NMI | an amount under $1.00: order `A0050` |
| Force a decline on Authorize.net | ZIP `46282` |
| eCheck on NMI | routing `123123123`, account `123123123` |
| eCheck on Authorize.net | any valid routing number, e.g. `011000015`, any account number |

**Script: card, payment desk.** End every keypad entry with `#`.

| You | Agent (approximately) |
|---|---|
| "I'd like to pay for order A1042." | Connects you to the secure payment line; the desk says "You're on the secure payment line. Say ready when you have your card or bank details." |
| "Ready." | "Your total is 84 dollars and 20 cents. For your security, please don't say your card number out loud. Please type your card number..." |
| `4111111111111111#` | "Got it, Visa ending in 1 1 1 1. Now type the expiration date..." |
| `1229#` | "And the 3-digit security code on the back of the card, then pound." |
| `999#` | "Last one: type the billing ZIP code, then pound." |
| `12345#` | "Would you like us to save this card for next time? Press 1 for yes, or 2 for no." |
| `2` | "To confirm: 84 dollars and 20 cents on your Visa ending in 1 1 1 1. Press 1 to pay, or 2 to change the card." |
| `1` | "One moment." (soft-timeout filler), then "You're all set... Your confirmation number ends in ... Thanks for calling. Goodbye." The call ends. |

In **Full Custom LLM** mode the first two rows are one: the model calls `start_payment` and
Airlock reads the total. After the payment the conversation continues; ask "what did I just
pay for?" and the model answers from the one-line result it was given.

**Try next:**

* Order `A0050` (NMI) or ZIP `46282` (Authorize.net): the agent offers a second card.
* "Pay order A2001 from my bank account": the desk asks the name on the account, then the
  routing number, account number, and checking or savings, then reads the authorization.
* Say a card number aloud: "For your security I didn't keep that."
* Press `*` to redo a field. Press `0` for a person: in Full mode the model takes over; the
  payment desk has no transfer, so it ends the call.
* Full mode: ask to pay the same order again after it went through: "That order is already
  paid."

NMI refuses the same card and amount for a while (about 20 minutes was observed) even under a
new order id. Use `T<cents>` orders (e.g. `T1234`, `T4321`) to vary the amount between runs.

## Next

* Watch a call's events: `curl -N https://airlock.example.com/events/<conversation_id>`
  ([API.md](API.md#get-eventsconversation_id)).
* Check the setup end to end: [TESTING.md](TESTING.md#live-tests).
* Before production: [OPERATIONS.md](OPERATIONS.md) and [PCI.md](PCI.md).
