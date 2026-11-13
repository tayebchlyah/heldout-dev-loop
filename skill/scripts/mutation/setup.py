"""The `mutation` lines of check_setup.py: the key, each built-in setup, and the proposal when mutation is off."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import stacks
from mutation import run

FAMILIES = {"python": "mutmut", "js": "stryker", "go": "mutago"}
TOOL_FILES = (("pyproject.toml", "mutmut"), ("stryker.config.json", "stryker"), ("go.mod", "mutago"))
STRYKER_CONFIGS = tuple(f"stryker.{kind}.{ext}" for kind in ("config", "conf") for ext in ("mjs", "js", "cjs", "json"))
EXACT_PIN = re.compile(r"^mutmut(\[[^\]]*\])?==\d+(\.\d+)*$")
POETRY_PIN = re.compile(r"^(==)?\d+\.\d+\.\d+$")
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
RANGE = re.compile(r":\d+(:\d+)?-\d+(:\d+)?$")
GO_VERSION = re.compile(r"\bgo(\d+)\.(\d+)(?:\.(\d+))?")
GO_MIN = (1, 26, 6)
GO_TIMEOUT = 30
CORE = "@stryker-mutator/core"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _tracked(repo: Path) -> list[str]:
    done = _git(repo, "-c", "core.quotePath=false", "ls-files", "-z")
    return [p for p in done.stdout.split("\0") if p] if done.returncode == 0 else []


def _ignored(repo: Path, rel: str) -> bool:
    return _git(repo, "check-ignore", "-q", "--no-index", rel).returncode == 0


def _toml(path: Path) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _get(data: object, *keys: str) -> object:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _pinned(config: dict) -> bool:
    listed = [_get(config, "dependency-groups", "dev"), _get(config, "tool", "uv", "dev-dependencies"),
              _get(config, "project", "optional-dependencies", "dev")]
    if any(isinstance(d, str) and EXACT_PIN.match(d.split(";")[0].replace(" ", "")) for deps in listed
           if isinstance(deps, list) for d in deps):
        return True
    poetry = [_get(config, "tool", "poetry", "group", "dev", "dependencies", "mutmut"),
              _get(config, "tool", "poetry", "dev-dependencies", "mutmut")]
    versions = [_get(v, "version") if isinstance(v, dict) else v for v in poetry]
    return any(isinstance(v, str) and POETRY_PIN.match(v.replace(" ", "")) for v in versions)


def _mutmut(repo: Path, rel: str) -> list[str]:
    config = _toml(repo / rel / "pyproject.toml")
    if config is None:
        return [f"no valid pyproject.toml in {rel or '.'}"]
    pinned = _pinned(config) or (rel != "" and _pinned(_toml(repo / "pyproject.toml") or {}))
    gaps = [] if pinned else ["no exact mutmut pin: add mutmut==<version> to the dev dependencies"]
    mutmut = _get(config, "tool", "mutmut")
    if not isinstance(mutmut, dict):
        gaps.append(f"no [tool.mutmut] in {rel}pyproject.toml")
    else:
        if not (mutmut.get("source_paths") or mutmut.get("paths_to_mutate")):
            gaps.append("[tool.mutmut] has no source_paths")
        selection = mutmut.get("pytest_add_cli_args_test_selection")
        if not isinstance(selection, list) or not selection:
            gaps.append("[tool.mutmut] has no pytest_add_cli_args_test_selection")
        elif any("acceptance" in str(arg) for arg in selection):
            gaps.append("[tool.mutmut] test selection names acceptance: run unit tests only")
    if not _ignored(repo, f"{rel}mutants/probe"):
        gaps.append(f"{rel}mutants/ is not ignored by git")
    return gaps


def _stryker(repo: Path, rel: str) -> list[str]:
    folder = repo / rel
    found = [name for name in STRYKER_CONFIGS if (folder / name).is_file()]
    if found != ["stryker.config.json"]:
        return [f"expected one stryker.config.json, found {', '.join(found) or 'none'}"]
    config = _json(folder / "stryker.config.json")
    if not isinstance(config, dict):
        return ["stryker.config.json is not a JSON object"]
    gaps = []
    runner = config.get("testRunner")
    if runner in (None, "command"):
        gaps.append("stryker.config.json testRunner is command or absent: name the runner of the project tests")
    if config.get("incremental") is True:
        gaps.append('stryker.config.json sets "incremental": true: a run never reuses results')
    if config.get("coverageAnalysis") == "off":
        gaps.append("stryker.config.json sets coverageAnalysis off: a report then has no test per mutant")
    package = _json(folder / "package.json")
    deps = {k: v for key in ("dependencies", "devDependencies") if isinstance(d := _get(package, key), dict)
            for k, v in d.items()}
    core = deps.get(CORE)
    plugin = f"@stryker-mutator/{runner}-runner" if isinstance(runner, str) and runner != "command" else None
    version = deps.get(plugin) if plugin else core
    if not (isinstance(core, str) and EXACT_VERSION.match(core) and core == version):
        named = f"{CORE} {core or 'missing'}" + (f" and {plugin} {version or 'missing'}" if plugin else "")
        gaps.append(f"{named} in {rel}package.json: pin them to the same exact version")
    for probe, shown in ((".stryker-tmp/probe", ".stryker-tmp/"), ("reports/mutation/mutation.json", "reports/")):
        if not _ignored(repo, f"{rel}{probe}"):
            gaps.append(f"{rel}{shown} is not ignored by git")
    return gaps


def _mutago(repo: Path, rel: str) -> list[str]:
    gaps = [] if (repo / rel / "go.mod").is_file() else ["no go.mod in the entry folder"]
    go = shutil.which("go")
    if go is None:
        return gaps + ["go not found on PATH: install Go 1.26.6 or later"]
    try:
        text = subprocess.run([go, "version"], cwd=repo / rel, capture_output=True, text=True, check=False,
                              timeout=GO_TIMEOUT, env={**os.environ, "GOTOOLCHAIN": "local"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    match = GO_VERSION.search(text)
    if not match or tuple(int(n or 0) for n in match.groups()) < GO_MIN:
        gaps.append(f"go version {match.group(0) if match else 'unknown'}: install Go 1.26.6 or later")
    return gaps


SETUPS = {"mutmut": _mutmut, "stryker": _stryker, "mutago": _mutago}


def _command_found(folder: Path, command: str) -> tuple[bool, str]:
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    word = words[0] if words else command
    return (folder / word).is_file() or ("/" not in word and shutil.which(word) is not None), word


def _entry_lines(repo: Path, entry: dict, tracked: list[str]) -> list[str]:
    path = entry["path"]
    rel = "" if path == "." else path
    gaps = []
    name = entry.get("adapter")
    if name is not None and name not in run.ADAPTERS:
        gaps.append(f"unknown adapter {name}")
    elif name is None:
        found, word = _command_found(repo / rel, entry["command"])
        if not found:
            gaps.append(f"command not found: {word}")
    inside = [p[len(rel):] for p in tracked if p.startswith(rel)]
    for glob in entry["critical"]:
        if not glob.startswith("!") and not any(run.glob_match(p, glob) for p in inside):
            gaps.append(f"critical glob {glob} matches no tracked file")
    if name in SETUPS:
        gaps += SETUPS[name](repo, rel)
    return [f"mutation entry {path}: {gap}" for gap in gaps]


def _critical(repo: Path, rel: str, adapter: str) -> list[str]:
    if adapter == "mutmut":
        found = _get(_toml(repo / rel / "pyproject.toml"), "tool", "mutmut", "only_mutate")
    elif adapter == "stryker":
        found = _get(_json(repo / rel / "stryker.config.json"), "mutate")
        found = [RANGE.sub("", g) for g in found if isinstance(g, str)] if isinstance(found, list) else None
    else:
        found = None
    globs = [g for g in found if isinstance(g, str) and g] if isinstance(found, list) else []
    return globs if any(not g.startswith("!") for g in globs) else []


def _from_stacks(listed: object) -> list[tuple[str, str]]:
    if stacks.validate(listed):
        return []
    try:
        presets = stacks.by_name(stacks.load_presets())
    except (OSError, ValueError):
        return []
    found = []
    for item in listed:
        family = presets.get(item["preset"], {}).get("family") if isinstance(item["preset"], str) else None
        if family in FAMILIES:
            found.append((item["path"], FAMILIES[family]))
    return found


def _from_files(repo: Path, tracked: list[str]) -> list[tuple[str, str]]:
    try:
        match = re.search(r"^stacks:\s*(.+)$", (repo / "AGENTS.md").read_text(encoding="utf-8"), re.MULTILINE)
    except (OSError, ValueError):
        match = None
    go = match is not None and "go" in {s.strip() for s in match.group(1).split(",")}
    chosen: dict[str, str] = {}
    for name, adapter in TOOL_FILES:
        for file in tracked:
            parts = PurePosixPath(file).parts
            folders = parts[:-1]
            if parts[-1] != name or not (len(folders) <= 1 or (len(folders) == 2 and folders[0] == "backend")):
                continue
            path = "/".join(folders) + "/" if folders else "."
            if adapter == "mutmut" and not isinstance(_get(_toml(repo / file), "tool", "mutmut"), dict):
                continue
            if adapter == "mutago" and not go:
                continue
            chosen.setdefault(path, adapter)
    return sorted(chosen.items())


def _proposal(repo: Path, config: dict, tracked: list[str]) -> list[tuple[str, list[str]]]:
    found = _from_stacks(config["stacks"]) if "stacks" in config else _from_files(repo, tracked)
    entries = [{"path": path, "adapter": adapter, "critical": _critical(repo, "" if path == "." else path, adapter)}
               for path, adapter in found]
    if not entries:
        return []
    items = ["list the critical files"] if any(not e["critical"] for e in entries) else []
    return [(f'mutation is off: to turn it on, add "mutation": {json.dumps(entries)} to dev-loop.json', items)]


def lines(repo: Path) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(failure lines, notices) of check_setup.py for `mutation`, read from the checkout dev-loop.json."""
    config = _json(repo / "dev-loop.json")
    if not isinstance(config, dict):
        return [], []
    tracked = _tracked(repo)
    failures = []
    for file in tracked:
        if PurePosixPath(file).parent == PurePosixPath(".github/workflows"):
            try:
                named = "select_mutants.py" in (repo / file).read_text(encoding="utf-8")
            except (OSError, ValueError):
                named = False
            if named:
                failures.append(f"{file} runs select_mutants.py: use devloop.py mutation")
    if "mutation" not in config:
        return failures, _proposal(repo, config, tracked)
    value = config["mutation"]
    rule = run.validate(value)
    if rule is None and value:
        rule = next((f"{e['path']} is not a folder in this checkout" for e in value
                     if e["path"] != "." and not (repo / e["path"]).is_dir()), None)
    if rule:
        return failures + [f"mutation in dev-loop.json is invalid: {rule}"], []
    for entry in value or []:
        failures += _entry_lines(repo, entry, tracked)
    return failures, []
