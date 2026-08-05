# opencode config

## Scope

This file specifies how the amplifier provider block is generated and written into opencode's own configuration file: which config file is targeted in each scope, the complete shape of what gets written, how each model entry is built from live discovery, the context-clamping safety net, what existing content is preserved versus replaced, the write's atomicity guarantee, and the hard failure modes on a malformed existing file. It does not cover how skills or modes are bridged into opencode's command and agent directories (see `skills-and-modes-bridge.md`), nor the full inventory of paths this tool reads and writes (see `file-locations.md`).

## Config file selection

Global scope, ordered candidates under the global opencode config directory (see `file-locations.md`), first existing file wins:

```
opencode.jsonc
opencode.json
config.json
```

If none exist, a new `opencode.jsonc` is created. The `.jsonc` extension permits user comments, although this tool's own writer never emits any and discards any it finds (see Preservation).

Project scope has no candidate search: the target is always the fixed path `<project-dir>/opencode.json`.

## What is written

A complete literal example of the written file, with every key this tool ever emits:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "amplifier": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Amplifier",
      "options": {
        "baseURL": "http://127.0.0.1:9099/v1",
        "apiKey": "local-dev-secret"
      },
      "models": {
        "claude-sonnet-4-6": {
          "name": "Claude Sonnet 4.6",
          "cost": {
            "input": 3.0,
            "output": 15.0,
            "cache_read": 0.3,
            "cache_write": 3.75
          },
          "limit": {
            "context": 200000,
            "output": 8192
          }
        }
      }
    }
  }
}
```

`amplifier` is the default provider key; a caller may choose a different key, which changes only that key name (see Preservation below). `models` holds one entry per discovered model, keyed by the model's own id.

## Model entries

A field that cannot be emitted with the correct type is omitted entirely, never emitted with a placeholder or a default value. See `ARCHITECTURE.md` for why.

For each discovered model:

```
name    always present. The model's display name when supplied, otherwise
        the model's bare id.

cost    {input, output, cache_read?, cache_write?} in USD per 1M tokens.
        Emitted only when the model id is found in the static pricing
        catalog. Never emitted with a zero value for an unknown model:
        showing zeros is worse than showing nothing, because a zero reads
        as "this model is free" rather than "unknown."

        cache_read and cache_write are each included only when the
        catalog entry for that model carries the corresponding field;
        neither is defaulted.

limit   {context, output}, both integers. Emitted only when the source
        data supplies both context and output as integers. If either is
        missing or not an integer, the whole limit block is omitted --
        never emitted with only one of the two fields.
```

## Context clamping

An optional ceiling may be applied to every model's advertised context window, to guard against a backend that advertises a larger window than the provider will actually honor at request time.

```
When the ceiling is unset (the default): the context value is forwarded
verbatim from the source data.

When the ceiling is set and a model's context value exceeds it: context
is clamped down to the ceiling.

The output value is never clamped, regardless of the ceiling.
```

## Preservation

Writing the config is a targeted update, not a full regeneration:

```
Every top-level key already present in the file survives untouched,
including $schema if the file already declares one (never overwritten).

The single sub-block provider.<provider-id> is replaced wholesale: its
entire previous contents are discarded and replaced with the freshly
built block. This is a full replace, never a deep merge -- a field
present in the old block but absent from the new one does not survive.

Every sibling entry under provider.* other than provider.<provider-id>
is left completely untouched.

Choosing a different provider id changes only which key under
provider.* receives the block. It has no effect on any other key,
scope, or file.
```

Comments in an existing `.jsonc` file are lost on rewrite. This is accepted behavior: the file is always read and written as plain JSON.

## Atomicity

The write renders the full target content to a temporary file in the same directory as the target, then replaces the target with that temporary file in one filesystem operation. A crash or interruption at any point before that replacement leaves the original file exactly as it was; there is no state in which the target file is partially written or truncated.

## Failure modes

Three conditions cause the write to fail outright rather than silently degrade, each with a fixed message shape:

```
Existing <config-path> is not valid JSON: <parse error>. Remove or fix
the file, then retry.
```

```
Existing <config-path> is valid JSON but not an object (found
<type-name>); refusing to overwrite.
```

```
Existing <config-path>.provider is not a dict; refusing to overwrite.
```

All three refuse to touch the file rather than guess at intent or overwrite user content with an empty structure.

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
no dynamic model refresh while opencode is running    | nothing (re-sync
                                                      | happens only at
                                                      | the next launch)
no writing of any opencode key other than $schema and | nothing
the one provider.<provider-id> entry                  |
```
