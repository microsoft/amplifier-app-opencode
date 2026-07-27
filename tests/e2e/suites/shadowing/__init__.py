"""Shadowing suite: a name collision the agent resolved must be REPORTED, not hidden.

amplifier-agent discovers skills from several roots in priority order and the first
match wins. Before the agent grew shadow reporting, the loser vanished silently: a user
who dropped an override into ``~/.amplifier/skills`` had no way to learn that a
same-named file earlier in the search order was the one actually running.

``GET /v1/skills`` now returns, per skill, the winning ``source`` plus a ``shadowed``
list naming every same-named file that lost. This suite proves the LAUNCHER carries that
through end to end: seed a colliding skill in two discovery roots, run
``amplifier-opencode prepare``, and assert its output names both the file that runs and
the file that was shadowed -- and that the bridge still produced exactly one command
file for the name.

Not a TUI suite. The conflict report is printed during launch/prepare, before the TUI
takes over the screen, so it scrolls away instantly under tmux -- driving this through
the TUI would test nothing. ``prepare`` does the same setup work as ``launch`` minus the
exec, so running it via ``driver.run_command`` and reading its stdout observes exactly
the block a user sees, as ground truth (same spirit as ``suites/traversal``).
"""

from __future__ import annotations
