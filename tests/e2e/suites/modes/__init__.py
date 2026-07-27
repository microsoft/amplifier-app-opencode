"""Modes suite: amplifier modes surfaced as opencode PRIMARY AGENTS.

Unlike skills (which become ``/`` commands), an amplifier mode is persistent session
state, which is exactly an opencode primary agent. The launcher must fetch amplifier's
modes (``GET /v1/modes`` -> at least the built-ins ``plan`` and ``brainstorm``, plus any
project/user modes under ``.amplifier/modes/``) and generate one opencode primary-agent
file per mode, PREFIXED with ``amplifier-`` so it never collides with opencode's own
native agents (e.g. a native ``plan``/``build``). The `` (Amplifier)`` suffix is an
opencode-layer concern only; amplifier-agent itself deals in the bare mode name.

Discovery is verified through opencode's native agent list dialog (leader ``ctrl+x`` then
``a`` -> "Select agent"), the modes analog of the skills suite's ``/`` command discovery.
Behavior is verified
by selecting the mode-agent and judging the visible reply -- never by grepping logs or
inspecting generated files, so a test breaks only if user-visible behavior breaks.

Group A (discovery) exercises the opencode-launcher layer alone. Group B (behavior)
additionally depends on amplifier-agent honoring the per-turn mode selection.
"""

from __future__ import annotations
