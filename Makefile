# amplifier-app-opencode
# Local command surface. docs/spec/ is the contract; tests/e2e/ proves it
# against the real installed binary in a container. There is no CI in this
# repo, so this Makefile is the gate.

.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help check fmt e2e e2e-up e2e-down

help: ## Show available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

check: ## Lint + format check (src/, tests/). Seconds. No container needed.
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

fmt: ## Auto-fix formatting and lint (src/, tests/). No container needed.
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# No bare "test"/pytest target here: tests/e2e/*.py self-skip green when
# amplifier-digital-twin is absent or no warm DTU exists (see
# docs/E2E_TESTING.md), so a plain `pytest` run over the whole tree would
# report green without having gone through the harness. Run the mocked unit
# tests directly with `uv run pytest tests/ --ignore=tests/e2e -q`; use
# `make e2e`, which goes through the harness and actually needs a DTU, for
# the rest.

e2e: ## Run e2e suite(s) in a DTU. SUITE=<name> for one; bare runs all (slow).
ifdef SUITE
	uv run python tests/e2e/cli.py run $(SUITE)
else
	@echo "WARNING: running the FULL e2e suite (all suites). This takes several"
	@echo "minutes end to end (DTU provision, install, run). Pass SUITE=<name>"
	@echo "to scope to one suite instead, e.g. make e2e SUITE=chat"
	uv run python tests/e2e/cli.py run
endif

e2e-up: ## Bring up the warm DTU without running a suite.
	uv run python tests/e2e/cli.py up

e2e-down: ## Tear down the warm DTU.
	uv run python tests/e2e/cli.py down
