# File locations

## Scope

This file is the complete inventory of every durable path this tool reads or writes, in both the global scope and the project scope, plus the paths of adjacent components that a user debugging this tool commonly looks for. It states, for each path, its format and whether a user may safely hand-edit it. It does not describe the byte-exact content of the opencode config file (see `opencode-config.md`) or of the bridged command and agent files (see `skills-and-modes-bridge.md`) beyond naming them, and it does not restate the reconciliation or write-atomicity rules those two files already own.

## Global scope

Used whenever no project directory is given.

```
path:      ~/.config/opencode/opencode.jsonc
           (or opencode.json / config.json, whichever already exists;
           first existing candidate wins, .jsonc created if none exist)
format:    JSON
editable:  yes, except provider.<provider-id>; see `opencode-config.md`
```

```
path:      ~/.config/opencode/command/<name>.md
format:    YAML frontmatter + one body line
editable:  no -- reconciled every launch; see `skills-and-modes-bridge.md`
```

```
path:      ~/.config/opencode/agent/amplifier-<name>.md
format:    YAML frontmatter + body
editable:  no -- reconciled every launch; see `skills-and-modes-bridge.md`
```

```
path:      ~/.config/opencode/.amplifier-generated-commands.json
format:    JSON ownership manifest
editable:  not meant to be hand-edited; owned entirely by this tool
```

```
path:      ~/.config/opencode/.amplifier-generated-agents.json
format:    JSON ownership manifest
editable:  not meant to be hand-edited; owned entirely by this tool
```

## Project scope

Used whenever a project directory is given; every path below is rooted at `<project-dir>`.

```
path:      <project-dir>/opencode.json
format:    JSON
editable:  yes, except provider.<provider-id> (no .jsonc candidate search
           in project scope: the filename is fixed); see `opencode-config.md`
```

```
path:      <project-dir>/.opencode/command/<name>.md
format:    YAML frontmatter + one body line
editable:  no -- reconciled every launch; see `skills-and-modes-bridge.md`
```

```
path:      <project-dir>/.opencode/agent/amplifier-<name>.md
format:    YAML frontmatter + body
editable:  no -- reconciled every launch; see `skills-and-modes-bridge.md`
```

```
path:      <project-dir>/.opencode/.amplifier-generated-commands.json
format:    JSON ownership manifest
editable:  not meant to be hand-edited; owned entirely by this tool
```

```
path:      <project-dir>/.opencode/.amplifier-generated-agents.json
format:    JSON ownership manifest
editable:  not meant to be hand-edited; owned entirely by this tool
```

## Transient write-temp files

Every config, manifest, or generated file this tool writes goes through a temp-file-then-rename, with one exception: the spawned server log below, which is opened for append and never goes through a temp file or a rename. Scope-independent; alongside any file this tool writes, in the same directory:

```
path:      .<target-filename>.<random>.tmp
format:    same as its target
editable:  no -- transient. Created and renamed within a single write; a
           leftover after a crash is safe to delete
```

## Spawned server log

Scope-independent; written regardless of global vs project mode.

```
path:      <platform temp dir>/amplifier-agent.log
format:    plain text, appended to (never truncated) across launches
editable:  read-only in practice; it is stdout/stderr from the spawned
           amplifier-agent process, useful only for debugging
```

The platform temp dir is resolved through the standard OS mechanism (the `TMPDIR`-style environment variable, falling back to `/tmp`, on macOS/Linux; the Windows temp-directory environment variable on native Windows). It is never hardcoded to `/tmp`.

There is no PID file. Liveness of an already-running server is determined purely by probing the server's own HTTP endpoint; no PID is ever written to disk or read back, on any scope, on any platform.

## Files this tool does not own

Paths a user debugging this tool commonly looks for, but which belong to a different component entirely and are never read or written by this tool.

```
path:      ~/.amplifier-agent/state/workspaces/
owner:     amplifier-agent
what:      amplifier-agent's own session storage
```

```
path:      ~/.amplifier-agent/credentials.json
owner:     amplifier-agent
what:      amplifier-agent's persistent provider credentials (mode 0600),
           set through amplifier-agent's own credential command
```

```
path:      ~/.local/share/opencode/log/opencode.log
owner:     opencode
what:      opencode's own log file. Path is opencode's to change; nothing
           here depends on or verifies it
```

```
path:      ~/.local/bin/amplifier-agent, ~/.local/bin/amplifier-opencode
owner:     uv
what:      tool binaries installed by `uv tool install`
```

```
path:      ~/.opencode/bin/opencode
owner:     opencode's installer
what:      the opencode binary, when installed via the bash one-liner
```

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
no PID file                           | nothing (liveness is an
                                      | HTTP probe against the
                                      | running server)
no lock file                          | nothing
no state directory owned by this tool | amplifier-agent
                                      | (~/.amplifier-agent/state/)
no cache directory owned by this tool | nothing
no config file for this tool's own    | nothing (every config,
settings                              | manifest, or generated file
                                      | this tool writes belongs to
                                      | opencode's config surface,
                                      | never to itself; the spawned
                                      | server log is the one
                                      | exception, and it holds no
                                      | settings of this tool's own)
```
