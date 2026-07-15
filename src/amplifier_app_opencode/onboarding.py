"""First-run credential onboarding.

If amplifier-agent has no resolvable provider credentials, launching
amplifier-opencode used to produce an empty model picker plus a ``doctor``
error telling the user to go run ``amplifier-agent auth set`` themselves.

Per the product lens -- *never make the user leave the tool* -- this module
walks the user through choosing a provider and storing a key, calling
``amplifier-agent auth set`` on their behalf, then re-verifying.

Non-interactive shells (CI, pipes) get concise printed guidance and a clean
exit instead of a hang.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import click

from . import platform_utils as plat
from . import prereqs

# Providers amplifier-agent understands, in the order we present them.
# ``key_style`` drives the prompt wording; ``ollama`` takes a host URL, not a
# secret key.


@dataclass(frozen=True)
class Provider:
    provider_id: str
    label: str
    prompt: str
    secret: bool = True


PROVIDERS: tuple[Provider, ...] = (
    Provider("anthropic", "Anthropic (Claude)", "Anthropic API key"),
    Provider("openai", "OpenAI (GPT)", "OpenAI API key"),
    Provider("azure", "Azure OpenAI", "Azure OpenAI API key"),
    Provider("ollama", "Ollama (local models)", "Ollama host URL", secret=False),
)


def _agent_bin() -> str | None:
    return shutil.which(prereqs.AGENT_BIN)


def needs_onboarding() -> bool:
    """True when we can positively determine there are NO resolvable providers.

    Returns False when unknown (agent missing / report unreadable) -- we only
    trigger the wizard on a confident "zero providers" signal so we never
    interrupt a working setup.
    """
    resolvable = prereqs.has_resolvable_provider()
    return resolvable is False


def _auth_set(agent_bin: str, provider: Provider, value: str) -> bool:
    """Store a credential via ``amplifier-agent auth set <provider> <value>``."""
    click.secho(f"  Storing {provider.label} credentials via amplifier-agent ...", fg="cyan")
    try:
        result = subprocess.run(
            [agent_bin, "auth", "set", provider.provider_id, value],
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        click.secho(f"  \u2717 could not run amplifier-agent auth set: {exc}", fg="red")
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        click.secho(f"  \u2717 amplifier-agent auth set failed: {detail}", fg="red")
        return False
    return True


def _print_manual_guidance() -> None:
    click.secho("No provider credentials are configured for amplifier-agent.", fg="yellow")
    click.echo("Configure at least one provider, for example:")
    click.echo("    amplifier-agent auth set anthropic sk-ant-...")
    click.echo("    amplifier-agent auth set openai sk-...")
    click.echo("Or export a key before launching, e.g. ANTHROPIC_API_KEY=...")


def run_onboarding(*, assume_yes: bool) -> bool:
    """Interactively configure a provider. Returns True if one was configured.

    ``assume_yes`` does NOT fabricate a key (we can't); it only suppresses the
    "shall we set this up?" confirmation. Without a TTY we cannot collect a
    secret, so we print guidance and return False.
    """
    agent_bin = _agent_bin()
    if not agent_bin:
        # Shouldn't happen (ensure ran first), but be defensive.
        _print_manual_guidance()
        return False

    if not plat.is_interactive():
        _print_manual_guidance()
        return False

    click.secho(
        "\nLet's connect a model provider so opencode has models to use.", fg="cyan", bold=True
    )
    if not assume_yes and not click.confirm("  Set up a provider now?", default=True):
        _print_manual_guidance()
        return False

    # Present the menu.
    for idx, prov in enumerate(PROVIDERS, start=1):
        click.echo(f"    {idx}. {prov.label}")
    choice = click.prompt("  Choose a provider", type=click.IntRange(1, len(PROVIDERS)), default=1)
    provider = PROVIDERS[choice - 1]

    value = click.prompt(f"  {provider.prompt}", hide_input=provider.secret).strip()
    if not value:
        click.secho("  No value entered; skipping.", fg="yellow")
        _print_manual_guidance()
        return False

    if not _auth_set(agent_bin, provider, value):
        return False

    # Re-verify: the agent should now report at least one resolvable provider.
    if prereqs.has_resolvable_provider():
        click.secho(f"  \u2713 {provider.label} configured and resolvable.", fg="green")
        return True

    click.secho(
        "  Stored the credential, but amplifier-agent still reports it as "
        "unresolvable. Double-check the value with `amplifier-agent providers list`.",
        fg="yellow",
    )
    return False
