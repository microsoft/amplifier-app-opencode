"""Agent integration suite: this tool's lifecycle contract with the amplifier-agent process.

``docs/spec/agent-integration.md`` specifies how this tool spawns, reuses, and never stops
the amplifier-agent server it drives; the version floor it enforces against the locally
installed binary; and how it degrades when a best-effort surface (skills/modes listing)
is unreachable. This suite drives that contract directly against the real installed
``amplifier-opencode`` binary and a real amplifier-agent process, deliberately starting
and stopping the server itself to exercise both sides of the reuse-vs-spawn decision.

Not a TUI suite. Every assertion is either a process-lifecycle fact (is the server up?
did its PID change?) or captured CLI stdout, gathered with ``driver.run_command`` (same
spirit as ``suites/shadowing``, ``suites/traversal``, and ``suites/bridge``).

Every test is written to be self-sufficient: each establishes its own precondition (server
up or down) rather than depending on what an earlier test in the file happened to leave
behind, so the suite stays green regardless of collection order.
"""

from __future__ import annotations
