"""Shared fixture of the held-out tests for the stack model (spec: 20261112 Stack Model).

Seams (spec `## Interfaces and seams`): the `check_setup.py --harness claude` process on a scratch project, its
stdout and exit code; the `devloop.py verify-init` and `devloop.py drive` processes on a scratch repo, their stdout
JSON, exit code and `dev-loop.json`; the kit text files, read as text. The kit runs from the source tree. Every name
in the scratch repos is invented.

Fixture choices the spec leaves open:
- The setup project (oracle lines 1 to 9) is a git repo on `main` where `bin/install.mjs init --yes --harness=both`
  ran, with a test `security-audit` skill in a scratch home (its tree id in `DEVLOOP_TEST_SECURITY_AUDIT_TREE`) and
  fake `claude`, `codex` and `gh` CLIs on `PATH`. Its `dev-loop.json` on `main` holds `"capabilities": []`,
  `commands.acceptance` and `source_dirs`, so `check_setup.py --harness claude` passes on it (the spec's setup).
  Each case edits the checkout `dev-loop.json` and `AGENTS.md`, and adds or removes compose files at the root.
- A line "about" a key is a stdout line that holds the key name; a line about `verify` holds `verify` or
  `base_url` (as in the Verify Skill tests).
- A Playwright install in a fixture is a `node_modules` folder that holds `playwright/` or `@playwright/test/`;
  a run of `drive` that must start a browser links `node_modules` to this repo's own install
  (`npm ci && npx playwright install chromium`).
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
NODE_MODULES = REPO_ROOT / "node_modules"

ACCEPTANCE_COMMAND = "scripts/held-out.sh"
FILL = "<!-- dev-loop: fill -->"
BASE_URL = "http://127.0.0.1:4173"

INVALID_LINE = ("- capabilities in dev-loop.json is invalid: must be a list of distinct values from ui, backend")
RUNNER_LINE = ("- commands.acceptance missing in dev-loop.json: map the command that runs the held-out suite and "
               "writes JUnit XML into $DEVLOOP_REPORT_DIR")
PLAYWRIGHT_LINE = "- verify.playwright in dev-loop.json must be a folder path in the repo"


def missing_line(proposal: str) -> str:
    """The `capabilities missing` user action line with that JSON proposal."""
    return (f'- capabilities missing in dev-loop.json: add "capabilities": {proposal}. ui: a web UI the verify skill '
            "drives; backend: a service the acceptance suite starts; [] for a CLI or a library")


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def clean_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX")) and key != "NODE_PATH"}
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def init_repo(path: Path, env: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, env, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(path, env, "config", key, value)


def commit_all(path: Path, env: dict[str, str], message: str) -> str:
    git(path, env, "add", "-A")
    git(path, env, "commit", "-q", "--allow-empty", "-m", message)
    return git(path, env, "rev-parse", "HEAD")


def run_json(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = 180) -> tuple[int, dict]:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout is not one JSON object (exit {result.returncode}):\n"
                             f"{result.stdout[-1500:]}\n{result.stderr[-1500:]}") from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"stdout is not a JSON object: {result.stdout[-1500:]}")
    return result.returncode, payload


def user_actions(output: str) -> list[str]:
    """The lines under the `User actions required` heading."""
    lines = output.splitlines()
    heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
    return lines[heads[0] + 1:] if heads else []


def lines_with(output: str, *tokens: str) -> list[str]:
    return [line for line in output.splitlines() if any(token in line for token in tokens)]


def verify_lines(output: str) -> list[str]:
    return lines_with(output, "verify", "base_url")


def stripped(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines()]


def entry_text(routes: list, paths: list, title: str = "Home page") -> str:
    """A feature map entry of the verify skill."""
    return ("---\n" + f"routes: {json.dumps(routes)}\npaths: {json.dumps(paths)}\n" + "---\n\n"
            f"# {title}\n\nWhere a visitor lands.\n\n"
            "## Sub-features\n\n- The main heading.\n\n"
            "## How to get to it (user POV)\n\nOpen the site in a browser.\n\n"
            "## Driving it with Playwright\n\nNavigate to the route and look for the heading.\n\n"
            "## Gotchas\n\nNone known.\n")


def fill_verify_skill(repo: Path, name: str, route: str, heading: str, paths: list[str]) -> None:
    """Replace the `verify-init` fill markers and add one feature map entry."""
    skill_dir = repo / ".agents" / "skills" / name
    for path in skill_dir.rglob("*.md"):
        path.write_text(path.read_text().replace(FILL, "Run the site dev tool."))
    put(skill_dir / "features" / "home.md",
        entry_text([{"path": route, "names": [{"role": "heading", "name": heading}]}], paths))


def page(body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><title>Kettle site</title></head><body>{body}</body></html>"


class PageServer:
    """A local HTTP server for fixture pages."""

    def __init__(self, pages: dict[str, str]) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                status, body = (200, pages[path]) if path in pages else (404, page("<h1>Not found</h1>"))
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class ScratchCase(unittest.TestCase):
    """A scratch folder (removed after the test) with a home and a clean environment."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stack-model-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = clean_env(self.home)

    def verify_init(self, repo: Path, *args: str) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "verify-init", *args], repo, self.env)

    def drive(self, repo: Path, *args: str) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "drive", *args], repo, self.env, timeout=300)


class SetupProject(ScratchCase):
    """An installed project `kettle-works` on `main` where `check_setup.py --harness claude` passes."""

    def setUp(self) -> None:
        super().setUp()
        bins = self.root / "fake-bin"
        for name, version in (("claude", "2.1.310 (Claude Code)"), ("codex", "codex-cli 0.170.0"),
                              ("gh", "gh version 2.103.2 (2026-10-01)")):
            put(bins / name, f"#!/bin/sh\n[ \"$1\" = --version ] || exit 1\necho '{version}'\n")
            (bins / name).chmod(0o755)
        self.env["PATH"] = f"{bins}{os.pathsep}{self.env.get('PATH', '')}"
        (self.root / "notes" / "Features").mkdir(parents=True)
        self.project = self.root / "kettle-works"
        init_repo(self.project, self.env)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both",
                               f"--notes={self.root / 'notes'}"], cwd=self.project, env=self.env,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(init.returncode, 0, init.stdout[-1500:] + init.stderr[-1500:])
        self.plant_security_audit()
        self.installed = json.loads((self.project / "dev-loop.json").read_text())
        commands = {**(self.installed.get("commands") or {}), "acceptance": ACCEPTANCE_COMMAND}
        self.base = {**self.installed, "capabilities": [], "commands": commands, "source_dirs": ["src/"]}
        put(self.project / "src" / "kettle.py", "def boil():\n    return 100\n")
        put(self.project / ACCEPTANCE_COMMAND, "#!/bin/sh\nexec python3 -m unittest discover -s tests/held-out\n")
        (self.project / ACCEPTANCE_COMMAND).chmod(0o755)
        self.write_config(self.base)
        commit_all(self.project, self.env, "Install dev-loop")

    def plant_security_audit(self) -> None:
        folder = self.home / ".agents" / "skills" / "security-audit"
        put(folder / "SKILL.md", "---\nname: security-audit\ndescription: Audit a codebase.\n---\n\n# Audit\n")
        link = self.home / ".claude" / "skills" / "security-audit"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(folder, target_is_directory=True)
        scratch = self.root / "tree-scratch"
        git(self.root, self.env, "init", "-q", str(scratch))
        tree_env = {**self.env, "GIT_DIR": str(scratch / ".git"), "GIT_WORK_TREE": str(folder)}
        git(folder, tree_env, "add", "-A", ".")
        self.env["DEVLOOP_TEST_SECURITY_AUDIT_TREE"] = git(folder, tree_env, "write-tree")

    def write_config(self, config: dict) -> None:
        put(self.project / "dev-loop.json", json.dumps(config, indent=2) + "\n")

    def config_with(self, drop: tuple[str, ...] = (), no_runner: bool = False, **changes: object) -> dict:
        """The base config with `drop` keys removed, `changes` set, and `commands.acceptance` gone on request."""
        config = {key: value for key, value in self.base.items() if key not in drop}
        config.update(changes)
        if no_runner:
            commands = {k: v for k, v in config["commands"].items() if k != "acceptance"}
            if commands:
                config["commands"] = commands
            else:
                del config["commands"]
        return config

    def stacks(self, line: str | None) -> None:
        """`AGENTS.md` with that `stacks:` line (None: no `AGENTS.md`)."""
        path = self.project / "AGENTS.md"
        if line is None:
            path.unlink(missing_ok=True)
        else:
            put(path, f"# AGENTS.md\n\nKettle works: boils water on request.\n\n{line}\n")

    def check(self) -> tuple[int, str]:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.home),
                                 "--repo", str(self.project)], env=self.env, capture_output=True, text=True,
                                timeout=300)
        return result.returncode, result.stdout + result.stderr

    def add_verify_skill(self) -> str:
        """`verify-init`, then the skeleton filled the way an agent fills it. Returns the skill name."""
        code, payload = self.verify_init(self.project, "--base-url", BASE_URL)
        self.assertEqual(code, 0, payload)
        name = payload.get("skill")
        self.assertTrue(name, payload)
        fill_verify_skill(self.project, name, "/", "Kettle", ["src/*"])
        return name


def listed_paths(error: str, repo: Path) -> list[str]:
    """The paths of a `playwright not found in <paths>: ...` error, each relative to the repo."""
    found = re.search(r"playwright not found in (.+?): ", error)
    if not found:
        raise AssertionError(f"not a 'playwright not found in' error: {error!r}")
    paths = []
    for item in found.group(1).split(","):
        item = item.strip()
        path = Path(item)
        if path.is_absolute():
            try:
                item = str(path.resolve().relative_to(repo.resolve()))
            except ValueError:
                pass
        paths.append(item)
    return paths
