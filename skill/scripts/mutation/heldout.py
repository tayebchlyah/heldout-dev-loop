"""Held-out folder rules shared by the mutation setup lines and the built-in adapters."""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True

from collections.abc import Iterable
from pathlib import Path

HELD_OUT = "tests/acceptance"
SELECTION_NO_PATH = "[tool.mutmut] test selection names no test path: name the unit test folder"
SELECTION_REACHES = f"[tool.mutmut] test selection reaches {HELD_OUT}: run unit tests only"
CLI_ARGS_REACHES = f"[tool.mutmut] pytest_add_cli_args reaches {HELD_OUT}: run unit tests only"
VALUE_OPTIONS = frozenset(("-k", "-m", "-p", "-c", "-o", "--deselect", "--ignore", "--ignore-glob", "--rootdir",
                           "--confcutdir", "--override-ini", "--basetemp"))


def _held(root: Path) -> Path:
    return (root / HELD_OUT).resolve()


def _paths(args: list[str], folder: Path) -> list[Path]:
    paths, value = [], False
    for arg in args:
        if value or arg.startswith("-"):
            value = arg in VALUE_OPTIONS
            continue
        path = folder / arg.split("::", 1)[0]
        if path.exists():
            paths.append(path.resolve())
    return paths


def _covers(outer: Path, inner: Path) -> bool:
    return any(outer == p or (outer.exists() and p.exists() and os.path.samefile(outer, p))
               for p in (inner, *inner.parents))


def _reaches(paths: list[Path], held: Path) -> bool:
    return any(_covers(held, path) or _covers(path, held) for path in paths)


def relative(folder: Path, root: Path) -> str | None:
    folder, held = folder.resolve(), _held(root)
    if folder == held or folder in held.parents:
        return held.relative_to(folder).as_posix()
    return None


def stryker_refusals(config: dict) -> list[str]:
    gaps = []
    if config.get("inPlace") is True:
        gaps.append('stryker.config.json sets "inPlace": true: the held-out suite then reaches the run')
    if "ignorePatterns" in config:
        patterns = config["ignorePatterns"]
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            gaps.append("stryker.config.json ignorePatterns is not a list of strings")
        else:
            gaps += [f"stryker.config.json ignorePatterns entry has a comma: {p}" for p in patterns if "," in p]
    return gaps


def ignore_patterns(config: dict, rel: str) -> str:
    return ",".join([*config.get("ignorePatterns", []), f"/{rel}"])


def ran(keys: Iterable[str], folder: Path, root: Path) -> int:
    held = _held(root)
    paths = [(folder / key).resolve() for key in keys]
    return sum(1 for path in paths if _covers(held, path))


def selection_refusal(selection: list[str], folder: Path, root: Path) -> str | None:
    paths = _paths(selection, folder)
    if not paths:
        return SELECTION_NO_PATH
    return SELECTION_REACHES if _reaches(paths, _held(root)) else None


def cli_args_refusal(args: list[str], folder: Path, root: Path) -> str | None:
    return CLI_ARGS_REACHES if _reaches(_paths(args, folder), _held(root)) else None
