# CLI Surface

## Scope

The complete `amplifier-opencode` command surface: dispatch (bare invocation, subcommands, unknown
input, signals), every global option and every subcommand's flags with their defaults and
environment variables, and the exit-code map. It does not cover install/self-update mechanics (see
`install-and-update.md`), provider credential handling or the onboarding wizard (see
`providers-and-credentials.md`), the generated opencode provider config (see `opencode-config.md`),
the skills/modes bridge (see `skills-and-modes-bridge.md`), or the full path inventory (see
`file-locations.md`).

## Dispatch

```
amplifier-opencode                 same as `amplifier-opencode prepare`
amplifier-opencode <unknown>        exit 2
SIGINT, at any point                exit 130
SIGTERM, at any point               exit 143
```

Subcommands: `launch`, `prepare`, `setup`, `doctor`, `update`.

Bare invocation dispatches to `prepare` with `prepare`'s own compile-time defaults for all seven of
its subcommand-local options (workspace, host config, project directory, no-start, agent binary
override, provider id, max context). The environment variables documented for those options are NOT
consulted on a bare invocation; they apply only when the subcommand name is typed explicitly. The
global options below DO read their environment variables on a bare invocation. None of `prepare`'s
flags are reachable when no subcommand name is typed.

`--help` and `-h` are both recognized on the top-level command and on every subcommand, and print
usage then exit 0.

## Global options

These apply to every subcommand (parsed before the subcommand name):

```
--version                 boolean; off unless given. Evaluated before every other option.
                          No env var.
                          Prints version information (see below) and exits before any
                          prerequisite check, install, or onboarding runs. Never prompts,
                          even under --yes.

--base-url TEXT           default "http://127.0.0.1:9099/v1". Env: AMPLIFIER_AGENT_BASE_URL.
                          amplifier-agent base URL used for the readiness probe and written
                          into the generated opencode provider config.

--api-key TEXT            default "local-dev-secret". Env: AMPLIFIER_AGENT_API_KEY.
                          Bearer token sent to amplifier-agent and written into the
                          generated opencode provider config.

-y, --yes                 boolean; off unless given. No env var.
                          Assume yes to install and onboarding prompts (non-interactive
                          bootstrap). Read by `launch`, `prepare`, and `setup`. NOT read by
                          `update`, which defines its own independent -y/--yes local to that
                          subcommand (see below).

--no-bootstrap            boolean; off unless given. No env var.
                          Skip the self-healing preflight entirely: no prerequisite
                          install/update check, no onboarding wizard. Assumes the
                          environment is already ready. Read by `launch` and `prepare`.
                          `doctor` and `update` never run the preflight regardless of this
                          flag; `setup` always runs the preflight and ignores this flag too
                          (see install-and-update.md).
```

### `--version` output

Three verbatim shapes, selected by amplifier-agent's detected state:

`<version>` stands for a literal dotted version and `<minimum>` for the declared floor.

```
Agent present and meets the minimum:
    amplifier-opencode <version>
    amplifier-agent    <version> (installed; minimum required <minimum>)

Agent present but below the minimum:
    amplifier-opencode <version>
    amplifier-agent    <version> (installed, below minimum; minimum required <minimum>)

Agent not installed:
    amplifier-opencode <version>
    amplifier-agent    not installed (minimum required <minimum>)
```

The agent version shown is the dot-joined `X.Y.Z` extracted from the detected version string; if no
`X.Y.Z` pattern is found in it, the raw detected string is shown instead.

## `launch`

```
amplifier-opencode launch [OPTIONS] [-- OPENCODE_ARGS...]

  --workspace TEXT              default "opencode". Env: AMPLIFIER_AGENT_WORKSPACE.
                                 Workspace name passed to the server. Only used when this
                                 invocation starts the server itself.
  --host-config PATH            default none. Env: AMPLIFIER_AGENT_HOST_CONFIG. Must be an
                                 existing file. Forwarded to the server's own config flag.
                                 Only used when starting the server; when omitted, the
                                 server auto-enables every provider whose credentials
                                 resolve.
  --project-dir PATH            default none (use the global opencode config). Write the
                                 opencode config, command files, and agent files into this
                                 directory's project scope instead of the global scope. It
                                 also becomes the working directory the opencode process is
                                 started in (the current directory otherwise), and the
                                 working directory of a server this invocation spawns.
  --no-start                    boolean; off unless given. Do NOT auto-start amplifier-agent.
                                 Fail loudly if it is unreachable.
  --no-launch                   boolean; off unless given. Discover models and write the
                                 opencode config, but do not exec opencode.
  --amplifier-agent-bin PATH    default none (resolve from PATH). Env: AMPLIFIER_AGENT_BIN.
                                 Override which server binary is used when starting the
                                 server.
  --provider-id TEXT            default "amplifier". Key under the opencode config's
                                 provider block that receives the generated entry.
  --max-context INTEGER         default none (forward the backend's advertised value
                                 verbatim). Env: AMPLIFIER_OPENCODE_MAX_CONTEXT. Minimum 1;
                                 a value below 1 is a parse error, exit 2. Clamp every
                                 discovered model's advertised context window to at most
                                 this many tokens.
  OPENCODE_ARGS                 every trailing positional argument, passed through to the
                                 opencode process unchanged. A leading `--` is optional and
                                 only needed to protect arguments that look like options;
                                 without it, an unrecognized option-shaped argument is a
                                 parse error, exit 2.
```

Bare positional arguments are collected with no separator required; `amplifier-opencode` itself
parses nothing past the point where its own known options end. A literal `--` is only needed to
pass an argument that would otherwise be read as one of `amplifier-opencode`'s own options.

## `prepare`

Identical option set to `launch` minus `--no-launch` (always behaves as if it were set) and minus
the `OPENCODE_ARGS` passthrough (there is nothing to exec). Never execs opencode.

```
amplifier-opencode prepare [OPTIONS]

  --workspace TEXT
  --host-config PATH
  --project-dir PATH
  --no-start
  --amplifier-agent-bin PATH
  --provider-id TEXT
  --max-context INTEGER
```

Same defaults, environment variables, and semantics as the identically named `launch` options above.

## `setup`

```
amplifier-opencode setup
```

No subcommand-local options. Runs the same prerequisite preflight `launch`/`prepare` run before
starting anything, then stops: no server start, no model discovery, no opencode config write, no
exec. Reads the global `--yes` for auto-install/auto-onboard. Ignores `--no-bootstrap` (this
command's entire purpose is to run the preflight; there is nothing to skip it to).

Two terminal outcomes:

```
prerequisites still not ready after the preflight, exit 1:
    Some prerequisites are not ready. Re-run with --yes to auto-install, or
    address the items above.

prerequisites ready, exit 0:
    ✓ Setup complete. Launch any time with: amplifier-opencode
```

## `doctor`

```
amplifier-opencode doctor
```

No subcommand-local options. Reads only the global `--base-url` / `--api-key`. The opencode-config
check always inspects the global config path; there is no project-scoped variant of this check, so
a project-scoped configuration (written via `--project-dir`) is never examined here. Output begins
with a header line followed by a blank line:

```
amplifier-opencode doctor
```

Then runs five checks in fixed order (amplifier-agent binary, opencode binary, server reachability,
opencode config, live models), then a provider-credential section (verbatim line shapes specified in
`providers-and-credentials.md`), then a summary line. Never installs, updates, or writes anything.

Per-check line shape:

```
  [ OK ]  <name>              <message>
  [FAIL]  <name>              <message>
  [INFO]  <name>              <message>
  [WARN]  <name>              <message>
```

The config check always inspects the literal key `amplifier`; it is not affected by
`launch`/`prepare`'s `--provider-id` option, which only names the key those commands themselves
write into the config -- `doctor` never reads that flag.

`<version>` in the `amplifier-agent` and `opencode` lines below is the raw first line of the
binary's own version output, not an extracted `X.Y.Z` (contrast `--version`'s output above, which
does extract `X.Y.Z`).

Verbatim messages, one per check, each tagged with its status:

```
amplifier-agent -- [ OK ] (binary present, meets minimum):
    amplifier-agent found at <path> (<version>, >= <minimum>)

amplifier-agent -- [FAIL] (binary present, below minimum):
    amplifier-agent at <path> is <version>; amplifier-opencode requires >= <minimum> (the
    agent resolves provider credentials at serve startup). Update with `amplifier-agent
    update`, or `amplifier-opencode update` to update both.

amplifier-agent -- [FAIL] (binary absent):
    amplifier-agent not on PATH. Install via `uv tool install amplifier-agent` or see
    https://github.com/microsoft/amplifier-agent.

opencode -- [ OK ] (binary present):
    opencode found at <path> (<version>)

opencode -- [FAIL] (binary absent):
    opencode not on PATH. Install via `curl -fsSL https://opencode.ai/install | bash` or
    see https://opencode.ai/docs/intro for other methods.

server -- [ OK ] (reachable):
    amplifier-agent server running at <base-url>

server -- [INFO] (unreachable -- amplifier-opencode auto-starts it, so this is not a FAIL):
    amplifier-agent server NOT running at <base-url> (amplifier-opencode will auto-start
    it on next launch)

opencode config -- [ OK ] (present and populated):
    opencode config has provider.amplifier with N models

opencode config -- [INFO] (not yet generated):
    opencode config at <path> not yet generated (run `amplifier-opencode` to create it).

opencode config -- [INFO] (generated but missing the provider block):
    opencode config at <path> has no provider.amplifier (run `amplifier-opencode` to
    populate it).

opencode config -- [FAIL] (malformed JSON):
    opencode config at <path> is malformed JSON: <parse error>

live models -- [ OK ] (server running, models found):
    Discovered N model(s): <id1>, <id2>, ... (first 5, "..." suffix if more than 5)

live models -- [WARN] (server running, zero models):
    /v1/models returned 0 models

live models -- [FAIL] (server running, request failed):
    /v1/models failed: <error>

live models -- [INFO] (server not running -- already reported by the server check):
    Skipped (server not running)
```

`opencode config`'s model count pluralizes properly ("1 model", "2 models"); it never emits the
parenthetical "model(s)" form. `live models`' own count uses the literal "model(s)" form shown
above, unchanged. `[INFO]` and `[WARN]` lines never count toward the exit code. Two things do:
a `[FAIL]` line among the five checks, and the provider-credential section counting as failed
(see below) -- the latter carries no `[FAIL]` tag of its own, so `doctor` can exit 1 with zero
`[FAIL]` lines on screen.

After the five checks and the provider section, one of two summary lines:

```
N check(s) failed. Fix the FAIL items above before running `amplifier-opencode`.
All required checks passed.
```

## `update`

```
amplifier-opencode update [OPTIONS]

  --ref TEXT             default "main". Git ref (branch, tag, or commit) of
                         amplifier-app-opencode to install.
  --force                boolean; off unless given. Overwrite an editable install. Without
                         it, an editable install is left untouched (see
                         install-and-update.md).
  --no-opencode          boolean; off unless given. Update amplifier-opencode and
                         amplifier-agent; leave opencode at its current version.
  -y, --yes              boolean; off unless given. Local to this subcommand -- independent
                         of the global `--yes`. Suppresses only the "Update opencode as
                         well?" confirmation prompt.
```

See `install-and-update.md` for the three-stage update sequence and its exit semantics.

## Exit codes

```
0    --help / -h; --version; `prepare` / `launch` / `setup` completing normally;
     `doctor` with zero FAIL checks; `update` completing (even if its amplifier-agent
     or opencode stage only warned)
1    a user-facing operational failure: a missing required binary, an unreachable
     /v1/models endpoint, the server failing to become ready in time, --no-start
     with no server reachable, a failed launch/prepare preflight, an unusable
     existing opencode config (see opencode-config.md's failure modes), a missing
     `uv` binary, an editable-install update refused without --force, an underlying
     `uv tool install` invocation exiting non-zero;
     `doctor`: at least one FAIL check, or the provider-credential section
     counting as failed (see above);
     `setup`: prerequisites still not ready after the preflight
2    unknown subcommand, unknown option, or any other command-line parse error
     (including --max-context below 1)
130  SIGINT (Ctrl-C) at any point
143  SIGTERM at any point
```

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
a model-selection flag                         | opencode's own model picker, after launch
a config file owned by this tool                | 3-tier resolution only: CLI flag, then
                                                 | environment variable, then a hardcoded
                                                 | default
telemetry                                       | nothing
hidden or undocumented flags                    | nothing; every flag appears in --help
```
