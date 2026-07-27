"""Skills suite: amplifier-agent user-invoked skills surfaced as opencode "/" commands.

The launcher bridges amplifier-agent's user-invoked skills (the ``disable-model-invocation``
set returned by ``GET /v1/skills`` -- ``code-review``, ``council``, plus any the user drops
in an amplifier skill dir) into opencode as native slash commands
(``~/.config/opencode/command/<name>.md``). Running ``/<name>`` sends a
``!amplifier:skill <name> $ARGUMENTS`` body, which amplifier-agent dispatches SERVER-SIDE to
its own ``load_skill`` tool (the real fork for ``code-review`` / ``council``).

Covers discovery (a user-invoked skill appears as a ``/<name>`` command, one case per
amplifier discovery dir), a negative case (a model-invocable skill must NOT appear as a
command), and invocation (running the command runs the skill and passes trailing text as
arguments).

The suite's own ``skills_session`` fixture launches the TUI with ``AMPLIFIER_SKILLS_DIR``
and ``--host-config`` set, so the env and hostcfg discovery dirs are visible to the server
it starts.
"""

from __future__ import annotations
