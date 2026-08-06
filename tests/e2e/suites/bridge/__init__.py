"""Bridge suite: the skills/modes bridge produces exact, byte-level opencode files.

``docs/spec/skills-and-modes-bridge.md`` specifies two generated faces (skill commands,
mode agents), two ownership manifests, and a reconciliation contract: every run rewrites
current sources, prunes files whose source disappeared, and never touches a file it did
not itself generate. This suite seeds ONE probe skill and ONE probe mode and proves, via
one initial ``amplifier-opencode prepare`` run plus two follow-up runs (source removed,
foreign file present), that all of that holds end to end.

Not a TUI suite. The bridge's effects (generated files, manifests) are FILESYSTEM side
effects with nothing to read off the screen, so assertions are ground truth gathered
inside the DTU with ``driver.run_command`` (same spirit as ``suites/shadowing`` and
``suites/traversal``). ``prepare`` does the same setup work as ``launch`` minus the exec,
so its stdout and the resulting filesystem state are exactly what a user's launch would
produce.
"""

from __future__ import annotations
