"""The `capabilities` key of dev-loop.json and the `commands.acceptance` line: validator, reader, proposal, setup lines."""

from __future__ import annotations

import json
import re
from pathlib import Path

VALUES: tuple[str, ...] = ("ui", "backend")
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
INVALID = "capabilities in dev-loop.json is invalid: must be a list of distinct values from ui, backend"
MISSING = ('capabilities missing in dev-loop.json: add "capabilities": {}. ui: a web UI the verify skill drives; '
           "backend: a service the acceptance suite starts; [] for a CLI or a library")
ACCEPTANCE_MISSING = ("commands.acceptance missing in dev-loop.json: map the command that runs the held-out suite "
                      "and writes JUnit XML into $DEVLOOP_REPORT_DIR")


def valid(value: object) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) and item in VALUES for item in value):
        return False
    return len(set(value)) == len(value)


def _config(repo: Path) -> dict | None:
    try:
        config = json.loads((repo / "dev-loop.json").read_text())
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) else None


def read(repo: Path) -> set[str]:
    """The capabilities of the checkout at `repo`; missing, invalid or unreadable gives none."""
    value = (_config(repo) or {}).get("capabilities")
    return set(value) if isinstance(value, list) and valid(value) else set()


def proposal(repo: Path) -> list[str]:
    try:
        match = re.search(r"^stacks:\s*(.+)$", (repo / "AGENTS.md").read_text(), re.MULTILINE)
    except OSError:
        match = None
    stacks = {s.strip() for s in match.group(1).split(",")} if match else set()
    found = ["ui"] if "ui" in stacks else []
    return found + (["backend"] if any((repo / name).is_file() for name in COMPOSE_FILES) else [])


def setup(repo: Path) -> tuple[list[str], list[str]]:
    """(failures, user actions) of check_setup.py for `capabilities` and `commands.acceptance`."""
    config = _config(repo)
    if config is None:
        return [], []
    failures: list[str] = []
    actions: list[str] = []
    if "capabilities" not in config:
        actions.append(MISSING.format(json.dumps(proposal(repo))))
    elif not valid(config["capabilities"]):
        failures.append(INVALID)
    commands = config.get("commands", {})
    if isinstance(commands, dict) and "acceptance" not in commands:
        actions.append(ACCEPTANCE_MISSING)
    return failures, actions
