"""E2E harness entry point -- invoked as ``uv run python tests/e2e/cli.py <verb>``.

Verbs:
    up       Cold-provision the warm DTU (launch profile + wait ready) and print state.
    run      Ensure warm (auto-``up`` unless --skip-setup), then run the pytest suites.
             Optionally scope to one or more suites: ``run chat``.
    down     Tear down the DTU instance and clear state.
    refresh  Re-run the in-DTU install in place inside the warm DTU.
    list     Print the discovered suites (works with NO DTU and NO network).

Not installed as a console script; run directly via uv run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Make the `framework` and `suites` packages importable when run directly as a script
# (tests/e2e/cli.py -> parent = tests/e2e, their shared parent dir).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import click
from framework import dtu_manager
from framework.progress import log

# tests/e2e/cli.py -> e2e -> tests -> repo root
E2E_ROOT = Path(__file__).resolve().parent
REPO_ROOT = E2E_ROOT.parents[1]
SUITES_ROOT = E2E_ROOT / "suites"


def _valid_suites() -> set[str]:
    """Suite names are the immediate subdirectories of suites/ with an __init__.py."""
    if not SUITES_ROOT.is_dir():
        return set()
    return {p.name for p in SUITES_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


@click.group()
def cli() -> None:
    """amplifier-app-opencode e2e harness."""


def _apply_published(published: bool) -> None:
    """Set OC_E2E_PUBLISHED for this invocation so the DTU installs published versions.

    Default (flag absent) leaves the env untouched, so provisioning uses the LOCAL
    working-tree Gitea mirror.
    """
    if published:
        os.environ["OC_E2E_PUBLISHED"] = "1"
        log("cli: --published set; DTU will install PUBLISHED versions (no local mirror)")


@cli.command()
@click.option(
    "--published", is_flag=True, help="Install published upstream versions (skip the local mirror)."
)
def up(published: bool) -> None:
    """Provision a fresh warm DTU (destroys any existing oc-e2e) and print state JSON."""
    _apply_published(published)
    new_state = dtu_manager.provision()
    click.echo(json.dumps(new_state, indent=2))


@cli.command()
def down() -> None:
    """Destroy the DTU instance and clear state."""
    dtu_manager.teardown()
    click.echo("torn down")


@cli.command()
def refresh() -> None:
    """Re-run the in-DTU install in place inside the warm DTU."""
    dtu_manager.refresh()
    click.echo("refreshed")


@cli.command(name="list")
def list_suites() -> None:
    """Print the discovered e2e suites (no DTU or network required)."""
    suites = sorted(_valid_suites())
    if not suites:
        click.echo("(no suites found)")
        return
    click.echo("suites:")
    for name in suites:
        click.echo(f"  - {name}")


@cli.command(context_settings={"ignore_unknown_options": True})
@click.option(
    "--skip-setup", is_flag=True, help="Run against the existing warm DTU as-is (no provision)."
)
@click.option("--ephemeral", is_flag=True, help="Tear down the DTU after the run.")
@click.option(
    "--published", is_flag=True, help="Install published upstream versions (skip the local mirror)."
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run(skip_setup: bool, ephemeral: bool, published: bool, args: tuple[str, ...]) -> None:
    """Ensure a warm DTU, then run the e2e pytest suites (optionally scoped by name)."""
    _apply_published(published)
    valid = _valid_suites()
    suites: list[str] = []
    idx = 0
    for token in args:
        if token.startswith("-") or "/" in token or "::" in token:
            break
        if token not in valid:
            known = ", ".join(sorted(valid)) or "(none found)"
            raise click.ClickException(f"unknown suite {token!r}; valid suites: {known}")
        suites.append(token)
        idx += 1
    pytest_args = list(args[idx:])

    if skip_setup:
        if not dtu_manager.is_warm():
            raise click.ClickException("no warm DTU and --skip-setup set; run `up` first")
        log("run: --skip-setup; using existing warm DTU as-is")
    else:
        dtu_manager.provision()

    if suites:
        targets = [f"tests/e2e/suites/{name}" for name in suites]
        log(f"run: scoping to suite(s): {', '.join(suites)}")
    else:
        targets = ["tests/e2e/suites"]

    proc = subprocess.run(
        ["uv", "run", "pytest", *targets, "-m", "dtu", "-ra", *pytest_args],
        cwd=str(REPO_ROOT),
    )
    if ephemeral:
        dtu_manager.teardown()
    sys.exit(proc.returncode)


if __name__ == "__main__":
    cli()
