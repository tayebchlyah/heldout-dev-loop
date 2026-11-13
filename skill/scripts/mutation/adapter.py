"""Adapter side of the `devloop.py mutation` protocol 1, shared by the built-in adapters."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import subprocess
from pathlib import Path
from typing import NoReturn

HEADERS = ("# ", "--- ", "+++ ", "@@", "diff ", "index ")


def setup_error(message: str) -> NoReturn:
    """Setup missing or broken: the message on stderr, exit 2."""
    print(message, file=sys.stderr)
    sys.exit(2)


def tool_failed(message: str) -> NoReturn:
    """The tool failed: the message on stderr, exit 1."""
    print(message, file=sys.stderr)
    sys.exit(1)


def read_request() -> dict:
    """The request JSON on stdin; exit 2 unless it is a protocol 1 object."""
    try:
        request = json.loads(sys.stdin.read())
    except ValueError:
        request = None
    if not isinstance(request, dict) or request.get("protocol") != 1:
        shown = request.get("protocol") if isinstance(request, dict) else None
        setup_error(f"unknown protocol {json.dumps(shown)}: this adapter speaks protocol 1")
    return request


def finish(request: dict, units: list[str], mutants: list[dict], not_covered: list[dict]) -> NoReturn:
    """Print the output object and exit 0; with `run`, each file nothing names gets `no mutable code`."""
    if not request["run"]:
        output: dict = {"units": units}
    else:
        named = {m["file"] for m in mutants} | {g["file"] for g in not_covered}
        rest = [{"file": file, "start": start, "end": end, "reason": "no mutable code"}
                for file, ranges in request["files"].items() if file not in named for start, end in ranges]
        output = {"units": units, "mutants": mutants, "not_covered": not_covered + rest}
    print(json.dumps(output))
    sys.exit(0)


def stream(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a tool, copy its stdout and stderr to stderr; return its exit and that text. Not found: exit 2."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace")
    except FileNotFoundError:
        setup_error(f"{command[0]} not found on PATH")
    kept: list[str] = []
    assert process.stdout is not None
    for chunk in process.stdout:
        sys.stderr.write(chunk)
        kept.append(chunk)
    return process.wait(), "".join(kept)


def diff_rows(text: str) -> str | None:
    """The rows of a unified diff without its header rows, an empty row as a space row; None when none is left."""
    lines = text.removesuffix("\n").split("\n") if text else []
    rows = [row or " " for row in lines if not row.startswith(HEADERS)]
    return "\n".join(rows) if rows else None
