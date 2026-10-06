# Contributing

Thanks for helping. Airlock handles payments, so the bar is evidence: every change comes with
a test that would fail without it, and nothing in the repository may ever hold real card data
or a secret.

## Development setup

You need Python 3.12 and Node.js (CI uses Node 22; the parity tests and `node --check` need
it).

```bash
git clone https://github.com/LiftedHoldings/airlock.git
cd airlock
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
make prove
```

`make prove` must end with `PROVE: all offline checks passed`, and pytest's summary must show
no skipped tests (a missing `node` skips the parity tests silently). Details in
[docs/TESTING.md](docs/TESTING.md).

To run the whole chain locally, use the vault emulator (`vaults/emulator/server.js`), which
runs the same transform files as the real vault: see
[docs/TESTING.md](docs/TESTING.md#end-to-end-driver).

Code style: `ruff check airlock cli tests spike` must pass. Python is formatted with
`ruff format --line-length 100`. Match the surrounding code; keep functions small and the
comments about *why*.

## Rules

* **Never commit real card data or secrets.** Use the gateways' published test cards (e.g.
  `4111111111111111`, `5424000000000015`, `370000000000002`) and sandbox bank details only,
  in code, tests, issues, logs and screenshots. No API keys, proxy keys, edge secrets or
  gateway credentials anywhere in the repository; `airlock.env` and `vault.json` stay local.
* **Keep the Python and JavaScript detectors identical.** The card-like detector, spoken-digit
  conversion and Unicode digit mapping exist in `airlock/core/cards.py` and in
  `vaults/basis_theory/inbound.js`. Change both in the same pull request; the parity tests in
  `tests/test_cards_and_cleaner.py` and `tests/test_hardening.py` must pass with Node.js
  installed.
* **The transforms are card-environment code.** `vaults/basis_theory/*.js` run on card data
  inside the vault. Keep them small, dependency-free (beyond the vault SDK), and covered by
  tests. Describe any change to them clearly in the pull request: deployments must re-run
  `cli/provision_basis_theory.py --update-transforms` and their assessor may ask about it.
* **Fail closed.** No `try/except` that turns an unclear result into a success. An unclear
  charge outcome is `unknown`, never "no charge". Do not loosen a test to make it pass.
* **Logs stay allowlisted.** Never log a request body. New event fields must be added to the
  allowlist in `airlock/app.py` deliberately and must never carry card or bank digits beyond a
  last four.
* **Docs follow the code.** If behaviour changes, update the matching document in `docs/` in
  the same pull request, and add a line to [CHANGELOG.md](CHANGELOG.md).

## Pull request checklist

- [ ] A test that fails without the change and passes with it.
- [ ] `make prove` passes locally with Node.js installed (no skipped tests).
- [ ] `ruff check airlock cli tests spike` passes.
- [ ] If `cards.py` or `inbound.js` changed: both changed identically; parity tests pass.
- [ ] If a vault transform changed: the change is described, and the live checks
      ([docs/TESTING.md](docs/TESTING.md#live-tests)) were run against a test tenant.
- [ ] No real card numbers, bank details, keys or secrets in the diff (run
      `python tests/e2e/leak_scan.py` over any new data files).
- [ ] Docs and [CHANGELOG.md](CHANGELOG.md) updated.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contributions are licensed under the Apache License 2.0
([LICENSE](LICENSE)).
