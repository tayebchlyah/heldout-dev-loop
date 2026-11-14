#!/usr/bin/env python3
"""Shared fixture of the held-out stale check project tests (spec: Stale Check Project).

Seam: the `devloop.py acceptance` process (the kit source, `skill/scripts/devloop.py`) in a linked worktree, with a
fake `docker` first in `PATH`, as in Stage 6 Mechanics oracle lines 15 to 19. Every name in the temp repos is
invented. HOME and TMPDIR are temporary folders; no `COMPOSE_*`, `DOCKER_*`, `DEVLOOP_*` or `GIT_*` variable leaks in.

Fixture choices the spec leaves open:
- `Stack` is the Merge Check Binding `GateRepo` (a repo `acme-widgets` on `main`, no origin, so `<main-ref>` is
  `refs/heads/main`; a linked worktree on `feat/demo`, `<top>`; the acceptance command a script outside the repo that
  appends a line to `ran.log` and copies a fixture JUnit file). Its first commit on `main` (2026-01-01) holds
  `dev-loop.json` with the same `commands.acceptance` and `commands.acceptance_failed`, plus
  `commands.compose_project` unless the case drops it, and `stack/compose.yml`; a case that needs the lookup of today
  adds a `compose.yaml` at the top. The worktree then commits a code change on 2026-01-10: the reference commit.
  Each key value is its own `Stack`, so the key sits in the first commit and never moves the reference commit.
- "Before" is 2026-01-05T00:00:00.123456789Z, "after" 2026-01-10T01:00:00Z.
- The fake `docker` logs each call as one JSON list of its arguments in `docker-calls.log`. It answers by the words
  of its arguments: `ps` (top level) prints the full id of each container whose `com.docker.compose.project` label
  equals the value of a `label=com.docker.compose.project=<name>` filter (every container with no such filter);
  `inspect <id> ...` prints `Id`, `Created`, `Name` and `Config.Labels` of each container whose id starts with a
  word; `compose ... config` prints the state services (exit 1 with a message on stderr when the state says
  `config_fail`); `compose ... ps ... <service>` prints the 12-character id of each container whose
  `com.docker.compose.service` label is that service. Any other call exits 1.
- The key command of a "marker" case appends a line to `key-ran.log` in the fixture folder, then prints `acme-wt`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from merge_check_binding_support import DEVLOOP, GateRepo, config_text, run_json

KEY = "echo acme-wt"
START = "2026-01-01T00:00:00Z"
CODE_TIME = "2026-01-10T00:00:00Z"
BEFORE = "2026-01-05T00:00:00.123456789Z"
AFTER = "2026-01-10T01:00:00Z"
PROJECT_FILTER = "label=com.docker.compose.project="

STACK_COMPOSE = "services:\n  api:\n    build: .\n    profiles: [test]\n  db:\n    image: postgres:17\n"
TOP_COMPOSE = "services:\n  api:\n    build: ./stack\n    profiles: [test]\n  db:\n    image: postgres:17\n"
BUILD_AND_IMAGE = {"api": {"build": {"context": ".", "dockerfile": "Dockerfile"}, "profiles": ["test"]},
                   "db": {"image": "postgres:17"}}
IMAGE_ONLY = {"db": {"image": "postgres:17"}}

FAKE_DOCKER = r'''import json
import pathlib
import sys

fix = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
with open(fix / "docker-calls.log", "a") as log:
    log.write(json.dumps(args) + "\n")
state = json.loads((fix / "docker-state.json").read_text())
containers = state.get("containers", [])
words = [a for a in args if not a.startswith("-")]
FILTER = "label=com.docker.compose.project="
if args and args[0] == "compose":
    if "config" in words:
        if state.get("config_fail"):
            sys.stderr.write("open /gone/compose.yml: no such file or directory\n")
            sys.exit(1)
        print(json.dumps({"name": "acme", "services": state["services"]}))
    elif "ps" in words:
        for c in containers:
            if c["labels"].get("com.docker.compose.service") == words[-1]:
                print(c["id"][:12])
    else:
        sys.exit(1)
elif args and args[0] == "ps":
    wanted = [a.split(FILTER, 1)[1] for a in args if FILTER in a]
    for c in containers:
        if not wanted or c["labels"].get("com.docker.compose.project") in wanted:
            print(c["id"])
elif args and args[0] == "inspect":
    out = []
    for word in words[1:]:
        for c in containers:
            if c["id"].startswith(word):
                out.append({"Id": c["id"], "Created": c["created"], "Name": "/" + c["name"],
                            "Config": {"Labels": c["labels"]}})
    print(json.dumps(out, indent=4))
else:
    sys.exit(1)
'''


def cid(seed: str) -> str:
    """A 64-hex container id from a two-hex seed."""
    return seed * 32


def marker_key(root: Path) -> str:
    """The "marker" key command of a `Stack` at `root`."""
    return f"echo k >> '{root / 'fixture' / 'key-ran.log'}'; echo acme-wt"


def option_values(args: list[str], *flags: str) -> list[str]:
    """The values of each flag in `flags`, as `<flag> <value>` or `<flag>=<value>`, in argument order."""
    values = []
    for index, arg in enumerate(args):
        for flag in flags:
            if arg == flag and index + 1 < len(args):
                values.append(args[index + 1])
            elif arg.startswith(flag + "=") and flag.startswith("--"):
                values.append(arg[len(flag) + 1:])
    return values


class Stack(GateRepo):
    """The `GateRepo` with `commands.compose_project` `key` (None: no key) on `main` and in the worktree, the
    stale-check-project fake `docker`, and a code commit in the worktree at `CODE_TIME`."""

    def __init__(self, root: Path, key: object = KEY, top_compose: bool = False) -> None:
        runner = root / "fixture" / "run.sh"
        commands = {"acceptance": f"sh '{runner}'", "acceptance_failed": f"sh '{runner}' --failed"}
        if key is not None:
            commands["compose_project"] = key
        files = {"dev-loop.json": config_text(commands=commands), "stack/compose.yml": STACK_COMPOSE,
                 "stack/Dockerfile": "FROM scratch\n"}
        if top_compose:
            files["compose.yaml"] = TOP_COMPOSE
        super().__init__(root, files, when=START)
        (self.fix / "fake_docker.py").write_text(FAKE_DOCKER)
        self.code = self.commit(self.wt, {"app.py": "def widget():\n    return 2\n"}, "Change the widget",
                                when=CODE_TIME)
        self.stack = str(self.wt / "stack")
        self.compose_file = str(self.wt / "stack" / "compose.yml")
        self.other = str(root / "worktrees" / "acme-widgets-other" / "stack")
        self.marker = self.fix / "key-ran.log"

    def key_config(self, key: object) -> str:
        """`dev-loop.json` with the acceptance commands and `commands.compose_project` `key` (None: no key)."""
        commands = dict(self.commands)
        if key is not None:
            commands["compose_project"] = key
        return config_text(commands=commands)

    def container(self, seed: str, service: str, created: str, project: str = "acme-wt",
                  working_dir: str | None = None, config_files: str | None = None, oneoff: str = "False") -> dict:
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": service,
                  "com.docker.compose.project.working_dir": working_dir or self.stack,
                  "com.docker.compose.project.config_files": config_files or self.compose_file,
                  "com.docker.compose.oneoff": oneoff, "com.docker.compose.container-number": "1"}
        return {"id": cid(seed), "created": created, "name": f"{project}-{service}-1", "labels": labels}

    def state(self, containers: list[dict], services: dict | None = None, config_fail: bool = False) -> None:
        """Write the fake `docker` state and clear its call log."""
        state = {"services": BUILD_AND_IMAGE if services is None else services, "containers": containers,
                 "config_fail": config_fail}
        (self.fix / "docker-state.json").write_text(json.dumps(state))
        (self.fix / "docker-calls.log").unlink(missing_ok=True)

    def calls(self) -> list[list[str]]:
        log = self.fix / "docker-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def key_runs(self) -> int:
        return len(self.marker.read_text().splitlines()) if self.marker.exists() else 0

    def run(self, env: dict[str, str] | None = None, *args: str) -> tuple[int, dict]:
        code, payload, _ = run_json([sys.executable, str(DEVLOOP), "acceptance", *args], self.wt,
                                    {**self.env, **(env or {})})
        return code, payload
