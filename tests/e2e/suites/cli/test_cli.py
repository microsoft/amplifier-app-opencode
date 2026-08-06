"""DTU-backed tests: the `amplifier-opencode` command surface behaves per `docs/spec/cli.md`.

Ground-truth assertions only -- no TUI, no AI judge. Every test here runs a single
non-interactive command inside the DTU and asserts on its exit code and/or stdout:

* `--version` names BOTH components (this tool and the minimum amplifier-agent it
  requires), and its reported version agrees with the version actually installed --
  the regression this suite exists to catch is the two going out of sync.
* `--help` exits 0 and lists the real subcommands.
* An unknown subcommand is a parse error (exit 2), the contract scripts rely on to
  distinguish "bad invocation" from "ran and failed" (exit 1).
* `doctor` names all five of its checks, tags every check line with a real status
  token, and its exit code is explained by what it actually printed (a `[FAIL]` line,
  or a provider section reporting zero resolvable providers) rather than a hardcoded
  expectation -- a legitimately unconfigured provider makes `doctor` exit 1 honestly.
"""

from __future__ import annotations

import re

import pytest
from suites.cli.conftest import DoctorRun

# Read-only smoke checks: no seeding, no server restart, safe against the WARM DTU
# alongside every other suite.
pytestmark = pytest.mark.dtu

_VERSION_LINE_RE = re.compile(r"^amplifier-opencode\s+\d+(?:\.\d+)+")
_AGENT_LINE_PREFIX = "amplifier-agent"
_MIN_REQUIRED_TOKEN = "minimum required"

_HELP_SUBCOMMANDS = ("launch", "prepare", "setup", "doctor", "update")

_DOCTOR_CHECK_NAMES = (
    "amplifier-agent",
    "opencode",
    "server",
    "opencode config",
    "live models",
)
_CHECK_LINE_RE = re.compile(r"^\s*\[( OK |FAIL|INFO|WARN)\]\s+(\S.*)$")
_STATUS_TOKENS = frozenset({" OK ", "FAIL", "INFO", "WARN"})
_FAIL_TOKEN = "[FAIL]"
_RESOLVABLE_SUMMARY_RE = re.compile(r"\u2192\s*(\d+)\s+providers?\s+will be auto-enabled on launch")


def test_version_reports_both_components(cli_driver) -> None:
    """`--version` prints exactly two lines: this tool's version, then the agent's.

    Both lines carry a dotted version -- never hardcoded here, so a legitimate
    version bump does not turn this suite red. What IS pinned is the shape: line one
    starts with `amplifier-opencode <dotted version>`, line two starts with
    `amplifier-agent` and mentions `minimum required` (true of all three verbatim
    shapes `docs/spec/cli.md` documents: present-and-sufficient, present-but-below-
    minimum, and not-installed).
    """
    proc = cli_driver.run_command(["bash", "-lc", "amplifier-opencode --version"])
    assert proc.returncode == 0, (
        f"--version exited {proc.returncode}, expected 0\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    lines = proc.stdout.splitlines()
    assert len(lines) >= 2, f"expected at least two lines from --version, got:\n{proc.stdout!r}"

    assert _VERSION_LINE_RE.match(lines[0]), (
        "expected first line to match 'amplifier-opencode <dotted version>', got "
        f"{lines[0]!r}\nfull output:\n{proc.stdout}"
    )
    assert lines[1].startswith(_AGENT_LINE_PREFIX), (
        f"expected second line to start with {_AGENT_LINE_PREFIX!r}, got {lines[1]!r}\n"
        f"full output:\n{proc.stdout}"
    )
    assert _MIN_REQUIRED_TOKEN in lines[1], (
        f"expected second line to mention {_MIN_REQUIRED_TOKEN!r}, got {lines[1]!r}\n"
        f"full output:\n{proc.stdout}"
    )


def test_version_agrees_with_installed_package_metadata(cli_driver) -> None:
    """The version `--version` reports must equal the installed distribution's own version.

    This is the regression this test exists to catch: `--version`'s reported string and
    the actual `amplifier-app-opencode` distribution metadata drifted apart in the
    past. Neither value is hardcoded -- both are read live from the DTU and compared
    against each other, so the test stays valid across every future release.
    """
    version_proc = cli_driver.run_command(["bash", "-lc", "amplifier-opencode --version"])
    assert version_proc.returncode == 0, (
        f"--version exited {version_proc.returncode}, expected 0\n"
        f"stdout:\n{version_proc.stdout}\nstderr:\n{version_proc.stderr}"
    )
    first_line = version_proc.stdout.splitlines()[0] if version_proc.stdout.splitlines() else ""
    match = _VERSION_LINE_RE.match(first_line)
    assert match, (
        f"could not parse a dotted version out of {first_line!r}\n"
        f"full output:\n{version_proc.stdout}"
    )
    cli_version = match.group(0).removeprefix("amplifier-opencode").strip()

    # The CLI is installed as a uv tool, which lives in its own isolated venv rather
    # than on the system interpreter's path, so importlib.metadata against a plain
    # python3 cannot see it. `uv tool list` is the supported way to read the version
    # that was actually installed from the distribution manifest.
    listing = cli_driver.run_command(["bash", "-lc", "uv tool list"])
    assert listing.returncode == 0, (
        f"`uv tool list` failed (exit {listing.returncode})\n"
        f"stdout: {listing.stdout!r}\nstderr: {listing.stderr!r}"
    )
    installed_match = re.search(
        r"^amplifier-app-opencode\s+v(\d+\.\d+\.\d+)\s*$", listing.stdout, re.MULTILINE
    )
    assert installed_match, (
        "could not find an installed amplifier-app-opencode entry in `uv tool list`\n"
        f"full output:\n{listing.stdout}"
    )
    installed_version = installed_match.group(1)

    assert cli_version == installed_version, (
        "amplifier-opencode --version disagrees with the installed distribution metadata: "
        f"cli={cli_version!r} vs installed={installed_version!r}\n"
        f"--version output: {first_line!r}"
    )


def test_help_exits_zero(cli_driver) -> None:
    """`--help` exits 0 and names every real subcommand, so users can discover the surface."""
    proc = cli_driver.run_command(["bash", "-lc", "amplifier-opencode --help"])
    assert proc.returncode == 0, (
        f"--help exited {proc.returncode}, expected 0\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    missing = [name for name in _HELP_SUBCOMMANDS if name not in proc.stdout]
    assert not missing, (
        f"--help output missing subcommand(s) {missing}\nfull output:\n{proc.stdout}"
    )


def test_unknown_subcommand_exits_two(cli_driver) -> None:
    """An unknown subcommand is a parse error (exit 2), distinct from a runtime failure (exit 1).

    Scripts that wrap this CLI depend on that distinction to tell "I typed it wrong"
    apart from "it ran and failed."
    """
    proc = cli_driver.run_command(["bash", "-lc", "amplifier-opencode definitely-not-a-command"])
    assert proc.returncode == 2, (
        f"expected exit 2 for an unknown subcommand, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "No such command" in combined, (
        "expected 'No such command' in output for an unknown subcommand\n"
        f"combined output:\n{combined}"
    )


def test_doctor_reports_all_checks(doctor_run: DoctorRun) -> None:
    """`doctor` names all five checks, and every printed check line carries a real status token.

    Does NOT assert exit 0: a legitimately unconfigured provider makes `doctor` exit 1
    honestly (see `test_doctor_exit_code_matches_reported_failures`), so this test only
    verifies the checks are all present and shaped correctly.
    """
    stdout = doctor_run.stdout
    missing = [name for name in _DOCTOR_CHECK_NAMES if name not in stdout]
    assert not missing, f"doctor output missing check name(s) {missing}\nfull output:\n{stdout}"

    check_lines = [line for line in stdout.splitlines() if _CHECK_LINE_RE.match(line)]
    assert check_lines, (
        f"expected at least one bracketed '[ OK ]'/'[FAIL]'/etc check line\nfull output:\n{stdout}"
    )
    for line in check_lines:
        match = _CHECK_LINE_RE.match(line)
        assert match is not None and match.group(1) in _STATUS_TOKENS, (
            f"check line {line!r} does not carry a known status token {sorted(_STATUS_TOKENS)}\n"
            f"full output:\n{stdout}"
        )


def test_doctor_exit_code_matches_reported_failures(doctor_run: DoctorRun) -> None:
    """`doctor`'s exit code is explained by what it printed, not a hardcoded expectation.

    Zero `[FAIL]` lines AND at least one resolvable provider -> exit 0. Otherwise -> exit
    1 (a `[FAIL]` check, or the provider section counting as failed even with zero
    `[FAIL]` tags on screen -- see `docs/spec/cli.md`'s exit-code map). This ties the
    assertion to the visible evidence in the SAME run rather than assuming a fixed
    environment state.
    """
    stdout = doctor_run.stdout
    fail_count = stdout.count(_FAIL_TOKEN)
    resolvable_match = _RESOLVABLE_SUMMARY_RE.search(stdout)
    resolvable_count = int(resolvable_match.group(1)) if resolvable_match else 0

    if fail_count == 0 and resolvable_count >= 1:
        assert doctor_run.returncode == 0, (
            f"expected exit 0 (zero [FAIL] lines, {resolvable_count} resolvable provider(s)), "
            f"got {doctor_run.returncode}\nfull output:\n{stdout}"
        )
    else:
        assert doctor_run.returncode == 1, (
            f"expected exit 1 (fail_count={fail_count}, resolvable_count={resolvable_count}), "
            f"got {doctor_run.returncode}\nfull output:\n{stdout}"
        )
