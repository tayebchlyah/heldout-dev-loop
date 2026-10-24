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


class AbsolutePathWallTests(unittest.TestCase):
    """Walled paths match relative to the repo: a repo under a folder named src is not walled whole."""

    def test_absolute_paths_in_a_repo_under_a_source_folder_name(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "src" / "repo"
        put(repo / "README.md")
        git(repo, "init", "-q")
        tops = {str(repo), str(repo.resolve())}  # /var/... and /private/var/... on macOS
        claude = ROOT / "claude/hooks/deny-acceptance-to-subagents.sh"
        codex = ROOT / "codex/hooks/acceptance-wall.sh"

        def patch(header: str) -> str:
            return "\n".join(["*** Begin Patch", header, "+x", "*** End Patch"])

        rows = []
        for top in sorted(tops):
            rows += [
                (claude, "spec-tester", "Read", {"file_path": f"{top}/tests/acceptance/x.py"}, 0),
                (claude, "spec-tester", "Read", {"file_path": f"{top}/src/y.py"}, 2),
                (claude, "spec-tester", "Bash", {"command": f"cat {top}/README.md"}, 0),
                (claude, "spec-tester", "Bash", {"command": f"git -C {top} log"}, 0),
                (claude, "implementer", "Read", {"file_path": f"{top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "Bash", {"command": f"cat -n{top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "Bash", {"command": f"cat -n{top}/README.md"}, 0),
            ]
            for script, agent in [(claude, "spec-tester"), (codex, "spec_tester")]:
                rows += [(script, agent, "Bash", {"command": command}, code) for command, code in [
                    (f"gcc -I{top}/src x.c", 2), (f"ls -d{top}/src", 2), (f"cat {top}2/src/y.py", 2),
                    (f"cat {top}/../repo/src/y.py", 2), (f"cat {top}/docs/../src/y.py", 2),
                    (f"cat {top}/README.md", 0)]]
            rows += [
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/tests/acceptance/x.py"}, 0),
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/src/y.py"}, 2),
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/README.md"}, 0),
                (codex, "spec_tester", "apply_patch", {"command": patch(f"*** Add File: {top}/tests/acceptance/x.py")}, 0),
                (codex, "spec_tester", "apply_patch", {"command": patch(f"*** Update File: {top}/src/y.py")}, 2),
                (codex, "implementer", "Bash", {"command": f"cat {top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "apply_patch",
                 {"command": patch(f"*** Update File: {top}/tests/acceptance/x.py")}, 2),
                (codex, "implementer", "apply_patch", {"command": patch(f"*** Update File: {top}/README.md")}, 0),
            ]
        for script, agent, tool, tool_input, code in rows:
            with self.subTest(script=script.name, agent=agent, tool=tool, tool_input=tool_input):
                payload = {"agent_id": "b", "agent_type": agent, "cwd": str(repo), "tool_name": tool,
                           "tool_input": tool_input}
                result = subprocess.run([SHELL[script.parent.parent.name], str(script)], input=json.dumps(payload),
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, code, result.stderr)


class CanaryScriptTests(unittest.TestCase):
    """Static only: the live run needs both harnesses and a login, so the controller runs it by hand."""
    script = ROOT / "scripts" / "canary.sh"

    def test_script_is_executable_and_parses(self):
        self.assertTrue(os.access(self.script, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(self.script)]).returncode, 0)

    def test_help_prints_usage_without_a_harness(self):
        bare = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bare, True)
        for name in ("dirname", "cat"):  # the script may need these, never claude or codex
            os.symlink(shutil.which(name), os.path.join(bare, name))
        result = subprocess.run([shutil.which("bash"), str(self.script), "--help"], capture_output=True, text=True,
                                env={"PATH": bare})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_each_route_has_its_own_token_under_system_bash(self):
        # /bin/bash is 3.2 on macOS: no associative arrays, or every route would share one token.
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-tokens", f"{work}/repo"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split() for line in result.stdout.splitlines()]
        self.assertEqual(len({tok for _, _, tok in lines}), len(lines))
        self.assertGreaterEqual(len(lines), 14)
        for kind, route, token in lines:
            path = Path(work, "repo", "tests/acceptance" if kind == "acc" else "src",
                        ("test_canary_" if kind == "acc" else "canary_") + route.replace("-", "_") + ".py")
            self.assertIn(token, path.read_text())

    def test_scratch_repo_init_succeeds(self):
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-init", f"{work}/repo"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(work, "repo", "dev-loop.json").is_file())
        self.assertEqual(result.stderr, "")

    def test_a_read_is_detected_when_the_agent_run_is_tainted(self):
        # An audit record keeps at most 3 lines, so one record may hold another route's token, or none.
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        transcripts, empty = work / "t", work / "empty"
        empty.mkdir()
        put(transcripts / "agent.jsonl", '{"out": "TOKREAD line 1"}\n{"out": "TOKOTHER line 1"}\n')
        tainted, foreign, absent = work / "taint.json", work / "foreign.json", work / "absent.json"
        tainted.write_text(json.dumps({"records": [{"agent_type": "spec-tester", "set": "source",
                                                    "lines": ["TOKOTHER line 1"]}]}))
        foreign.write_text(json.dumps({"records": [{"agent_type": "implementer", "set": "acceptance",
                                                    "lines": ["TOKREAD line 1"]}]}))
        for where, taint, token, verdict in [
            (transcripts, tainted, "TOKREAD", "read-detected"),
            (transcripts, tainted, "TOKOTHER", "read-detected"),
            (transcripts, foreign, "TOKREAD", "LEAK"),
            (transcripts, absent, "TOKREAD", "LEAK"),
            (transcripts, tainted, "TOKNEVER", "blocked"),
            (empty, tainted, "TOKREAD", "ERROR"),
        ]:
            with self.subTest(where=where.name, taint=taint.name, token=token):
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-verdict", str(where), str(taint),
                                         "spec-tester", token], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, verdict + "\n"), result.stderr)

    def test_main_session_denial_text_of_either_wall_reads_as_blocked(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        for name, text, verdict in [
            ("claude", "Blocked: held-out suite. Only spec-tester writes tests/acceptance. The main session may read it.", "blocked"),
            ("claude-path", "Blocked: tests/acceptance is outside this agent's territory.", "blocked"),
            ("codex", "Blocked: this agent cannot access tests/acceptance", "blocked"),
            ("none", "edit applied", "ERROR"),
        ]:
            with self.subTest(name=name):
                put(work / name / "main.jsonl", json.dumps({"out": text}) + "\n")
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-main", str(work / name)],
                                        capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, verdict + "\n"), result.stderr)

    def test_unknown_flag_is_a_usage_error(self):
        result = subprocess.run(["bash", str(self.script), "--nope"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


KIT_AGENTS = sorted([(".claude/agents", p.name) for p in (ROOT / "claude/agents").glob("*.md")]
                    + [(".codex/agents", p.name) for p in (ROOT / "codex/agents").glob("*.toml")])


def patch(*headers: str, body: str = "+x") -> str:
    return "\n".join(["*** Begin Patch", *headers, body, "*** End Patch"])


class KitWallTests(unittest.TestCase):
    """No caller edits an installed kit file through a file tool: kit files change only by `upgrade`."""

    def run_hook(self, script: str, payload: dict) -> subprocess.CompletedProcess[str]:
        harness = "codex" if script == "acceptance-wall.sh" else "claude"
        return subprocess.run([SHELL[harness], str(ROOT / harness / "hooks" / script)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)

    def test_file_tools_cannot_change_a_kit_file(self) -> None:
        kit = [".agents/skills/dev-loop/scripts/seal/check.py", ".agents/skills/dev-loop/SKILL.md",
               ".dev-loop/hooks/claude/git-guard.sh", ".dev-loop/MANIFEST.json", ".claude/skills/dev-loop/SKILL.md",
               ".codex/rules/dev-loop.rules", *[f"{folder}/{name}" for folder, name in KIT_AGENTS]]
        open_paths = ["skill/scripts/seal/check.py", "claude/hooks/git-guard.sh", ".claude/agents/my-reviewer.md",
                      ".codex/agents/mine.toml", ".codex/rules/mine.rules", "docs/dev-loop/x.md",
                      "dev-loop.json", "../dev-loop-worktrees/x/README.md"]
        callers = [{}, {"agent_id": "a", "agent_type": "implementer"}]
        rows = []
        for path, deny in [(p, True) for p in kit] + [(p, False) for p in open_paths]:
            for caller in callers + [{"agent_id": "b", "agent_type": "spec-tester"}]:
                rows += [("deny-acceptance-to-subagents.sh", caller, "Edit", {"file_path": f"/repo/{path}"}, deny),
                         ("deny-acceptance-to-subagents.sh", caller, "Write", {"file_path": path}, deny)]
            for caller in callers + [{"agent_id": "b", "agent_type": "spec_tester"}]:
                rows += [("acceptance-wall.sh", caller, "apply_patch",
                          {"command": patch(f"*** Update File: {path}")}, deny)]
        rows += [
            ("deny-acceptance-to-subagents.sh", {}, "NotebookEdit",
             {"notebook_path": "/repo/.agents/skills/dev-loop/x.ipynb"}, True),
            ("deny-acceptance-to-subagents.sh", {}, "Edit", {"file_path": "/repo/.Dev-Loop//hooks/./claude/gate.sh"}, True),
            ("deny-acceptance-to-subagents.sh", {}, "Write",
             {"file_path": "/repo/docs/x.md", "content": "edit .dev-loop/hooks/claude/gate.sh"}, False),
            ("deny-acceptance-to-subagents.sh", {}, "Read", {"file_path": "/repo/.dev-loop/MANIFEST.json"}, False),
            ("deny-acceptance-to-subagents.sh", {"agent_id": "a", "agent_type": "implementer"}, "Read",
             {"file_path": "/repo/.agents/skills/dev-loop/SKILL.md"}, False),
            ("acceptance-wall.sh", {}, "apply_patch",
             {"command": patch("*** Update File: src/y.py", "*** Move to: .dev-loop/hooks/codex/gate.sh")}, True),
            ("acceptance-wall.sh", {}, "apply_patch",
             {"command": patch("*** Add File: docs/y.md", body="+see .dev-loop/hooks/codex/gate.sh")}, False),
            ("acceptance-wall.sh", {}, "Bash", {"command": "cat .dev-loop/hooks/codex/gate.sh"}, False),
            ("deny-acceptance-to-subagents.sh", {}, "Write", {"file_path": "../other/.dev-loop/x"}, False),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: .codex/rules/dev-loop.rules\r")}, True),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: .codex/rules/dev-loop.rules ")}, True),
        ]
        for caller in callers:
            rows += [("deny-acceptance-to-subagents.sh", caller, "Write", {"file_path": "/home/u/.claude/agents/implementer.md"}, False),
                     ("acceptance-wall.sh", caller, "apply_patch",
                      {"command": patch("*** Update File: /home/u/.codex/agents/implementer.toml")}, False)]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        git(repo, "init", "-q")
        (repo / "tests").mkdir()
        rows = [(*row, "/repo") for row in rows] + [
            ("deny-acceptance-to-subagents.sh", {}, "Write", {"file_path": "../.dev-loop/hooks/x.sh"}, True, str(repo / "tests")),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: ../.dev-loop/hooks/x.sh")}, True,
             str(repo / "tests")),
        ]
        for script, caller, tool, tool_input, deny, cwd in rows:
            with self.subTest(script=script, caller=caller.get("agent_type", "main"), tool=tool, tool_input=tool_input,
                              cwd=cwd):
                result = self.run_hook(script, {**caller, "cwd": cwd, "tool_name": tool, "tool_input": tool_input})
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)
                if deny:
                    self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
                    self.assertIn("upgrade", result.stderr)


class CdWallTests(unittest.TestCase):
    """A `cd` earlier in the same command moves the folder the walls resolve a relative path against."""

    def test_a_cd_in_the_command_cannot_hide_a_walled_path(self) -> None:
        claude = ("bash", ROOT / "claude/hooks/deny-acceptance-to-subagents.sh")
        codex = ("sh", ROOT / "codex/hooks/acceptance-wall.sh")
        rows = []
        for (shell, script), tester in [(claude, "spec-tester"), (codex, "spec_tester")]:
            implementer = {"agent_id": "a", "agent_type": "implementer"}
            spec_tester = {"agent_id": "b", "agent_type": tester}
            rows += [(shell, script, implementer, command, deny) for command, deny in [
                ("cd tests && cat acceptance/x.py", True),
                ("cd tests; cd acceptance && ls", True),
                ("cd tests && cd acceptance && cat x.py", True),
                ("cd /repo/tests && cat acceptance/x.py", True),
                ("cd ./docs/../tests && cat acceptance/x.py", True),
                ("(cd tests && ls acceptance)", True),
                ("bash -c 'cd tests && cat acceptance/x.py'", True),
                ("pushd tests && cat acceptance/x.py", True),
                ("cd -P tests && cat acceptance/x.py", True),
                ("cd tests && cat unit/x.py", False),
                ("cd docs && ls", False),
                ("cd tests/unit && cat ../README.md", False),
                ("{ cd tests; cat acceptance/x.py; }", True),
                ("if true; then cd tests; cat acceptance/x.py; fi", True),
                ("for f in a; do cd tests; cat acceptance/x.py; done", True),
                ("echo `cd tests && cat acceptance/x.py`", True),
                ("cd -- tests && cat acceptance/x.py", True),
                ("builtin cd tests && cat acceptance/x.py", True),
                ("command cd tests && cat acceptance/x.py", True),
                ("cat acceptance/notes.md; cd tests && make", False),
            ]]
            rows += [(shell, script, spec_tester, command, deny) for command, deny in [
                ("cd lib/.. && cd backend && cat main.py", True),
                ("cd tests && cat ../src/x.py", True),
                ("cd tests && cat acceptance/x.py", False),
            ]]
        rows.append(("sh", codex[1], {}, "cd tests && cat acceptance/x.py", False))
        for shell, script, caller, command, deny in rows:
            with self.subTest(script=script.name, caller=caller.get("agent_type", "main"), command=command):
                payload = {**caller, "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": command}}
                result = subprocess.run([shell, str(script)], input=json.dumps(payload), capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)


class CodexTrustWarningTests(unittest.TestCase):
    """Codex skips an untrusted hook without a message: its SessionStart hook names the untrusted events."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve() / "home"
        self.repo = Path(tmp.name).resolve() / "repo"
        put(self.repo / "dev-loop.json", '{"version": "2.2.0", "harnesses": ["codex"]}\n')
        put(self.repo / ".codex" / "hooks.json", json.dumps({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "y"}]}]}}))
        (self.repo / ".agents" / "skills").mkdir(parents=True)
        (self.repo / ".agents" / "skills" / "dev-loop").symlink_to(ROOT / "skill")
        git(self.repo, "init", "-q")

    def start(self, *events: str) -> subprocess.CompletedProcess[str]:
        lines = [f'[projects."{self.repo}"]', 'trust_level = "trusted"']
        for event in events:
            lines += [f'[hooks.state."{self.repo}/.codex/hooks.json:{event}:0:0"]', 'trusted_hash = "sha256:x"']
        put(self.home / ".codex" / "config.toml", "\n".join(lines) + "\n")
        return subprocess.run(["sh", str(ROOT / "codex/hooks/session-start.sh")],
                              input=json.dumps({"cwd": str(self.repo)}), capture_output=True, text=True, timeout=60,
                              env={**os.environ, "HOME": str(self.home)})

    def test_untrusted_hooks_are_named_and_trusted_hooks_add_nothing(self) -> None:
        result = self.start("session_start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Invoke it before any other action.", result.stdout)
        self.assertIn("Warning:", result.stdout)
        self.assertIn("stop", result.stdout)
        self.assertNotIn("session_start", result.stdout.split("Warning:")[1])
        self.assertIn("/hooks", result.stdout)
        result = self.start("session_start", "stop")
        self.assertEqual((result.returncode, result.stdout.count("\n")), (0, 1), result.stdout)
        self.assertNotIn("Warning:", result.stdout)

    def test_no_kit_checker_or_no_dev_loop_adds_no_warning(self) -> None:
        (self.repo / ".agents" / "skills" / "dev-loop").unlink()
        result = self.start()
        self.assertEqual((result.returncode, result.stdout.count("\n")), (0, 1), result.stdout)
        (self.repo / "dev-loop.json").unlink()
        self.assertEqual(self.start().stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
