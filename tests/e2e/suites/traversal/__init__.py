"""Traversal suite: a hostile skill NAME must not escape the opencode command dir.

The skills bridge turns a server-supplied skill ``name`` into a filename under
``~/.config/opencode/command/`` and records that name in an ownership manifest that a
later run prunes by unlinking. This suite seeds a skill whose name traverses
(``../../../../tmp/e2e-traversal-pwned``) and asserts both halves stay closed: nothing is
written outside the command dir, and no traversing name reaches the manifest (which would
arm the next run's prune into an arbitrary delete).

Not a TUI suite. The bridge's effect is a FILESYSTEM side effect with nothing to read off
the screen, so assertions are ground truth gathered inside the DTU with
``driver.run_command`` (same spirit as ``suites/shadowing``).
"""
