"""amplifier-opencode: install the Amplifier plugin into opencode and run its HTTP face."""

import argparse
from importlib import metadata, resources
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

from amplifier_app_opencode import cleanup

PLUGIN_NAME = "amplifier.js"
OPTIONS_NAME = "amplifier.json"
COMMAND = "amplifier-opencode"
DEFAULT_OPTIONS: dict[str, Any] = {
    "defaultModel": None,
    "setDefaultAgent": True,
    "approvals": "opencode",
    "environment": {},
}
# The opencode integrations the plugin passes to Amplifier: the kind of stored credential it uses from each, and the
# Amplifier provider it becomes.
OPENCODE_CONNECTIONS = {
    "anthropic": ("key", "anthropic"),
    "openai": ("key", "openai"),
    "google": ("key", "gemini"),
    "azure": ("key", "azure-openai"),
    "github-copilot": ("oauth", "github-copilot"),
}
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_BANNER = re.compile(r"^// amplifier-opencode-plugin (\S+)")


def version() -> str:
    return metadata.version("amplifier-app-opencode")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "opencode"


def plugin_path(project: Path | None) -> Path:
    if project is None:
        return config_dir() / "plugins" / PLUGIN_NAME
    return project / ".opencode" / "plugins" / PLUGIN_NAME


def bundled_plugin() -> bytes:
    return resources.files("amplifier_app_opencode").joinpath("plugin_bundle", PLUGIN_NAME).read_bytes()


def plugin_version(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as handle:
            head = [handle.readline() for _ in range(5)]
    except (OSError, UnicodeDecodeError):
        return None
    return next((match[1] for line in head if (match := _BANNER.match(line))), None)


def opencode_version() -> str | None:
    """opencode's version, or None when the opencode command is not found or does not answer."""
    executable = shutil.which("opencode")
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(result.stdout)
    return match[0] if match else None


def opencode_connections() -> list[str] | str:
    """The Amplifier providers whose opencode integration has a stored credential the plugin passes to Amplifier as
    its active connection, or the reason opencode did not list them."""
    executable = shutil.which("opencode")
    if executable is None:
        return "opencode was not found"
    try:
        result = subprocess.run(
            [executable, "auth", "list", "--format", "json"], capture_output=True, text=True, timeout=120, check=False
        )
        integrations = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return "`opencode auth list` did not answer"
    connected: list[str] = []
    for integration in integrations if isinstance(integrations, list) else []:
        if integration.get("id") not in OPENCODE_CONNECTIONS:
            continue
        method, provider = OPENCODE_CONNECTIONS[integration["id"]]
        active = (integration.get("connections") or [None])[0]
        if isinstance(active, dict) and active.get("type") == "credential" and active.get("method") == method:
            connected.append(provider)
    return connected


def _opencode_problem() -> str | None:
    found = opencode_version()
    if found is None:
        return "opencode was not found on PATH. Install opencode 2.x: https://opencode.ai"
    if not found.startswith("2."):
        return f"opencode {found} is installed; this plugin needs opencode 2.x. Run: opencode upgrade"
    return None


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _clean(project: Path | None, assume_yes: bool) -> None:
    found = cleanup.find(config_dir(), project)
    if not found:
        return
    print("Found files from amplifier-app-opencode 0.4.x:")
    for line in found.describe():
        print(f"  {line}")
    commented = [block.path for block in found.provider_blocks if block.has_comments]
    for path in commented:
        print(f"  {path} is rewritten without its comments.")
    if not assume_yes and not _confirm("Remove them?"):
        print("Kept them. Run `amplifier-opencode install --yes` to remove them.")
        return
    cleanup.remove(found)
    print("Removed them.")


def install(project: Path | None, assume_yes: bool) -> int:
    if project is not None:
        project = project.expanduser().resolve()
    problem = _opencode_problem()
    if problem:
        print(f"Warning: {problem}")

    target = plugin_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bundled_plugin())
    print(f"Installed the plugin: {target}")

    options = config_dir() / OPTIONS_NAME
    if not options.exists():
        content = dict(DEFAULT_OPTIONS)
        if shutil.which(COMMAND) is None:
            content["command"] = str(Path(sys.argv[0]).resolve())
        options.parent.mkdir(parents=True, exist_ok=True)
        options.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote the options: {options}")

    _clean(project, assume_yes)
    return 0


def uninstall(project: Path | None) -> int:
    if project is not None:
        project = project.expanduser().resolve()
    target = plugin_path(project)
    if target.exists():
        target.unlink()
        print(f"Removed the plugin: {target}")
    else:
        print(f"No plugin at {target}")
    options = config_dir() / OPTIONS_NAME
    if options.exists():
        print(f"Kept the options: {options}")
    return 0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str, token: str) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _options() -> dict[str, Any]:
    try:
        options = json.loads((config_dir() / OPTIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return options if isinstance(options, dict) else {}


def _options_environment() -> dict[str, str]:
    environment = _options().get("environment")
    if not isinstance(environment, dict):
        return {}
    return {str(key): str(value) for key, value in environment.items()}


def _probe_face(timeout: float = 120) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | str:
    """Models and providers from a face started for this check, or the reason it did not answer."""
    token = secrets.token_hex(24)
    port = _free_port()
    environment = {**os.environ, **_options_environment(), "AMPLIFIER_OPENCODE_FACE_TOKEN": token}
    # A default provider that needs no credentials starts the face whatever is connected; no turn runs here.
    environment |= {"AMPLIFIER_AGENT_PROVIDER": "ollama", "AMPLIFIER_AGENT_MODEL": "doctor"}
    command = [sys.executable, "-m", "amplifier_app_opencode", "face", "--port", str(port)]
    command += ["--token-env", "AMPLIFIER_OPENCODE_FACE_TOKEN"]
    with tempfile.TemporaryFile("w+") as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}/v1"
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    return f"the face exited with code {process.returncode}: {log.read().strip()[-2000:]}"
                try:
                    return _get(f"{base}/models", token)["data"], _get(f"{base}/providers", token)["data"]
                except (urllib.error.URLError, ConnectionError, TimeoutError):
                    time.sleep(0.5)
            return f"the face did not answer within {timeout:.0f} s"
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def doctor() -> int:
    problems: list[str] = []
    own = version()
    print(f"amplifier-opencode {own}")

    found = opencode_version()
    print(f"opencode: {found or 'not found'}")
    problem = _opencode_problem()
    if problem:
        problems.append(problem)

    plugins = [plugin_path(None), plugin_path(Path.cwd())]
    present = [path for path in plugins if path.exists()]
    for path in present:
        embedded = plugin_version(path)
        print(f"plugin: {path} ({embedded or 'unknown version'})")
        if embedded != own:
            problems.append(f"The plugin at {path} is version {embedded}, not {own}. Run: amplifier-opencode install")
    if not present:
        print("plugin: not installed")
        problems.append("The plugin is not installed. Run: amplifier-opencode install")

    options = config_dir() / OPTIONS_NAME
    if options.exists():
        try:
            json.loads(options.read_text(encoding="utf-8"))
            print(f"options: {options}")
        except (OSError, json.JSONDecodeError) as error:
            problems.append(
                f"{options} is not valid JSON ({error}). Fix it or delete it and run: amplifier-opencode install"
            )

    leftovers = cleanup.find(config_dir(), Path.cwd())
    if leftovers:
        problems.append("Files from amplifier-app-opencode 0.4.x remain. Run: amplifier-opencode install")

    connected = opencode_connections()
    print(
        f"providers connected in opencode: {connected if isinstance(connected, str) else ', '.join(connected) or 'none'}"
    )

    print("Starting the Amplifier face to list providers...")
    probed = _probe_face()
    if isinstance(probed, str):
        problems.append(f"The Amplifier face did not start: {probed}")
    else:
        _, providers = probed
        found = [entry["provider"] for entry in providers if entry.get("credentials") == "found"]
        print(f"providers with credentials in the environment: {', '.join(found) or 'none'}")
        ready = {*found, *(connected if isinstance(connected, list) else [])}
        ready |= {entry["provider"] for entry in providers if entry.get("credentials") == "not_required"}
        default_model = _options().get("defaultModel")
        provider = default_model.split("/", 1)[0] if isinstance(default_model, str) and "/" in default_model else None
        if not found and not (isinstance(connected, list) and connected):
            variables = sorted({name for entry in providers for name in entry.get("credential_variables", [])})
            problems.append(
                "No provider has credentials. Connect one in opencode with /connect or "
                "`opencode auth login <provider>`, or export one of "
                f"{', '.join(variables) or 'the provider variables'}, or set it under environment in {options}"
            )
        elif provider and isinstance(connected, list) and provider not in ready:
            problems.append(
                f"defaultModel {default_model} needs {provider} credentials. Connect {provider} in opencode with "
                f"/connect or `opencode auth login <provider>`, or set defaultModel to a provider that has them"
            )

    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nNo problems found.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=COMMAND, description="Use Amplifier Agent as the agent inside opencode.")
    parser.add_argument("--version", action="version", version=f"{COMMAND} {version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    install_parser = commands.add_parser("install", help="install the opencode plugin")
    install_parser.add_argument("--project", type=Path, help="install into DIR/.opencode/plugins instead of globally")
    install_parser.add_argument("--yes", action="store_true", help="remove 0.4.x leftovers without asking")

    uninstall_parser = commands.add_parser("uninstall", help="remove the opencode plugin")
    uninstall_parser.add_argument("--project", type=Path, help="remove from DIR/.opencode/plugins")

    commands.add_parser("doctor", help="check the installation and provider credentials")

    face_parser = commands.add_parser("face", help="run the HTTP face in the current directory (started by the plugin)")
    face_parser.add_argument("--port", type=int, required=True)
    face_parser.add_argument("--token-env", required=True, help="environment variable holding the bearer token")
    face_parser.add_argument(
        "--approvals",
        choices=("allow", "deny"),
        default=os.environ.get("AMPLIFIER_AGENT_APPROVALS") or "allow",
        help="approval policy for the agent's tools (default: AMPLIFIER_AGENT_APPROVALS, else allow)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "install":
        code = install(args.project, args.yes)
    elif args.command == "uninstall":
        code = uninstall(args.project)
    elif args.command == "doctor":
        code = doctor()
    else:
        from amplifier_app_opencode import face

        code = face.run(args.port, args.token_env, args.approvals)
    raise SystemExit(code)
