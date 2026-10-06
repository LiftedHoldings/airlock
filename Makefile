# `make prove`: every offline proof in one command (no network, no accounts).
PY ?= python

.PHONY: prove test lint parity leakscan

prove: lint test leakscan
	@echo "PROVE: all offline checks passed"

test:
	$(PY) -m pytest -q tests

lint:
	ruff check airlock cli tests
	node --check vaults/basis_theory/inbound.js
	node --check vaults/basis_theory/outbound.js

leakscan:
	$(PY) tests/e2e/leak_scan.py site/data/evidence.json
