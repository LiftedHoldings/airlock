# eCheck (ACH) payments by phone

Airlock takes bank-account payments the same way it takes cards: the numbers are typed on
the keypad, become vault tokens at the edge, and are detokenized only inside the outbound
proxy that builds the gateway request.

Related: [API.md](API.md#outbound-the-charge-request) (request format),
[CONFIGURATION.md](CONFIGURATION.md) (SEC code settings), [DESIGN.md](DESIGN.md).

## Caller flow

1. The caller asks to pay "from my bank account".
   * **Full Custom LLM mode:** the model asks for the **name on the account** in ordinary
     conversation (a name is not sensitive), then calls
     `start_payment(order_id, method="check", account_holder_name=...)`. If it calls without
     a usable name, Airlock asks for it and waits for the model to call again.
   * **Payment desk mode:** the desk treats the payment as an eCheck if the caller's last few
     turns mention bank, checking, savings, e-check, ACH, routing or account number. The
     script asks "please say the name on the bank account" first.
   * Both modes: if the merchant's order lookup returns a usable `customer_name` (plain
     letters), Airlock uses it and does not ask. In Full mode that applies only when the model
     passed no name at all; a name the caller gave that the bank cannot take (accents, commas)
     is asked for again, never replaced by the order's name.
2. Airlock takes over: routing number (9 digits, ABA checksum checked at the vault edge and
   again in the outbound transform), then account number (4–17 digits), then 1 = checking /
   2 = savings (spoken "checking" / "savings" also works). No ZIP is asked.
3. Optional (`AIRLOCK_OFFER_SAVE=1`): save the account for next time (gateway vault).
4. Authorization, read in full before the debit (NACHA TEL):
   *"To authorize: you're allowing Lifted Coffee Roasters to make a one-time electronic
   debit of 12 dollars from your checking account ending in 3 1 2 3, today. You can revoke
   this by calling us before it settles. Press 1 to authorize, or 2 to change the account."*
   The merchant name comes from `AIRLOCK_MERCHANT_NAME` (default "us"); set it to your
   business name. Pressing 2 restarts at the routing number.

## Why the name is asked for

Both gateways require it, verified 2026-10-06 (reproduce with
[tests/live/echeck_probe.py](TESTING.md#live-tests)):

| Gateway | Without a name |
|---|---|
| NMI | `response=3`, "The checkname field is required" |
| Authorize.net | schema error: `bankAccount` expects `nameOnAccount` |

Through Airlock, an empty name never reaches the gateway: the outbound transform refuses it
(`holder_name`), which the caller hears as an error with no charge.

## Gateway mapping

| Field | NMI (`transact.php`) | Authorize.net (`createTransactionRequest`) |
|---|---|---|
| Method | `payment=check` | `payment.bankAccount` |
| Routing | `checkaba` | `routingNumber` |
| Account | `checkaccount` | `accountNumber` |
| Type | `account_type=checking\|savings`, `account_holder_type=personal` | `accountType` |
| Name | `checkname` | `nameOnAccount` (22 chars max) |
| SEC code | `sec_code` (default `TEL`) | `echeckType` (default `TEL`) |
| Save | `customer_vault=add_customer` | `profile.createProfile` + `customer.id` |

## SEC code: TEL

Payments authorized over the phone are NACHA **TEL** entries. Merchants must keep evidence
of the oral authorization (a recording or a written notice sent before settlement) and the
authorization must state the amount, date, account and how to revoke.

* The provisioning scripts set `record_voice: false`, so ElevenLabs keeps no call audio. With
  those defaults you need the written-notice route, and Airlock does not send that notice:
  your order system must.
* **NMI** sandbox accepted `TEL` (approved).
* **Authorize.net** rejected `TEL` with error 246, "This eCheck.Net type is not allowed": TEL
  must be enabled on the merchant account by Authorize.net. The sandbox accepted `WEB`, `PPD`
  and `CCD`; the sandbox runs use `PPD` as a stand-in (`ANET_ECHECK_SEC=PPD` when
  provisioning). Production merchants should request TEL and leave the default.

The SEC code is set per outbound proxy (`ECHECK_SEC_CODE`, from `NMI_ECHECK_SEC` /
`ANET_ECHECK_SEC` at provisioning).

## Sandbox values

NMI: routing `123123123`, account `123123123`. Authorize.net: any valid routing number (e.g.
`011000015`) with any account number; amounts under $100 approved in our runs.
