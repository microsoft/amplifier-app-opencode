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

  amplifier-opencode             default: discover + write + launch
  amplifier-opencode launch      explicit launch (same as default)
  amplifier-opencode doctor      health checks for all prerequisites
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click
import httpx

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:9099/v1"
DEFAULT_API_KEY = "local-dev-secret"
DEFAULT_WORKSPACE = "opencode"
DEFAULT_SERVER_READY_TIMEOUT_S = 30.0
DEFAULT_SERVER_PROBE_TIMEOUT_S = 2.0
DEFAULT_PROVIDER_ID = "amplifier"
DEFAULT_PROVIDER_NAME = "Amplifier"
DEFAULT_PROVIDER_NPM = "@ai-sdk/openai-compatible"

SERVER_LOG_PATH = Path("/tmp/amplifier-agent.log")
PID_FILE = Path("/tmp/amplifier-opencode-agent.pid")

# Global opencode config dir (XDG-aligned).
GLOBAL_OPENCODE_DIR = Path.home() / ".config" / "opencode"

# Provider credential env var mapping -- mirrors amplifier-agent's catalog,
# kept here so ``doctor`` and ``build_host_config`` can share the same
# single source of truth without importing amplifier-agent code.
KNOWN_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure-openai": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY"),
    "ollama": ("OLLAMA_HOST",),
}

# Flat tuple of every env var name across all providers (for error messages).
_ALL_PROVIDER_ENV_VARS: tuple[str, ...] = tuple(
    v for vs in KNOWN_PROVIDER_ENV_VARS.values() for v in vs
)


# ---------------------------------------------------------------------------
# host_config.json auto-generation
# ---------------------------------------------------------------------------


def build_host_config(state_dir: Path) -> Path:
    """Generate a host_config.json declaring every provider whose creds are in env.

    Probes :data:`KNOWN_PROVIDER_ENV_VARS` and includes each provider whose env
    var is set. Writes ``host_config.json`` into ``state_dir`` (creating the
    directory at mode 0700 if needed) with mode 0600. Returns the path.

    Raises :class:`click.ClickException` if no providers have credentials; the
    message lists every env var the user could set.

    The generated config is intentionally minimal (``{"providers": {<id>: {}}}``)
    -- power users who want ``mcp``, ``approval``, ``skills``, or
    per-provider config overrides should write their own host_config.json
    and pass ``--host-config PATH``.
    """
    providers = {
        pid: {}
        for pid, env_vars in KNOWN_PROVIDER_ENV_VARS.items()
        if any(os.environ.get(v) for v in env_vars)
    }

    if not providers:
        raise click.ClickException(
            "No provider credentials found. Set at least one of: "
            + ", ".join(_ALL_PROVIDER_ENV_VARS)
            + "\n\n"
            + "Or write your own host_config.json and pass --host-config PATH."
        )

    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)

    config_path = state_dir / "host_config.json"
    config_path.write_text(json.dumps({"providers": providers}, indent=2), encoding="utf-8")
    config_path.chmod(0o600)

    return config_path


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
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
    "claude-sonnet-4-6":         {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-opus-4-8":           {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    # ===== OpenAI =====
    # GPT-4o
    "gpt-4o":                    {"input": 2.5,  "output": 10.0,  "cache_read": 1.25},
    "gpt-4o-mini":               {"input": 0.15, "output": 0.6,   "cache_read": 0.075},
    # GPT-4.1
    "gpt-4.1":                   {"input": 2.0,  "output": 8.0,   "cache_read": 0.5},
    "gpt-4.1-mini":              {"input": 0.4,  "output": 1.6,   "cache_read": 0.1},
    "gpt-4.1-nano":              {"input": 0.1,  "output": 0.4,   "cache_read": 0.025},
    # GPT-5
    "gpt-5":                     {"input": 1.25, "output": 10.0,  "cache_read": 0.125},
    "gpt-5-codex":               {"input": 1.25, "output": 10.0,  "cache_read": 0.125},
    "gpt-5-mini":                {"input": 0.25, "output": 2.0,   "cache_read": 0.025},
    "gpt-5-nano":                {"input": 0.05, "output": 0.4,   "cache_read": 0.005},
    "gpt-5-pro":                 {"input": 15.0, "output": 120.0},
    # GPT-5.1
    "gpt-5.1":                   {"input": 1.25, "output": 10.0,  "cache_read": 0.125},
    "gpt-5.1-codex":             {"input": 1.25, "output": 10.0,  "cache_read": 0.125},
    "gpt-5.1-codex-max":         {"input": 1.25, "output": 10.0,  "cache_read": 0.125},
    "gpt-5.1-codex-mini":        {"input": 0.25, "output": 2.0,   "cache_read": 0.025},
    # GPT-5.2
    "gpt-5.2":                   {"input": 1.75, "output": 14.0,  "cache_read": 0.175},
    "gpt-5.2-codex":             {"input": 1.75, "output": 14.0,  "cache_read": 0.175},
    # o-series (reasoning models)
    "o1":                        {"input": 15.0, "output": 60.0,  "cache_read": 7.5},
    "o1-pro":                    {"input": 150.0,"output": 600.0},
    "o3":                        {"input": 2.0,  "output": 8.0,   "cache_read": 0.5},
    "o3-mini":                   {"input": 1.1,  "output": 4.4,   "cache_read": 0.55},
    "o3-pro":                    {"input": 20.0, "output": 80.0},
    "o4-mini":                   {"input": 1.1,  "output": 4.4,   "cache_read": 0.275},
    # Embeddings (output is always 0; opencode schema requires both fields)
    "text-embedding-3-large":    {"input": 0.13, "output": 0.0},
    "text-embedding-3-small":    {"input": 0.02, "output": 0.0},
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
) -> subprocess.Popen[bytes]:
    """Spawn ``amplifier-agent serve chat-completions`` in the background.

    Output is appended to /tmp/amplifier-agent.log. PID is written to
    /tmp/amplifier-opencode-agent.pid so subsequent ``amplifier-opencode``
    invocations can detect (and optionally clean up) the same instance.
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
    )
    PID_FILE.write_text(f"{proc.pid}\n")
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
    data = r.json().get("data", [])
    if not isinstance(data, list):
        raise click.ClickException(
            f"/v1/models returned malformed data: {type(data).__name__}, expected list"
        )
    return data


def build_provider_block(
    *,
    base_url: str,
    api_key: str,
    models: list[dict[str, Any]],
    provider_name: str = DEFAULT_PROVIDER_NAME,
    provider_npm: str = DEFAULT_PROVIDER_NPM,
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
                  ``max_output_tokens``).

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
            entry["limit"] = {
                "context": int(limit["context"]),
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
            existing = json.loads(text)
        except json.JSONDecodeError as exc:
            raise click.ClickException(
                f"Existing {config_path} is not valid JSON: {exc}. "
                "Remove or fix the file, then retry."
            ) from exc

    existing.setdefault("$schema", "https://opencode.ai/config.json")
    existing.setdefault("provider", {})
    if not isinstance(existing["provider"], dict):
        raise click.ClickException(
            f"Existing {config_path}.provider is not a dict; refusing to overwrite."
        )
    existing["provider"][provider_id] = provider

    config_path.write_text(json.dumps(existing, indent=2, sort_keys=False) + "\n")
    return config_path


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
) -> None:
    """The check -> start -> discover -> write -> exec flow."""
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
        # Resolve the host_config to pass to amplifier-agent. When the user
        # did not supply --host-config, auto-generate one from env vars so
        # amplifier-agent >= 0.8.0 (which requires an explicit providers block
        # at boot) starts successfully.
        if host_config is not None:
            resolved_config = host_config
        else:
            _state_dir = Path.home() / ".amplifier-opencode" / "state"
            resolved_config = build_host_config(_state_dir)

        start_amplifier_agent(
            port=port,
            workspace=workspace,
            host_config=resolved_config,
            api_key=api_key,
            binary=str(amplifier_agent_bin) if amplifier_agent_bin else None,
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
        provider_tag = m.get("_provider", "?")
        click.secho(
            f"      - {provider_tag:10s}  {m.get('id', '?'):35s}  {m.get('display_name', '')}",
            fg="white",
        )

    # Step 3: write the opencode config -------------------------------------
    provider_block = build_provider_block(
        base_url=base_url,
        api_key=api_key,
        models=models,
    )
    config_path = write_opencode_config(config_path, provider_block, provider_id=provider_id)
    scope = "global" if project_dir is None else "project"
    click.secho(f"[3/4] Wrote {config_path}  ({scope} config)", fg="green")

    # Step 4: exec opencode --------------------------------------------------
    if no_launch:
        click.secho(
            f"[4/4] --no-launch given; not exec'ing opencode. "
            f"cd {launch_dir} && opencode  to enter the TUI.",
            fg="yellow",
        )
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


def _binary_version(binary: str, version_flag: str = "--version") -> str:
    """Best-effort version string for a binary. Returns "?" on failure."""
    try:
        r = subprocess.run(
            [binary, version_flag],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "?"


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
    version = _binary_version(binary)
    return _OK, f"amplifier-agent found at {binary} ({version})"


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
    version = _binary_version(binary)
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
    """Build per-provider env-var status lines for the doctor output.

    Returns ``(lines, failed)`` where ``failed`` is True when zero providers
    have credentials (i.e. ``launch`` would abort with a ClickException).

    The returned lines include the "Providers:" header, one line per provider
    with a ✓/✗ indicator, a blank line, and a summary line.
    """
    lines: list[str] = []
    lines.append("  Providers:")

    available: list[str] = []
    unavailable_vars: list[str] = []  # primary env var name for each missing provider

    for provider_id, env_vars in KNOWN_PROVIDER_ENV_VARS.items():
        found_var = next((v for v in env_vars if os.environ.get(v)), None)
        if found_var:
            lines.append(f"    \u2713 {found_var} set \u2192 {provider_id} provider will be served")
            available.append(provider_id)
        else:
            primary_var = env_vars[0]
            lines.append(
                f"    \u2717 {primary_var} not set \u2192 {provider_id} provider will NOT be served"
            )
            unavailable_vars.append(primary_var)

    lines.append("")

    if available:
        count = len(available)
        label = "providers" if count != 1 else "provider"
        lines.append(
            f"    \u2192 {count} {label} will be available on launch ({', '.join(available)})"
        )
        if unavailable_vars:
            lines.append(
                "    \u2192 Set "
                + " and/or ".join(unavailable_vars)
                + " to enable additional providers"
            )
        failed = False
    else:
        lines.append(
            "    \u2192 No provider credentials found. Set at least one of: "
            + ", ".join(_ALL_PROVIDER_ENV_VARS)
        )
        lines.append("    \u2192 Or write your own host_config.json and pass --host-config PATH.")
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
    ids = ", ".join(m.get("id", "?") for m in models[:5])
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
# Click surface (subcommand-shaped, default = launch)
# ---------------------------------------------------------------------------


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
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
@click.pass_context
def main(ctx: click.Context, base_url: str, api_key: str) -> None:
    """Launch opencode with auto-discovered amplifier-agent models.

    Default action (when no subcommand is given): check the server, start
    it if needed, discover models from ``/v1/models``, write
    ``opencode.json``, then ``exec`` opencode.

    \b
    Subcommands:

      launch   Same as default; explicit form for clarity in scripts.
               Accepts pass-through args after ``--``.
      doctor   Health checks for all prerequisites (binaries, server,
               credentials, config). No side effects.

    \b
    Examples:

      amplifier-opencode
      amplifier-opencode doctor
      amplifier-opencode --base-url http://localhost:9099/v1
      amplifier-opencode launch --no-launch
      amplifier-opencode launch -- run "hello"
    """
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key

    if ctx.invoked_subcommand is None:
        # Default action: invoke the launch subcommand with defaults.
        ctx.invoke(launch)


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
        "Path to a host_config.json to use verbatim. "
        "Overrides the auto-generated config built from your env vars. "
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
        opencode_args=opencode_args,
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


# Allow ``python -m amplifier_app_opencode`` --------------------------------

if __name__ == "__main__":
    main()
