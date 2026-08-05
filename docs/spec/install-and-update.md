# Install and Update

## Scope

How `amplifier-opencode` itself gets onto a machine, how the rest of the stack (amplifier-agent,
opencode) is brought up automatically at launch time, and how a user brings all three up to date
with one command. It does not cover the CLI flag surface (see `cli.md`), provider credentials or
the onboarding wizard (see `providers-and-credentials.md`), the generated opencode config (see
`opencode-config.md`), or the inventory of paths this tool reads and writes (see
`file-locations.md`).

## The one-line installer

Canonical invocation:

```
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
```

The installer does exactly three things, and deliberately no more: ensure `uv` is available,
install the `amplifier-opencode` CLI as a `uv` tool from git, and print the next command to run.

### Native Windows refusal

Checked first, before anything else runs. On native Windows (detected via the shell's own uname
string, e.g. Git Bash / MSYS / Cygwin), the installer refuses outright with this verbatim message,
then exits 1:

```
Error: Native Windows detected. Please run this inside WSL (`wsl --install`), then re-run.
```

WSL itself is unaffected by this check and proceeds normally; only a native (non-WSL) Windows shell
trips it. This refusal is specific to the shell installer; it says nothing about whether the CLI it
installs can run on native Windows (see Non-goals).

### Ensuring `uv`

If `uv` is already on PATH, nothing is installed. Otherwise the installer fetches and runs the
official astral install script (`https://astral.sh/uv/install.sh`), then makes the freshly
installed binary visible to the current shell before continuing:

```
export PATH="${HOME}/.local/bin:${PATH}"
```

`XDG_BIN_HOME`, when it is set in the environment, is additionally prepended to `PATH` at this
point (an unset `XDG_BIN_HOME` is never prepended as an empty segment, which would otherwise
introduce a `::` in `PATH` that POSIX shells read as the current directory).

Two failure modes are possible in this step, each printed verbatim and exiting 1:

```
curl is missing:
    Error: curl is required to install uv.

the freshly installed uv is still not resolvable on PATH:
    Error: uv installed but not on PATH. Open a new terminal and re-run.
```

### Installing the CLI

```
uv tool install --force git+https://github.com/microsoft/amplifier-app-opencode.git@${REF}
```

`REF` is `AMPLIFIER_OPENCODE_REF` if set in the environment, else `main`. There is no `--tag` or
`--ref` command-line flag for the installer itself; the ref is environment-variable-only.

### What the installer deliberately does not do

It intentionally does not install amplifier-agent or opencode, and does not run any credential
wizard. That lifecycle lives inside the CLI: the first `amplifier-opencode` launch (or
`amplifier-opencode setup`) self-heals the rest of the stack and walks the user through provider
credentials. Keeping the bootstrap in the tool, not this script, is deliberate: stdin here is the
curl pipe, not the user, so an interactive wizard cannot run correctly from here.

Concretely: after this installer finishes, only the `amplifier-opencode` binary exists.
amplifier-agent, opencode, and any provider credential are all still to be resolved, and are
resolved by the CLI itself on next invocation (see Self-heal below and
`providers-and-credentials.md`), never by this script.

### Exit codes

Any failing command aborts the installer immediately with that command's own non-zero status. An
explicit failure (the native-Windows guard, or either `uv` failure above) prints `Error: <message>`
in red to stderr and exits 1. Success (uv already present or freshly installed, CLI installed)
exits 0.

## Self-heal at launch

`launch` and `prepare` run the same preflight before doing anything else, unless `--no-bootstrap` is
passed, in which case the preflight does not run at all: nothing is checked, nothing is reported,
and the environment is assumed ready. `setup` always runs the preflight and ignores
`--no-bootstrap` entirely; see `cli.md` for this flag's full semantics. The preflight independently
ensures amplifier-agent and opencode are each present and healthy.

### amplifier-agent: three observable states

```
present, meets the minimum
    Reported and nothing else happens.

absent
    Interactive: prompted to install, then installed (or auto-installed
    immediately under --yes).
    Non-interactive without --yes:
        "  <name> is missing and this is a non-interactive shell. Re-run
        with --yes to auto-install, or install it manually."

present but below the required minimum
    "  amplifier-agent <version> is below required <minimum>; healing ..."
    Force-reinstalled to the exact pinned known-good version tag (never a
    moving branch), then re-checked. Never prompts before healing --
    healing a stale agent is treated as the whole point of the check, not
    an optional install.
```

Installing amplifier-agent fresh prefers `uv tool install` against the pinned known-good git tag;
if that fails, it retries via amplifier-agent's own official install script, where that script can
run (see opencode's states below for where it cannot).

### opencode: five observable states

opencode has no version floor for this adapter (any installed version can read the static config
this tool writes), so its preflight only cares about presence:

```
present
    Reported and nothing else happens, regardless of version.

absent, native Windows with none of npm/scoop/choco available
    "  Native Windows detected without npm/scoop/choco available."
    "    opencode's own documentation recommends running inside WSL for
    the best experience. Either:"
    "      1. Install WSL (`wsl --install`) and run amplifier-opencode
    there, or"
    "      2. Install Node.js, then: npm install -g opencode-ai"
    The whole preflight then fails.

absent, macOS/Linux/WSL with none of curl/brew/npm available
    "  Could not find a usable opencode installer on this system."
    "    Install one of:"
    "      - macOS/Linux/WSL:  curl -fsSL https://opencode.ai/install | bash"
    "      - Homebrew:         brew install opencode"
    "      - npm:              npm install -g opencode-ai"
    The whole preflight then fails.

absent, a usable install method is available
    Interactive: prompted to install, then installed via the best method
    for the host (see the ordered preference below). Non-interactive
    without --yes: the same "non-interactive shell" message shown above
    for amplifier-agent.

installed, but still not on PATH afterward
    "  ✗ opencode still not on PATH after install. You may need to open a
    new terminal (the installer edits your shell rc), then re-run."
    The whole preflight then fails.
```

The install method preference, in order:

```
macOS/Linux/WSL:  the bash installer (curl), else Homebrew, else npm, else nothing usable
native Windows:   npm, else scoop, else choco, else nothing usable
```

## `update`

See `cli.md` for `update`'s flags. It reinstalls the whole stack the user installed as one thing, in
three ordered stages:

```
1. amplifier-opencode (self)   ALWAYS runs.
2. amplifier-agent              ALWAYS runs.
3. opencode                     OPT-OUT. Skipped via --no-opencode, or by answering "no" at an
                                 interactive confirmation ("Update opencode as well?", default
                                 yes). Never prompted under --yes or when non-interactive.
```

Stage 1 (self) is the only stage whose ordinary failure modes are fatal to the whole command.
Stages 2 and 3 report an ordinary failure with a warning (`"! amplifier-agent could not be brought
up to date; see output above."` / `"! opencode could not be updated; see output above."`) and the
command still completes and exits 0. This is deliberate: a stale amplifier-opencode binary would
keep re-running stale update logic on every future invocation, so its own update must be the hard
gate; a stale amplifier-agent or opencode can still be healed on the very next launch's preflight.

One exception cuts across all three stages: if `uv` is not on PATH, any stage that needs it aborts
the whole command with exit 1 rather than reporting a warning and continuing.

Stage 2's actual behavior is three branches, not a single healing step: amplifier-agent absent ->
fresh install; present but below the required floor -> forced reinstall, without attempting the
agent's own update subcommand (see `agent-integration.md`'s "The version floor" for why anything
below the floor is distrusted); present and at or above the floor -> delegate to the agent's own
`update` subcommand, falling back to a forced reinstall only if that fails.

Six verbatim strings a caller of `update` can observe:

```
== Updating amplifier-opencode ==
== Updating amplifier-agent ==
== Updating opencode ==
== Skipping opencode update (left at its current version) ==
✓ Update complete.
  Run `amplifier-opencode doctor` to verify, or launch as usual.
```

Stage 1's own success and failure outcomes:

```
success:
    ✓ amplifier-opencode updated from <ref>.

before either outcome, the current install is always reported:
    Current install: amplifier-app-opencode <version> (via <source>)
    (a second line, only when the source is git and a commit is known:)
      commit: <first 12 chars>
```

Stage 2's two heal outcomes, printed by the same healing step the launch-time preflight uses:

```
healed successfully:
    ✓ amplifier-agent now <version> (>= <minimum>)

still below the floor after healing:
    ✗ amplifier-agent is <version> after healing, still below <minimum>.
    See output above.
```

The non-interactive install prompt shown when a component needs installing and a real terminal is
available:

```
  <name> is not installed. Install it now?
```

### Editable-install refusal

Stage 1 refuses to overwrite a developer's local editable checkout unless `--force` is given:

```
Refusing to overwrite an editable install. Pass --force to clobber it with the
latest <ref>, or update your dev checkout manually with `git pull`.
```

This refusal is a hard failure of the whole `update` invocation (see above).

## Versioning

Two independent surfaces report a version of the installed adapter:

```
--version   reports a version string compiled into the package (see cli.md)
update      reports the version recorded in the installed package's own metadata,
            alongside how it was installed (git ref and commit, editable, or metadata-only;
            see Stage 1's own success and failure outcomes above)
```

`doctor` reports no adapter version at all.

These two values are maintained separately and are not mechanically tied to each other. A past
release shipped them out of sync -- the compiled-in string was left at an older value while the
package metadata had moved on, so `--version` under-reported the installed adapter. Keeping the two
values equal is a release-time obligation with no automated check behind it.

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
PyPI distribution                                | git install only: `git+<repo>@<ref>`
                                                  | via `uv tool install`
a native-Windows path through the shell           | WSL, or install the CLI directly with
installer                                         | `uv tool install`; the shell installer
                                                  | refuses on native Windows, but the CLI
                                                  | itself does run on native Windows
a credential wizard inside the installer          | the CLI's own launch-time self-heal and
                                                  | `setup` command (see
                                                  | providers-and-credentials.md)
automatic update of amplifier-agent or opencode   | only the version-floor self-heal for
at ordinary launch, beyond the floor self-heal    | amplifier-agent; neither component is
                                                  | otherwise silently updated by a launch
rollback                                          | re-run install or update pinned to an
                                                  | earlier ref (`--ref` for `update`,
                                                  | `AMPLIFIER_OPENCODE_REF` for the
                                                  | installer)
```
