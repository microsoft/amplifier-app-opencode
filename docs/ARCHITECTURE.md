# Architecture & design notes

Background on *why* amplifier-app-opencode works the way it does. This is
contributor/maintainer material — end users don't need any of it (see the
top-level `README.md` for install and usage).

## Live config from a "static" contract

opencode's documented contract for custom OpenAI-compatible providers (the
["Atomic Chat" pattern](https://opencode.ai/docs/providers/#atomic-chat))
requires a **static** `models` block listing every model ID the upstream
server serves. opencode does not auto-fetch `/v1/models` at runtime for
custom-config providers.

Rather than maintain the model list by hand or attempt fragile runtime
monkey-patches against opencode's internals, this binary **materialises the
static config from the live `/v1/models` response before opencode launches**.
Every invocation re-syncs. The model list is therefore always live; the
"static" nature of opencode's config is just an implementation detail.

## No forks, no patches

opencode itself is unmodified. The adapter lives entirely outside both
upstream projects — no plugins, no patches, no npm packages, no JavaScript.
