"""DTU-backed tests: the generated opencode config matches `docs/spec/opencode-config.md`.

Ground-truth assertions only -- no TUI, no AI judge. All six tests read the SAME
`amplifier-opencode prepare` run's generated `<project-dir>/opencode.json`:

* it lands at the fixed project-scope path (no candidate search, unlike global scope),
* `provider.amplifier` has the exact shape the spec promises,
* the model set matches what the live `/v1/models` endpoint actually reports (not a
  hardcoded count or id list -- an empty live listing is a real defect, not something
  to skip past),
* every model entry follows the all-or-nothing `limit`/`cost` rules, and
* unrelated top-level keys and sibling provider entries survive a second `prepare` run
  untouched -- the preservation guarantee that makes re-running `prepare` safe.
"""

from __future__ import annotations

import json
import shlex

import pytest
from conftest import PROJECT_DIR
from framework import dtu
from framework.driver import TmuxTuiDriver
from suites.config.conftest import CONFIG_PATH, PreparedConfig

# This suite restarts the agent server (model discovery is fixed at server startup), so
# it must not run concurrently with another suite depending on server state -- same
# constraint as suites/shadowing and suites/traversal.
pytestmark = pytest.mark.dtu

_EXPECTED_SCHEMA = "https://opencode.ai/config.json"
_LIVE_MODELS_URL = "http://127.0.0.1:9099/v1/models"
_AUTH_HEADER = "Authorization: Bearer local-dev-secret"


def _read_config(driver: TmuxTuiDriver) -> dict:
    """Read and JSON-parse the generated config from the DTU, failing loud on either."""
    proc = driver.run_command(["bash", "-lc", f"cat {shlex.quote(CONFIG_PATH)}"])
    assert proc.returncode == 0, (
        f"could not read {CONFIG_PATH} in the DTU (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{CONFIG_PATH} is not valid JSON: {exc}\ncontent:\n{proc.stdout}")


def _fetch_live_model_ids(dtu_id: str) -> set[str]:
    """Return the set of model ids the live `/v1/models` endpoint reports."""
    result = dtu.exec_json(
        dtu_id,
        [
            "bash",
            "-lc",
            f"curl -s --max-time 5 -H {shlex.quote(_AUTH_HEADER)} {shlex.quote(_LIVE_MODELS_URL)}",
        ],
    )
    raw_stdout = str(result.get("stdout", ""))
    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"GET {_LIVE_MODELS_URL} did not return JSON: {exc}\nresponse:\n{raw_stdout}")
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return {entry["id"] for entry in data if isinstance(entry, dict) and entry.get("id")}


def test_prepare_succeeds(prepared_config: PreparedConfig) -> None:
    """`prepare` must complete successfully before anything downstream can be trusted."""
    assert prepared_config.returncode == 0, f"prepare failed:\n{prepared_config.stdout}"


def test_project_scope_writes_config_at_project_root(prepared_config: PreparedConfig) -> None:
    """Project scope has no candidate search: the config always lands at the fixed project path."""
    driver = prepared_config.driver
    exists = driver.run_command(["bash", "-lc", f"test -f {shlex.quote(CONFIG_PATH)}"])
    assert exists.returncode == 0, (
        f"expected {CONFIG_PATH} to exist after prepare; prepare output:\n{prepared_config.stdout}"
    )
    config = _read_config(driver)
    assert "provider" in config, f"expected a top-level 'provider' key, got keys: {sorted(config)}"


def test_provider_block_has_required_shape(prepared_config: PreparedConfig) -> None:
    """`provider.amplifier` carries the exact literal shape `opencode-config.md` promises."""
    config = _read_config(prepared_config.driver)
    assert config.get("$schema") == _EXPECTED_SCHEMA, (
        f"expected '$schema' == {_EXPECTED_SCHEMA!r}, got {config.get('$schema')!r}\n"
        f"config:\n{config}"
    )
    provider = config.get("provider", {})
    assert "amplifier" in provider, (
        f"expected 'amplifier' under provider, got keys: {sorted(provider)}"
    )

    amplifier_block = provider["amplifier"]
    missing_keys = [
        key for key in ("npm", "name", "options", "models") if key not in amplifier_block
    ]
    assert not missing_keys, f"provider.amplifier missing key(s) {missing_keys}: {amplifier_block}"

    options = amplifier_block["options"]
    missing_options = [key for key in ("baseURL", "apiKey") if key not in options]
    assert not missing_options, (
        f"provider.amplifier.options missing key(s) {missing_options}: {options}"
    )


def test_models_block_is_non_empty_and_matches_live_models(prepared_config: PreparedConfig) -> None:
    """The written `models` block must exactly match the live `/v1/models` id set.

    An empty live listing fails loud here rather than skipping: an empty model picker
    in opencode is a real, user-visible defect, not a condition to tolerate silently.
    """
    config = _read_config(prepared_config.driver)
    models = config["provider"]["amplifier"]["models"]
    assert models, f"expected a non-empty provider.amplifier.models block, got: {models}"

    live_ids = _fetch_live_model_ids(prepared_config.dtu_id)
    assert live_ids, (
        "GET /v1/models returned zero live model ids; an empty picker is a real defect, "
        f"not a condition to skip past. config models were: {sorted(models)}"
    )
    assert set(models.keys()) == live_ids, (
        f"config models {sorted(models.keys())} do not match live model ids {sorted(live_ids)}"
    )


def test_every_model_entry_has_a_name(prepared_config: PreparedConfig) -> None:
    """Every model entry has a name, and `limit`/`cost` are each all-or-nothing.

    `limit` must carry BOTH `context` and `output` as ints, never just one. `cost` must
    carry both `input` and `output` when present. `opencode-config.md` is explicit that a
    field that cannot be emitted correctly is omitted entirely, never partially emitted.
    """
    config = _read_config(prepared_config.driver)
    models = config["provider"]["amplifier"]["models"]
    assert models, "expected a non-empty models block to check entry shape"

    for model_id, entry in models.items():
        name = entry.get("name")
        assert isinstance(name, str) and name, (
            f"model {model_id!r} missing a non-empty 'name': {entry!r}"
        )

        if "limit" in entry:
            limit = entry["limit"]
            missing = [key for key in ("context", "output") if key not in limit]
            assert not missing, (
                f"model {model_id!r} 'limit' must carry BOTH context and output, missing "
                f"{missing}: {limit!r}"
            )
            assert isinstance(limit["context"], int) and isinstance(limit["output"], int), (
                f"model {model_id!r} 'limit' context/output must both be ints, got {limit!r}"
            )

        if "cost" in entry:
            cost = entry["cost"]
            missing = [key for key in ("input", "output") if key not in cost]
            assert not missing, (
                f"model {model_id!r} 'cost' must carry both input and output, missing "
                f"{missing}: {cost!r}"
            )


def test_unrelated_config_keys_survive_a_second_prepare(prepared_config: PreparedConfig) -> None:
    """Unrelated top-level keys and sibling provider entries survive a second `prepare` run.

    `opencode-config.md`'s preservation guarantee: every top-level key already present
    survives untouched, and every sibling under `provider.*` other than the target
    provider id is left completely alone. This is what makes re-running `prepare` safe
    against a config a user has hand-edited.
    """
    driver = prepared_config.driver

    sentinel_script = (
        "python3 - <<'PY'\n"
        "import json\n"
        f"path = {CONFIG_PATH!r}\n"
        "with open(path, encoding='utf-8') as f:\n"
        "    cfg = json.load(f)\n"
        "cfg['e2eSentinel'] = {'keep': True}\n"
        "cfg.setdefault('provider', {})['e2e-other'] = {\n"
        "    'npm': 'noop', 'name': 'E2E Other', 'options': {}, 'models': {}\n"
        "}\n"
        "with open(path, 'w', encoding='utf-8') as f:\n"
        "    json.dump(cfg, f)\n"
        "PY\n"
    )
    write_proc = driver.run_command(["bash", "-lc", sentinel_script])
    assert write_proc.returncode == 0, (
        f"failed to write sentinel keys into {CONFIG_PATH}:\n"
        f"stdout:\n{write_proc.stdout}\nstderr:\n{write_proc.stderr}"
    )

    # Rebind through an f-string so the static type is a plain str: the dynamic
    # ``from conftest import`` confuses the type checker into treating the name as the
    # conftest module (same workaround the shadowing/traversal suites use).
    project_dir = f"{PROJECT_DIR}"
    rerun = driver.run_command(
        [
            "bash",
            "-lc",
            f"cd {shlex.quote(project_dir)} && "
            f"amplifier-opencode --yes prepare --project-dir {shlex.quote(project_dir)} 2>&1",
        ]
    )
    assert rerun.returncode == 0, f"second prepare run failed:\n{rerun.stdout}"

    config = _read_config(driver)
    assert config.get("e2eSentinel") == {"keep": True}, (
        f"expected sentinel top-level key 'e2eSentinel' to survive a second prepare run: {config}"
    )
    provider = config.get("provider", {})
    assert "e2e-other" in provider, (
        f"expected sibling provider 'e2e-other' to survive a second prepare run: {sorted(provider)}"
    )
    amplifier_models = provider.get("amplifier", {}).get("models", {})
    assert amplifier_models, (
        "expected provider.amplifier.models to remain non-empty after the second prepare run: "
        f"{provider.get('amplifier')}"
    )
