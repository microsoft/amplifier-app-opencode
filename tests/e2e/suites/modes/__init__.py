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

These are intentionally-FAILING (TDD) tests. Today the launcher only writes a
provider/models block into ``opencode.json``; it does not read ``/v1/modes`` and generates
no ``<mode> (Amplifier)`` agents, so every case below goes red. Group A (discovery) fails
purely at the opencode-launcher layer. Group B (behavior) additionally depends on
amplifier-agent interpreting the per-turn mode selection, which is unbuilt, so those stay
red longer. The point of these tests is to specify exactly what "done" looks like.
"""

from __future__ import annotations
