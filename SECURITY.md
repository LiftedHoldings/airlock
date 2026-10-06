# Security policy

Airlock exists to keep card and bank-account numbers away from language models and
merchant servers, so we treat any way around that as a serious bug.

## Reporting

Email **will@liftedholdings.com** with "Airlock security" in the subject. We aim to
acknowledge within one business day. Please do not open a public issue for a vulnerability.
Machine-readable contact: https://liftedholdings.com/.well-known/security.txt

## Never send real card data

Use only the gateways' published sandbox test cards in reports, issues and pull requests.
The public demo's vault edge discards any real-looking card number that is not a published
test card, but a real number would still have crossed ElevenLabs and the vault provider in
transit.

## What counts

* Any path by which a card number, security code, routing or account number reaches Airlock,
  its logs, its event feed, or the language model.
* Any way to make an outbound proxy send card data anywhere but the configured gateway.
* Any way to read a token's value with Airlock's credentials.
* Any way to cause a second charge for one confirmation.

## Design invariants

See [docs/DESIGN.md](docs/DESIGN.md#security-model) for the invariants, where each is
enforced, and the test that proves it. PCI scope: [docs/PCI.md](docs/PCI.md).
