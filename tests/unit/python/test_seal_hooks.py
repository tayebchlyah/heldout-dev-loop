#!/usr/bin/env python3
"""Tests for seal.sh (both harnesses) and the `.head` file of Claude gate-snapshot.sh, fed hook JSON on stdin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from test_devloop import git, put  # noqa: E402  (also sets the core.fsmonitor override)
from test_seal import L1, L2, L3, PROTECTED, SUITE, claude_line, codex_line  # noqa: E402

SHELL = {"claude": "bash", "codex": "sh"}
TRANSCRIPT_LINE = {"claude": claude_line, "codex": codex_line}


class SealHookTests(unittest.TestCase):
    """A repo with the kit layout copied in (board.sh a no-op), `main` holding the base, checked out on `feat/x`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        put(self.repo / "README.md", "readme\n")
        put(self.repo / "dev-loop.json", '{"version": "0.0.0"}\n')
        put(self.repo / ".gitignore", ".agents/\n.dev-loop/\n")
        put(self.repo / PROTECTED, f"def test_total():\n    {L1}\n    {L2}\n    {L3}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "feat/x")
        shutil.copytree(ROOT / "skill" / "scripts", self.repo / ".agents/skills/dev-loop/scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*.py"))
        for harness in SHELL:
            target = self.repo / ".dev-loop/hooks" / harness
            shutil.copytree(ROOT / harness / "hooks", target)
            put(target / "board.sh", "#!/bin/sh\ncat >/dev/null\nexit 0\n")

    def hook(self, harness: str, script: str, payload: dict | str, *args: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "DEV_LOOP_NOTES")}
        if harness == "claude":
            env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        text = payload if isinstance(payload, str) else json.dumps({"cwd": str(self.repo), **payload})
        return subprocess.run([SHELL[harness], str(self.repo / ".dev-loop/hooks" / harness / script), *args],
                              input=text, capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)

    def commit(self, path: str, text: str) -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")
        return git(self.repo, "rev-parse", "HEAD").strip()

    def head_file(self, agent_id: str) -> Path:
        return Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop-subagents" / f"{agent_id}.head"

    def test_o14_turn_blocks_on_an_unsealed_commit(self) -> None:
        sha = self.commit(SUITE, "def test_flow():\n    pass\n")
        for harness in SHELL:
            for name, payload, blocks in [
                ("stop", {"hook_event_name": "Stop", "stop_hook_active": False}, True),
                ("stop_hook_active", {"hook_event_name": "Stop", "stop_hook_active": True}, False),
                ("not json", "{not json", False),
            ]:
                with self.subTest(harness=harness, case=name):
                    result = self.hook(harness, "seal.sh", payload, "turn")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if blocks:
                        out = json.loads(result.stdout)
                        self.assertEqual(out["decision"], "block")
                        self.assertIn(f"unsealed {sha[:7]} {SUITE}", out["reason"])
                    else:
                        self.assertEqual(result.stdout, "")

    def test_stop_records_taint_and_only_turn_blocks_on_it(self) -> None:
        for harness in SHELL:
            with self.subTest(harness=harness):
                agent = f"a-{harness}"
                put(self.head_file(agent), git(self.repo, "rev-parse", "HEAD"))
                sha = self.commit(f"src/{harness}.py", f"def {harness}():\n    return 1\n")
                transcript = self.tmp / f"{harness}.jsonl"
                line = TRANSCRIPT_LINE[harness]
                transcript.write_text("".join(json.dumps(item) + "\n" for item in [
                    line(f"[feat/x {sha[:7]}] change\n 1 file changed"), line(f"{L1}\n{L2}\n")]))
                stop = {"hook_event_name": "SubagentStop", "stop_hook_active": False, "agent_id": agent,
                        "agent_type": "implementer", "agent_transcript_path": str(transcript)}
                result = self.hook(harness, "seal.sh", stop, "stop")
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                taint = json.loads((self.head_file(agent).parent.parent / "dev-loop" / "taint.json").read_text())
                self.assertIn(agent, [r["agent_id"] for r in taint["records"]])
                result = self.hook(harness, "seal.sh", {"hook_event_name": "Stop", "stop_hook_active": False}, "turn")
                reason = json.loads(result.stdout)["reason"]
                self.assertIn(f"implementer {sha[:7]}", reason)
                self.assertIn("git revert", reason)
                git(self.repo, "revert", "--no-edit", sha)

    def test_subagent_start_writes_the_head_file(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD").strip()
        for harness, script, args in [("claude", "gate-snapshot.sh", []), ("codex", "seal.sh", ["start"])]:
            with self.subTest(harness=harness):
                agent = f"s-{harness}"
                result = self.hook(harness, script, {"hook_event_name": "SubagentStart", "agent_id": agent,
                                                     "agent_type": "implementer"}, *args)
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                self.assertEqual(self.head_file(agent).read_text().strip(), head)


if __name__ == "__main__":
    unittest.main(verbosity=2)
