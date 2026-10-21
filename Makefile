.DEFAULT_GOAL := help
SHELL := /bin/bash

VERSION := $(shell sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml)
BUNDLE := src/amplifier_app_opencode/plugin_bundle/amplifier.js
AMPLIFIER_AGENT_DIR ?= ../amplifier-agent
# Resolve git+https://github.com/microsoft/amplifier-agent from a local checkout instead of GitHub.
LOCAL_AGENT := GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.$(abspath $(AMPLIFIER_AGENT_DIR)).insteadOf \
	GIT_CONFIG_VALUE_0=https://github.com/microsoft/amplifier-agent

.PHONY: help plugin-deps plugin check test fmt sync-local lock-local

help: ## Show targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

plugin/node_modules: plugin/package.json plugin/bun.lock
	cd plugin && bun install --frozen-lockfile --ignore-scripts
	@touch $@

plugin-deps: plugin/node_modules ## Install the plugin's development dependencies

define build_plugin
	cd plugin && bun build src/index.ts --target bun --format esm \
		--external @opencode/plugin --external effect \
		--define 'AMPLIFIER_OPENCODE_VERSION="$(VERSION)"' \
		--banner '// amplifier-opencode-plugin $(VERSION)' \
		--outfile $(1) >/dev/null
endef

plugin: plugin/node_modules ## Build the plugin bundle into the package
	$(call build_plugin,$(abspath $(BUNDLE)))

check: plugin/node_modules ## Lint, type-check, and confirm the plugin bundle is up to date
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check --output-format concise --no-progress
	cd plugin && bunx tsc --noEmit
	$(call build_plugin,$(abspath build/amplifier.js))
	@cmp -s build/amplifier.js $(BUNDLE) || { echo "$(BUNDLE) is out of date. Run: make plugin"; exit 1; }

test: plugin/node_modules ## Run the unit tests
	uv run pytest
	cd plugin && bun test

fmt: ## Format and fix lint
	uv run ruff format src tests
	uv run ruff check --fix src tests

sync-local: ## uv sync with amplifier-agent from AMPLIFIER_AGENT_DIR (its committed HEAD)
	$(LOCAL_AGENT) uv lock --upgrade-package amplifier-agent-http
	$(LOCAL_AGENT) uv sync

lock-local: ## Relock amplifier-agent-http against AMPLIFIER_AGENT_DIR without syncing
	$(LOCAL_AGENT) uv lock --upgrade-package amplifier-agent-http
