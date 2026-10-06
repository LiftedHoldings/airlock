# Configuration

Three places hold Airlock's configuration:

1. **Airlock's environment** (`airlock.env`), read by `airlock/*.py` at start-up.
2. **The vault** (Basis Theory): applications, proxies, transform configuration. Set by
   `cli/provision_basis_theory.py`.
3. **ElevenLabs Agents**: agent settings. Set by `cli/provision_elevenlabs_desk.py` or
   `cli/provision_elevenlabs_agent.py`.

See also: [QUICKSTART.md](QUICKSTART.md) for the order of operations,
[OPERATIONS.md](OPERATIONS.md#rotating-keys) for rotation, [API.md](API.md) for the formats.

## 1. Airlock environment variables

All variables Airlock reads, from `airlock/app.py` and `airlock/orders.py`. Values are read
once at start-up, except `ORDER_LOOKUP_URL`, which is read on every lookup. "Required" means
Airlock exits at start-up if the variable is unset, empty or whitespace.

### Security and vault

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AIRLOCK_EDGE_SECRET` | yes | none | HMAC-SHA256 key that checks the inbound transform's signature on every request. At least 32 bytes or Airlock exits. Must equal the inbound proxy's `EDGE_SECRET`. |
| `AIRLOCK_MAX_SKEW_SECONDS` | no | `300` | Largest accepted difference between the signature timestamp and Airlock's clock. |
| `VAULT_OUTBOUND_URL` | yes | none | Where charges are posted: the vault proxy endpoint, e.g. `https://api.basistheory.com/proxy`. With the local emulator: `http://127.0.0.1:8920/outbound/nmi`. |
| `VAULT_OUTBOUND_PROXY_KEY` | no | empty | Sent as `BT-PROXY-KEY`; selects the gateway's outbound proxy (`nmi_proxy_key` or `anet_proxy_key`). Empty only with the emulator. |
| `VAULT_TOKENS_URL` | no | empty | Token API base, e.g. `https://api.basistheory.com/tokens`. Airlock sends `DELETE <url>/<token_id>` after each attempt. Empty disables deletion (tokens then expire on their TTL). |
| `VAULT_API_KEY` | no | empty | Sent as `BT-API-KEY` on token deletes. The `airlock-service` key, which holds `token:delete` only. |
| `VAULT_INBOUND_URL` | no | empty | Inbound proxy endpoint for keep-warm pings, e.g. `https://api.basistheory.com/proxy`. Airlock posts to `<url>/chat/completions`. |
| `VAULT_INBOUND_KEY` | no | empty | The inbound proxy key, for keep-warm pings. Without both inbound settings only the outbound proxy is kept warm. |
| `AIRLOCK_KEEPWARM_SECONDS` | no | `240` | Interval between keep-warm pings. `0` turns the loop off. |
| `AIRLOCK_GATEWAY` | no | `nmi` | A label written into `charge` and `charge-start` events. It does **not** choose the gateway: the outbound proxy key does. Set it to match (`nmi` or `authorizenet`). |

One Airlock instance charges through one outbound proxy, so one gateway. Run a second instance
(with its own inbound proxy and agent) for a second gateway.

### Language model (Full Custom LLM mode)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `UPSTREAM_BASE_URL` | Full mode | empty | OpenAI-compatible base URL. Airlock posts to `<url>/chat/completions` with `stream: true`. |
| `UPSTREAM_API_KEY` | Full mode | empty | Sent as `Authorization: Bearer <key>`. |
| `UPSTREAM_MODEL` | Full mode | empty | Model name sent upstream. ElevenLabs' `custom_llm.model_id` is ignored. |

A payment-desk-only deployment leaves these empty. If a non-desk turn arrives without them,
Airlock answers "I'm sorry, I can only take payments on this line." and logs a `refused` event
(`no-upstream-model-configured`).

### Payments

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ORDER_LOOKUP_URL` | for production | empty | Merchant endpoint that prices an order: `GET <url>?order_id=<id>` → `{"amount": "84.20"}`. See [API.md](API.md#order-lookup-merchant-endpoint). Takes precedence over the demo catalogue. |
| `AIRLOCK_DEMO` | no | unset | `1` enables the built-in sandbox catalogue (`A1042`, `A2001`, `A3003`, `A0050`, `T<cents>`) and lets callers say just the digits ("1042"). **Never set in production.** With neither this nor `ORDER_LOOKUP_URL`, every payment is refused. |
| `MAX_AMOUNT` | no | `500.00` | Largest amount Airlock will open a capture for. The outbound transform has its own `MAX_AMOUNT`; keep the two equal. |
| `AIRLOCK_OFFER_SAVE` | no | `1` | `1` adds the "save this card/account for next time" step (gateway vault). Any other value removes it. |
| `AIRLOCK_ASK_ZIP` | no | `1` | `1` asks for the billing ZIP on card payments (sent for AVS). Any other value skips it. Never asked for eChecks. |
| `AIRLOCK_MERCHANT_NAME` | for eChecks | `us` | Business name read in the eCheck authorization ("you're allowing <name> to make a one-time electronic debit..."). NACHA TEL authorizations should name the merchant. |

### Latency, logs, site

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AIRLOCK_CHARGE_FILLER` | no | empty | Text Airlock streams before a charge. The vault proxy buffers Airlock's reply, so the caller does not hear it early; use the agent's soft timeout instead. Leave empty unless your path streams end to end. |
| `AIRLOCK_EVENT_LOG` | no | empty | File to append the allowlisted event log to (JSON lines). Events are always printed to stdout (`docker logs`); this file keeps a durable copy. See [OPERATIONS.md](OPERATIONS.md#logs). |
| `AIRLOCK_CAPTURE_INBOUND` | no | empty | **Tests only.** Appends every received message list and every outbound request (token expressions) and reduced reply to this file, for evidence. Holds no card data, but holds conversation text. Do not set in production. |
| `AIRLOCK_SITE_DIR` | no | empty (`/app/site` in the Docker image) | Directory served at `/airlock/` (the report and demo page). Not mounted if unset or missing. |

Fixed in code, not configurable: session TTL 600 s (sliding), at most 3 invalid entries per
field, 2 silences re-prompted (the third cancels), 2 cards per capture, model `max_tokens`
capped at 600, HTTP timeouts (charge 24 s, model 20 s, order lookup 5 s).

**Set `AIRLOCK_MERCHANT_NAME` before taking eChecks.** The NACHA TEL authorization reads
"you're allowing <name> to make a one-time electronic debit"; unset, it says "us".

## 2. The vault (Basis Theory)

### Provisioning script

`cli/provision_basis_theory.py` creates or updates everything below and prints the ids and
keys as JSON. Run it from a workstation; the management key never goes on the Airlock host.
A full run deletes and recreates the three applications; only the outbound proxies whose
gateway credentials are set in that run are re-attached, so always set credentials for every
gateway you use ([OPERATIONS.md](OPERATIONS.md#rotating-keys)).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BT_MANAGEMENT_KEY` | yes | none | Management key for applications and proxies |
| `BT_API_BASE` | no | `https://api.test.basistheory.com` | `https://api.basistheory.com` for production |
| `AIRLOCK_URL` | yes | none | Airlock's public base URL including `/v1`; the inbound proxy's destination |
| `AIRLOCK_EDGE_SECRET` | yes | none | Becomes the inbound proxy's `EDGE_SECRET` |
| `TOKEN_TTL_SECONDS` | no | `600` | Token lifetime set by the inbound transform |
| `TEST_CARDS_ONLY` | no | `0` | `1` drops any Luhn-valid 13+ digit keypad entry that is not a published sandbox test card |
| `MAX_AMOUNT` | no | `500.00` | Outbound transforms refuse larger amounts |
| `NMI_SECURITY_KEY` | to create the NMI proxy | none | NMI key, stored in the proxy configuration |
| `NMI_URL` | no | `https://secure.nmi.com/api/transact.php` | NMI proxy destination |
| `NMI_ECHECK_SEC` | no | `TEL` | NACHA SEC code for NMI eChecks |
| `ANET_LOGIN_ID`, `ANET_TRANSACTION_KEY` | to create the Authorize.net proxy | none | Authorize.net credentials, stored in the proxy configuration |
| `ANET_URL` | no | `https://apitest.authorize.net/xml/v1/request.api` | Use `https://api.authorize.net/xml/v1/request.api` in production |
| `ANET_ECHECK_SEC` | no | `TEL` | Set `PPD` for the Authorize.net sandbox, which rejects TEL (error 246) |

`--update-transforms` redeploys the transform code and proxy configuration without recreating
the applications (and so without changing the service key). It still needs every variable
above, because it rewrites each proxy's configuration.

Output fields: `api_base`, `edge_application_id`, `service_application_id`,
`service_api_key`, `inbound_proxy_id`, `inbound_proxy_key`, `inbound_proxy_host`,
`nmi_proxy_id`, `nmi_proxy_key`, `anet_proxy_id`, `anet_proxy_key`.

### Applications and permissions

| Application | Permissions | Used by | Why |
|---|---|---|---|
| `airlock-edge` | `token:create` | the inbound proxy and its transform (the transform runtime is also granted `token:create`) | The edge creates tokens from keypad digits and nothing else. It cannot read them back. |
| `airlock-outbound` | `token:use` | attached to the fixed-destination outbound proxies only | Detokenization happens only inside a proxy whose destination is the gateway. |
| `airlock-service` | `token:delete` | Airlock (`VAULT_API_KEY`) | Airlock deletes tokens after each attempt. |

**Why Airlock's key has `token:delete` only.** In Basis Theory, a key that holds `token:use`
can also drive an *ephemeral* proxy (`BT-PROXY-URL`) to any URL, which would let whoever holds
the key send a live token's value anywhere. So `token:use` lives only on the application
attached to the fixed-destination outbound proxies. Tested on a live tenant: an ephemeral
proxy call with Airlock's key returns 403. A stolen Airlock key can at worst cause a charge of
a still-live token to the merchant's own gateway account, capped by `MAX_AMOUNT`.

Airlock also holds the outbound proxy key, which lets it invoke that one proxy. The proxy's
destination is fixed and its transform builds the gateway body itself.

### Proxies

All proxies are created with `require_auth: false`: the proxy key (`BT-PROXY-KEY`) alone
invokes them. Transforms run on the `node24` runtime with `@basis-theory/node-sdk` 6.1.1 and a
10 s timeout.

| Proxy | Destination | Transforms | Configuration keys |
|---|---|---|---|
| `airlock-inbound` | `AIRLOCK_URL` | request: `inbound.js` | `EDGE_SECRET`, `TOKEN_TTL_SECONDS`, `TEST_CARDS_ONLY` |
| `airlock-outbound-nmi` | `NMI_URL` | request + response: `outbound.js` | `GATEWAY=nmi`, `NMI_SECURITY_KEY`, `ECHECK_SEC_CODE`, `MAX_AMOUNT` |
| `airlock-outbound-authorizenet` | `ANET_URL` | request + response: `outbound.js` | `GATEWAY=authorizenet`, `ANET_LOGIN_ID`, `ANET_TRANSACTION_KEY`, `ECHECK_SEC_CODE`, `MAX_AMOUNT` |

Transform configuration keys:

| Key | Read by | Default if absent | Meaning |
|---|---|---|---|
| `EDGE_SECRET` | inbound | none (the transform fails closed) | HMAC key for `X-Airlock-Edge-Sig` |
| `TOKEN_TTL_SECONDS` | inbound | `120` (the script sets `600`) | Token expiry. Long enough for a slow caller to reach confirm. |
| `TEST_CARDS_ONLY` | inbound | off | `"1"` enables the test-card-only rule |
| `GATEWAY` | outbound | NMI | `authorizenet` selects Authorize.net bodies and reply parsing |
| `MAX_AMOUNT` | outbound | `500` | Amount ceiling, checked in the vault |
| `ECHECK_SEC_CODE` | outbound | `TEL` | `sec_code` (NMI) or `echeckType` (Authorize.net) |
| `NMI_SECURITY_KEY` | outbound | none | NMI credential |
| `ANET_LOGIN_ID`, `ANET_TRANSACTION_KEY` | outbound | none | Authorize.net credentials |

Gateway credentials live only in the proxy configuration. Airlock never holds them.

## 3. ElevenLabs Agents

### Provisioning scripts

| Variable | Script | Required | Default | Purpose |
|---|---|---|---|---|
| `ELEVENLABS_API_KEY` | both | yes | none | ElevenLabs API key |
| `VAULT_INBOUND_URL` | both | yes | none | The Custom LLM URL, e.g. `https://api.basistheory.com/proxy` |
| `VAULT_INBOUND_KEY` | both | yes | none | Stored as the workspace secret `airlock-vault-inbound-key` |
| `VOICE_ID` | both | yes | none | ElevenLabs voice id |
| `TTS_MODEL` | both | no | `eleven_v4_turbo` | Text-to-speech model |
| `ALLOWED_HOSTS` | both | no | empty | Comma-separated hostnames for the agent allowlist |
| `AGENT_NAME` | agent | no | `Airlock demo` | Agent name (an existing agent with this name is updated) |
| `NAME_PREFIX` | desk | no | `Airlock demo` | Agents are named `<prefix> - payment desk` and `<prefix> - hosted model + payment desk` |
| `MAIN_LLM` | desk | no | an ElevenLabs-hosted model id set in the script | The main agent's hosted model |

Secret handling differs: `provision_elevenlabs_agent.py` deletes any secret named
`airlock-vault-inbound-key` and creates a new one; `provision_elevenlabs_desk.py` reuses an
existing secret with that name and does **not** update its value.

### Settings the scripts set, and why

| Setting | Value | Agent | Why |
|---|---|---|---|
| `prompt.llm` | `custom-llm` | desk, full | Puts the vault on every model request of that agent |
| `custom_llm.url` | `VAULT_INBOUND_URL` | desk, full | ElevenLabs posts to `<url>/chat/completions`; the vault forwards to `AIRLOCK_URL/chat/completions` |
| `custom_llm.model_id` | `airlock` / `airlock-desk` | full / desk | Informational; Airlock ignores it |
| `custom_llm.request_headers.BT-PROXY-KEY` | `{"secret_id": ...}` | desk, full | The proxy key comes from a workspace secret, never inline in the agent config |
| `custom_llm.request_headers.X-Airlock-Mode` | `desk` | desk | Tells Airlock to script every turn and end the call; no model is called |
| `prompt.prompt` contains `{{system__conversation_id}}` | — | desk, full | Airlock keys each capture on the `conv_…` id it finds in the system prompt; without it no capture opens |
| `cascade_timeout_seconds` | `15` | desk, full | The default (4 s) is shorter than a charge (~4–5 s) or a cold transform |
| `built_in_tools.end_call` | system tool | desk, full, main | The desk ends the call through it after payment |
| `built_in_tools.transfer_to_agent` | to the desk, `enable_transferred_agent_first_message: true` | main | Hands the caller to the desk once the order number is known |
| `conversation.dtmf_input_settings` | `hash_terminator: true`, `dtmf_input_timeout: 5.0`, `redact_input: true` | desk, full | `#` ends an entry; 5 s pause ends it too; stored transcripts show keypad entries redacted |
| `turn.soft_timeout_config` | 2.0 s; "One moment.", then "Still working on that, thanks for waiting.", "Almost there."; at most 3; not before the first user message | desk, full, main | Covers the charge and cold starts, because the vault buffers Airlock's reply |
| `soft_timeout_config.use_llm_generated_message` | `false` (static fillers only) | all | A generated filler would hand recent conversation context, which may contain keypad turns, to a model |
| `conversation.max_duration_seconds` | `300` | all | Demo value; raise for production |
| `tts` | `VOICE_ID`, `eleven_v4_turbo` | all | Low-latency model for live calls |
| `platform_settings.privacy.record_voice` | `false` | all | No stored call audio (see the eCheck note in [ECHECK.md](ECHECK.md#sec-code-tel)) |
| `platform_settings.auth.allowlist` | `ALLOWED_HOSTS` | all | Hosts that may embed the widget |
| `platform_settings.auth.enable_auth` | `false` | all | Public demo. For a private deployment consider signed sessions. |
| `platform_settings.auth.require_origin_header` | `false` | all | **Must stay false.** With it on, WebRTC voice sessions dropped immediately. |
| `platform_settings.call_limits` | `agent_concurrency_limit: 3`, `daily_limit: 150` | all | Demo abuse limits; size them for production |
| `platform_settings.widget.text_input_enabled` | `true` | all | Lets demo users type digits. Typed text is stored in the transcript as typed (`redact_input` covers keypad entries only), so turn this off in production. |

The main agent in desk mode has no `dtmf_input_settings` and no Custom LLM: it runs as an
ordinary ElevenLabs agent and is not behind the vault.

The prompts and first messages in the scripts are for the demo store (Lifted Coffee Roasters,
orders `A1042`, `A2001`, `A3003`, `A0050`). Replace them for your business.
