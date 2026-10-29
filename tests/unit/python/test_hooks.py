"""The board hook launch, the Claude gate hook and the board import of the hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import CLAUDE_HOOKS, CODEX_ASSETS, write


class BoardRobustImportTests(unittest.TestCase):
    def test_board_robust_imports_in_a_fresh_interpreter(self) -> None:
        path = Path(__file__).resolve().parent / "test_board_robust.py"
        code = f"import runpy; runpy.run_path({str(path)!r}, run_name='probe')"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class ClaudeGateTests(unittest.TestCase):
    """The Claude Stop and SubagentStop gate, and its SubagentStart snapshot."""

    def repo(self, root: str) -> Path:
        repo = Path(root)
        subprocess.run(["git", "init", "-q", str(repo)], check=True, stderr=subprocess.DEVNULL)
        write(repo / "tracked.txt", "before\n")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        write(repo / "tracked.txt", "after\n")
        gate = repo / "scripts" / "gate.sh"
        write(gate, '#!/bin/sh\necho "scope=${GATE_SCOPE:-full}"\nexit 1\n')
        os.chmod(gate, 0o755)
        return repo

    def hook(self, name: str, repo: Path, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(CLAUDE_HOOKS / name)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        )

    def test_stop_asks_for_the_selective_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_a_read_only_subagent_runs_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            for agent_type in ["Explore", "Plan"]:
                with self.subTest(agent_type=agent_type):
                    result = self.hook(
                        "gate.sh",
                        repo,
                        {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": agent_type},
                    )
                    self.assertEqual(result.stdout, "")

    def test_a_subagent_that_wrote_nothing_runs_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            start = {"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "general-purpose"}
            self.hook("gate-snapshot.sh", repo, start)
            result = self.hook("gate.sh", repo, {**start, "hook_event_name": "SubagentStop"})
            self.assertEqual(result.stdout, "")

    def test_a_subagent_that_wrote_a_file_runs_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            start = {"hook_event_name": "SubagentStart", "agent_id": "a3", "agent_type": "implementer"}
            self.hook("gate-snapshot.sh", repo, start)
            write(repo / "new.txt", "written\n")
            result = self.hook("gate.sh", repo, {**start, "hook_event_name": "SubagentStop"})
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_a_subagent_with_no_snapshot_runs_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            result = self.hook(
                "gate.sh",
                repo,
                {"hook_event_name": "SubagentStop", "agent_id": "a4", "agent_type": "general-purpose"},
            )
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_stop_runs_the_mapped_gate_command_first(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            write(repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo mapped scope=$GATE_SCOPE; exit 1"}}))
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])
            (repo / "scripts" / "gate.sh").unlink()
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])

    def test_a_stdlib_name_in_the_project_runs_no_code(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            marker = repo / "ran"
            write(repo / "json.py", f"open({str(marker)!r}, 'a').write('json')\nraise SystemExit\n")
            for command, payload, env in (
                    (["bash", str(CLAUDE_HOOKS / "gate.sh")], {"hook_event_name": "Stop"}, {"CLAUDE_PROJECT_DIR": str(repo)}),
                    (["sh", str(CODEX_ASSETS / "hooks" / "gate.sh")], {"hook_event_name": "Stop", "cwd": str(repo)}, {})):
                marker.unlink(missing_ok=True)
                with self.subTest(hook=command[1]):
                    result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                            check=False, env={**os.environ, **env})
                    self.assertIn("scope=changed", json.loads(result.stdout)["reason"])
                    self.assertFalse(marker.exists())


class BoardHookLaunchTests(unittest.TestCase):
    """D1, D12, O1: how board.sh and gate.sh start `devloop.py board`. Hooks run from a kit layout copy;
    each repo holds a fake devloop.py that sleeps BOARD_SLEEP seconds, then logs its label, cwd and stdin."""

    FAKE = (
        "#!/usr/bin/env python3\nimport json, os, sys, time\ndata = sys.stdin.read()\n"
        "time.sleep(float(os.environ.get('BOARD_SLEEP', '0')))\n"
        "with open(os.environ['BOARD_TEST_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'label': %r, 'cwd': os.getcwd(), 'stdin': data}) + '\\n')\n"
    )
    HOOKS = {"claude": CLAUDE_HOOKS, "codex": CODEX_ASSETS / "hooks"}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.log = self.root / "board.jsonl"

    def repo(self, path: Path, label: str) -> Path:
        subprocess.run(["git", "init", "-q", str(path)], check=True, stderr=subprocess.DEVNULL)
        write(path / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py", self.FAKE % label)
        for harness, source in self.HOOKS.items():
            target = path / ".dev-loop" / "hooks" / harness
            target.mkdir(parents=True)
            for script in source.iterdir():
                shutil.copy2(script, target / script.name)
        return path

    def start(self, repo: Path, harness: str, script: str, args: list[str], cwd: Path, sleep: str,
              log: Path | None = None):
        payload = {"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False}
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "BOARD_TEST_LOG": str(log or self.log), "BOARD_SLEEP": sleep}
        shell = "bash" if harness == "claude" else "sh"
        return subprocess.Popen([shell, str(repo / ".dev-loop" / "hooks" / harness / script), *args], cwd=repo,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True), payload

    def calls(self, count: int, wait: float, log: Path | None = None) -> list[dict]:
        log = log or self.log
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            lines = log.read_text().splitlines() if log.exists() else []
            if len(lines) >= count:
                break
            time.sleep(0.1)
        time.sleep(0.5)
        return [json.loads(line) for line in (log.read_text().splitlines() if log.exists() else [])]

    def test_d1_o1_hook_returns_while_board_runs(self) -> None:
        repo = self.repo(self.root / "project", "A")
        # The four boards sleep at the same time, each with its own log.
        runs = []
        for harness, script, args in [("claude", "board.sh", ["prompt"]), ("claude", "gate.sh", []),
                                      ("codex", "board.sh", ["prompt"]), ("codex", "gate.sh", [])]:
            log = self.root / f"board-{harness}-{script}.jsonl"
            began = time.monotonic()
            proc, payload = self.start(repo, harness, script, args, repo, "3", log)
            out, _ = proc.communicate(json.dumps(payload), timeout=30)
            runs.append((harness, script, proc, out, time.monotonic() - began, log))
        for harness, script, proc, out, elapsed, log in runs:
            with self.subTest(harness=harness, script=script):
                calls = self.calls(1, 6, log)
                self.assertLess(elapsed, 1.5)
                self.assertEqual((proc.returncode, out), (0, ""))
                self.assertEqual([c["label"] for c in calls], ["A"])

    def test_d12_claude_hooks_run_the_project_cli_not_the_cwd_one(self) -> None:
        repo = self.repo(self.root / "project", "A")
        nested = self.repo(repo / "nested", "B")
        for script, args in [("board.sh", ["prompt"]), ("gate.sh", [])]:
            with self.subTest(script=script):
                self.log.unlink(missing_ok=True)
                proc, payload = self.start(repo, "claude", script, args, nested, "0")
                proc.communicate(json.dumps(payload), timeout=30)
                calls = self.calls(1, 6)
                self.assertEqual([c["label"] for c in calls], ["A"])
                self.assertEqual((calls[0]["cwd"], json.loads(calls[0]["stdin"])["cwd"]), (str(nested), str(nested)))
