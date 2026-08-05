# Skills and modes bridge

## Scope

This file specifies how server-supplied skills and modes are turned into opencode-native files: the two output locations, the byte-exact templates for each face, the ownership manifests that let generated files be safely reconciled run over run, the name-safety contract both faces are expected to meet, and the conflict and failure reporting a user sees at launch. It does not cover the opencode provider config file or model entry construction (see `opencode-config.md`), nor the complete path inventory across scopes (see `file-locations.md`).

## Output locations

Both faces (skills and modes) share the same scope decision used for the provider config: a global target by default, a project-scoped target when a project directory is given. Skill commands land in the scope's `command/` directory, mode agents in its `agent/` directory, and each face's ownership manifest sits in the scope directory itself, next to (not inside) its output directory. Exact paths for both scopes: see `file-locations.md`.

## Skill command files

Each server-supplied skill, carrying a name and a description, becomes one file named `<name>.md` in the command directory:

```
---
description: "<marker><description>"
---
!amplifier:skill <name> $ARGUMENTS
```

The description is written as a JSON string, which is also a valid YAML double-quoted scalar, so colons, quotes, and newlines in the description cannot break the frontmatter. Worked example, for a skill named `commit` with description `Create a well-formed commit message`:

```
---
description: "(Amplifier) Create a well-formed commit message"
---
!amplifier:skill commit $ARGUMENTS
```

The marker leads the description rather than trailing it. Applying it is idempotent: a description that already carries it is left unchanged, so re-running the bridge over its own previously generated data can never stack the marker twice.

## Mode agent files

Each server-supplied mode, carrying a name and a description, becomes one file named `amplifier-<name>.md` in the agent directory:

```
---
mode: primary
name: "<name> (Amplifier)"
description: "<description>"
---
Amplifier mode "<name>". Behaviour is applied server-side by amplifier-agent.

[amplifier-agent:mode=<name>]
```

Worked example, for a mode named `plan` with description `Plan before executing`:

```
---
mode: primary
name: "plan (Amplifier)"
description: "Plan before executing"
---
Amplifier mode "plan". Behaviour is applied server-side by amplifier-agent.

[amplifier-agent:mode=plan]
```

The `amplifier-` filename prefix keeps generated agent files from colliding with opencode's own native agents. The `" (Amplifier)"` suffix on the frontmatter `name` field is what the user actually sees, because opencode gives an agent a single name that serves simultaneously as its config key, its entry in the "Select agent" picker, and its status-line label; there is no separate display field.

The agent file carries no `model` field. The mode is carried to the server by the `[amplifier-agent:mode=<name>]` directive in the body. See `ARCHITECTURE.md` for why both choices were made.

## Ownership manifests

Each face keeps its own ownership manifest, recording exactly which files in its output directory this tool generated. A manifest sits next to its scope directory, not inside it. Exact paths for both scopes: see `file-locations.md`.

Shape of the skills manifest, a sorted list of generated filenames under the `commands` key:

```json
{
  "commands": [
    "commit.md",
    "review.md"
  ]
}
```

Shape of the modes manifest, a sorted list of generated filenames under the `agents` key:

```json
{
  "agents": [
    "amplifier-build.md",
    "amplifier-plan.md"
  ]
}
```

Both are serialized with two-space indentation, one array element per line, sorted, and a trailing newline.

The two manifests are independent: each face reconciles only against its own manifest and never reads or writes the other's.

## Reconciliation

Each run reconciles its output directory against the previous run's manifest for that same face:

```
Every current skill or mode is (re)written and recorded in the new
manifest.

A file recorded in the OLD manifest whose source (skill or mode) no
longer exists is deleted.

A target that already exists on disk but was NOT recorded in the old
manifest is left alone and skipped -- it is treated as the user's own
file, never overwritten.

Two entries in the SAME run that would map to the same filename: the
first one wins and is written; the later one is skipped. This mirrors
the server's own first-match-wins discipline for same-named resources.

A missing or corrupt manifest degrades to treating every existing file
in the output directory as unowned. Nothing is ever clobbered as a
result of a manifest that could not be read.
```

The four warning strings this reconciliation can print (`<filename>` and `<name>` are the offending values; `<name>` appears quoted, normally in single quotes, switching to double quotes when the name itself contains a single quote). Every string quoted from here through the rest of this file is printed indented six spaces under the launch step that produced it; the indentation is elided from the fenced examples below:

```
skills: skipping duplicate <filename> -- more than one skill is named '<name>' in this run (keeping the first)
```

```
skills: skipping <filename> -- exists and was not generated by amplifier-opencode (leaving your command untouched)
```

```
modes: skipping duplicate <filename> -- more than one mode is named '<name>' in this run (keeping the first)
```

```
modes: skipping <filename> -- exists and was not generated by amplifier-opencode (leaving your agent untouched)
```

## Name safety

A skill or mode name is used, unmodified except for a fixed prefix/suffix, as a filename component under a directory this tool owns, and later pruning unlinks by that same recorded name. The contract, intended to apply identically to both faces:

```
Accepted:   one or more characters from [A-Za-z0-9._-]

Rejected:   the empty string
            the literal name "."
            the literal name ".."

Also required: the name must equal its own basename and must not be an
absolute path -- the general statement of the property the character
whitelist and the "." / ".." rejections approximate.
```

The refusal message, printed once per rejected name (`<kind>` is `skills` or `modes`; `<name>` appears quoted, normally in single quotes, switching to double quotes when the name itself contains a single quote):

```
<kind>: refusing '<name>' -- unsafe name (must be a bare filename of [A-Za-z0-9._-] characters); not bridged
```

A name that fails this check is dropped before it can reach either the write step or the ownership manifest, so an unsafe name can never later be unlinked via the prune step either.

## Conflict reporting

When the server reports that a same-named resource collided with another (one file actually runs, one or more same-named files were shadowed by it), the bridge prints the collision so an invisible override does not stay invisible. Header line (`<kind>` is `skills` or `modes`), singular and plural forms:

```
<kind>: 1 name conflict (the file under 'runs' is the one that runs):
<kind>: <count> name conflicts (the file under 'runs' is the one that runs):
```

Then, per conflicting name, one block (nested two spaces deeper than the header line above, with the `runs:`/`shadowed:` lines nested two spaces deeper still):

```
  <name>
    runs:     <source that actually runs>
    shadowed: <source that lost>
```

with one `shadowed:` line per losing file when more than one same-named file lost.

A clean setup, with no reported collisions on either face, prints nothing for this section.

## Failure isolation

Both bridges are best-effort with respect to the overall launch: any failure while fetching, writing, or reconciling either face is caught and never blocks the launch or exec step that follows, but the two failure modes are not reported the same way.

A fetch failure (network error, non-200 response, malformed body) is swallowed at the fetch step itself and yields zero skills or modes, with no diagnostic printed. Reconciliation cannot distinguish "zero fetched because the fetch failed" from "zero fetched because every skill or mode was legitimately removed upstream": both prune every file the previous run's manifest recorded. A failed fetch therefore silently deletes every previously generated command or agent file for that face on this run.

The two warning strings below are printed only when the write or reconciliation step itself fails (creating the output directory, writing or pruning a file, or updating the manifest) -- not for a fetch failure, which produces no warning at all. One string per face (`<error>` is the caught failure's message):

```
WARNING: skills bridge failed (<error>); continuing without commands.
```

```
WARNING: modes bridge failed (<error>); continuing without mode agents.
```

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
no model field in generated mode agent files         | nothing (the
                                                     | session's
                                                     | current model
                                                     | is inherited)
no overwriting of a user's own command or agent file | nothing (skip
                                                     | with a warning
                                                     | is the whole
                                                     | mechanism)
no sharing of ownership state between the two faces  | nothing (each
                                                     | face keeps its
                                                     | own manifest)
```
