"""amplifier-app-opencode installed and driven as a user would, in a Digital Twin Universe.

One universe per run, from the profile at `.agents/digital-twin-universe/opencode-amplifier/`. Docker and
ANTHROPIC_API_KEY are required; AMPLIFIER_AGENT_DIR names the amplifier-agent checkout served at its GitHub URL.

    make e2e                    every suite
    make e2e SUITE=chat         one suite: install, chat, coding, continuity, or tui
    E2E_KEEP=1 make e2e         leave the universe running and print its id
    E2E_UNIVERSE=<id> make e2e  run against a kept universe instead of launching one
    E2E_REF=<branch>            the branch to install (default: this checkout's branch)
"""

from collections.abc import Iterator
import os
import shlex

from digital_twin_universe import lib
from digital_twin_universe.schemas import DigitalTwinUniverseError, ExecResult
import pytest
from support import HOME, LAUNCH_TIMEOUT, PROFILE, Twin, current_branch


@pytest.fixture(scope="session")
def twin(request: pytest.FixtureRequest) -> Iterator[Twin]:
    existing = os.environ.get("E2E_UNIVERSE")
    if existing:
        yield Twin(lib.status(existing).id)
        return
    keep = os.environ.get("E2E_KEEP") == "1"
    try:
        universe = lib.launch(PROFILE, timeout_seconds=LAUNCH_TIMEOUT)
    except DigitalTwinUniverseError as error:
        if error.universe_id and not keep:
            lib.destroy(error.universe_id)
        raise
    try:
        yield Twin(universe.id)
    finally:
        if keep:
            reporter = request.config.pluginmanager.get_plugin("terminalreporter")
            reporter.write_line(
                f"Kept universe {universe.id}. Remove it: digital-twin-universe destroy --id {universe.id}"
            )
        else:
            lib.destroy(universe.id).require_removed()


@pytest.fixture(scope="session")
def ref() -> str:
    return current_branch()


@pytest.fixture(scope="session")
def installed(twin: Twin, ref: str) -> ExecResult:
    """The README's install, run once: install.sh fetched from raw.githubusercontent.com for this branch."""
    url = f"https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/{ref}/install.sh"
    return twin.run(f"curl -fsSL {url} | AMPLIFIER_OPENCODE_REF={shlex.quote(ref)} bash", workdir=HOME, timeout=900)


@pytest.fixture(scope="session")
def opencode(twin: Twin, installed: ExecResult) -> Twin:
    """The twin with amplifier-opencode installed."""
    assert installed.exit_code == 0, f"install failed\n{installed.stdout}\n{installed.stderr}"
    return twin


SUITES = ("install", "chat", "coding", "continuity", "tui")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Suites run in the order a user meets them; tests within a file keep their order."""
    items.sort(key=lambda item: SUITES.index(item.path.stem.removeprefix("test_")))
