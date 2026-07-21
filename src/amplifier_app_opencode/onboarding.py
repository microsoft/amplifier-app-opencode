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


@dataclass(frozen=True)
class Provider:
    """Presentation metadata for one provider the wizard can configure.

    ``provider_id`` is the exact token ``amplifier-agent auth set`` expects --
    it is the provider's ``name`` from ``amplifier-agent providers list --json``.
    The remaining fields are pure presentation (prompt wording, whether the
    value is a secret to mask, whether a separate endpoint URL is required).
    """

    provider_id: str
    label: str
    prompt: str
    secret: bool = True
    needs_endpoint: bool = False
    endpoint_prompt: str = "Endpoint URL"


# Presentation overlay, keyed by the agent's own provider id (``name``). The
# agent is the source of truth for *which* providers exist; this table only
# supplies human wording the report doesn't carry. Any id we don't recognise
# still gets offered via a generic masked-secret prompt (see
# ``_presentation_for``), so a newly-added agent provider works with no change
# here. ``ollama`` takes a host URL, not a secret. ``azure-openai`` also needs
# an endpoint URL alongside the key.
_PRESENTATION: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "Anthropic (Claude)", "Anthropic API key"),
    "openai": Provider("openai", "OpenAI (GPT)", "OpenAI API key"),
    "azure-openai": Provider(
        "azure-openai",
        "Azure OpenAI",
        "Azure OpenAI API key",
        needs_endpoint=True,
        endpoint_prompt="Azure OpenAI endpoint URL (https://<resource>.openai.azure.com)",
    ),
    "ollama": Provider("ollama", "Ollama (local models)", "Ollama host URL", secret=False),
}


def _presentation_for(provider_id: str) -> Provider:
    """Presentation metadata for ``provider_id``, generic fallback if unknown."""
    known = _PRESENTATION.get(provider_id)
    if known is not None:
        return known
    # Unknown to our overlay: offer it generically as a masked secret so the
    # wizard keeps working when the agent gains a provider before we do.
    return Provider(provider_id, provider_id, f"{provider_id} API key")


def _available_providers() -> tuple[Provider, ...]:
    """Providers to offer, sourced from the agent's own report.

    The agent's ``providers list --json`` ``name`` values are exactly what
    ``auth set`` accepts, so we enumerate from there and overlay presentation
    metadata -- no hardcoded id list to drift out of sync. If the report is
    unreadable (agent missing / offline) we fall back to the overlay's own ids
    so the wizard still functions.
    """
    report = prereqs.fetch_provider_report()
    names: list[str] = []
    if report is not None:
        names = [
            row["name"]
            for row in report.get("providers", [])
            if isinstance(row.get("name"), str) and row["name"]
        ]
    if not names:
        return tuple(_PRESENTATION.values())
    return tuple(_presentation_for(name) for name in names)


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


def _auth_set(
    agent_bin: str, provider: Provider, value: str, *, endpoint: str | None = None
) -> bool:
    """Store a credential via ``amplifier-agent auth set <provider> --stdin``.

    The API key is piped to the child process over stdin (``--stdin``) rather
    than passed as an argv token, so the plaintext secret never appears in the
    process list (``ps``/``/proc``) of the machine running onboarding.

    Providers that need a separate endpoint (Azure OpenAI) pass it through as
    ``--endpoint <url>`` -- an endpoint URL is not a secret.
    """
    click.secho(f"  Storing {provider.label} credentials via amplifier-agent ...", fg="cyan")
    cmd = [agent_bin, "auth", "set", provider.provider_id, "--stdin"]
    if endpoint:
        cmd += ["--endpoint", endpoint]
    try:
        result = subprocess.run(
            cmd,
            input=value,  # key over stdin -- never on argv
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except subprocess.TimeoutExpired:
        # The secret is passed via stdin, not argv, so ``cmd`` no longer holds
        # it -- but still avoid formatting the exception object as a matter of
        # hygiene (its str() embeds the cmd list).
        click.secho("  \u2717 amplifier-agent auth set timed out after 30s", fg="red")
        return False
    except OSError as exc:
        # OSError.str() is strerror/errno, not the argv -- safe to surface.
        click.secho(f"  \u2717 could not run amplifier-agent auth set: {exc}", fg="red")
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        # Defense in depth: the key is no longer on argv, but if the agent ever
        # echoes it back in its error text for any other reason, scrub it so the
        # plaintext key never lands in our output.
        if value:
            detail = detail.replace(value, "***")
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

    # Present the menu, enumerated from the agent's own provider report.
    providers = _available_providers()
    for idx, prov in enumerate(providers, start=1):
        click.echo(f"    {idx}. {prov.label}")
    choice = click.prompt("  Choose a provider", type=click.IntRange(1, len(providers)), default=1)
    provider = providers[choice - 1]

    value = click.prompt(f"  {provider.prompt}", hide_input=provider.secret).strip()
    if not value:
        click.secho("  No value entered; skipping.", fg="yellow")
        _print_manual_guidance()
        return False

    endpoint: str | None = None
    if provider.needs_endpoint:
        endpoint = click.prompt(f"  {provider.endpoint_prompt}").strip()
        if not endpoint:
            click.secho("  No endpoint entered; skipping.", fg="yellow")
            _print_manual_guidance()
            return False

    if not _auth_set(agent_bin, provider, value, endpoint=endpoint):
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
