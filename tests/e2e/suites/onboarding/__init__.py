"""Onboarding suite: `onboarding.py`'s credential wizard, and its secret-redaction guard.

``_auth_set`` in ``onboarding.py`` pipes a provider credential to ``amplifier-agent auth
set <provider> --stdin`` over stdin (never argv), so the plaintext secret never appears
in the machine's process listing. That much is defense-in-depth against `ps`/`/proc`. But
if the delegated command ever exits non-zero and echoes the secret back in its own stderr
or stdout, this module's LAST line of defense is a scrub: every occurrence of the
plaintext value is replaced with ``***`` before the failure message reaches the user's
terminal. Until this suite, that scrub had zero automated coverage anywhere -- no e2e
scenario ever drove the wizard at all, because every other suite's DTU starts with a
resolvable ``ANTHROPIC_API_KEY`` already present (see ``docs/ISSUES.md``'s "Known
coverage gaps" section, "Highest risk" entry).

No real amplifier-agent can be made to report zero resolvable providers AND fail `auth
set` with the secret echoed back on demand, so this suite installs a FAKE
``amplifier-agent`` executable in a scratch directory and prepends that directory to
PATH for exactly one spawned command (``amplifier-opencode --yes setup``). This mirrors
``suites/agent_integration``'s precedent of controlling an external dependency (there: a
fake HTTP server standing in for amplifier-agent's HTTP face; here: a fake CLI standing
in for amplifier-agent's process face) -- it controls the boundary this tool talks to,
never amplifier-opencode's own internals.

Not a TUI suite in the ``suites/chat``/``modes``/``skills`` sense (no opencode TUI is
ever spawned), but IS a tmux-driven suite: the wizard's key prompt uses hidden input
(``click.prompt(..., hide_input=True)``), which requires a real, interactive TTY on both
stdin and stdout -- exactly what tmux provides and what an ``exec``-only driver cannot.

Safety: the fake binary lives under a scratch directory unique to this suite, never
overwrites or shadows the real ``amplifier-agent`` install used by every other suite
sharing this warm DTU, and is removed in fixture teardown. ``setup`` itself never starts
the amplifier-agent server or writes any opencode config, so this suite touches nothing
any other suite in the container depends on.
"""

from __future__ import annotations
