"""Find and remove what amplifier-app-opencode 0.4.x wrote into opencode configuration."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

PROVIDER_ID = "amplifier"
GLOBAL_CONFIG_NAMES = ("opencode.jsonc", "opencode.json", "config.json")
MANIFESTS = (
    (".amplifier-generated-commands.json", "commands", "command"),
    (".amplifier-generated-agents.json", "agents", "agent"),
)
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ProviderBlock:
    """A config file whose `provider.amplifier` entry is removed by rewriting the file."""

    path: Path
    has_comments: bool


@dataclass(frozen=True)
class Leftovers:
    provider_blocks: list[ProviderBlock]
    files: list[Path]

    def __bool__(self) -> bool:
        return bool(self.provider_blocks or self.files)

    def describe(self) -> list[str]:
        lines = [f"provider.{PROVIDER_ID} in {block.path}" for block in self.provider_blocks]
        lines += [str(path) for path in self.files]
        return lines


def strip_jsonc(text: str) -> str:
    """JSON text from JSONC: comments and trailing commas removed, strings untouched."""
    return _strip(text)[0]


def _strip(text: str) -> tuple[str, bool]:
    out: list[str] = []
    comments = False
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("//", i):
            comments = True
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
        elif text.startswith("/*", i):
            comments = True
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
        elif char == ",":
            rest = i + 1
            while rest < n and text[rest] in " \t\r\n":
                rest += 1
            if rest < n and text[rest] in "}]":
                i += 1
            else:
                out.append(char)
                i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out), comments


def _load(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(strip_jsonc(text) or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (data, text) if isinstance(data, dict) else None


def _provider_block(path: Path) -> ProviderBlock | None:
    loaded = _load(path)
    if loaded is None:
        return None
    data, text = loaded
    providers = data.get("provider")
    if not isinstance(providers, dict) or PROVIDER_ID not in providers:
        return None
    return ProviderBlock(path, _strip(text)[1])


def _manifest_entries(manifest: Path, key: str) -> list[str]:
    try:
        loaded = json.loads(manifest.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    items = loaded.get(key, []) if isinstance(loaded, dict) else loaded
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str) and _SAFE_NAME.fullmatch(item) and item not in (".", "..")]


def _generated_files(scope: Path) -> list[Path]:
    files: list[Path] = []
    for manifest_name, key, directory in MANIFESTS:
        manifest = scope / manifest_name
        if not manifest.is_file():
            continue
        files += [
            scope / directory / name
            for name in _manifest_entries(manifest, key)
            if (scope / directory / name).is_file()
        ]
        files.append(manifest)
    return files


def find(global_dir: Path, project: Path | None = None) -> Leftovers:
    """Leftovers in the global opencode config directory and, with `project`, in that project."""
    configs = [global_dir / name for name in GLOBAL_CONFIG_NAMES]
    scopes = [global_dir]
    if project is not None:
        configs.append(project / "opencode.json")
        scopes.append(project / ".opencode")
    blocks = [block for path in configs if path.is_file() and (block := _provider_block(path)) is not None]
    files = [path for scope in scopes for path in _generated_files(scope)]
    return Leftovers(blocks, files)


def _write_atomic(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def remove(leftovers: Leftovers) -> None:
    """Remove the leftovers. Other content of rewritten config files is kept; their comments are not."""
    for block in leftovers.provider_blocks:
        loaded = _load(block.path)
        if loaded is None:
            continue
        data, _ = loaded
        providers = data.get("provider")
        if not isinstance(providers, dict):
            continue
        providers.pop(PROVIDER_ID, None)
        if not providers:
            del data["provider"]
        _write_atomic(block.path, json.dumps(data, indent=2) + "\n")
    for path in leftovers.files:
        path.unlink(missing_ok=True)
