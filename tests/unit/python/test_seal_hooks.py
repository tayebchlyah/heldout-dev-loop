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
from test_devloop import git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
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

    def hook(self, harness: str, script: str, payload: dict | str, *args: str,
             project: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "DEV_LOOP_NOTES")}
        if harness == "claude":
            env["CLAUDE_PROJECT_DIR"] = str(project or self.repo)
        text = payload if isinstance(payload, str) else json.dumps({"cwd": str(self.repo), **payload})
        return subprocess.run([SHELL[harness], str(self.repo / ".dev-loop/hooks" / harness / script), *args],
                              input=text, capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)

    def commit(self, path: str, text: str) -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")
        return git(self.repo, "rev-parse", "HEAD").strip()

    def head_file(self, agent_id: str, repo: Path | None = None) -> Path:
        git_dir = git(repo or self.repo, "rev-parse", "--absolute-git-dir").strip()
        return Path(git_dir) / "dev-loop-subagents" / f"{agent_id}.head"

    def reason(self, harness: str, mode: str = "turn", **payload) -> str:
        result = self.hook(harness, "seal.sh", {"hook_event_name": "Stop", "stop_hook_active": False, **payload}, mode)
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], "block")
        return out["reason"]

    def test_o14_turn_and_stop_block_on_an_unsealed_commit(self) -> None:
        sha = self.commit(SUITE, "def test_flow():\n    pass\n")
        for harness in SHELL:
            for name, mode, payload, blocks in [
                ("turn", "turn", {"hook_event_name": "Stop", "stop_hook_active": False}, True),
                ("stop", "stop", {"hook_event_name": "SubagentStop", "stop_hook_active": False,
                                  "agent_id": "v", "agent_type": "implementer"}, True),
                ("stop_hook_active", "turn", {"hook_event_name": "Stop", "stop_hook_active": True}, False),
                ("not json", "turn", "{not json", False),
            ]:
                with self.subTest(harness=harness, case=name):
                    result = self.hook(harness, "seal.sh", payload, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if blocks:
                        out = json.loads(result.stdout)
                        self.assertEqual(out["decision"], "block")
                        self.assertIn(f"unsealed {sha} {SUITE}", out["reason"])
                    else:
                        self.assertEqual(result.stdout, "")
        (self.repo / "dev-loop.json").unlink()
        for harness in SHELL:
            with self.subTest(harness=harness, case="no dev-loop.json"):
                result = self.hook(harness, "seal.sh", {"hook_event_name": "Stop", "stop_hook_active": False}, "turn")
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_a_kit_change_names_the_upgrade_remedy(self) -> None:
        put(self.repo / ".dev-loop/MANIFEST.json", '{"version": "0.0.0", "files": {}}')
        put(self.repo / ".dev-loop/hooks/claude/x.sh", "exit 0\n")
        git(self.repo, "add", "-f", ".dev-loop/MANIFEST.json", ".dev-loop/hooks/claude/x.sh")
        git(self.repo, "commit", "-q", "-m", "kit edit")
        for harness in SHELL:
            with self.subTest(harness=harness):
                reason = self.reason(harness)
                self.assertIn("kit-changed - .dev-loop/hooks/claude/x.sh", reason)
                self.assertIn("run upgrade", reason)

    def test_changed_after_seal_names_the_change_not_the_seal(self) -> None:
        put(self.repo / SUITE, "def test_flow():\n    pass\n")
        git(self.repo, "add", "--", SUITE)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, out)
        later = self.commit(SUITE, "def test_flow():\n    assert True\n")
        for harness in SHELL:
            with self.subTest(harness=harness):
                reason = self.reason(harness)
                self.assertIn(f"changed-after-seal {later} {SUITE}", reason)
                self.assertNotIn(out["commit"], reason)

    def test_d6_a_revert_seal_is_not_named_as_an_offender(self) -> None:
        put(self.repo / SUITE, "def test_flow():\n    pass\n")
        git(self.repo, "add", "--", SUITE)
        code, sealed = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, sealed)
        git(self.repo, "revert", "--no-commit", sealed["commit"])
        code, revert = run_cli("seal", "--feature", "x", "--message", "revert", "--reverts", sealed["commit"],
                               cwd=self.repo)
        self.assertEqual(code, 0, revert)
        later = self.commit(SUITE, "def test_flow():\n    assert True\n")
        for harness in SHELL:
            with self.subTest(harness=harness):
                reason = self.reason(harness)
                self.assertIn(f"changed-after-seal {later} {SUITE}", reason)
                self.assertNotIn(revert["commit"], reason)

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
                reason = self.reason(harness)
                self.assertIn(f"implementer {sha}", reason)
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

    def test_claude_hooks_use_the_input_cwd_not_the_project_dir(self) -> None:
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "other")
        head = git(self.repo, "rev-parse", "HEAD").strip()
        start = {"hook_event_name": "SubagentStart", "agent_id": "w", "agent_type": "implementer"}
        result = self.hook("claude", "gate-snapshot.sh", start, project=other)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertEqual(self.head_file("w").read_text().strip(), head)
        self.assertFalse(self.head_file("w", other).exists())
        sha = self.commit(SUITE, "def test_flow():\n    pass\n")
        result = self.hook("claude", "seal.sh", {"hook_event_name": "Stop", "stop_hook_active": False}, "turn",
                           project=other)
        self.assertIn(f"unsealed {sha} {SUITE}", json.loads(result.stdout)["reason"])


class GuardTests(unittest.TestCase):
    """The PreToolUse guards of the kit source, fed hook JSON on stdin."""

    def guard(self, harness: str, script: str, payload: dict | str, *args: str,
              env: dict | None = None) -> subprocess.CompletedProcess[str]:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([shutil.which(SHELL[harness]), str(ROOT / harness / "hooks" / script), *args],
                              input=text, capture_output=True, text=True, timeout=60, env=env)

    def denied(self, result: subprocess.CompletedProcess[str]) -> bool:
        if result.returncode == 2:
            return True
        return bool(result.stdout) and json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_o1_o2_controller_and_subagent_walls(self) -> None:
        main = {"cwd": "/repo"}
        implementer = {"cwd": "/repo", "agent_id": "a", "agent_type": "implementer"}
        tester = {"cwd": "/repo", "agent_id": "b", "agent_type": "spec-tester"}
        for caller, tool, tool_input, code in [
            (main, "Edit", {"file_path": f"/repo/{SUITE}"}, 2),
            (main, "Write", {"file_path": "tests/acceptance/x.py"}, 2),
            (main, "NotebookEdit", {"notebook_path": "/repo/tests/acceptance/x.ipynb"}, 2),
            (main, "Write", {"file_path": "/repo/tests/acceptance_notes/x.md"}, 0),
            (main, "Write", {"file_path": "/repo/docs/x.md", "content": "see tests/acceptance/x"}, 0),
            (main, "Read", {"file_path": f"/repo/{SUITE}"}, 0),
            (main, "Bash", {"command": f"cat {SUITE}"}, 0),
            (tester, "Write", {"file_path": f"/repo/{SUITE}"}, 0),
            (implementer, "Read", {"file_path": f"/repo/{SUITE}"}, 2),
            (implementer, "Grep", {"pattern": "x", "path": "tests/acceptance"}, 2),
            (implementer, "Glob", {"pattern": "tests/acceptance/**"}, 2),
            (implementer, "Bash", {"command": f"cat {SUITE}"}, 2),
        ]:
            with self.subTest(caller=caller.get("agent_type", "main"), tool=tool, tool_input=tool_input):
                result = self.guard("claude", "deny-acceptance-to-subagents.sh",
                                    {**caller, "tool_name": tool, "tool_input": tool_input})
                self.assertEqual(result.returncode, code, result.stderr)

    def test_o5_each_guard_fails_closed_on_bad_input(self) -> None:
        guards = [("claude", "deny-acceptance-to-subagents.sh", ()), ("claude", "deny-path.sh", ("src/",)),
                  ("codex", "deny-path.sh", ("src/",)), ("claude", "git-guard.sh", ()), ("codex", "git-guard.sh", ()),
                  ("codex", "acceptance-wall.sh", ())]
        no_jq = tempfile.TemporaryDirectory()
        self.addCleanup(no_jq.cleanup)
        os.symlink(shutil.which("cat"), Path(no_jq.name) / "cat")
        valid = json.dumps({"cwd": "/repo", "tool_input": {"command": "ls"}})
        for harness, script, args in guards:
            for name, text, env in [("not json", "not json", None), ("empty", "", None),
                                    ("no jq", valid, {"PATH": no_jq.name})]:
                with self.subTest(harness=harness, script=script, input=name):
                    result = self.guard(harness, script, text, *args, env=env)
                    self.assertEqual(result.returncode, 2)
                    self.assertRegex(result.stderr, r"^Blocked: guard error: \S.*\n$")
            with self.subTest(harness=harness, script=script, input="another tool"):
                result = self.guard(harness, script, {"cwd": "/repo", "tool_name": "TaskList", "tool_input": {}}, *args)
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_o13_only_the_spec_tester_seals(self) -> None:
        cli = ".agents/skills/dev-loop/scripts/devloop.py"
        seal = f"python3 {cli} seal --feature x --message y"
        trailer = f'git commit -m "x\n\nDev-Loop-Seal: {"ab" * 32}"'
        for harness, tester in [("claude", "spec-tester"), ("codex", "spec_tester")]:
            main = {"cwd": "/repo"}
            implementer = {"cwd": "/repo", "agent_id": "a", "agent_type": "implementer"}
            spec_tester = {"cwd": "/repo", "agent_id": "b", "agent_type": tester}
            for caller, command, deny in [
                (main, seal, True),
                (implementer, seal, True),
                (main, "devloop.py 'seal' --feature x --message y", True),
                (main, "cd /repo && devloop.py \"seal\" --feature x", True),
                (main, trailer, True),
                (main, "git commit -m 'Add tests' -m 'Dev-Loop-Seal: 0123456789abcdef'", True),
                (implementer, "git commit -m x -m 'Dev-Loop-Seal:abc'", True),
                # D10: a trailer typed with = or --trailer, the patch trailer, git interpret-trailers.
                (main, 'git commit -m x --trailer "Dev-Loop-Seal=abc"', True),
                (main, "git commit -m x --trailer 'Dev-Loop-Seal-Patch: abc'", True),
                (implementer, "git commit -m x --trailer Dev-Loop-Seal-Patch=abc", True),
                (main, "git commit -m x -m 'Dev-Loop-Seal = abc'", True),
                (main, 'git commit -m x --trailer "Dev-Loop-Seal"', True),
                (main, "git -c trailer.s.key=Dev-Loop-Seal interpret-trailers --trailer s=abc msg.txt", True),
                (main, 'git commit -m "Explain the Dev-Loop-Seal-Patch trailer"', False),
                # D11: a quoted path, interpreter flags, env, python3.N, uv run, a variable.
                (main, f"python3 '{cli}' seal --feature x --message y", True),
                (main, f"python3 -I {cli} seal --feature x --message y", True),
                (main, f"env python3 {cli} seal --feature x --message y", True),
                (implementer, f"env FOO=1 python3 {cli} seal --feature x --message y", True),
                (main, f"python3.12 {cli} seal --feature x --message y", True),
                (main, f"uv run python {cli} seal --feature x --message y", True),
                (implementer, f"python3 -I {cli} seal --feature x --message y", True),
                # D11: only a run counts, within one segment: a read-only tool that names both passes.
                (main, "grep -n seal skill/scripts/devloop.py", False),
                (main, 'rg "devloop.py seal" skill/', False),
                (main, "sed -n 1,20p skill/scripts/devloop.py | grep seal", False),
                (main, 'grep -rn "Dev-Loop-Seal" skill/', False),
                (main, 'grep -rn "devloop.py seal" skill/', False),
                (main, 'git commit -m "Document devloop.py seal"', False),
                (main, 'git commit -m "Name the Dev-Loop-Seal trailer"', False),
                (main, "python3 devloop.py seal-check", False),
                (implementer, "devloop.py seal-check --uncommitted", False),
                (spec_tester, seal, False),
                (spec_tester, "git commit -m x", True),
                (spec_tester, "git -C /r --no-pager commit", True),
                (spec_tester, "git add tests && git commit -m x", True),
                (spec_tester, "cat <<'EOF'\ngit commit\nEOF", False),
                (spec_tester, "git status && git log", False),
                (spec_tester, "git revert --no-commit abc", False),
            ]:
                with self.subTest(harness=harness, caller=caller.get("agent_type", "main"), command=command):
                    result = self.guard(harness, "git-guard.sh", {**caller, "tool_input": {"command": command}})
                    self.assertEqual(self.denied(result), deny, result.stderr)
                    if caller is spec_tester and deny:
                        self.assertEqual(result.returncode, 2)
                        self.assertIn("devloop.py seal", result.stderr)


class SpecTesterSourceWallTests(unittest.TestCase):
    """The Claude settings hook walls src/ from spec-tester too: its frontmatter hook did not fire (canary)."""

    def test_spec_tester_cannot_reach_a_source_folder_by_any_route(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "repo"
        put(repo / "src" / "x.py", "X = 1\n")
        put(repo / "tests" / "test_mapping.py", "def test_x():\n    pass\n")
        git(repo, "init", "-q")
        tester = {"agent_id": "b", "agent_type": "spec-tester"}
        for cwd, tool, tool_input, code in [
            (repo, "Read", {"file_path": f"{repo}/src/x.py"}, 2),
            (repo, "Bash", {"command": "cat src/x.py"}, 2),
            (repo, "Bash", {"command": "cd src && cat x.py"}, 2),
            (repo / "src", "Bash", {"command": "cat x.py"}, 2),
            (repo / "src", "Bash", {"command": "cd src && cat x.py"}, 2),
            (repo, "Bash", {"command": f"git -C {repo} show HEAD:src/x.py"}, 2),
            (repo, "Bash", {"command": "python3 -c \"open('src/x.py')\""}, 2),
            (repo, "Bash", {"command": "ls src*"}, 2),
            (repo, "Grep", {"pattern": "X", "path": "app"}, 2),
            (repo, "Write", {"file_path": f"{repo}/{SUITE}", "content": "from src.x import X\n"}, 0),
            (repo, "Bash", {"command": "cat tests/test_mapping.py"}, 0),
            (repo, "Bash", {"command": "ls application/"}, 0),
        ]:
            with self.subTest(cwd=cwd, tool=tool, tool_input=tool_input):
                payload = {**tester, "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}
                result = subprocess.run(["bash", str(ROOT / "claude/hooks/deny-acceptance-to-subagents.sh")],
                                        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1 if code else 0, result.stderr)


class CodexWallTests(unittest.TestCase):
    """Codex acceptance-wall.sh, fed hook JSON on stdin."""

    def wall(self, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(ROOT / "codex/hooks/acceptance-wall.sh")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)

    def test_o3_o4_walls_by_agent_type(self) -> None:
        def patch(*headers: str, body: str = "+x") -> str:
            return "\n".join(["*** Begin Patch", *headers, body, "*** End Patch"])

        main: dict = {}
        implementer = {"agent_id": "a", "agent_type": "implementer"}
        tester = {"agent_id": "b", "agent_type": "spec_tester"}
        for caller, cwd, tool, tool_input, deny in [
            (main, "/repo", "apply_patch", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch",
             {"command": patch("*** Add File: src/y.py", body="+# see tests/acceptance/x.py")}, False),
            (main, "/repo", "apply_patch", {"patch": patch("*** Delete File: /repo/tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch",
             {"command": patch("*** Update File: src/y.py", "*** Move to: tests/acceptance/y.py")}, True),
            (main, "/repo/tests", "apply_patch", {"command": patch("*** Add File: acceptance/x.py")}, True),
            (main, "/repo", "Edit", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch", {"command": patch("*** Add File: tests/acceptance_notes/x.md")}, False),
            (main, "/repo", "Bash", {"command": f"cat {SUITE}"}, False),
            (implementer, "/repo", "Bash", {"command": f"cat {SUITE}"}, True),
            (implementer, "/repo/tests", "Bash", {"command": "cat acceptance/x.py"}, True),
            (implementer, "/repo", "Bash", {"command": "cat src/y.py"}, False),
            (implementer, "/repo", "apply_patch", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (implementer, "/repo", "apply_patch",
             {"command": patch("*** Add File: docs/y.md", body="+never read tests/acceptance")}, False),
            (tester, "/repo", "Bash", {"command": "cat src/y.py"}, True),
            (tester, "/repo/src", "Bash", {"command": "cat y.py"}, True),
            (tester, "/repo", "Bash", {"command": f"cat {SUITE} tests/test_mapping.py"}, False),
            (tester, "/repo", "apply_patch", {"command": patch("*** Add File: tests/acceptance/x.py")}, False),
            (tester, "/repo", "apply_patch", {"command": patch("*** Update File: app/main.py")}, True),
        ]:
            with self.subTest(caller=caller.get("agent_type", "main"), cwd=cwd, tool=tool, tool_input=tool_input):
                result = self.wall({**caller, "cwd": cwd, "tool_name": tool, "tool_input": tool_input})
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1 if deny else 0, result.stderr)

    def test_a_real_repo_root_and_a_repo_under_a_source_folder_name(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "src" / "repo"
        put(repo / "tests" / "README.md")
        git(repo, "init", "-q")
        tester = {"agent_id": "b", "agent_type": "spec_tester", "tool_name": "Bash"}
        implementer = {"agent_id": "a", "agent_type": "implementer", "tool_name": "Bash"}
        for caller, cwd, command, code in [
            (tester, repo, "cat README.md", 0), (tester, repo / "tests", "ls", 0),
            (implementer, repo / "tests", "cat acceptance/x.py", 2),
            (implementer, repo, "ls tests/acceptance | head", 2), (implementer, repo, "rg foo tests/acceptance -n", 2),
            (implementer, repo, "pytest tests/acceptance -q", 2), (implementer, repo, "ls tests/acceptance*", 2),
            (implementer, None, "grep -r foo tests/acceptance --include=x", 2),
            (tester, repo, "find src -name x", 2), (tester, repo, "ls src*", 2),
        ]:
            with self.subTest(caller=caller["agent_type"], cwd=cwd, command=command):
                where = {} if cwd is None else {"cwd": str(cwd)}
                result = self.wall({**caller, **where, "tool_input": {"command": command}})
                self.assertEqual(result.returncode, code, result.stderr)


def patch(*headers: str, body: str = "+x") -> str:
    return "\n".join(["*** Begin Patch", *headers, body, "*** End Patch"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
