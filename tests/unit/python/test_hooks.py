"""The board hook launch, the Claude gate hook and the board import of the hooks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import CLAUDE_HOOKS, CODEX_ASSETS, codex_gate_env, write


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
            codex_env = codex_gate_env()
            for command, payload, env in (
                    (["bash", str(CLAUDE_HOOKS / "gate.sh")], {"hook_event_name": "Stop"}, {"CLAUDE_PROJECT_DIR": str(repo)}),
                    (["sh", str(CODEX_ASSETS / "hooks" / "gate.sh")], {"hook_event_name": "Stop", "cwd": str(repo)}, codex_env)):
                marker.unlink(missing_ok=True)
                with self.subTest(hook=command[1]):
                    result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                            check=False, env={**os.environ, **env})
                    self.assertIn("scope=changed", json.loads(result.stdout)["reason"])
                    self.assertFalse(marker.exists())

    def test_a_repo_fsmonitor_runs_no_program(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            marker = repo / "ran"
            monitor = Path(root) / "monitor.sh"
            write(monitor, f"#!/bin/sh\necho x >> {marker}\n")
            os.chmod(monitor, 0o755)
            subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(monitor)], check=True)
            unpinned = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
            codex_env = codex_gate_env()
            for command, payload, env in (
                    (["bash", str(CLAUDE_HOOKS / "gate.sh")], {"hook_event_name": "Stop"}, {"CLAUDE_PROJECT_DIR": str(repo)}),
                    (["sh", str(CODEX_ASSETS / "hooks" / "gate.sh")], {"hook_event_name": "Stop", "cwd": str(repo)}, codex_env)):
                marker.unlink(missing_ok=True)
                with self.subTest(hook=command[1]):
                    result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                            check=False, cwd=repo, env={**unpinned, **env})
                    self.assertIn("scope=changed", json.loads(result.stdout)["reason"])
                    self.assertFalse(marker.exists())


class HookGitPinTests(unittest.TestCase):
    """Each kit hook sources git-pin.sh before its first git or CLI call, and the pin starts no repo program (#155)."""

    HOOKS = {"claude": CLAUDE_HOOKS, "codex": CODEX_ASSETS / "hooks"}

    def test_each_hook_sources_the_pin_before_git(self) -> None:
        sys.path.insert(0, str(CLAUDE_HOOKS.parent.parent / "skill" / "scripts"))
        from check_setup import GIT_CALL

        cli = re.compile(r"\bpython3\b|devloop\.py")
        source = '. "${0%/*}/git-pin.sh"'
        scripts = sorted(script for hooks in self.HOOKS.values() for script in hooks.glob("*.sh")
                         if script.name != "git-pin.sh")
        self.assertGreater(len(scripts), 15)
        for script in scripts:
            with self.subTest(script=f"{script.parent.parent.name}/{script.name}"):
                lines = [line for line in script.read_text().splitlines() if not line.lstrip().startswith("#")]
                pin = next((i for i, line in enumerate(lines) if line.strip() == source), None)
                self.assertIsNotNone(pin)
                self.assertFalse([line for line in lines[:pin] if GIT_CALL.search(line) or cli.search(line)])

    def test_both_payloads_ship_the_same_pin(self) -> None:
        self.assertEqual((CLAUDE_HOOKS / "git-pin.sh").read_bytes(), (CODEX_ASSETS / "hooks" / "git-pin.sh").read_bytes())


class HookPlantedProgramTests(unittest.TestCase):
    """A repo config an agent wrote starts no program from a hook: textconv, clean and process filters, signature
    verification. A failing required filter or a failing status runs the gate (#155)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.marker = self.root / "ran"
        self.program = self.root / "plant.sh"
        write(self.program, f"#!/bin/sh\necho x >> {self.marker}\ncat\n")
        os.chmod(self.program, 0o755)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
        self.git("init", "-q", str(self.repo))
        write(self.repo / ".gitattributes", "*.txt filter=plant diff=plant\n")
        write(self.repo / "tracked.txt", "before\n")
        self.git("-C", str(self.repo), "add", ".")
        self.git("-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "fixture")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], env=self.env, check=True, capture_output=True, text=True)

    def plant(self, *keys: str) -> None:
        for key in keys:
            self.git("-C", str(self.repo), "config", key, str(self.program))

    def run_hook(self, harness: str, script: str, payload: dict) -> subprocess.CompletedProcess[str]:
        shell = "bash" if harness == "claude" else "sh"
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(self.repo)}
        if harness == "codex":
            env.update(codex_gate_env())
        return subprocess.run([shell, str(HookGitPinTests.HOOKS[harness] / script)], input=json.dumps(payload),
                              text=True, capture_output=True, check=False, cwd=self.repo, env=env)

    def stop(self, harness: str) -> subprocess.CompletedProcess[str]:
        return self.run_hook(harness, "gate.sh", {"hook_event_name": "Stop", "cwd": str(self.repo)})

    def test_tree_hash_runs_no_textconv_or_clean_filter(self) -> None:
        self.plant("diff.plant.textconv", "filter.plant.clean")
        write(self.repo / "tracked.txt", "after\n")
        write(self.repo / "untracked.txt", "new\n")
        result = self.run_hook("claude", "tree-hash.sh", {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_gate_status_runs_no_clean_or_process_filter(self) -> None:
        self.plant("filter.plant.clean", "filter.plant.process")
        write(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "exit 1"}}))
        (self.repo / "tracked.txt").touch()
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                self.stop(harness)
                self.assertFalse(self.marker.exists())

    def test_a_submodule_filter_never_runs_and_a_moved_pointer_runs_the_gate(self) -> None:
        sub = self.repo / "sub"
        self.git("init", "-q", str(sub))
        write(sub / ".gitattributes", "* filter=plant\n")
        write(sub / "f.txt", "x\n")
        self.git("-C", str(sub), "add", ".")
        self.git("-C", str(sub), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "sub")
        write(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo gate ran; exit 1"}}))
        self.git("-C", str(self.repo), "-c", "advice.addEmbeddedRepo=false", "add", "sub", "dev-loop.json")
        self.git("-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "gitlink")
        self.git("-C", str(sub), "config", "filter.plant.clean", str(self.program))
        later = time.time() + 3600
        os.utime(sub / "f.txt", (later, later))
        for move, harness, script in [(False, "claude", "gate.sh"), (False, "codex", "gate.sh"), (False, "claude", "tree-hash.sh"),
                                      (True, "claude", "gate.sh"), (True, "codex", "gate.sh")]:
            if move:
                self.git("-C", str(sub), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q",
                         "--allow-empty", "-m", "moved")
            self.marker.unlink(missing_ok=True)
            with self.subTest(move=move, harness=harness, script=script):
                result = self.run_hook(harness, script, {"hook_event_name": "Stop", "cwd": str(self.repo)})
                self.assertFalse(self.marker.exists())
                if move:
                    self.assertIn("gate ran", json.loads(result.stdout)["reason"])

    def plant_index_hook(self) -> float:
        self.env.pop("GIT_OPTIONAL_LOCKS", None)
        write(self.repo / ".husky" / "post-index-change", f"#!/bin/sh\necho x >> {self.marker}\n")
        os.chmod(self.repo / ".husky" / "post-index-change", 0o755)
        self.git("-C", str(self.repo), "config", "core.hooksPath", ".husky")
        write(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "exit 1"}}))
        self.git("-C", str(self.repo), "add", "dev-loop.json", ".husky")
        self.git("-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "husky")
        self.marker.unlink(missing_ok=True)
        later = time.time() + 3600
        os.utime(self.repo / "tracked.txt", (later, later))
        return later

    def test_tree_hash_runs_no_index_hook_from_the_worktree(self) -> None:
        self.plant_index_hook()
        self.git("-C", str(self.repo), "diff", "HEAD", "--binary")
        self.assertTrue(self.marker.exists(), "control: the unpinned diff refreshes the index and runs the hook")
        self.marker.unlink()
        later = time.time() + 7200
        os.utime(self.repo / "tracked.txt", (later, later))
        result = self.run_hook("claude", "tree-hash.sh", {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_gate_status_runs_no_index_hook_from_the_worktree(self) -> None:
        later = self.plant_index_hook()
        self.git("-C", str(self.repo), "status", "--porcelain")
        self.assertTrue(self.marker.exists(), "control: the unpinned status refreshes the index and runs the hook")
        for harness in ("claude", "codex"):
            self.marker.unlink(missing_ok=True)
            later += 60
            os.utime(self.repo / "tracked.txt", (later, later))
            with self.subTest(harness=harness):
                self.stop(harness)
                self.assertFalse(self.marker.exists())

    def test_a_failing_required_filter_runs_the_gate(self) -> None:
        self.git("-C", str(self.repo), "config", "filter.plant.clean", "false")
        self.git("-C", str(self.repo), "config", "filter.plant.required", "true")
        write(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo gate ran; exit 1"}}))
        (self.repo / "tracked.txt").write_text("after\n")
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                self.assertIn("gate ran", json.loads(self.stop(harness).stdout)["reason"])

    def test_a_failing_status_runs_the_gate(self) -> None:
        write(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo gate ran; exit 1"}}))
        (self.repo / ".git" / "index").write_bytes(b"not an index")
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                self.assertIn("gate ran", json.loads(self.stop(harness).stdout)["reason"])

    def test_the_pin_stops_a_promisor_lazy_fetch(self) -> None:
        self.env.pop("GIT_NO_LAZY_FETCH", None)
        sub = self.root / "sub"
        self.git("init", "-q", "--bare", str(sub))
        for key, value in [("extensions.partialClone", "origin"), ("remote.origin.promisor", "true"),
                           ("remote.origin.url", str(self.root / "nowhere")),
                           ("remote.origin.uploadpack", f"sh -c 'echo x >> {self.marker}; exit 1'")]:
            self.git("-C", str(sub), "config", key, value)
        tree = subprocess.run(["git", "-C", str(sub), "mktree"], env=self.env, check=True, capture_output=True,
                              text=True, input="").stdout.strip()
        commit = subprocess.run(["git", "-C", str(sub), "-c", "user.name=T", "-c", "user.email=t@example.com",
                                 "commit-tree", "-m", "x", tree], env=self.env, check=True, capture_output=True,
                                text=True, input="").stdout.strip()
        (sub / "objects" / commit[:2] / commit[2:]).unlink()
        read = f'git -C "{sub}" rev-parse -q --verify {commit}^{{commit}}'
        subprocess.run(["sh", "-c", read], env=self.env, check=False, capture_output=True)
        self.assertTrue(self.marker.exists(), "control: the unpinned read of a missing object runs the lazy fetch")
        self.marker.unlink()
        for harness, hooks in HookGitPinTests.HOOKS.items():
            with self.subTest(harness=harness):
                subprocess.run(["sh", "-c", f'. "{hooks}/git-pin.sh"; {read}'], env=self.env, check=False,
                               capture_output=True)
                self.assertFalse(self.marker.exists())

    def test_the_pin_stops_signature_verification_in_log(self) -> None:
        gpg = self.root / "gpg.sh"
        write(gpg, f'#!/bin/sh\ncat >/dev/null\ncase "$*" in *--verify*) echo x >> {self.marker} ;; esac\n'
                   'printf "\\n[GNUPG:] SIG_CREATED \\n" >&2\necho "-----BEGIN PGP SIGNATURE-----"\necho x\necho "-----END PGP SIGNATURE-----"\n')
        os.chmod(gpg, 0o755)
        self.git("-C", str(self.repo), "config", "gpg.program", str(gpg))
        (self.repo / "tracked.txt").write_text("signed\n")
        self.git("-C", str(self.repo), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qa", "-S", "-m", "signed")
        self.git("-C", str(self.repo), "config", "log.showSignature", "true")
        self.git("-C", str(self.repo), "log", "-1", "--format=%H")
        self.assertTrue(self.marker.exists(), "control: the unpinned log verifies the signature")
        self.marker.unlink()
        for harness, hooks in HookGitPinTests.HOOKS.items():
            with self.subTest(harness=harness):
                subprocess.run(["sh", "-c", f'. "{hooks}/git-pin.sh"; git log -1 -p --format=%H'], cwd=self.repo,
                               env=self.env, check=True, capture_output=True)
                self.assertFalse(self.marker.exists())


class BoardHookLaunchTests(unittest.TestCase):
    """D1, D12, O1: how board.sh and gate.sh start `devloop.py board`. Hooks run from a kit layout copy;
    each repo holds a fake devloop.py that waits until the BOARD_HOLD file exists (#289), then logs its label, cwd and stdin."""

    FAKE = (
        "#!/usr/bin/env python3\nimport json, os, sys, time\ndata = sys.stdin.read()\n"
        "while os.environ.get('BOARD_HOLD') and not os.path.exists(os.environ['BOARD_HOLD']):\n"
        "    if not os.path.isdir(os.path.dirname(os.environ['BOARD_HOLD'])):\n        sys.exit()\n    time.sleep(0.05)\n"
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
            for script in (entry for entry in source.iterdir() if entry.is_file()):
                shutil.copy2(script, target / script.name)
        return path

    def start(self, repo: Path, harness: str, script: str, args: list[str], cwd: Path, hold: Path | None = None,
              log: Path | None = None):
        payload = {"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False}
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "BOARD_TEST_LOG": str(log or self.log), "BOARD_HOLD": str(hold or "")}
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
        hold = self.root / "hold"
        self.addCleanup(hold.touch)
        # #289: the four boards wait on the hold file, each with its own log: a hook that waits for its board never returns.
        runs = []
        for harness, script, args in [("claude", "board.sh", ["prompt"]), ("claude", "gate.sh", []),
                                      ("codex", "board.sh", ["prompt"]), ("codex", "gate.sh", [])]:
            log = self.root / f"board-{harness}-{script}.jsonl"
            proc, payload = self.start(repo, harness, script, args, repo, hold, log)
            out, _ = proc.communicate(json.dumps(payload), timeout=30)
            runs.append((harness, script, proc, out, log.exists(), log))
        hold.touch()
        for harness, script, proc, out, logged, log in runs:
            with self.subTest(harness=harness, script=script):
                calls = self.calls(1, 30, log)
                self.assertFalse(logged)
                self.assertEqual((proc.returncode, out), (0, ""))
                self.assertEqual([c["label"] for c in calls], ["A"])

    def test_d12_claude_hooks_run_the_project_cli_not_the_cwd_one(self) -> None:
        repo = self.repo(self.root / "project", "A")
        nested = self.repo(repo / "nested", "B")
        for script, args in [("board.sh", ["prompt"]), ("gate.sh", [])]:
            with self.subTest(script=script):
                self.log.unlink(missing_ok=True)
                proc, payload = self.start(repo, "claude", script, args, nested)
                proc.communicate(json.dumps(payload), timeout=30)
                calls = self.calls(1, 6)
                self.assertEqual([c["label"] for c in calls], ["A"])
                self.assertEqual((calls[0]["cwd"], json.loads(calls[0]["stdin"])["cwd"]), (str(nested), str(nested)))
