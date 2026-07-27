"""amplifier-opencode CLI.

Single-binary integration that wraps opencode for amplifier-agent users:

  1. CHECK    -- is amplifier-agent's chat-completions server running?
  2. START    -- if not, spawn ``amplifier-agent serve chat-completions``
                 in the background, with the user's --workspace + --host-config
                 options.
  3. DISCOVER -- GET /v1/models from amplifier-agent. The response includes
                 amplifier-agent's metadata extensions (display_name, limit,
                 cost, capabilities, reasoning, _provider) so we can render
                 a rich opencode model picker.
  4. WRITE    -- materialise an ``opencode.json`` file at the project root
                 (or the global ``~/.config/opencode/opencode.jsonc``) with
                 the dynamic provider block. Preserves existing keys.
  5. EXEC     -- replace this process with ``opencode``, passing through any
                 extra argv. The user lands in the TUI with the live model
                 list already populated.

The whole adapter is this one binary. No opencode plugin, no monkey-patch,
no node_modules. The "dynamic discovery" the user wants happens at launch
time -- before opencode even starts -- so opencode itself only ever sees
a static config (which is what it can accept).

Drift handling: every ``amplifier-opencode`` launch re-fetches /v1/models
and rewrites the config. If amplifier-agent's host_config.json adds OpenAI
or Ollama or anything else, the next launch picks it up automatically.

Subcommands:

  amplifier-opencode             default: discover + write the bridge config
                                 (does NOT exec opencode)
  amplifier-opencode prepare     same as the default; explicit form for scripts
  amplifier-opencode launch      same setup, then exec the opencode TUI
  amplifier-opencode doctor      health checks for all prerequisites
  amplifier-opencode update      reinstall amplifier-opencode from latest main
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import click
import httpx

from . import onboarding, prereqs
from . import platform_utils as plat

# Provider report lives in prereqs (shared with onboarding); doctor patches
# this name in tests, so keep the historical ``cli._fetch_provider_report``
# symbol as a thin re-export.
from .prereqs import fetch_provider_report as _fetch_provider_report

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:9099/v1"
DEFAULT_API_KEY = "local-dev-secret"
DEFAULT_WORKSPACE = "opencode"
# 2 minutes: covers cold-prep first-launch (amplifier-agent installs 4 provider
# modules + the bundle's tool/hook modules via git fetch on first invocation,
# which can take 60-90s on slower networks). Warm boots typically finish in
# under 5s and exit the poll loop early.
DEFAULT_SERVER_READY_TIMEOUT_S = 120.0
DEFAULT_SERVER_PROBE_TIMEOUT_S = 2.0
DEFAULT_PROVIDER_ID = "amplifier"
DEFAULT_PROVIDER_NAME = "Amplifier"
DEFAULT_PROVIDER_NPM = "@ai-sdk/openai-compatible"
# Opt-in ceiling for the advertised per-model context window. None == forward
# the backend's value verbatim (default). Set via --max-context / env
# AMPLIFIER_OPENCODE_MAX_CONTEXT to guard against backend-vs-enforced limit
# mismatches that overflow the real provider cap (see build_provider_block).
DEFAULT_MAX_CONTEXT: int | None = None

# Self-update target. ``PACKAGE_NAME`` is the distribution name on PyPI / in
# ``importlib.metadata``; ``REPO_URL`` is the git remote ``update`` installs
# from when the user runs ``amplifier-opencode update``.
PACKAGE_NAME = "amplifier-app-opencode"
REPO_URL = "https://github.com/microsoft/amplifier-app-opencode.git"

# The minimum amplifier-agent version and the version-comparison helpers live in
# one place -- prereqs.py -- and are used from here via ``prereqs.<name>``. Keep
# them defined once so the floor can never disagree with itself across files.

# Resolved via platform_utils so they land in %TEMP% on native Windows instead
# of a non-existent /tmp. Tests monkeypatch these module attributes directly.
SERVER_LOG_PATH = plat.server_log_path()

# Global opencode config dir (XDG-aligned).
GLOBAL_OPENCODE_DIR = Path.home() / ".config" / "opencode"


# ---------------------------------------------------------------------------
# Static per-model price catalog (USD per 1M tokens)
# ---------------------------------------------------------------------------

# opencode looks up pricing by ``(providerID, modelID)``. The models.dev
# catalog opencode bundles has entries for the canonical provider IDs
# (anthropic, openai, ...) but NOT for our custom ``amplifier`` provider
# -- so opencode renders our turns at $0.00 unless we declare cost in
# the model entry ourselves.
#
# We mirror models.dev's pricing here so the model picker and per-turn
# cost display work out of the box. Source of truth:
#   https://github.com/sst/models -- the same registry opencode uses.
#
# Maintenance: when an upstream provider changes prices, update this
# table. amplifier-agent's per-turn ``cost_usd`` (PR #68 on amplifier-agent)
# still ships the authoritative dollar value on the wire for clients that
# read it; this catalog is purely for opencode's TUI cost display.
#
# Verified pricing date: 2026-06-21.
MODEL_PRICING_PER_MILLION: dict[str, dict[str, float]] = {
    # ===== Anthropic =====
    "claude-haiku-4-5-20251001": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.1,
        "cache_write": 1.25,
    },
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    # ===== OpenAI =====
    # GPT-4o
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache_read": 0.075},
    # GPT-4.1
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache_read": 0.5},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6, "cache_read": 0.1},
    "gpt-4.1-nano": {"input": 0.1, "output": 0.4, "cache_read": 0.025},
    # GPT-5
    "gpt-5": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gpt-5-codex": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gpt-5-mini": {"input": 0.25, "output": 2.0, "cache_read": 0.025},
    "gpt-5-nano": {"input": 0.05, "output": 0.4, "cache_read": 0.005},
    "gpt-5-pro": {"input": 15.0, "output": 120.0},
    # GPT-5.1
    "gpt-5.1": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gpt-5.1-codex": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gpt-5.1-codex-max": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gpt-5.1-codex-mini": {"input": 0.25, "output": 2.0, "cache_read": 0.025},
    # GPT-5.2
    "gpt-5.2": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    "gpt-5.2-codex": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    # o-series (reasoning models)
    "o1": {"input": 15.0, "output": 60.0, "cache_read": 7.5},
    "o1-pro": {"input": 150.0, "output": 600.0},
    "o3": {"input": 2.0, "output": 8.0, "cache_read": 0.5},
    "o3-mini": {"input": 1.1, "output": 4.4, "cache_read": 0.55},
    "o3-pro": {"input": 20.0, "output": 80.0},
    "o4-mini": {"input": 1.1, "output": 4.4, "cache_read": 0.275},
    # Embeddings (output is always 0; opencode schema requires both fields)
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def lookup_pricing(model_id: str) -> dict[str, float] | None:
    """Return per-million-token pricing for ``model_id``, or None when absent.

    Exact-match lookup against :data:`MODEL_PRICING_PER_MILLION`. Returns
    ``None`` for unknown models so callers can distinguish "no pricing
    declared" from "free model" (emitting zeros would falsely claim the
    model is free).
    """
    return MODEL_PRICING_PER_MILLION.get(model_id)


def resolve_global_config_path() -> Path:
    """Locate (or pick) the global opencode config file.

    opencode loads config from any of these paths under ``~/.config/opencode``:
    ``config.json``, ``opencode.json``, ``opencode.jsonc``. We prefer the
    .jsonc file when it already exists (matches the standard global file
    opencode's installer drops), then .json, otherwise create a new
    .jsonc so user-side comments are allowed.
    """
    for name in ("opencode.jsonc", "opencode.json", "config.json"):
        candidate = GLOBAL_OPENCODE_DIR / name
        if candidate.exists():
            return candidate
    return GLOBAL_OPENCODE_DIR / "opencode.jsonc"


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def server_is_running(
    base_url: str,
    api_key: str,
    timeout_s: float = DEFAULT_SERVER_PROBE_TIMEOUT_S,
) -> bool:
    """Return True if amplifier-agent is reachable at ``base_url``.

    Probes ``GET {base_url}/models`` with the bearer token. Connection
    failures and non-200 responses both yield False.
    """
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        return r.status_code == 200
    except httpx.RequestError:
        return False


def start_amplifier_agent(
    *,
    port: int,
    workspace: str,
    host_config: Path | None,
    api_key: str,
    binary: str | None,
    cwd: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn ``amplifier-agent serve chat-completions`` in the background.

    Output is appended to /tmp/amplifier-agent.log. Liveness of an
    already-running instance is detected via an HTTP probe
    (``server_is_running``), not a PID file.

    ``cwd`` sets the server's working directory. It must be the launch/project
    dir so amplifier-agent's project-relative ``.amplifier/skills/`` discovery
    finds skills seeded under the project (otherwise the server inherits our
    CWD and silently misses project-scoped skills).
    """
    binary = binary or shutil.which("amplifier-agent")
    if not binary:
        raise click.ClickException(
            "amplifier-agent binary not found in PATH. Install it via "
            "``uv tool install amplifier-agent`` or use ``--no-start`` if "
            "the server is already running elsewhere."
        )

    cmd = [
        binary,
        "serve",
        "chat-completions",
        "--port",
        str(port),
        "--workspace",
        workspace,
        "--api-key",
        api_key,
    ]
    if host_config:
        cmd.extend(["--config", str(host_config)])

    log_fh = SERVER_LOG_PATH.open("ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # so amplifier-agent survives our exec
        cwd=str(cwd) if cwd is not None else None,
    )
    return proc


def wait_for_server_ready(
    base_url: str,
    api_key: str,
    timeout_s: float = DEFAULT_SERVER_READY_TIMEOUT_S,
    poll_interval_s: float = 1.0,
) -> bool:
    """Poll /v1/models until healthy or timeout. Returns True on success."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server_is_running(base_url, api_key, timeout_s=1.0):
            return True
        time.sleep(poll_interval_s)
    return False


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


def fetch_models(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Return the list of model dicts from amplifier-agent's /v1/models."""
    r = httpx.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15.0,
    )
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise click.ClickException(
            f"/v1/models returned malformed body: {type(body).__name__}, expected object"
        )
    data = body.get("data", [])
    if not isinstance(data, list):
        raise click.ClickException(
            f"/v1/models returned malformed data: {type(data).__name__}, expected list"
        )
    # Keep only well-formed row objects. A stray scalar/None in the array would
    # crash every downstream consumer (the discovery print loop, provider-block
    # build, and the doctor enumeration), so filter it out at the source.
    return [m for m in data if isinstance(m, dict)]


def build_provider_block(
    *,
    base_url: str,
    api_key: str,
    models: list[dict[str, Any]],
    provider_name: str = DEFAULT_PROVIDER_NAME,
    provider_npm: str = DEFAULT_PROVIDER_NPM,
    max_context: int | None = None,
) -> dict[str, Any]:
    """Transform amplifier-agent's /v1/models into an opencode provider block.

    opencode's per-model schema lenient-strips unknown keys but FAILS the
    WHOLE config decode on type mismatches in known fields. When decode
    fails on the global config, opencode catches via ``orElseSucceed(() =>
    ({}))`` -- silently wiping every provider's models block. We're
    therefore paranoid about types: every field we surface is strictly
    validated before emission, and we skip a field rather than risk
    dropping the whole entry.

    Fields surfaced:

      - ``name``  (always; falls back to id when no display_name)
      - ``cost``  ``{input, output, cache_read?, cache_write?}`` -- USD
                  per 1M tokens. Pulled from the static
                  :data:`MODEL_PRICING_PER_MILLION` catalog because
                  opencode keys catalog lookups by ``(providerID,
                  modelID)`` and our custom ``amplifier`` providerID
                  doesn't match anything in opencode's bundled models.dev
                  registry. Without this declaration, opencode renders
                  every turn at $0.00. Emitted only when both ``input``
                  and ``output`` are present in the catalog (skipped
                  silently for unknown models).
      - ``limit`` ``{context, output}`` -- token-count budgets opencode
                  uses for context-window warnings. Lifted from
                  amplifier-agent's /v1/models ``limit`` field
                  (originally ``ModelInfo.context_window`` /
                  ``max_output_tokens``). When ``max_context`` is set, the
                  ``context`` value is clamped down to that ceiling (see
                  below).

    Defensive clamp (``max_context``)
        opencode triggers context compaction near ~90% of the advertised
        ``limit.context``. If the backend advertises a window larger than
        the provider will actually honor at request time (e.g. amplifier-
        agent reporting a 1,000,000-token window for a Claude model whose
        Anthropic account is not entitled to the 1M-context beta), opencode
        never compacts in the danger zone and the request is rejected with
        a hard ``prompt is too long: N > 200000 maximum`` 400.

        ``max_context`` is an opt-in safety net: when set (CLI
        ``--max-context`` / env ``AMPLIFIER_OPENCODE_MAX_CONTEXT``), every
        model's advertised context window is capped at this value so
        opencode compacts before the real enforced limit. Left ``None``
        (the default) the adapter forwards the backend's value verbatim --
        the proper fix is for the backend to advertise a window it can
        honor (tracked upstream in amplifier-module-provider-anthropic).

    Note: amplifier-agent's PR #68 ALSO surfaces real per-turn
    ``cost_usd`` on the chat-completions response (telemetry on the
    wire). The catalog here is for opencode's TUI display since
    opencode's @ai-sdk/openai-compatible adapter doesn't read
    ``cost_usd`` today.
    """
    models_block: dict[str, dict[str, Any]] = {}
    for m in models:
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue

        entry: dict[str, Any] = {"name": m.get("display_name") or mid}

        # Cost block — from our static catalog. Skip when the model is
        # not in the catalog (showing $0 zeros is worse than showing
        # nothing; an empty cost block lets opencode know the data is
        # missing rather than claiming the model is free).
        pricing = lookup_pricing(mid)
        if pricing is not None:
            cost_entry: dict[str, float] = {
                "input": float(pricing["input"]),
                "output": float(pricing["output"]),
            }
            if "cache_read" in pricing:
                cost_entry["cache_read"] = float(pricing["cache_read"])
            if "cache_write" in pricing:
                cost_entry["cache_write"] = float(pricing["cache_write"])
            entry["cost"] = cost_entry

        # Limit block — only emit when both context AND output are ints.
        limit = m.get("limit")
        if (
            isinstance(limit, dict)
            and isinstance(limit.get("context"), int)
            and isinstance(limit.get("output"), int)
        ):
            context = int(limit["context"])
            # Defensive clamp: cap the advertised context window so opencode
            # compacts before any backend-vs-enforced limit mismatch overflows
            # the real provider cap. Opt-in; None == faithful passthrough.
            if max_context is not None and context > max_context:
                context = max_context
            entry["limit"] = {
                "context": context,
                "output": int(limit["output"]),
            }

        models_block[mid] = entry

    return {
        "npm": provider_npm,
        "name": provider_name,
        "options": {"baseURL": base_url, "apiKey": api_key},
        "models": models_block,
    }


# ---------------------------------------------------------------------------
# opencode.json materialisation
# ---------------------------------------------------------------------------


def write_opencode_config(
    config_path: Path,
    provider: dict[str, Any],
    *,
    provider_id: str = DEFAULT_PROVIDER_ID,
) -> Path:
    """Merge the provider block into the target opencode config file.

    Preserves any existing keys (other providers, plugins, etc.). Only
    replaces ``provider.<provider_id>``. Creates the parent directory and
    the file itself if they do not exist.

    Supports both ``opencode.json`` and ``opencode.jsonc`` target paths. We
    always read/write as JSON; any existing comments in a .jsonc file will
    be lost on rewrite (documented in the README).
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            text = config_path.read_text() or "{}"
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise click.ClickException(
                f"Existing {config_path} is not valid JSON: {exc}. "
                "Remove or fix the file, then retry."
            ) from exc
        if not isinstance(loaded, dict):
            raise click.ClickException(
                f"Existing {config_path} is valid JSON but not an object "
                f"(found {type(loaded).__name__}); refusing to overwrite."
            )
        existing = loaded

    existing.setdefault("$schema", "https://opencode.ai/config.json")
    existing.setdefault("provider", {})
    if not isinstance(existing["provider"], dict):
        raise click.ClickException(
            f"Existing {config_path}.provider is not a dict; refusing to overwrite."
        )
    existing["provider"][provider_id] = provider

    # Atomic write: render to a sibling temp file, then os.replace() into place.
    # A crash mid-write leaves the original config intact rather than truncated.
    payload = json.dumps(existing, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{config_path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(payload)
        os.replace(tmp_path, config_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return config_path


# ---------------------------------------------------------------------------
# Shared bridge-name validation (used by BOTH the skills and modes bridges)
# ---------------------------------------------------------------------------

# Both bridges turn a server-supplied ``name`` into a FILENAME under a directory we
# own. That name is attacker-influenced: a skill's name is read verbatim from SKILL.md
# YAML frontmatter (upstream tool-skills discovery checks it against its own pattern but
# only logs a warning and registers it anyway), and any actor who can drop a file into a
# discovery dir controls it. Without a shape check, ``command_dir / f"{name}.md"`` with
# a name like ``../../../../tmp/evil`` escapes the directory entirely, and
# ``_atomic_write_text``'s ``os.replace`` overwrites the target silently.
#
# Worse, the name is persisted in the ownership manifest, so the NEXT run's prune step
# (``(command_dir / stale).unlink()``) turns it into an arbitrary-delete primitive.
# Validating here -- at fetch, before anything is written or recorded -- closes both.
_SAFE_BRIDGE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def is_safe_bridge_name(name: str) -> bool:
    """True if ``name`` is safe to use as a bare filename component.

    Three independent conditions, deliberately redundant so loosening any one of them
    cannot silently reopen traversal:

    * the whitelist admits no ``/`` and no ``\\``, so no separator of either flavour
      survives (the backslash case matters on Windows, where a mode name derived from a
      file stem can legitimately contain one),
    * ``.`` and ``..`` are rejected explicitly -- they pass the whitelist but are
      directory references, not names,
    * the name must equal its own basename and must not be absolute, which is the
      general statement of the property the first two conditions approximate.
    """
    if not name or name in {".", ".."}:
        return False
    if not _SAFE_BRIDGE_NAME.fullmatch(name):
        return False
    pure = PurePosixPath(name)
    return name == pure.name and not pure.is_absolute()


def _usable_bridge_rows(data: list[Any], kind: str) -> list[dict[str, Any]]:
    """Keep well-formed ``{name, ...}`` rows whose name is a safe filename component.

    The SINGLE choke point for both ``fetch_skills`` and ``fetch_modes`` -- shared on
    purpose so the two bridges cannot drift apart on what they consider acceptable.

    Rejection is LOUD (a yellow warning naming the offender) rather than silent: a name
    that fails this check is either a misconfigured resource the author needs to fix or
    an attempted traversal the user needs to know about. Neither should be swallowed.
    Rejection is also non-fatal -- one bad row must not block the launch, matching the
    best-effort contract of the fetch functions.
    """
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        if not is_safe_bridge_name(name):
            click.secho(
                f"      {kind}: refusing {name!r} -- unsafe name (must be a bare filename "
                "of [A-Za-z0-9._-] characters); not bridged",
                fg="yellow",
            )
            continue
        rows.append(row)
    return rows


def _normalized_shadowed(raw: Any) -> list[dict[str, str]]:
    """Coerce a server-supplied ``shadowed`` field into ``[{"source": str}, ...]``.

    amplifier-agent reports, per resource, the file that actually RUNS (``source``)
    plus every same-named file that lost to it (``shadowed``). The agent is a separate
    process on its own release cadence, so this launcher must not assume the field is
    present or well-shaped: an older server omits it entirely, and any future shape
    drift must degrade to "no conflicts known" rather than crash a launch.

    Anything that is not a dict carrying a non-empty string ``source`` is dropped, and
    each surviving entry is rebuilt as a minimal ``{"source": ...}`` so downstream
    rendering never has to re-validate.
    """
    if not isinstance(raw, list):
        return []
    losers: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            continue
        losers.append({"source": source})
    return losers


def _normalized_bridge_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return ``row`` with ``source`` and ``shadowed`` coerced to their contract shape.

    ``name`` and ``description`` are passed through untouched -- their handling is
    already settled by ``_usable_bridge_rows`` and the write loops. Only the two
    provenance fields are normalized here, so every consumer downstream can rely on
    ``source`` being a ``str`` and ``shadowed`` being a clean list.
    """
    normalized = dict(row)
    source = row.get("source")
    normalized["source"] = source if isinstance(source, str) else ""
    normalized["shadowed"] = _normalized_shadowed(row.get("shadowed"))
    return normalized


def render_bridge_conflicts(kind: str, entries: list[dict[str, Any]]) -> None:
    """Print the name collisions the agent reported for ``kind`` (skills / modes).

    A shadowed override used to vanish silently: the user dropped a ``code-review``
    override into ``~/.amplifier/skills`` and never found out that a same-named file
    earlier in the search order was the one actually running. The agent now reports
    both sides; this surfaces them at launch, where the user is already looking.

    Prints NOTHING when no entry carries a shadowed file, so a clean setup stays quiet.
    Re-normalizes defensively so the helper is total on any input (it is also the unit
    under test, and must not depend on having been fed post-fetch rows).
    """
    conflicts = [(entry, _normalized_shadowed(entry.get("shadowed"))) for entry in entries]
    conflicts = [(entry, losers) for entry, losers in conflicts if losers]
    if not conflicts:
        return

    plural = "" if len(conflicts) == 1 else "s"
    click.secho(
        f"      {kind}: {len(conflicts)} name conflict{plural} "
        "(the file under 'runs' is the one that runs):",
        fg="yellow",
    )
    for entry, losers in conflicts:
        name = entry.get("name", "")
        source = entry.get("source", "")
        click.secho(f"        {name if isinstance(name, str) else ''}", fg="yellow")
        click.secho(f"          runs:     {source if isinstance(source, str) else ''}", fg="yellow")
        for loser in losers:
            click.secho(f"          shadowed: {loser['source']}", fg="yellow")


# ---------------------------------------------------------------------------
# Skills bridge: /v1/skills -> opencode /command files
# ---------------------------------------------------------------------------

# Sidecar manifest that records which command files THIS launcher generated.
# opencode's command frontmatter schema is closed and rejects unknown keys, so
# ownership can't live in the file itself -- we track it out-of-band. The
# manifest lives next to the command dir (in its parent scope dir).
GENERATED_COMMANDS_MANIFEST = ".amplifier-generated-commands.json"


def fetch_skills(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Return user-invocable skills from amplifier-agent's /v1/skills.

    Mirrors :func:`fetch_models` (bearer auth + ``data`` extraction) but is
    strictly best-effort: on ANY error (network failure, non-200, malformed
    JSON, missing/oddly-shaped ``data``) it returns an empty list and never
    raises. Skills are an optional enhancement; they must never block launch.

    A skill's ``name`` becomes a FILENAME downstream, so rows carrying an unsafe name
    are dropped here via the shared :func:`_usable_bridge_rows`. Filtering at fetch --
    rather than at write -- means an unsafe name can never reach the ownership manifest
    either, which is what closes the delayed-delete path through the prune step.

    Every surviving row is passed through :func:`_normalized_bridge_row`, so callers can
    rely on ``source`` being a ``str`` and ``shadowed`` being a list of
    ``{"source": str}`` regardless of what the (separately-versioned) server sent.
    """
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/skills",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
    except Exception:
        return []
    if not isinstance(body, dict):
        return []
    data = body.get("data", [])
    if not isinstance(data, list):
        return []
    return [_normalized_bridge_row(row) for row in _usable_bridge_rows(data, "skills")]


def resolve_command_dir(project_dir: Path | None) -> Path:
    """Return the opencode command dir for the same scope as the provider config.

    Matches how ``_run_launch`` chooses the provider-config path:
      * ``project_dir`` given -> ``<project>/.opencode/command/``
      * otherwise             -> ``~/.config/opencode/command/``
    """
    if project_dir is None:
        return GLOBAL_OPENCODE_DIR / "command"
    return project_dir / ".opencode" / "command"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``).

    Same idiom as :func:`write_opencode_config`: render to a sibling temp file,
    then ``os.replace`` into place so a crash mid-write leaves any existing file
    intact rather than truncated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_generated_manifest(manifest_path: Path, key: str = "commands") -> list[str]:
    """Return the list of filenames a previous run generated.

    Best-effort: a missing or corrupt manifest yields an empty list (we simply
    treat every existing file as unowned and never clobber it). ``key`` selects
    the manifest field (``commands`` for the skills bridge, ``agents`` for the
    modes bridge) so the two bridges keep independent ownership manifests.
    """
    if not manifest_path.exists():
        return []
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return []
    items = loaded.get(key, []) if isinstance(loaded, dict) else loaded
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, str)]


def _write_generated_manifest(manifest_path: Path, items: list[str], key: str = "commands") -> None:
    """Persist the ownership manifest atomically under ``key``."""
    payload = json.dumps({key: sorted(items)}, indent=2) + "\n"
    _atomic_write_text(manifest_path, payload)


def write_command_files(skills: list[dict[str, Any]], command_dir: Path) -> None:
    """Materialise one opencode ``/command`` file per skill, with reconciliation.

    Each skill ``{name, description}`` becomes ``{command_dir}/{name}.md``::

        ---
        description: "<skill description>"
        ---
        !amplifier:skill <name> $ARGUMENTS

    The description is serialised with ``json.dumps`` -- a JSON string is also a
    valid YAML double-quoted scalar, so colons, quotes and newlines cannot break
    the frontmatter. The body line is emitted verbatim (opencode only treats a
    leading ``!`` specially when immediately followed by a backtick).

    Ownership is tracked in a sidecar manifest at
    ``{command_dir.parent}/.amplifier-generated-commands.json``. On each run we:
      * (re)write a file for every current skill and record it in the new manifest,
      * delete files listed in the OLD manifest whose skill no longer exists,
      * skip -- with a warning -- any target that already exists but is NOT in the
        old manifest (a user's own command is never overwritten).
    """
    command_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = command_dir.parent / GENERATED_COMMANDS_MANIFEST
    old_owned = set(_read_generated_manifest(manifest_path))

    new_owned: list[str] = []
    for skill in skills:
        name = skill.get("name")
        if not isinstance(name, str) or not name:
            continue
        filename = f"{name}.md"
        target = command_dir / filename

        # Two skills in THIS run can map to the same filename (two rows carrying the
        # same name -- e.g. a server that stopped de-duplicating). Without this the
        # second write silently clobbers the first and the manifest lists the file
        # twice. First wins, matching the agent's own first-match-wins discipline.
        if filename in new_owned:
            click.secho(
                f"      skills: skipping duplicate {filename} -- more than one skill is "
                f"named {name!r} in this run (keeping the first)",
                fg="yellow",
            )
            continue

        # Never overwrite a user's own command file: it exists but we didn't
        # generate it (not in the previous run's manifest).
        if target.exists() and filename not in old_owned:
            click.secho(
                f"      skills: skipping {target.name} -- exists and was not "
                "generated by amplifier-opencode (leaving your command untouched)",
                fg="yellow",
            )
            continue

        description = skill.get("description", "")
        if not isinstance(description, str):
            description = str(description)
        content = (
            "---\n"
            f"description: {json.dumps(description)}\n"
            "---\n"
            f"!amplifier:skill {name} $ARGUMENTS\n"
        )
        _atomic_write_text(target, content)
        new_owned.append(filename)

    # Prune generated files that are no longer backed by a skill.
    for stale in old_owned - set(new_owned):
        with contextlib.suppress(FileNotFoundError):
            (command_dir / stale).unlink()

    _write_generated_manifest(manifest_path, new_owned)


# ---------------------------------------------------------------------------
# Modes bridge: /v1/modes -> opencode primary-agent files
# ---------------------------------------------------------------------------

# Sidecar manifest that records which agent files THIS launcher generated. Kept
# separate from the skills/commands manifest so the two bridges reconcile
# independently. Lives next to the agent dir (in its parent scope dir).
GENERATED_AGENTS_MANIFEST = ".amplifier-generated-agents.json"

# Prefix applied to every generated agent so amplifier modes never collide with
# opencode's native agents (e.g. ``build``). The prefix is an opencode-layer
# concern only; amplifier-agent deals in the bare mode name.
MODE_AGENT_PREFIX = "amplifier-"


def fetch_modes(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Return the shipped modes from amplifier-agent's /v1/modes.

    Mirrors :func:`fetch_skills` (bearer auth + ``data`` extraction) but is
    strictly best-effort: on ANY error (network failure, non-200, malformed
    JSON, missing/oddly-shaped ``data``) it returns an empty list and never
    raises. Modes are an optional enhancement; they must never block launch.

    Unlike skills there is no server-side filter -- /v1/modes already returns
    exactly the modes we want to surface (all shipped/discovered modes).

    Names are validated by the same shared :func:`_usable_bridge_rows` the skills
    bridge uses, so the two faces cannot drift on what they accept. A mode's name is
    a ``.md`` file stem server-side, so it cannot contain ``/`` on POSIX -- but it CAN
    be ``..`` or contain a backslash (which traverses on Windows), and
    ``MODE_AGENT_PREFIX`` offers no protection since ``amplifier-../../evil`` still
    resolves through the ``..`` segments.

    Every surviving row is passed through :func:`_normalized_bridge_row`, so callers can
    rely on ``source`` being a ``str`` and ``shadowed`` being a list of
    ``{"source": str}`` regardless of what the (separately-versioned) server sent.
    """
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/modes",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
    except Exception:
        return []
    if not isinstance(body, dict):
        return []
    data = body.get("data", [])
    if not isinstance(data, list):
        return []
    # Keep only well-formed rows that at least carry a usable name.
    return [
        _normalized_bridge_row(m)
        for m in data
        if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"]
    ]


def resolve_agent_dir(project_dir: Path | None) -> Path:
    """Return the opencode agent dir for the same scope as the provider config.

    Matches how ``_run_launch`` chooses the provider-config path:
      * ``project_dir`` given -> ``<project>/.opencode/agent/``
      * otherwise             -> ``~/.config/opencode/agent/``
    """
    if project_dir is None:
        return GLOBAL_OPENCODE_DIR / "agent"
    return project_dir / ".opencode" / "agent"


def write_agent_files(
    modes: list[dict[str, Any]],
    agent_dir: Path,
) -> None:
    """Materialise one opencode primary-agent file per mode, with reconciliation.

    Each mode ``{name, description}`` becomes
    ``{agent_dir}/amplifier-{name}.md``::

        ---
        mode: primary
        description: "<mode description>"
        ---
        Amplifier mode "<name>". Behaviour is applied server-side by amplifier-agent.

        [amplifier-agent:mode=<name>]

    The agent deliberately has NO ``model`` field. It inherits the session's
    current model, so opencode never rejects the agent with "configured model ...
    is not valid" and never lists a synthetic mode model in its ``/models`` picker.

    The mode is signalled to amplifier-agent by the ``[amplifier-agent:mode=<name>]``
    directive in the body. opencode forwards a primary agent's prompt (this body)
    to the backend as a system message every turn, and amplifier-agent recovers the
    active mode from that directive (see routes/chat_completions.py
    ``_detect_mode_from_messages``). No model alias is involved. Because opencode
    re-sends the active agent's prompt each turn, mode persistence is free.

    Ownership is tracked in a sidecar manifest at
    ``{agent_dir.parent}/.amplifier-generated-agents.json``. On each run we:
      * (re)write a file for every current mode and record it in the new manifest,
      * delete files listed in the OLD manifest whose mode no longer exists,
      * skip -- with a warning -- any target that already exists but is NOT in the
        old manifest (a user's own agent is never overwritten).
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = agent_dir.parent / GENERATED_AGENTS_MANIFEST
    old_owned = set(_read_generated_manifest(manifest_path, key="agents"))

    new_owned: list[str] = []
    for mode in modes:
        name = mode.get("name")
        if not isinstance(name, str) or not name:
            continue
        filename = f"{MODE_AGENT_PREFIX}{name}.md"
        target = agent_dir / filename

        # Two modes in THIS run can map to the same filename (two rows carrying the
        # same name -- e.g. a server that stopped de-duplicating). Without this the
        # second write silently clobbers the first and the manifest lists the file
        # twice. First wins, matching the agent's own first-match-wins discipline.
        if filename in new_owned:
            click.secho(
                f"      modes: skipping duplicate {filename} -- more than one mode is "
                f"named {name!r} in this run (keeping the first)",
                fg="yellow",
            )
            continue

        # Never overwrite a user's own agent file: it exists but we didn't
        # generate it (not in the previous run's manifest).
        if target.exists() and filename not in old_owned:
            click.secho(
                f"      modes: skipping {target.name} -- exists and was not "
                "generated by amplifier-opencode (leaving your agent untouched)",
                fg="yellow",
            )
            continue

        description = mode.get("description", "")
        if not isinstance(description, str):
            description = str(description)
        # No ``model`` field: the agent inherits the session's current model, so
        # opencode never rejects it as invalid and never lists a synthetic mode
        # model in its picker. The mode is signalled to amplifier-agent by the
        # ``[amplifier-agent:mode=<name>]`` directive in the body, which opencode
        # forwards as a system message each turn (see amplifier-agent
        # routes/chat_completions.py ``_detect_mode_from_messages``).
        content = (
            "---\n"
            "mode: primary\n"
            f"description: {json.dumps(description)}\n"
            "---\n"
            f"Amplifier mode {json.dumps(name)}. "
            "Behaviour is applied server-side by amplifier-agent.\n"
            f"\n[amplifier-agent:mode={name}]\n"
        )
        _atomic_write_text(target, content)
        new_owned.append(filename)

    # Prune generated files that are no longer backed by a mode.
    for stale in old_owned - set(new_owned):
        with contextlib.suppress(FileNotFoundError):
            (agent_dir / stale).unlink()

    _write_generated_manifest(manifest_path, new_owned, key="agents")


# ---------------------------------------------------------------------------
# opencode launch
# ---------------------------------------------------------------------------


def exec_opencode(project_dir: Path, opencode_args: tuple[str, ...]) -> None:
    """Replace this process with ``opencode``.

    Uses os.execvp so opencode inherits our terminal cleanly and we don't
    sit between it and the user's stdin/stdout.
    """
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        raise click.ClickException(
            "opencode binary not found in PATH. Install via "
            "``curl -fsSL https://opencode.ai/install | bash`` or see "
            "https://opencode.ai/docs/intro for other install methods."
        )
    os.chdir(project_dir)
    os.execvp(opencode_bin, [opencode_bin, *opencode_args])


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _port_from_url(url: str) -> int:
    """Best-effort port extraction from a base URL. Defaults to 9099."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.port:
            return int(parsed.port)
    except (ValueError, AttributeError):
        pass
    return 9099


def _on_signal(signum: int, _frame: Any) -> None:
    sys.exit(128 + signum)


for _sig in (signal.SIGTERM, signal.SIGINT):
    with contextlib.suppress(ValueError, OSError):
        signal.signal(_sig, _on_signal)


# ---------------------------------------------------------------------------
# Launch implementation (shared between default + ``launch`` subcommand)
# ---------------------------------------------------------------------------


def _run_launch(
    *,
    base_url: str,
    api_key: str,
    workspace: str,
    host_config: Path | None,
    project_dir: Path | None,
    no_start: bool,
    no_launch: bool,
    amplifier_agent_bin: Path | None,
    provider_id: str,
    opencode_args: tuple[str, ...],
    max_context: int | None = None,
    bootstrap: bool = True,
    assume_yes: bool = False,
) -> None:
    """The (bootstrap ->) check -> start -> discover -> write -> exec flow.

    ``bootstrap`` (default True) makes the launch self-healing: before doing
    anything else we ensure amplifier-agent and opencode are installed and
    healthy, and -- if the agent reports zero resolvable providers -- walk the
    user through configuring one. Pass ``bootstrap=False`` (``--no-bootstrap``)
    to skip all of that and assume the environment is already prepared.
    """
    # Step 0: self-healing preflight ----------------------------------------
    if bootstrap:
        if not prereqs.ensure_prerequisites(assume_yes=assume_yes, allow_install=True):
            raise click.ClickException(
                "Prerequisites are not ready (see messages above). Re-run with "
                "--yes to auto-install, or fix the reported items and retry. "
                "Use --no-bootstrap to skip this preflight entirely."
            )
        # Configure a provider credential now (before we start serve) so the
        # server comes up with models available, rather than an empty picker.
        if onboarding.needs_onboarding():
            onboarding.run_onboarding(assume_yes=assume_yes)

    # Resolve the config target: global by default, project-level if --project-dir.
    if project_dir is None:
        config_path = resolve_global_config_path()
        launch_dir = Path.cwd()
    else:
        project_dir = project_dir.resolve()
        config_path = project_dir / "opencode.json"
        launch_dir = project_dir

    # Step 1: ensure server is running --------------------------------------
    if server_is_running(base_url, api_key):
        click.secho(f"[1/4] amplifier-agent already running at {base_url}", fg="green")
    elif no_start:
        raise click.ClickException(
            f"amplifier-agent is NOT running at {base_url} and --no-start "
            "was passed. Start it manually or remove --no-start to have "
            "us spawn it."
        )
    else:
        port = _port_from_url(base_url)
        click.secho(
            f"[1/4] Starting amplifier-agent (port {port}, workspace={workspace!r})",
            fg="cyan",
        )
        # Pass --host-config through verbatim when the user supplied one;
        # otherwise pass nothing. amplifier-agent auto-enables every provider
        # whose credentials resolve (env var or credentials.json) when no
        # explicit `--config` providers block is given, so a bare
        # `amplifier-agent auth set <provider> <key>` is sufficient to boot.
        start_amplifier_agent(
            port=port,
            workspace=workspace,
            host_config=host_config,
            api_key=api_key,
            binary=str(amplifier_agent_bin) if amplifier_agent_bin else None,
            cwd=launch_dir,
        )
        if not wait_for_server_ready(base_url, api_key):
            raise click.ClickException(
                f"amplifier-agent did not become ready within "
                f"{DEFAULT_SERVER_READY_TIMEOUT_S:.0f}s. "
                f"Check the log: {SERVER_LOG_PATH}"
            )
        click.secho(f"      amplifier-agent ready at {base_url}", fg="green")

    # Step 2: discover models -----------------------------------------------
    click.secho(f"[2/4] Discovering models via GET {base_url}/models", fg="cyan")
    try:
        models = fetch_models(base_url, api_key)
    except httpx.HTTPStatusError as exc:
        raise click.ClickException(
            f"/v1/models returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except httpx.RequestError as exc:
        raise click.ClickException(f"Could not reach /v1/models at {base_url}: {exc}") from exc

    if not models:
        click.secho(
            "      WARNING: /v1/models returned 0 models. opencode picker will be empty.",
            fg="yellow",
        )
    for m in models:
        # Coerce to str: the ``:10s`` / ``:35s`` format specs raise TypeError on
        # a non-string value (e.g. a numeric id), so never feed them raw fields.
        provider_tag = str(m.get("_provider", "?"))
        model_id = str(m.get("id", "?"))
        display = str(m.get("display_name") or "")
        click.secho(
            f"      - {provider_tag:10s}  {model_id:35s}  {display}",
            fg="white",
        )

    # Step 3: write the opencode config -------------------------------------
    provider_block = build_provider_block(
        base_url=base_url,
        api_key=api_key,
        models=models,
        max_context=max_context,
    )
    config_path = write_opencode_config(config_path, provider_block, provider_id=provider_id)
    scope = "global" if project_dir is None else "project"
    click.secho(f"[3/4] Wrote {config_path}  ({scope} config)", fg="green")

    # Step 3b: bridge user-invocable skills into opencode /commands ----------
    # Best-effort: fetch /v1/skills and materialise a command file per skill in
    # the same scope as the provider config. Any failure is logged and must
    # NEVER block the launch/exec path.
    try:
        command_dir = resolve_command_dir(project_dir)
        skills = fetch_skills(base_url, api_key)
        write_command_files(skills, command_dir)
        if skills:
            click.secho(
                f"      Bridged {len(skills)} skill command(s) into {command_dir}",
                fg="green",
            )
        # Surface any same-name collisions the agent resolved for us. Silent when clean.
        render_bridge_conflicts("skills", skills)
    except Exception as exc:  # skills are optional, never fatal
        click.secho(
            f"      WARNING: skills bridge failed ({exc}); continuing without commands.",
            fg="yellow",
        )

    # Step 3c: bridge modes into opencode primary agents ---------------------
    # Best-effort: fetch /v1/modes and materialise an ``amplifier-<mode>``
    # primary-agent file per mode in the same scope as the provider config. Any
    # failure is logged and must NEVER block the launch/exec path.
    try:
        agent_dir = resolve_agent_dir(project_dir)
        modes = fetch_modes(base_url, api_key)
        write_agent_files(modes, agent_dir)
        if modes:
            click.secho(
                f"      Bridged {len(modes)} mode(s) into {agent_dir}",
                fg="green",
            )
        # Surface any same-name collisions the agent resolved for us. Silent when clean.
        render_bridge_conflicts("modes", modes)
    except Exception as exc:  # modes are optional, never fatal
        click.secho(
            f"      WARNING: modes bridge failed ({exc}); continuing without mode agents.",
            fg="yellow",
        )

    # Step 4: exec opencode --------------------------------------------------
    if no_launch:
        click.secho("[4/4] Configuration complete.", fg="green")
        click.echo()
        click.secho(
            "\u2713 opencode is configured to use Amplifier. Pick how you want to drive it:",
            fg="green",
            bold=True,
        )
        click.echo()
        click.echo("    TUI       run `opencode` in any directory")
        click.echo(
            "    Desktop   open the opencode desktop app \u2014 it picks up the global config"
        )
        click.echo('    Headless  opencode run "your prompt here"')
        click.echo()
        click.echo("To jump straight into the TUI next time: amplifier-opencode launch")
        return

    click.secho(f"[4/4] Launching opencode in {launch_dir}", fg="cyan")
    exec_opencode(launch_dir, opencode_args)


# ---------------------------------------------------------------------------
# Doctor implementation
# ---------------------------------------------------------------------------


_OK = "[ OK ]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


def _check_amplifier_agent_binary() -> tuple[str, str]:
    """Check 1: amplifier-agent binary on PATH."""
    binary = shutil.which("amplifier-agent")
    if not binary:
        return (
            _FAIL,
            "amplifier-agent not on PATH. Install via "
            "`uv tool install amplifier-agent` or see "
            "https://github.com/microsoft/amplifier-agent.",
        )
    version = prereqs.binary_version(binary)
    if not prereqs.version_ge(version, prereqs.MIN_AGENT_VERSION):
        return (
            _FAIL,
            f"amplifier-agent at {binary} is {version}; amplifier-opencode "
            f"requires >= {prereqs.MIN_AGENT_VERSION} (the agent resolves provider "
            "credentials at serve startup). Update with `amplifier-agent "
            "update`, or `amplifier-opencode update` to update both.",
        )
    return _OK, f"amplifier-agent found at {binary} ({version}, >= {prereqs.MIN_AGENT_VERSION})"


def _check_opencode_binary() -> tuple[str, str]:
    """Check 2: opencode binary on PATH."""
    binary = shutil.which("opencode")
    if not binary:
        return (
            _FAIL,
            "opencode not on PATH. Install via "
            "`curl -fsSL https://opencode.ai/install | bash` or see "
            "https://opencode.ai/docs/intro for other methods.",
        )
    version = prereqs.binary_version(binary)
    return _OK, f"opencode found at {binary} ({version})"


def _check_amplifier_agent_server(base_url: str, api_key: str) -> tuple[str, str]:
    """Check 3: amplifier-agent server reachability at base_url.

    INFO (not FAIL) when down -- amplifier-opencode auto-starts it.
    """
    if server_is_running(base_url, api_key):
        return _OK, f"amplifier-agent server running at {base_url}"
    return (
        _INFO,
        f"amplifier-agent server NOT running at {base_url} "
        f"(amplifier-opencode will auto-start it on next launch)",
    )


def _provider_section_lines() -> tuple[list[str], bool]:
    """Build per-provider credential status lines for the doctor output.

    Delegates entirely to ``amplifier-agent providers list --json`` (see
    :func:`_fetch_provider_report`) instead of re-probing env vars or
    ``credentials.json`` ourselves -- the agent is what actually decides
    which providers get auto-enabled at ``serve`` startup, so it is the
    only source that can answer this truthfully.

    Returns ``(lines, failed)`` where ``failed`` is True when the report
    could not be obtained at all, or when it was obtained but zero
    providers are resolvable.
    """
    lines: list[str] = ["  Providers (via `amplifier-agent providers list`):"]

    report = _fetch_provider_report()
    if report is None:
        lines.append("    \u2717 Could not run `amplifier-agent providers list --json`")
        lines.append(
            "    \u2192 Install/upgrade amplifier-agent, or run that command manually to see why"
        )
        return lines, True

    rows = report.get("providers", [])
    available: list[str] = []
    unavailable: list[str] = []

    for row in rows:
        name = row.get("name", "?")
        if row.get("resolvable"):
            source = row.get("source", "?")
            lines.append(f"    \u2713 {name} resolvable (source={source}) \u2192 will be served")
            available.append(name)
        else:
            env_var = row.get("env_var") or "?"
            lines.append(
                f"    \u2717 {name} not resolvable \u2192 set {env_var} or run "
                f"`amplifier-agent auth set {name} <key>` to enable it"
            )
            unavailable.append(name)

    lines.append("")

    if available:
        count = len(available)
        label = "providers" if count != 1 else "provider"
        lines.append(
            f"    \u2192 {count} {label} will be auto-enabled on launch ({', '.join(available)})"
        )
        failed = False
    else:
        lines.append(
            "    \u2192 No provider credentials resolvable. Run: "
            "amplifier-agent auth set <provider> <key>"
        )
        lines.append("    \u2192 Or export the provider's env var (e.g. ANTHROPIC_API_KEY)")
        failed = True

    return lines, failed


def _check_opencode_config() -> tuple[str, str]:
    """Check 5: opencode global config has the amplifier provider block."""
    config_path = resolve_global_config_path()
    if not config_path.exists():
        return (
            _INFO,
            f"opencode config at {config_path} not yet generated "
            "(run `amplifier-opencode` to create it).",
        )
    try:
        data = json.loads(config_path.read_text() or "{}")
    except json.JSONDecodeError as exc:
        return _FAIL, f"opencode config at {config_path} is malformed JSON: {exc}"

    amp = (data.get("provider") or {}).get(DEFAULT_PROVIDER_ID)
    if not isinstance(amp, dict):
        return (
            _INFO,
            f"opencode config at {config_path} has no provider.{DEFAULT_PROVIDER_ID} "
            "(run `amplifier-opencode` to populate it).",
        )
    models = amp.get("models") or {}
    return (
        _OK,
        f"opencode config has provider.{DEFAULT_PROVIDER_ID} with "
        f"{len(models)} model{'s' if len(models) != 1 else ''}",
    )


def _check_live_models(base_url: str, api_key: str) -> tuple[str, str]:
    """Check 6: enumerate live models from /v1/models if server is running.

    INFO when server is down (we already reported that in check 3).
    """
    if not server_is_running(base_url, api_key):
        return _INFO, "Skipped (server not running)"
    try:
        models = fetch_models(base_url, api_key)
    except (httpx.HTTPError, click.ClickException) as exc:
        return _FAIL, f"/v1/models failed: {exc}"
    if not models:
        return _WARN, "/v1/models returned 0 models"
    ids = ", ".join(str(m.get("id", "?")) for m in models[:5])
    suffix = "..." if len(models) > 5 else ""
    return _OK, f"Discovered {len(models)} model(s): {ids}{suffix}"


def _run_doctor(base_url: str, api_key: str) -> int:
    """Run all diagnostic checks. Returns exit code: 0=ok, 1=any FAIL."""
    click.echo("amplifier-opencode doctor")
    click.echo()

    checks: list[tuple[str, str, str]] = [
        ("amplifier-agent", *_check_amplifier_agent_binary()),
        ("opencode", *_check_opencode_binary()),
        ("server", *_check_amplifier_agent_server(base_url, api_key)),
        ("opencode config", *_check_opencode_config()),
        ("live models", *_check_live_models(base_url, api_key)),
    ]

    fail_count = 0
    for name, status, message in checks:
        color = {
            _OK: "green",
            _FAIL: "red",
            _INFO: "cyan",
            _WARN: "yellow",
        }.get(status, "white")
        click.secho(f"  {status}  {name:<18}  {message}", fg=color)
        if status == _FAIL:
            fail_count += 1

    # Provider section -- separate multi-line block showing per-provider status.
    click.echo()
    provider_lines, provider_failed = _provider_section_lines()
    for line in provider_lines:
        click.secho(line, fg="red" if provider_failed else None)
    if provider_failed:
        fail_count += 1

    click.echo()
    if fail_count:
        click.secho(
            f"{fail_count} check(s) failed. Fix the FAIL items above before "
            "running `amplifier-opencode`.",
            fg="red",
        )
        return 1
    click.secho("All required checks passed.", fg="green")
    return 0


# ---------------------------------------------------------------------------
# Self-update implementation
# ---------------------------------------------------------------------------


def _update_self(*, ref: str, force: bool) -> None:
    """Reinstall amplifier-app-opencode from a git ref via ``uv tool install --force``.

    Prints the current install metadata, then shells out to ``uv tool
    install --force git+{REPO_URL}@{ref}``. Refuses to clobber editable
    installs unless ``force=True`` -- a developer working on a local
    checkout almost certainly does not want their dev tree silently
    replaced with a release build.
    """
    info = prereqs.get_self_install_info()
    click.secho(
        f"Current install: {PACKAGE_NAME} {info['version']} (via {info['source']})",
        fg="cyan",
    )
    if info["source"] == "git" and info.get("commit"):
        click.secho(f"  commit: {info['commit'][:12]}", fg="cyan")

    if info["source"] == "editable" and not force:
        raise click.ClickException(
            "Refusing to overwrite an editable install. "
            "Pass --force to clobber it with the latest "
            f"{ref}, or update your dev checkout manually with `git pull`."
        )

    uv = shutil.which("uv")
    if not uv:
        raise click.ClickException(
            "uv binary not found in PATH. Install uv (https://docs.astral.sh/uv/) and re-run."
        )

    spec = f"git+{REPO_URL}@{ref}"
    click.secho(f"Installing {spec} ...", fg="cyan")
    # We deliberately stream uv's own output to the user's terminal rather
    # than capturing it -- uv prints rich progress the user expects to see,
    # and any failure mode is easier to debug when uv's error stays visible.
    result = subprocess.run([uv, "tool", "install", "--force", spec])
    if result.returncode != 0:
        raise click.ClickException(
            f"`uv tool install --force {spec}` exited with code "
            f"{result.returncode}. See uv's output above for details."
        )
    click.secho(f"\u2713 amplifier-opencode updated from {ref}.", fg="green", bold=True)


def _run_update(*, ref: str, force: bool, include_opencode: bool, assume_yes: bool) -> int:
    """Update the whole stack: amplifier-opencode, amplifier-agent, and opencode.

    The user installs and updates ONE thing; this keeps all three in lockstep.
    amplifier-opencode and amplifier-agent are always updated (a stale agent
    breaks the adapter). opencode is updated too, but the user can opt out --
    via ``--no-opencode`` or by answering "no" to the interactive prompt --
    since some users pin opencode deliberately.
    """
    # 1. amplifier-opencode (self) -- always.
    click.secho("== Updating amplifier-opencode ==", fg="cyan", bold=True)
    _update_self(ref=ref, force=force)

    # 2. amplifier-agent -- always (force-heals if below the required floor).
    click.echo()
    click.secho("== Updating amplifier-agent ==", fg="cyan", bold=True)
    if not prereqs.update_agent_to_latest():
        click.secho(
            "! amplifier-agent could not be brought up to date; see output above.",
            fg="yellow",
        )

    # 3. opencode -- opt-out. Respect --no-opencode, else ask when interactive.
    click.echo()
    do_opencode = include_opencode
    if include_opencode and not assume_yes and plat.is_interactive():
        do_opencode = click.confirm("Update opencode as well?", default=True)
    if do_opencode:
        click.secho("== Updating opencode ==", fg="cyan", bold=True)
        if not prereqs.update_opencode():
            click.secho("! opencode could not be updated; see output above.", fg="yellow")
    else:
        click.secho("== Skipping opencode update (left at its current version) ==", fg="cyan")

    click.echo()
    click.secho("\u2713 Update complete.", fg="green", bold=True)
    click.echo("  Run `amplifier-opencode doctor` to verify, or launch as usual.")
    return 0


# ---------------------------------------------------------------------------
# Click surface (subcommand-shaped, default = launch)
# ---------------------------------------------------------------------------


def _print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager ``--version`` callback: report amplifier-opencode's own version
    plus the running amplifier-agent version and the minimum this build
    requires, then exit before any bootstrap/self-heal runs.
    """
    if not value or ctx.resilient_parsing:
        return
    from . import __version__

    click.echo(f"amplifier-opencode {__version__}")

    agent = prereqs.detect_agent()
    minimum = f"minimum required {prereqs.MIN_AGENT_VERSION}"
    if agent.present:
        semver = prereqs.extract_semver(agent.version or "")
        shown = ".".join(str(n) for n in semver) if semver else (agent.version or "unknown")
        state = "installed" if agent.meets_min else "installed, below minimum"
        click.echo(f"amplifier-agent    {shown} ({state}; {minimum})")
    else:
        click.echo(f"amplifier-agent    not installed ({minimum})")
    ctx.exit()


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show amplifier-opencode + amplifier-agent versions (and the required minimum) and exit.",
)
@click.option(
    "--base-url",
    default=DEFAULT_BASE_URL,
    envvar="AMPLIFIER_AGENT_BASE_URL",
    show_default=True,
    help="amplifier-agent base URL (probe + opencode provider config).",
)
@click.option(
    "--api-key",
    default=DEFAULT_API_KEY,
    envvar="AMPLIFIER_AGENT_API_KEY",
    show_default=True,
    help="API key the server expects (Authorization: Bearer ...).",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Assume yes to install/onboarding prompts (non-interactive bootstrap).",
)
@click.option(
    "--no-bootstrap",
    is_flag=True,
    help=(
        "Skip the self-healing preflight (do not install/update prerequisites "
        "or run credential onboarding). Assume the environment is ready."
    ),
)
@click.pass_context
def main(
    ctx: click.Context,
    base_url: str,
    api_key: str,
    assume_yes: bool,
    no_bootstrap: bool,
) -> None:
    """Bridge amplifier-agent and opencode: set up the server + config,
    then tell you how to drive opencode.

    Default action (when no subcommand is given): check the server, start
    it if needed, discover models from ``/v1/models``, write
    ``opencode.json``, and report success. Does NOT exec opencode --
    opencode has a TUI, a Desktop app, and a headless mode; pick which
    one to drive after the bridge is ready.

    \b
    Subcommands:

      prepare  Same as default; explicit form for clarity in scripts.
      launch   Same setup + exec the opencode TUI in one step.
               Accepts pass-through args after ``--``.
      doctor   Health checks for all prerequisites (binaries, server,
               credentials, config). No side effects.
      update   Reinstall amplifier-opencode from the latest main of
               microsoft/amplifier-app-opencode via uv.

    \b
    Examples:

      amplifier-opencode                 # set up the bridge, then run `opencode`
      amplifier-opencode launch          # set up + jump straight into the TUI
      amplifier-opencode doctor          # what's wrong?
      amplifier-opencode update
      amplifier-opencode --base-url http://localhost:9099/v1
      amplifier-opencode launch -- run "hello"
    """
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key
    ctx.obj["assume_yes"] = assume_yes
    ctx.obj["bootstrap"] = not no_bootstrap

    if ctx.invoked_subcommand is None:
        # Default action: prepare the bridge without exec'ing opencode.
        # Users explicitly pick how to drive opencode (TUI / Desktop /
        # headless) after this completes.
        ctx.invoke(prepare)


@main.command("launch")
@click.option(
    "--workspace",
    default=DEFAULT_WORKSPACE,
    envvar="AMPLIFIER_AGENT_WORKSPACE",
    show_default=True,
    help="amplifier-agent workspace name. Only used when starting the server.",
)
@click.option(
    "--host-config",
    "host_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    envvar="AMPLIFIER_AGENT_HOST_CONFIG",
    help=(
        "Path to a host_config.json to pass to `amplifier-agent serve` via "
        "--config. When omitted, no --config is passed and amplifier-agent "
        "auto-enables every provider whose credentials resolve. "
        "Only used when starting the server."
    ),
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path),
    default=None,
    show_default="(use global config)",
    help=(
        "Write opencode.json into THIS directory instead of the global "
        "opencode config. The global path "
        "(~/.config/opencode/opencode.jsonc) is the default so the adapter "
        "is available from every directory."
    ),
)
@click.option(
    "--no-start",
    is_flag=True,
    help="Do NOT auto-start amplifier-agent. Fail loudly if /v1/models is unreachable.",
)
@click.option(
    "--no-launch",
    is_flag=True,
    help="Discover models and write opencode.json, but don't actually exec opencode.",
)
@click.option(
    "--amplifier-agent-bin",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    envvar="AMPLIFIER_AGENT_BIN",
    help="Override the amplifier-agent binary to use when starting the server.",
)
@click.option(
    "--provider-id",
    default=DEFAULT_PROVIDER_ID,
    show_default=True,
    help="Provider ID under opencode.json's provider.<id> key.",
)
@click.option(
    "--max-context",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_CONTEXT,
    envvar="AMPLIFIER_OPENCODE_MAX_CONTEXT",
    show_default="(forward backend value)",
    help=(
        "Clamp every model's advertised context window to at most this many "
        "tokens. Use to guard against a backend advertising a window larger "
        "than the provider will honor (e.g. a Claude 1M window without the "
        "1M-context beta entitlement), which makes opencode skip compaction "
        "and overflow the real cap. Default: forward the backend value."
    ),
)
@click.argument("opencode_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def launch(
    ctx: click.Context,
    workspace: str,
    host_config: Path | None,
    project_dir: Path | None,
    no_start: bool,
    no_launch: bool,
    amplifier_agent_bin: Path | None,
    provider_id: str,
    max_context: int | None,
    opencode_args: tuple[str, ...],
) -> None:
    """Discover models, write opencode.json, exec opencode.

    Any args after ``--`` are passed through to opencode unchanged.
    """
    _run_launch(
        base_url=ctx.obj["base_url"],
        api_key=ctx.obj["api_key"],
        workspace=workspace,
        host_config=host_config,
        project_dir=project_dir,
        no_start=no_start,
        no_launch=no_launch,
        amplifier_agent_bin=amplifier_agent_bin,
        provider_id=provider_id,
        max_context=max_context,
        opencode_args=opencode_args,
        bootstrap=ctx.obj.get("bootstrap", True),
        assume_yes=ctx.obj.get("assume_yes", False),
    )


@main.command("prepare")
@click.option(
    "--workspace",
    default=DEFAULT_WORKSPACE,
    envvar="AMPLIFIER_AGENT_WORKSPACE",
    show_default=True,
    help="amplifier-agent workspace name. Only used when starting the server.",
)
@click.option(
    "--host-config",
    "host_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    envvar="AMPLIFIER_AGENT_HOST_CONFIG",
    help=(
        "Path to a host_config.json to pass to `amplifier-agent serve` via "
        "--config. When omitted, no --config is passed and amplifier-agent "
        "auto-enables every provider whose credentials resolve. "
        "Only used when starting the server."
    ),
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path),
    default=None,
    show_default="(use global config)",
    help=(
        "Write opencode.json into THIS directory instead of the global "
        "opencode config. The global path "
        "(~/.config/opencode/opencode.jsonc) is the default so the adapter "
        "is available from every directory."
    ),
)
@click.option(
    "--no-start",
    is_flag=True,
    help="Do NOT auto-start amplifier-agent. Fail loudly if /v1/models is unreachable.",
)
@click.option(
    "--amplifier-agent-bin",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    envvar="AMPLIFIER_AGENT_BIN",
    help="Override the amplifier-agent binary to use when starting the server.",
)
@click.option(
    "--provider-id",
    default=DEFAULT_PROVIDER_ID,
    show_default=True,
    help="Provider ID under opencode.json's provider.<id> key.",
)
@click.option(
    "--max-context",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_CONTEXT,
    envvar="AMPLIFIER_OPENCODE_MAX_CONTEXT",
    show_default="(forward backend value)",
    help=(
        "Clamp every model's advertised context window to at most this many "
        "tokens. Use to guard against a backend advertising a window larger "
        "than the provider will honor (e.g. a Claude 1M window without the "
        "1M-context beta entitlement), which makes opencode skip compaction "
        "and overflow the real cap. Default: forward the backend value."
    ),
)
@click.pass_context
def prepare(
    ctx: click.Context,
    workspace: str,
    host_config: Path | None,
    project_dir: Path | None,
    no_start: bool,
    amplifier_agent_bin: Path | None,
    provider_id: str,
    max_context: int | None,
) -> None:
    """Set up the bridge: start the server, discover models, write opencode.json.

    Does NOT exec opencode -- opencode has a TUI, a Desktop app, and a
    headless mode; pick which one to drive after the bridge is ready.

    Use ``amplifier-opencode launch`` if you want to set up AND jump into
    the opencode TUI in one step.
    """
    _run_launch(
        base_url=ctx.obj["base_url"],
        api_key=ctx.obj["api_key"],
        workspace=workspace,
        host_config=host_config,
        project_dir=project_dir,
        no_start=no_start,
        no_launch=True,
        amplifier_agent_bin=amplifier_agent_bin,
        provider_id=provider_id,
        max_context=max_context,
        opencode_args=(),
        bootstrap=ctx.obj.get("bootstrap", True),
        assume_yes=ctx.obj.get("assume_yes", False),
    )


@main.command("setup")
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Install/repair prerequisites and walk through provider credentials.

    Runs the same self-healing preflight as a normal launch, but stops before
    starting the server or opencode. Use it to get a fresh machine ready in one
    step: it installs amplifier-agent and opencode if missing, force-heals them
    if they are too old, and -- if no provider credentials are configured --
    walks you through setting one up. Respects the global ``--yes`` flag.
    """
    assume_yes = ctx.obj.get("assume_yes", False)
    ok = prereqs.ensure_prerequisites(assume_yes=assume_yes, allow_install=True)
    if not ok:
        click.secho(
            "\nSome prerequisites are not ready. Re-run with --yes to auto-install, "
            "or address the items above.",
            fg="red",
        )
        sys.exit(1)

    # Credential onboarding (only when the agent reports zero resolvable providers).
    if onboarding.needs_onboarding():
        onboarding.run_onboarding(assume_yes=assume_yes)

    click.echo()
    click.secho(
        "\u2713 Setup complete. Launch any time with: amplifier-opencode", fg="green", bold=True
    )


@main.command("doctor")
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Run health checks for amplifier-agent, opencode, credentials, config.

    Reports on every prerequisite the default ``amplifier-opencode``
    invocation depends on, plus a live model probe if the server is up.
    Exits 0 when all required checks pass; exits 1 on any FAIL.
    """
    sys.exit(_run_doctor(ctx.obj["base_url"], ctx.obj["api_key"]))


@main.command("update")
@click.option(
    "--ref",
    default="main",
    show_default=True,
    help="Git ref (branch, tag, or commit) of amplifier-app-opencode to install.",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Overwrite an editable (-e) install. Without this flag, editable "
        "installs are preserved so a dev checkout is not silently replaced."
    ),
)
@click.option(
    "--no-opencode",
    is_flag=True,
    help="Update amplifier-opencode and amplifier-agent, but leave opencode untouched.",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Don't prompt; assume yes (updates opencode too unless --no-opencode).",
)
def update(ref: str, force: bool, no_opencode: bool, assume_yes: bool) -> None:
    """Update the whole stack: amplifier-opencode, amplifier-agent, and opencode.

    You installed one thing; you update one thing. This reinstalls the
    ``amplifier-opencode`` binary from ``git+<repo>@<ref>`` (default ``main``),
    then brings amplifier-agent up to date (force-healing it if it is below the
    required minimum), then updates opencode.

    opencode is the one component you can opt out of -- pass ``--no-opencode``
    (or answer "no" at the prompt) to keep your pinned opencode version while
    still updating the amplifier pieces.

    \b
    Examples:

      amplifier-opencode update                    # update all three
      amplifier-opencode update --no-opencode      # keep opencode pinned
      amplifier-opencode update --ref v0.2.0        # pin adapter to a tag
      amplifier-opencode update --force             # clobber editable install
    """
    sys.exit(
        _run_update(
            ref=ref,
            force=force,
            include_opencode=not no_opencode,
            assume_yes=assume_yes,
        )
    )


# Allow ``python -m amplifier_app_opencode`` --------------------------------

if __name__ == "__main__":
    main()
