# Operations

Running Airlock in production. Start from a working sandbox ([QUICKSTART.md](QUICKSTART.md));
settings are in [CONFIGURATION.md](CONFIGURATION.md), formats in [API.md](API.md), scope in
[PCI.md](PCI.md).

## Deploy

Airlock is one container (`Dockerfile`, `docker-compose.yml`):

* Python 3.12 slim image, runs as an unprivileged user (uid 10001), code read-only.
* uvicorn on port 8080 with `--no-access-log`; compose publishes it on `127.0.0.1:8931` only.
* Compose sets `read_only: true`, a tmpfs on `/tmp`, `no-new-privileges`, and
  `restart: unless-stopped`. Secrets come from `airlock.env` (keep it `chmod 600`, never commit
  it).
* One uvicorn worker. Do not add `--workers`: sessions live in process memory (see
  [Scaling](#scaling)).

```bash
docker compose up -d --build
curl -s http://127.0.0.1:8931/health
```

Put a TLS reverse proxy in front (nginx example in [QUICKSTART.md](QUICKSTART.md#5-run-airlock-behind-https)).
Requirements for that proxy:

* `proxy_buffering off` (or equivalent) for `/v1/chat/completions`, `/chat/completions` and
  `/events/`. Airlock also sends `X-Accel-Buffering: no`.
* Pass the request body through byte for byte: the edge signature covers the exact bytes.
* Expose only what you need: the chat route (called by the vault), `/health`, and if wanted
  `/events/` and `/airlock/`.
* Keep the host clock in sync (NTP). Signatures older or newer than 300 s are refused.

Production differences from the sandbox:

| Setting | Sandbox | Production |
|---|---|---|
| `BT_API_BASE` (provisioning) and vault URLs in `airlock.env` | `api.test.basistheory.com` | `api.basistheory.com` |
| `TEST_CARDS_ONLY` | `1` for any shared demo | `0` |
| `ANET_URL` | `apitest.authorize.net` | `api.authorize.net` |
| `ANET_ECHECK_SEC` | `PPD` | `TEL` (ask Authorize.net to enable it) |
| `AIRLOCK_DEMO` | `1` | `0`, with `ORDER_LOOKUP_URL` set |
| Gateway credentials | sandbox | live |
| Agent prompts, call limits, `max_duration_seconds` | demo values | your business |
| `widget.text_input_enabled` | on | off (typed text is stored in transcripts as typed) |
| Merchant name in the eCheck authorization | `us` | yours (`AIRLOCK_MERCHANT_NAME`) |

## Health

`GET /health` returns `{"ok": true, "open_captures": N}`. It proves the process is up. It does
not check the vault, the gateway or the model. For an end-to-end check, run
`tests/live/coldstart_probe.py` (vault proxies answer) and a scripted call
([TESTING.md](TESTING.md#live-tests)).

## Logs

* **uvicorn's access log is off.** Request lines are not logged.
* **Request bodies are never logged.** Airlock writes only its allowlisted event records
  (fields listed in [API.md](API.md#get-eventsconversation_id)).
* **Where the events go.** Airlock prints each event to stdout as a JSON line (so
  `docker logs airlock` shows them) and, if `AIRLOCK_EVENT_LOG` is set, also appends it to
  that file. Set `AIRLOCK_EVENT_LOG` for a copy that survives container recreation.
* The container's filesystem is read-only. `/tmp` is writable but lost on restart. For a
  persistent log, mount a directory owned by uid 10001, e.g. `docker-compose.override.yml`:

  ```yaml
  services:
    airlock:
      environment:
        AIRLOCK_EVENT_LOG: /var/log/airlock/events.jsonl
      volumes:
        - ./logs:/var/log/airlock
  ```

  ```bash
  mkdir -p logs && sudo chown 10001 logs
  ```

  Rotate the file with your usual tool (e.g. logrotate with `copytruncate`).
* Never set `AIRLOCK_CAPTURE_INBOUND` in production. It records conversation text for test
  evidence.
* The events hold the last four of cards and bank accounts and the scripted text spoken to
  the caller. Treat the log as customer data under your retention policy.

## The live event feed

`GET /events/{conversation_id}` streams one conversation's events as SSE. It is what the
public report page draws its flow diagram from, and it is useful for a support console. It has
no authentication (conversation ids are long and random). If you do not need it publicly,
block `/events/` at the reverse proxy or allow it only from your own network. The feed is per
process; it does not replay.

## Latency, keep-warm and cold starts

Measured on 2026-10-06 against a Basis Theory test tenant, Airlock on a small cloud VM:

| What | Measured |
|---|---|
| Keypad turn through the vault and back (warm) | median ~0.65 s (648 ms, 13 samples); Airlock's own handling under 20 ms |
| A charge, end to end through the outbound proxy | about 4–5 s |
| A cold transform runtime | added about 10.8 s to the first request |
| Streaming | the vault proxy **buffers** Airlock's reply: time to first byte equals total time |

What covers each:

* **Cold runtimes.** Airlock pings both proxies every `AIRLOCK_KEEPWARM_SECONDS` (240) with
  `{"airlock_ping": true}`, which the transforms answer themselves, and pings the outbound
  proxy again when a caller reaches the confirm step. Set `VAULT_INBOUND_URL` and
  `VAULT_INBOUND_KEY` or only the outbound proxy is kept warm.
* **The charge wait.** Because the vault buffers the reply, a filler from Airlock is not heard
  early. The agent's soft timeout speaks "One moment." after 2 s of silence, then up to two
  more static fillers.
* **ElevenLabs' own timeout.** `cascade_timeout_seconds` is 15 (default 4). If ElevenLabs
  retries during a charge, Airlock answers "Still working on that" and never charges twice.

Watch the `keepwarm` event's `ms` and the `charge` event's `ms` for drift.

## Rotating keys

Re-running a provisioning script changes some keys and not others:

| Secret | How to rotate | What else changes |
|---|---|---|
| `AIRLOCK_EDGE_SECRET` | Generate a new one; run `cli/provision_basis_theory.py --update-transforms` with it; update `airlock.env`; restart Airlock | Airlock accepts one secret at a time, so requests between the two steps get 401. Do it in a quiet window. |
| Airlock's service key (`VAULT_API_KEY`) | Run `cli/provision_basis_theory.py` (full, without `--update-transforms`) | The script deletes and recreates all three applications, so **all** application keys change; put the new `service_api_key` in `airlock.env` and restart. The inbound proxy, and each outbound proxy **whose gateway credentials are set in the environment for this run**, are updated in place to the new applications. Set the credentials for every gateway you use: an outbound proxy skipped on this run stays attached to the deleted `airlock-outbound` application. Run in a quiet window: between the delete and the proxy update, proxies point at deleted applications. |
| Gateway credentials | Change them at the gateway; run `--update-transforms` with the new values | They live only in the proxy configuration. Airlock is not touched. |
| Proxy keys (inbound, outbound) | Not done by the scripts: existing proxies are updated in place and keep their keys. Use Basis Theory's own tools to replace a proxy key, then update `airlock.env` and the ElevenLabs secret | |
| The ElevenLabs workspace secret `airlock-vault-inbound-key` | `cli/provision_elevenlabs_agent.py` deletes and recreates it. `cli/provision_elevenlabs_desk.py` reuses an existing secret and does not update its value: change it in ElevenLabs (or delete it) first | |
| `BT_MANAGEMENT_KEY` | In Basis Theory | Lives only with the operator; never on the Airlock host |
| `UPSTREAM_API_KEY` | At your model provider; update `airlock.env`; restart | |

After any rotation, run the live checks ([TESTING.md](TESTING.md#live-tests)) and one test
call.

## Scaling

Airlock keeps all per-call state in one process's memory:

* `SessionStore` in `airlock/sessions.py`: open captures, payment results for the model, and
  the settled-orders record that blocks a second charge. TTL 600 s, refreshed on access.
* The live event feed's subscriber queues and the in-flight charge tasks in `airlock/app.py`.

So run **one instance with one worker**. One instance handles many concurrent calls: a turn is
mostly waiting on the vault, the model or the gateway. To run more than one instance:

1. Replace `SessionStore` with a shared store (e.g. Redis) that keeps the same interface:
   `get`, `put`, `finish`, `settle`, `settled`, `handbacks`, `open_count`. Captures must be
   saved after every change (the script mutates the `Capture` object in place), and two
   instances must not run the same conversation's turn at once (use a per-conversation lock).
2. Move the event feed to a shared pub/sub, or route `/events/` to every instance.
3. Keep keep-warm on, on at least one instance.

A restart drops open captures, payment results and the settled-orders record. On the next
turn a caller mid-capture starts the payment over (the desk reopens it from the order id in
the conversation; in Full mode the model answers). A charge that was in flight is not recorded
by Airlock and must be reconciled from the gateway. Restart in a quiet window.

## Monitoring

Suggested alerts, from the event log:

| Event | Why |
|---|---|
| `charge` with `status: unknown` | Money may have moved. Reconcile in the gateway by order id (`orderid` on NMI, `invoiceNumber` on Authorize.net). |
| `charge` with `status: held` | Pending gateway review. |
| `tripwire` | Card data reached Airlock: the vault edge failed or was bypassed. Investigate at once. |
| `rejected` (bad edge signature) | Someone is calling Airlock directly, or the edge secret or clock is wrong. |
| `refused` | The cleaner, output guard or tool check stopped something; or a prompt lacks the conversation id. |
| `upstream-error` | The model endpoint is failing (Full mode). |
| `keepwarm` `ms` rising, or missing | The vault is slow or the keep-warm loop stopped. |
| Docker health check failing | The process is down. |

Also reconcile approved charges against your orders daily, and watch ElevenLabs' call limits.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Charge comes back `duplicate`; caller hears "this exact payment already went through" | NMI refuses the same card and amount for a while (about 20 minutes observed), whatever the order id. Authorize.net returns error 11 for duplicates. | Expected protection. For tests, vary the amount (`T<cents>` orders in demo mode). |
| Authorize.net eCheck fails, gateway message error 246 "This eCheck.Net type is not allowed" | TEL is not enabled on the merchant account; sandbox accounts reject it. | Sandbox: provision with `ANET_ECHECK_SEC=PPD`. Production: ask Authorize.net to enable TEL. |
| NMI says "The checkname field is required" | An eCheck was sent without the account-holder name. Through Airlock, the outbound transform rejects an empty name first (400 `invalid`, `holder_name`), which the caller hears as "couldn't process". | Both modes use the model's `account_holder_name` (Full) or the lookup's `customer_name`, and otherwise ask. Check that your lookup does not return a blank or non-alphabetic name. |
| Voice calls drop immediately; text chats work | `require_origin_header` is on in the agent's auth settings. | Set it to `false` (the provisioning scripts do). |
| Airlock returns 401; `rejected` events | The request did not come through the vault (agent URL points straight at Airlock); the edge secret differs between the inbound proxy and `airlock.env`; the clock is off by more than 300 s; or something between the vault and Airlock changed the body. | Point the agent at the inbound proxy; re-run `--update-transforms` with the same secret as `airlock.env`; enable NTP; pass the body through unchanged. |
| "I couldn't find that order number" (Full) or the desk keeps asking "Which order number?" | Neither `ORDER_LOOKUP_URL` nor `AIRLOCK_DEMO=1` is set; or the lookup returned non-200, invalid JSON, `paid: true`, an amount ≤ 0 or above `MAX_AMOUNT`, or took over 5 s; or the id has characters outside letters, digits and `-` or is over 20 characters. | Set `ORDER_LOOKUP_URL` and test it with `curl "<url>?order_id=A1042"`. Check `MAX_AMOUNT`. |
| "I'm sorry, I can't take a payment on this line right now." | The agent prompt lacks `{{system__conversation_id}}`, so Airlock has no `conv_…` id. | Add `Conversation id: {{system__conversation_id}}.` to the prompt. |
| "I'm sorry, I can't take a card payment right now. I can send you a secure payment link instead." | The inbound transform failed closed (`X-Airlock-Edge-Error` header: `transform-error` or `residual-card-data`). Nothing reached Airlock. | Check the inbound proxy's configuration (`EDGE_SECRET` present), the transform logs in Basis Theory, and that the transform deployed. |
| "I'm sorry, I can't take a card payment right now." (no link offer) | Airlock's tripwire found card data in a request. | Treat as an incident: the edge was bypassed or failed. |
| First turn after a quiet period takes ~10 s or times out | Cold transform runtime. | Set `VAULT_INBOUND_URL` and `VAULT_INBOUND_KEY`; keep `AIRLOCK_KEEPWARM_SECONDS` at 240 or lower; keep `cascade_timeout_seconds` at 15. |
| Silence for several seconds after the caller confirms | The charge takes 4–5 s and the vault buffers the reply. | Keep the agent's `soft_timeout_config` (2 s, static fillers). Do not rely on `AIRLOCK_CHARGE_FILLER`. |
| "Sorry, I'm having trouble right now. Could you say that again?" | The model endpoint failed (Full mode). | Check `UPSTREAM_*` and the provider's status. |
| "I'm sorry, I can only take payments on this line." on every turn | `UPSTREAM_*` is empty and the request was not a desk turn: usually a desk agent missing the `X-Airlock-Mode: desk` header. | Add the header to the desk agent's `request_headers`, or configure `UPSTREAM_*` for Full mode. |
| Caller says "Ready" on the desk and nothing happens beyond "Which order number?" | The order id was never said in the conversation, or the lookup could not price it. | The main agent's prompt should confirm the order number before transferring. |
