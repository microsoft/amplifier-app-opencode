# Agent Integration

## Scope

This file specifies this tool's contract with respect to the amplifier-agent process it
drives: the version floor it enforces, how it spawns, reuses, and never stops that process,
and how it degrades when a consumed agent surface is absent, non-200, or malformed. It does
not restate amplifier-agent's own HTTP API, endpoint schemas, or wire formats; those belong
to amplifier-agent's own specification. It does not cover the CLI flag surface (see
`cli.md`), self-heal install mechanics for the agent binary itself (see
`install-and-update.md`), the credential wizard or provider discovery (see
`providers-and-credentials.md`), the model entries written from discovered models (see
`opencode-config.md`), the skill/mode entries written from discovered skills and modes (see
`skills-and-modes-bridge.md`), or the paths this tool reads and writes (see
`file-locations.md`).

## The version floor

There is a single declared minimum amplifier-agent version. Two other values are mechanical
followers of it, not independent dials:

```
one dial:       the minimum agent version this tool requires

follower 1:     the exact known-good git tag the self-heal installer targets,
                derived textually from the minimum ("v" + the minimum)

follower 2:     the version below which the agent's own `update` subcommand is
                distrusted (skipped in favor of a forced reinstall), equal to
                the minimum itself
```

Bumping the floor is a single deliberate edit; both followers move with it automatically.

The floor is a requirement statement, not a mirror of whatever amplifier-agent most recently
released. It moves only when this tool comes to depend on something a newer agent introduced.
An unnecessary bump has a real cost: every user whose installed agent falls below the new
floor is forced through a healing reinstall on their next launch, whether or not they wanted
one.

The self-heal installer targets the exact pinned tag, never a moving branch. A reliability
mechanism that silently drags users onto un-vetted trunk would defeat its own purpose.

The floor's history is a ratchet: each step up records the specific capability that forced
it, kept alongside the current floor as the justification for that step (the changelog
carries the authoritative per-release record):

```
0.9.1    ->  the first floor recorded; `amplifier-opencode update` began
             cascading into updating the agent at this step
0.9.3    ->  `auth set --stdin`, used by the onboarding wizard to hand a
             provider key to the agent off-argv
0.10.0   ->  `GET /v1/skills` and `GET /v1/modes`, read by the skills and
             modes bridges
0.11.0   ->  namespaced reseller model ids (e.g. a Copilot-served model
             namespaced from a native provider's model of the same name),
             without which two same-named models collide in the picker
0.12.0   ->  the HTTP face honoring `provider.config` from the host config
             passed via `--host-config`; below this, that flag was silently
             accepted but not applied on every turn, and a related debug
             option was rejected outright as unknown
```

Version comparison fails closed: a version string that cannot be parsed into a comparable
form is treated as not meeting the minimum. It is never assumed to be new enough merely
because it could not be understood.

## Lifecycle

When no server is already reachable (see Reuse below) and starting one is not disabled, this
tool spawns the agent's chat-completions server directly, passing it the workspace name, the
API key, the host config file when one was supplied, and a port. The port is not a separate
option: it is taken from the configured base URL, and falls back to a fixed default port when
that URL carries no parseable port. The readiness probe below always targets the base URL as
given, never the derived port, so a base URL whose port cannot be parsed spawns a server the
probe will not find.

The spawned process's combined output is appended to a log file in the platform temp
directory, never truncated across launches (see `file-locations.md` for the exact path). The
spawned server is placed in its own process session, independent of this tool's own, so it
keeps running after this process is replaced by the terminal UI in the exec step. The
server's working directory is set to the launch directory, or the project directory when one
was given, so that the agent's own project-relative discovery finds project-scoped resources
seeded there rather than whatever directory this tool happened to be invoked from.

### Readiness

After spawning, this tool polls for readiness rather than assuming the server is up
immediately: a one-second poll interval against a 120-second overall timeout, until either a
probe succeeds or the timeout elapses; each individual probe in the poll loop has its own
one-second timeout. On timeout:

```
amplifier-agent did not become ready within 120s. Check the log: <log-path>
```

Unless stated otherwise, a message quoted in this file appears exactly as printed, with no
leading indent. The one exception is any message prefixed `WARNING:`, which is printed with a
six-space indent not shown in the quoted forms below.

The timeout is set as long as it is because a first-ever launch on a machine has the agent
fetch its provider and bundle modules over the network, which can take on the order of a
minute on a slow connection. A warm boot, by contrast, typically finishes in a few seconds
and exits the poll loop early; the long timeout is a ceiling for the cold case, not the
expected wait on every launch.

This poll loop is distinct from the standalone reachability probe used by the Reuse check
below and by `doctor`'s server check: that probe uses its own two-second timeout and is not
retried.

### Shutdown

This tool never stops the server it spawns, under any code path: not on normal exit, not on
an error, not on any subcommand. The server outlives the launcher by design, so a later
invocation (from this tool, or from anything else that knows its base URL) can reuse it
without paying the cold-start cost again.

## Reuse

Before considering a spawn, this tool checks whether a server is already reachable at the
configured base URL, using a liveness probe alone. If that probe succeeds, the existing
server is reused as-is and nothing is spawned.

**No version check is performed against a reused server.** The version floor described above
is enforced only against the locally installed agent binary, and only during the preflight
that runs before a spawn decision is made. Once a server has answered the liveness probe,
this tool never separately queries or validates its version. When the preflight itself is
skipped (bootstrap disabled, see `cli.md` and `install-and-update.md`), no floor check
happens at all, and a below-floor local binary is spawned unchecked.

A server reached at a custom base URL need not correspond to any locally installed binary at
all: it could be a remote server, a server started by something else entirely, or a different
agent version than the one installed on this machine. Reuse depends solely on the probe
succeeding, never on any claim about what is actually running behind it.

## Degradation

One row per agent surface this tool consumes, and what happens when it is absent, non-200,
or malformed. Only the model listing is required; everything else is best-effort.

```
model listing            required; failure is fatal to the launch. Verbatim messages:
                          "Could not reach /v1/models at <base-url>: <error>"
                          "/v1/models returned HTTP <status>: <response body, truncated>"
                          "/v1/models returned malformed body: <type>, expected object"
                          "/v1/models returned malformed data: <type>, expected list"
                          A response that parses but lists zero models is NOT fatal, only
                          a warning: "WARNING: /v1/models returned 0 models. opencode
                          picker will be empty." A row in the response's model list that
                          is not itself an object is silently dropped rather than
                          reported, so a partially malformed response can yield a
                          smaller model list than the server actually reported, with no
                          message. A response body that is not valid JSON at all is not
                          one of the four messages above; see the issues log entry on
                          this (an unhandled failure, not the clean exit-1 this section
                          otherwise promises).

skills listing            best-effort. A fetch failure (unreachable, non-200, malformed
                          body, malformed data) yields an empty result SILENTLY: no
                          warning is printed, and the bridge proceeds as though the
                          server had reported zero skills, pruning every command file
                          this tool had generated on an earlier launch. Only a failure
                          while writing or reconciling the command files afterward is
                          reported, and only that failure blocks nothing:
                          "WARNING: skills bridge failed (<error>); continuing without
                          commands."

modes listing             best-effort; identical shape to skills listing: a fetch
                          failure is silent and prunes mode-agent files this tool had
                          generated on an earlier launch, with no warning; only a
                          write/reconcile failure prints:
                          "WARNING: modes bridge failed (<error>); continuing without
                          mode agents."

agent version query       best-effort; any failure (binary absent, the subprocess call
                          itself failing, a timeout, or output that cannot be parsed as a
                          version) degrades to an unknown/absent marker rather than
                          raising. Comparison against the floor then fails closed, per
                          The version floor above. A subprocess that exits non-zero but
                          still produces output has that output's first line treated as
                          the version string; the exit code itself is never consulted.
                          That first line is then searched for a recognizable version
                          exactly as any other detected version string would be, so a
                          non-zero exit whose first line still names a version passes
                          the floor comparison normally. Only when that first line
                          contains no recognizable version does the floor comparison
                          fail closed, the same outcome as any other malformed string.

provider report           best-effort; any failure (binary absent, unreachable, non-zero
                          exit, malformed output) degrades to an unknown marker that is
                          kept distinct from "asked, and zero providers resolve" -- this
                          tool never treats "could not ask" as though it were "asked and
                          got a negative answer." See providers-and-credentials.md for how
                          the onboarding trigger and doctor treat that marker.
```

The silent skills/modes pruning is a caller-visible consequence, not an internal detail: a
network hiccup or an agent restart that briefly serves a non-200 on `/v1/skills` or
`/v1/modes` is indistinguishable, from this tool's side, from the agent genuinely reporting
zero skills or modes -- and either one deletes every bridged command or mode-agent file this
tool had written from an earlier launch, with nothing printed to explain why they are gone.

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
a version check performed against a reused server   | nothing; liveness alone governs
                                                     | reuse (see Reuse)
a shutdown/stop command for the spawned server      | nothing owned by this tool; the
                                                     | server outlives the launcher by
                                                     | design (see Shutdown, and cli.md's
                                                     | own non-goal on this)
amplifier-agent bundled as a package dependency     | nothing; it is an external binary
                                                     | this tool locates on PATH (or a
                                                     | caller-supplied path) and spawns,
                                                     | never imports
a runtime version negotiation with the agent        | nothing; the required minimum is
                                                     | fixed at build time and never
                                                     | adjusted from what the agent reports
an automatic floor bump                             | nothing; raising the minimum agent
                                                     | version is a deliberate, manual
                                                     | decision made only when this tool
                                                     | depends on something new (see The
                                                     | version floor)
```
