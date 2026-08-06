"""Config suite: the generated opencode provider config matches `docs/spec/opencode-config.md`.

`amplifier-opencode prepare --project-dir <dir>` writes `<dir>/opencode.json` with a
`provider.amplifier` block built from amplifier-agent's live `/v1/models` listing. This
suite proves that generation end to end: the file lands at the fixed project-scope
path, the provider block has the exact shape the spec promises (npm/name/options/
models, with baseURL/apiKey under options), every discovered model is represented with
the all-or-nothing `limit`/`cost` rules honored, the model set agrees with what the live
server actually reports, and -- the preservation guarantee that makes `prepare` safe to
re-run -- unrelated top-level keys and sibling provider entries survive a second run
untouched.

Not a TUI suite, same reasoning as `suites/shadowing`/`suites/traversal`: there is
nothing to read off a screen here, only a JSON file and a live HTTP endpoint. `prepare`
performs the identical config-writing work `launch` does, minus the `opencode` exec, so
driving it via `driver.run_command` and reading the written file is ground truth.

Unlike `suites/cli`, this suite DOES restart the agent server: model discovery -- and
therefore the exact config content asserted on here -- is fixed at server STARTUP, so a
server left running from a previous suite would not reflect a clean run of `prepare`.
"""

from __future__ import annotations
