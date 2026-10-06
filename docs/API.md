# API

Airlock's HTTP surface, the formats the vault transforms produce and accept, and the event
feed. Everything here is taken from `airlock/app.py`, `airlock/core/`, `airlock/orders.py` and
`vaults/basis_theory/*.js`.

Related: [CONFIGURATION.md](CONFIGURATION.md), [OPERATIONS.md](OPERATIONS.md),
[DESIGN.md](DESIGN.md).

| Method and path | Purpose |
|---|---|
| `POST /v1/chat/completions` | The Custom LLM endpoint (reached only through the vault) |
| `POST /chat/completions` | Same handler, for base URLs without `/v1` |
| `GET /events/{conversation_id}` | Live, allowlisted event feed for one conversation (SSE) |
| `GET /health` | Liveness |
| `GET /airlock/` | The report and demo page, when `AIRLOCK_SITE_DIR` is set |

There is no OpenAPI page (`/docs` and `/redoc` are off).

## POST /v1/chat/completions

ElevenLabs Agents sends an OpenAI Chat Completions request to the agent's Custom LLM URL,
which is the vault's inbound proxy. The inbound transform rewrites and signs it, then forwards
it here.

### Request

Headers:

| Header | Required | Set by | Meaning |
|---|---|---|---|
| `X-Airlock-Edge-Ts` | yes | inbound transform | Unix time in seconds, digits only |
| `X-Airlock-Edge-Sig` | yes | inbound transform | Hex HMAC-SHA256, key `AIRLOCK_EDGE_SECRET`, over the bytes `"<ts>." + <raw request body>` |
| `X-Airlock-Mode` | no | agent's `custom_llm.request_headers` | `desk` (case-insensitive) selects payment-desk mode |
| `Content-Type` | yes | ElevenLabs | `application/json` |

Body: the Chat Completions request as ElevenLabs sends it. Recorded fields are `messages`,
`model`, `max_tokens`, `stream`, `stream_options`, `temperature` and `tools`
([spike/README.md](../spike/README.md)). Airlock reads:

| Field | Use |
|---|---|
| `messages` | The conversation. The `conv_…` id is taken from the first system message that contains one. The last `user` message with string content is the caller's turn. |
| `tools` | Platform tools (`end_call`, `transfer_to_agent`, ...). Relayed to the model in Full mode; used to emit `end_call` in desk mode. |
| `temperature` | Passed to the model (default `0.3` if absent). |
| `max_tokens` | Passed to the model, capped at 600 (default 400). |

The signature is checked over the exact bytes received, so nothing between the vault and
Airlock may re-encode the body.

### Processing order

1. **Edge signature.** Missing headers, a non-numeric timestamp, a timestamp more than
   `AIRLOCK_MAX_SKEW_SECONDS` (300) from Airlock's clock, or a wrong signature → `401`.
2. **Tripwire.** The raw body, with `[[airlock:…]]` placeholders removed, is scanned for any
   card-like number. A hit means the vault edge failed: Airlock answers with a fixed apology
   (below) and nothing else happens.
3. **JSON parse.** Invalid JSON → `400`.
4. **Routing:**
   * A capture is open for this conversation → the capture script answers (no model).
   * Else `X-Airlock-Mode: desk` → a desk turn (no model).
   * Else → a model turn.

### Responses

Every successful answer is an SSE stream (`Content-Type: text/event-stream`,
`Cache-Control: no-cache`, `X-Accel-Buffering: no`), whatever the request's `stream` value.
Chunks follow the Chat Completions chunk format:

```
data: {"id":"airlock","object":"chat.completion.chunk","created":1791292673,"model":"airlock","choices":[{"index":0,"delta":{"role":"assistant","content":"Your total is 84 dollars and 20 cents. ..."},"finish_reason":null}]}

data: {"id":"airlock","object":"chat.completion.chunk","created":1791292673,"model":"airlock","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

A platform tool call (relayed from the model, or `end_call` from the desk) ends with
`finish_reason: "tool_calls"`:

```
data: {..."choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_end","type":"function","function":{"name":"end_call","arguments":"{}"}}]},"finish_reason":null}]}

data: {..."choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

`end_call` arguments are filled from the tool's JSON schema in the request: each required
string gets `"Payment complete."` (or the named value), each required number gets `0`. If the
request has no `end_call` tool, the desk stream ends with a plain `stop`.

Errors and fixed answers:

| Situation | HTTP | Body / what the caller hears |
|---|---|---|
| Bad or missing edge signature | 401 | `{"error": "forbidden"}` |
| Body is not JSON | 400 | `{"error": "bad json"}` |
| Tripwire hit | 200 (SSE) | "I'm sorry, I can't take a card payment right now." |
| Card-like number in the cleaned history (Full mode) | 200 (SSE) | "I'm sorry, I can't continue with that. Let me get you some help." |
| Card-like text, placeholder or token id in model output | 200 (SSE) | the stream stops with " Sorry, let me rephrase that." |
| Card-like data in a relayed tool call's arguments | 200 (SSE) | "Sorry, let me try that another way." |
| Model endpoint error | 200 (SSE) | "Sorry, I'm having trouble right now. Could you say that again?" |
| No `conv_…` id in the system prompt when a payment starts | 200 (SSE) | "I'm sorry, I can't take a payment on this line right now." |

### Model turns (Full Custom LLM mode)

Airlock forwards to `POST <UPSTREAM_BASE_URL>/chat/completions`:

```json
{
  "model": "<UPSTREAM_MODEL>",
  "messages": [ "...cleaned history, payment rules, payment results..." ],
  "tools": [ "...the request's tools except any start_payment...", "<start_payment>" ],
  "stream": true,
  "temperature": 0.3,
  "max_tokens": 400
}
```

* **Cleaned history.** A user turn that is a keypad entry or holds a placeholder becomes
  `[keypad entry captured]`. A user turn with `[[airlock:spoken]]` keeps its other words, with
  the number replaced by `[card number said aloud: removed for security]`. Placeholders and
  token ids in other messages become `[keypad entry captured]` and `[ref]`. Any card-like
  number left anywhere refuses the turn: the model is not called.
* **Payment rules.** A fixed system message is inserted after the first system message. It
  tells the model to call `start_payment`, never to ask for or repeat numbers, and to answer
  in one or two spoken sentences.
* **Payment results.** After a capture, its one-line result (below) is appended as a system
  message on every later turn of the conversation (kept for the session TTL).
* **Output guard.** The accumulated reply is checked before each chunk is relayed. A
  card-like number, a placeholder or a token id stops the reply.
* **`start_payment` is intercepted.** It is never relayed to ElevenLabs. Airlock opens a
  capture and speaks the opening line instead. Other tool calls are relayed unchanged.

### The start_payment tool

Added to the model's tools on every model turn:

```json
{
  "type": "function",
  "function": {
    "name": "start_payment",
    "description": "Start a secure payment for an order, by card or by bank account (eCheck). Call this when the caller is ready to pay. Never ask for card or bank details yourself; this tool collects them securely by keypad.",
    "parameters": {
      "type": "object",
      "properties": {
        "order_id": {"type": "string", "description": "The order or invoice id being paid."},
        "method": {"type": "string", "enum": ["card", "check"], "description": "card (default), or check if the caller wants to pay from a bank account / eCheck / ACH."},
        "account_holder_name": {"type": "string", "description": "For method=check only: the name on the bank account. Ask the caller for it before calling."}
      },
      "required": ["order_id"]
    }
  }
}
```

* `method` other than `check` means card.
* `account_holder_name` is trimmed to 60 characters and kept only if it matches
  `[A-Za-z][A-Za-z .'\-]{0,59}` (what the outbound transform accepts) and holds nothing
  card-like. For `check`:
  * no name given: Airlock uses the order lookup's `customer_name` if it has one; otherwise it
    asks "Before we start, what's the name on the bank account?";
  * a name given that fails the check (accents, commas): Airlock asks again ("The bank needs
    the account name in plain letters...") and never substitutes the order's name, since the
    account may belong to someone else.

  In both asking cases the capture is not opened; the model calls again with the name.
* Opening a capture: an already-open capture repeats its current prompt; an order already
  settled in this conversation is refused ("That order is already paid." for approved; "We're
  still confirming the earlier payment for that order..." for unknown, held or duplicate); an
  order the lookup cannot price gets "I couldn't find that order number."

### Desk turns (payment desk mode)

No model is called.

* If this conversation already had a capture that finished, Airlock says "Thanks for calling.
  Goodbye." and emits `end_call`.
* Otherwise it looks for an order id in the last 8 caller turns (newest first), matching
  `[A-Za-z]{0,2}-?\d{3,8}(-[A-Za-z0-9]{2,6})?`, skipping anything card-like, and opens a
  capture for the first one the order lookup can price. The method is eCheck if any of the
  last 4 caller turns mention bank, checking, savings, e-check, ACH, routing or account number;
  otherwise card. For an eCheck with no `customer_name` from the lookup, the script first asks
  for the name on the account.
* If the caller typed a number before being prompted, that entry is used straight away.
* No order found: "I can take that payment. Which order number are you paying?"
* When the capture finishes, the desk never hands back. A cancelled capture (caller said
  cancel, pressed `0`, asked for a person or a link, or ran out of tries) is spoken as "Okay,
  I've stopped there. No payment was taken. You're welcome to call back and try again." —
  the capture's own line, which offers a link or a person, is replaced because the desk cannot
  deliver either. `unknown` and `duplicate` outcomes keep the script's full line ("...our team
  will confirm the payment and follow up"). Every finished capture then says "Thanks for
  calling. Goodbye." and emits `end_call`.

  The `capture-reply` event logs the line the caller heard (before the goodbye).

### Capture turns (both modes)

While a capture is open, `airlock/core/script.py` answers every turn deterministically. The
same inputs always give the same outputs. Field order:

* Card: card number → expiry → security code → ZIP (if `AIRLOCK_ASK_ZIP=1`) → save (if
  `AIRLOCK_OFFER_SAVE=1`) → confirm.
* eCheck: name (desk mode, if needed) → routing → account → checking/savings → save (if on)
  → confirm (NACHA TEL authorization).

Validation per field, from placeholder metadata only:

| Field | Accepted |
|---|---|
| Card number | `luhn=1` and a length valid for the brand (Amex 15; Visa 13/16/19; Mastercard 16; Discover 16/19; JCB 16–19; Diners 14/16; unknown 13–19) |
| Expiry | length 4 or 6 and `expok=1` |
| Security code | length 4 for Amex, 3 otherwise |
| ZIP | length 5 or 9 |
| Routing | length 9 and `aba=1` |
| Account | length 4–17 |

Short inputs pass through the vault unchanged: `1`/`2` answer menus, `*` redoes the current
field, `0` asks for a person. Three invalid entries for a field, a third silence in a row, or a
second decline end the capture.

## Placeholders (inbound transform output)

The inbound transform (`vaults/basis_theory/inbound.js`) rewrites `messages` before Airlock
sees them.

| Placeholder | Produced when |
|---|---|
| `[[airlock:kp id=<token> len=<n> luhn=<0\|1> ...]]` | The **newest** message is a user message made only of digits, spaces, `*` and `#`, with 3 or more such characters, and no `*`. The digits become a vault token. |
| `[[airlock:kp-prior]]` | Any earlier user message that is a keypad entry (ElevenLabs re-sends history). No new token. |
| `*` | The newest message is a keypad entry containing `*`: forwarded as a bare `*` (redo the field). |
| `[[airlock:spoken]]` | A card-like number (13–19 digits, Luhn-valid, separators or spoken words allowed, any Unicode digit script) anywhere in any other message, any role. The number is cut out and the rest of the sentence kept; if the number was only recognisable as spoken words, the whole message is replaced. |
| `[[airlock:not-test-card]]` | `TEST_CARDS_ONLY=1` and the newest keypad entry is Luhn-valid, 13+ digits, and not a published sandbox test card. Nothing is tokenized. |

Keypad entries of 1–2 characters (menu digits) pass through unchanged.

Metadata in `[[airlock:kp …]]`, in this order:

| Field | Present when | Value |
|---|---|---|
| `id` | always | vault token id |
| `len` | always | number of digits |
| `luhn` | always | `1` if the digits pass the Luhn check |
| `last4` | 13–19 digits and `luhn=1` | last four digits |
| `brand` | 13–19 digits and `luhn=1` | `visa`, `mastercard`, `amex`, `discover`, `jcb`, `diners` or `unknown` |
| `expok` | exactly 4 or 6 digits | `1` if it reads as a valid MMYY or MMYYYY not in the past and at most 20 years ahead |
| `aba` | exactly 9 digits | `1` if the ABA routing checksum passes (and not all zeros) |
| `last4` | 8 or more digits and no `last4` yet | last four digits (bank account numbers) |

Entries of 3–7 digits never carry any of their digits. Example card entry:
`[[airlock:kp id=7c2d1d0e-3f2a-4b1e-9c3f-0a1b2c3d4e5f len=16 luhn=1 last4=1111 brand=visa]]`.

After rewriting, the transform scans the whole body again (placeholders removed). Anything
still card-like, or any error, makes the transform answer the caller itself with "I'm sorry,
I can't take a card payment right now. I can send you a secure payment link instead." (SSE,
header `X-Airlock-Edge-Error: residual-card-data` or `transform-error`). Nothing is forwarded.

## Outbound: the charge request

Airlock posts JSON to `VAULT_OUTBOUND_URL` with header `BT-PROXY-KEY: <outbound proxy key>`.
Card and bank values are token expressions, `{{ <token_id> }}`; the vault replaces them with
the real values before the request transform runs.

Card:

```json
{
  "order_id": "A1042",
  "amount": "84.20",
  "currency": "USD",
  "pan": "{{ 7c2d1d0e-3f2a-4b1e-9c3f-0a1b2c3d4e5f }}",
  "exp": "{{ <token> }}",
  "cvv": "{{ <token> }}",
  "zip": "{{ <token> }}",
  "save_card": false,
  "customer_ref": "A1042"
}
```

`zip` is present only if the caller entered one.

eCheck:

```json
{
  "method": "check",
  "order_id": "A2001",
  "amount": "12.00",
  "currency": "USD",
  "routing": "{{ <token> }}",
  "account": "{{ <token> }}",
  "account_type": "checking",
  "holder_name": "Test Payer",
  "save_card": false,
  "customer_ref": "A2001"
}
```

The request transform (`outbound.js`) validates, then builds the gateway body itself:

| Check | Card | eCheck |
|---|---|---|
| `order_id` | `^[A-Za-z0-9][A-Za-z0-9-]{0,19}$` | same |
| `amount` | `^\d{1,6}\.\d{2}$`, > 0, ≤ `MAX_AMOUNT` | same |
| `currency` | `USD` | same |
| Sensitive fields | `pan` 13–19 digits, Luhn; `exp` 4 or 6 digits; `cvv` 3–4 digits; `zip` 5 or 9 digits if present; no leftover `{{ }}` | `routing` passes ABA checksum; `account` 4–17 digits; `account_type` checking or savings; `holder_name` letters, spaces, `.'-`, max 60 |
| Every other field | must not contain anything card-like | must not contain anything card-like, nor a run of 9+ digits (except `order_id`, `customer_ref`) |

A failed check returns HTTP 400 `{"status": "error", "reason": "invalid", "message": "<field>"}`
and nothing is sent to the gateway. A transform exception returns 400 with
`reason: "transform-error"`.

Gateway bodies: NMI `transact.php` form fields (`type=sale`, `ccnumber`, `ccexp` MMYY, `cvv`,
`zip`, `industry=moto`, `customer_vault=add_customer` to save; eCheck `payment=check`,
`checkname`, `checkaba`, `checkaccount`, `account_type`, `account_holder_type=personal`,
`sec_code`). Authorize.net `createTransactionRequest` JSON (`authCaptureTransaction`,
`creditCard` or `bankAccount`, `profile.createProfile` and `customer.id` to save,
`order.invoiceNumber`, `billTo.zip`, `retail.marketType=1` for cards, `echeckType` for
eChecks). See [DESIGN.md](DESIGN.md#gateways) and [ECHECK.md](ECHECK.md#gateway-mapping).

### The normalized reply

The response transform reduces the gateway reply to (example values):

```json
{
  "status": "approved",
  "gateway": "nmi",
  "response_code": "100",
  "message": "SUCCESS",
  "transaction_id": "10000000001",
  "auth_code": "123456",
  "avs": "N",
  "cvv_result": "M",
  "vault_id": ""
}
```

Authorize.net replies add `payment_profile_id`; `message` is cut to 80 characters (NMI) or
120 (Authorize.net). If the reduced reply contains anything card-like, the transform returns
`{"status": "error", "reason": "card-data-in-response"}` instead. If the gateway reply cannot
be parsed: `{"status": "unknown", "reason": "unparseable-gateway-response"}`.

| Gateway result | `status` |
|---|---|
| NMI `response=1`; Authorize.net `responseCode=1` | `approved` |
| NMI `response=2`; Authorize.net `responseCode=2` | `declined` |
| Authorize.net `responseCode=4` | `held` |
| NMI `response=3` with "Duplicate transaction…"; Authorize.net error code 11 | `duplicate` |
| Anything else the gateway answered | `error` |

Airlock keeps only `status`, `transaction_id`, `auth_code` and `vault_id` from the reply.

### Outcome classification

Airlock (`classify_charge_reply`) trusts a reply only when it can prove where it came from:

| Proxy reply | Outcome |
|---|---|
| HTTP 200, `status` in approved / declined / duplicate / held | that status |
| HTTP 200, `status: error` with a `gateway` field (the gateway answered with an error) | `error` |
| HTTP 400 with `reason` `invalid` or `transform-error`, or a vault `proxy_error` (e.g. a deleted or expired token) | `error` (rejected before the gateway) |
| Timeout, transport error, unparseable body, anything else | `unknown` |

| Outcome | Money moved? | Caller hears | Model is told |
|---|---|---|---|
| `approved` | yes | "You're all set..." with the last four of the reference | `approved, Visa ending 1111, amount 84.20 USD, reference ending 4471.` |
| `declined` | no | offered a second card; after two declines, a link or a person | (capture continues) / `cancelled (card declined twice). No charge was made.` |
| `duplicate` | no new charge | "this exact payment already went through a few minutes ago" | `duplicate ... No new charge.` |
| `held` | possibly (pending gateway review) | "received and is being reviewed" | `held for review by the gateway; not yet approved.` |
| `error` | no | "I couldn't process that card just now..." ("that payment" for eChecks) | `error, no charge made.` |
| `unknown` | **possibly** | "To be safe I won't charge you again; our team will confirm" | `outcome unknown (gateway timeout); flagged for manual review. Do not retry.` |

Only `declined`, `duplicate` and `error` mean no new charge. `approved`, `unknown`, `held` and
`duplicate` block any further capture for that order in the same conversation. An `unknown`
outcome is never retried. The model is told in lines prefixed `start_payment result:`.

Tokens the capture holds are deleted (`DELETE <VAULT_TOKENS_URL>/<id>`, header
`BT-API-KEY`) after the outcome, on cancel, and on a change of card or account. These tokens
are not tracked and expire on the vault TTL (`TOKEN_TTL_SECONDS`, 600 s as provisioned):
entries the script rejected (wrong length, failed check); a field's earlier token when the
caller presses `*` and re-enters it; tokens of a capture the caller abandoned (hung up);
keypad entries made outside a capture (model or desk turns); and duplicate tokens when
ElevenLabs re-sends the same newest keypad turn (the edge tokenizes on every request).

The charge runs in its own task, so it settles even if ElevenLabs drops the request. If
ElevenLabs sends another turn while it runs, the caller hears "Still working on that, just a
moment." A charge still running after 40 s is settled as `unknown`.

## Order lookup (merchant endpoint)

With `ORDER_LOOKUP_URL` set, Airlock prices an order with:

```
GET <ORDER_LOOKUP_URL>?order_id=<normalized id>
```

and expects HTTP 200 with JSON:

```json
{"amount": "84.20", "customer_name": "Test Payer", "paid": false}
```

| Field | Required | Use |
|---|---|---|
| `amount` | yes | Decimal string; rounded to cents; must be > 0 and ≤ `MAX_AMOUNT` |
| `customer_name` | no | Used as the eCheck account-holder name when the caller has not given one. Ignored (the caller is asked) unless it is plain letters, spaces and `.'-`, up to 60 characters; `null` counts as absent. |
| `paid` | no | `true` means "not found": the order cannot be paid again |

The id is normalized first: characters other than letters, digits and `-` are dropped and
the rest upper-cased. It must then match `^[A-Za-z0-9][A-Za-z0-9-]{0,19}$`. Any other status,
invalid JSON, a timeout (5 s), `paid: true` or a bad amount means "I couldn't find that order
number". Airlock sends no credentials, so restrict the endpoint by network. Return
`paid: true` once an order is paid: Airlock's own double-charge guard covers one conversation
only.

## GET /events/{conversation_id}

A read-only SSE stream of one conversation's events, for dashboards and the report page.

* `conversation_id` must match `conv_[A-Za-z0-9]{8,}`; otherwise `400 {"error": "bad request"}`.
* At most 4 subscribers per conversation and about 2,000 in total; beyond that, 400.
* The stream starts with `retry: 2000`, sends `: keep-alive` every 15 s when idle, and closes
  after 600 s or when the client disconnects. Each event is `data: <json>`.
* There is no authentication: anyone who knows a conversation id can watch it. Conversation
  ids are long and random, and events carry no card data, but restrict the route at your
  reverse proxy if you do not need it publicly.
* Events are kept in memory per process; there is no replay of past events.

Every event has `t` (ISO 8601 UTC) and `kind`, plus only these allowlisted fields:
`conversation`, `step`, `intent`, `status`, `ms`, `reason`, `gateway`, `turn`, `mode`, `hop`,
`brand`, `last4`, `len`, `field`, `say`. A `say` that contains anything card-like is dropped.

| `kind` | Fields | When |
|---|---|---|
| `turn` | `conversation`, `hop: "vault-in"`, `field` (`keypad-token`, `spoken-card-removed`, `refused-not-test-card`, `speech`), `mode` (`capture` or `model`); for `keypad-token` also `len`, `brand`, `last4` | Every request that passes the signature check and the tripwire |
| `capture-open` | `conversation` | A capture opened |
| `desk-order-found` | `conversation` | The desk found a payable order in the conversation |
| `capture` | `conversation`, `step`, `intent` (`keypad`, or the spoken intent) | Each capture turn |
| `capture-reply` | `conversation`, `step`, `ms`, `say`, `status` | Airlock's scripted answer |
| `charge-start` | `conversation`, `hop: "vault-out"`, `gateway`, `brand`, `last4` | The charge is sent to the vault |
| `charge` | `conversation`, `status`, `gateway`, `ms` | The outcome |
| `model-first-token` | `conversation`, `ms` | First token from the model (Full mode) |
| `refused` | `conversation`, `reason` | A safety refusal: card-like history, model output or tool arguments. A refusal for a missing conversation id has `conversation: null` and appears only in the event log. |
| `upstream-error` | `conversation` | The model endpoint failed |

`rejected` (bad edge signature), `tripwire` and `keepwarm` events carry no conversation, so
they appear only in the event log, never on the feed.

`last4` is the last four of a card or bank account; `say` is the scripted text, which includes
the amount and the spoken last four. Both are already spoken to the caller.

## GET /health

```json
{"ok": true, "open_captures": 0}
```

Shows the process is up and how many captures are open. It does not check the vault, the
gateway or the model. The Docker image's health check calls it every 30 s.

## Keep-warm ping

Every `AIRLOCK_KEEPWARM_SECONDS` (240), and when a caller reaches the confirm step, Airlock
posts:

```json
{"airlock_ping": true}
```

to the outbound proxy (`VAULT_OUTBOUND_URL`, `BT-PROXY-KEY`) and, if configured, to
`<VAULT_INBOUND_URL>/chat/completions` (`BT-PROXY-KEY: VAULT_INBOUND_KEY`). Both transforms
answer `{"pong": true}` themselves: the ping is never forwarded to Airlock or to a gateway.
Failures are ignored. Each round logs a `keepwarm` event with its duration.

## /airlock (static site)

If `AIRLOCK_SITE_DIR` names a directory (the Docker image sets `/app/site`), it is served at
`/airlock/` with `index.html` as the default; `/airlock` redirects there (301). It is the
public report and demo page. Leave the variable empty to serve nothing.
