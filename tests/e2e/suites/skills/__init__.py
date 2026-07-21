"""Skills suite: user-invocable skills surfaced through the opencode ``/skills`` menu.

Covers discovery (a seeded probe skill appears in the native ``/skills`` popup) and
invocation (typing ``/<name>`` runs the skill and passes trailing text as arguments)
across EVERY discovery directory -- opencode-native AND amplifier-agent-specific.

These are intentionally-FAILING (TDD) tests: the launcher does not yet bridge
amplifier-agent skill dirs into opencode's native ``/skills`` menu, so the
amplifier-dir cases go red until that feature is built. The opencode-native and
built-in cases serve as the "don't regress" baseline.
"""

from __future__ import annotations
