# Providers and Credentials

## Scope

How model-provider credentials are configured, stored, and reported for the amplifier-agent
backend this tool talks to. Covers credential ownership, the interactive onboarding wizard, how the
provider list is discovered, secret-handling hygiene, and the provider section of `doctor`'s output.
It does not cover the CLI flag surface (see `cli.md`), install/self-heal mechanics (see
`install-and-update.md`), or the generated opencode provider config's model entries (see
`opencode-config.md`).

## Credential ownership

This tool stores no credentials of its own, anywhere. Every credential write is delegated to
amplifier-agent's own credential-set command, invoked as a subprocess. This tool has no credentials
file, no credentials directory, and no in-memory cache of a secret beyond the single subprocess call
that hands it off.

The one non-secret exception is the Azure OpenAI endpoint URL: it is not sensitive, and is passed to
the delegated command as an ordinary command-line argument rather than over stdin.

## The onboarding wizard

### Tri-state trigger

The wizard runs only on a confident, positive "zero resolvable providers" signal from
amplifier-agent's own provider report. If that report cannot be obtained at all (amplifier-agent
missing, unreachable, or its output unreadable), the trigger evaluates to "do not run onboarding" --
the same as when at least one provider already resolves. Onboarding is deliberately never triggered
on an unknown state, so a setup that is merely unreachable or slow is never mistaken for a setup
with no credentials, and a working setup is never interrupted.

### TTY requirement

The wizard requires a real, interactive terminal on both stdin and stdout. Without one, it prints
guidance and returns without prompting -- it never blocks waiting for input that cannot arrive. The
verbatim guidance, printed here, when the agent binary cannot be located at wizard time, and
whenever the user declines or aborts partway through:

```
No provider credentials are configured for amplifier-agent.
Configure at least one provider, for example:
    amplifier-agent auth set anthropic sk-ant-...
    amplifier-agent auth set openai sk-...
Or export a key before launching, e.g. ANTHROPIC_API_KEY=...
```

### Confirmation and provider menu

Interactively, the wizard opens with a banner, printed whether or not `--yes` is given:

```
Let's connect a model provider so opencode has models to use.
```

preceded by a blank line. It then asks "  Set up a provider now?" (default yes), skipped entirely
under `--yes`.

It then presents a numbered menu of providers, sourced from amplifier-agent's own provider report,
so a provider the agent knows about but this tool has no special wording for still appears, with a
generic prompt reading `<provider id> API key` (masked as a secret) and the bare provider id as its
menu label. If the report cannot be obtained at that moment, or is obtained but carries no usable
provider names, the menu falls back to exactly the four ids listed below. Four provider ids carry
richer presentation wording:

```
anthropic       label "Anthropic (Claude)"        prompt "Anthropic API key"            secret
openai          label "OpenAI (GPT)"               prompt "OpenAI API key"               secret
azure-openai    label "Azure OpenAI"               prompt "Azure OpenAI API key"          secret
                                                    + endpoint prompt "Azure OpenAI
                                                      endpoint URL
                                                      (https://<resource>.openai.azure.com)"
ollama          label "Ollama (local models)"      prompt "Ollama host URL"               NOT secret
                                                    (value is shown while typing, not masked)
```

The default menu selection (pressing Enter with no input) is always the first entry.

The banner above and the manual-guidance block carry no indent. Menu entries carry a
four-space indent. The three terminal outcomes below each carry a two-space indent as part
of the printed string; that indent is elided from the nested block below for readability.

```
no value entered for the provider's key:
    No value entered; skipping.
    (followed by the TTY-requirement guidance block above)

no endpoint entered (azure-openai only, after a key was already entered):
    No endpoint entered; skipping.
    (followed by the TTY-requirement guidance block above)

value (and endpoint, if any) stored; re-checking amplifier-agent's provider report:
  resolves:
      ✓ <label> configured and resolvable.
  still does not resolve (storage is not treated as sufficient proof of success):
      Stored the credential, but amplifier-agent still reports it as
      unresolvable. Double-check the value with `amplifier-agent providers list`.
```

## Provider discovery

The set of providers the wizard offers, and everything `doctor`'s provider section reports, comes
from asking amplifier-agent itself which providers it knows about and which currently resolve. This
tool never independently inspects environment variables or a credentials file to answer that
question -- amplifier-agent is the only thing that can truthfully say which providers it will
auto-enable at its own server startup, so it is asked directly rather than the answer being
re-derived here.

## Secret hygiene

A secret value is handed to the delegated credential-set command over the subprocess's stdin, never
as a command-line argument -- so the plaintext never appears in the machine's process listing.

On failure, defense in depth applies on top of the stdin-only channel: if the delegated command's
own error output happens to echo the secret back for any reason, every occurrence of the plaintext
value is replaced with `***` before the message is shown to the user.

Verbatim failure messages:

```
timeout (30 seconds):
    ✗ amplifier-agent auth set timed out after 30s

could not launch the delegated command at all:
    ✗ could not run amplifier-agent auth set: <OS error>

delegated command exited non-zero (value already scrubbed from the detail text):
    ✗ amplifier-agent auth set failed: <scrubbed detail>
```

## `doctor` provider reporting

`doctor` (see `cli.md`) includes a dedicated provider section, always headed:

```
  Providers (via `amplifier-agent providers list`):
```

Per-provider lines, one of two shapes:

```
resolvable:
    ✓ <name> resolvable (source=<source>) → will be served

not resolvable:
    ✗ <name> not resolvable → set <env var> or run `amplifier-agent auth set
      <name> <key>` to enable it
```

If the underlying report cannot be obtained at all:

```
✗ Could not run `amplifier-agent providers list --json`
→ Install/upgrade amplifier-agent, or run that command manually to see why
```

Summary line, after the per-provider lines and one blank line:

```
at least one provider resolvable:
    → N providers will be auto-enabled on launch (<name>, <name>, ...)
    (singular "provider" when N is 1)

zero providers resolvable:
    → No provider credentials resolvable. Run: amplifier-agent auth set
      <provider> <key>
    → Or export the provider's env var (e.g. ANTHROPIC_API_KEY)
```

The whole provider section counts as a failing check in `doctor`'s pass/fail summary whenever the
report could not be obtained at all, or was obtained but zero providers resolve.

## GitHub Copilot

This tool contains no Copilot-specific code path. `GITHUB_TOKEN` is exported by the user (for
example via `export GITHUB_TOKEN=$(gh auth token)`) and is read and resolved entirely on the
amplifier-agent side; this tool never reads it and never mentions it except by inheriting whatever
amplifier-agent's own provider report says about it.

This tool passes model ids through verbatim from what amplifier-agent reports; it has no
namespacing logic of its own to keep in sync (see `agent-integration.md`'s "The version floor" for
why namespaced reseller model ids matter and which agent version introduced them).

## Non-goals

Deliberately absent surfaces, and where the capability lives instead.

```
credential storage in this tool                 | amplifier-agent's own credentials file
a credential import/export format owned by      | nothing
this tool                                        |
a provider configuration UI beyond the wizard    | nothing; no settings screen, no TUI panel
direct LLM calls from this tool                  | amplifier-agent's HTTP face and its own
                                                  | auth/providers subcommands
reading of provider environment variables by     | amplifier-agent resolves provider environment
this tool                                        | variables itself at server startup
```
