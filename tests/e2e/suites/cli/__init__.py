"""CLI suite: fast, read-only smoke checks of the `amplifier-opencode` command surface.

This suite protects `docs/spec/cli.md` at the level a user experiences it BEFORE any
model interaction happens: does `--version` name both components, does `--help` list
the real subcommands, does an unknown subcommand fail the way scripts depend on, and
does `doctor` describe the environment honestly (including tying its exit code to what
it actually reported, not a hardcoded assumption).

Deliberately read-only: no fixture seeds files, starts a server, or writes config.
`doctor` and `--version` are read-only by contract, and the config check tolerates
whatever opencode config state already exists in the DTU -- these tests only observe,
they never arrange. That is what makes this suite safe to run against the WARM DTU
alongside every other suite without a `_stop_agent_server` precondition: nothing here
depends on server state freshly discovered at startup.

Not a TUI suite: every check here is a single non-interactive command and its exit
code/stdout, nothing is read off a rendered screen.
"""

from __future__ import annotations
