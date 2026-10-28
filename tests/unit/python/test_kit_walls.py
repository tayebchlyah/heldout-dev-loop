"""The walls on absolute paths, kit files and `cd`, the canary script and the Codex trust warning."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import check_setup
from test_devloop import git, put
from test_seal_hooks import ROOT, SHELL, patch


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

    def test_second_review_canary_shows_a_reviewer_that_quotes_a_canary(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        table = "printf '| file | line | finding | class | verdict |\\n| --- | --- | --- | --- | --- |\\n'"
        quote = "for f in $(grep -o '/[^ ,]*canary[^ ,]*' .second-review.diff | sort -u); do cat \"$f\"; done"
        for name, body, line, code in [
            ("clean", table, "second-review codex done canary=no", 0),
            ("quotes", quote, "second-review codex tainted canary=yes", 1),
        ]:
            with self.subTest(name=name):
                bins = work / name / "bin"
                put(bins / "codex", "#!/bin/sh\nout=\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && out=$2; shift; done\n"
                                    f"cat >/dev/null\n{{ {body}; }} >\"$out\"\n")
                (bins / "codex").chmod(0o755)
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-review", str(work / name / "repo"), "codex"],
                                        capture_output=True, text=True,
                                        env={**os.environ, "PATH": f"{bins}:{os.environ['PATH']}"})
                self.assertEqual((result.returncode, result.stdout.strip().splitlines()[-1:]), (code, [line]), result.stderr)

    def test_second_review_state_maps_to_its_verdict_row(self):
        for name, js, line, code in [
            ("done", '{"state": "done", "record": null}', "second-review codex done canary=no", 0),
            ("tainted", '{"state": "tainted", "record": null}', "second-review codex tainted canary=yes", 1),
            ("shown", '{"state": "done", "record": null, "text": "CANARYacc"}', "second-review codex done canary=yes", 1),
            ("skipped", '{"state": "skipped", "record": null}', "second-review codex skipped canary=no", 1),
            ("failed", '{"state": "failed", "record": null}', "second-review codex failed canary=no", 1),
            ("no-json", "", "second-review codex ERROR canary=no", 1),
            ("garbage", "not json", "second-review codex ERROR canary=no", 1),
        ]:
            with self.subTest(name=name):
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-review-verdict", "codex", js,
                                         "CANARYacc", "CANARYout"], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout.strip()), (code, line), result.stderr)

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


class KitWallTests(unittest.TestCase):
    """No caller edits an installed kit file through a file tool: kit files change only by `upgrade`."""

    def run_hook(self, script: str, payload: dict) -> subprocess.CompletedProcess[str]:
        harness = "codex" if script == "acceptance-wall.sh" else "claude"
        return subprocess.run([SHELL[harness], str(ROOT / harness / "hooks" / script)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)

    def test_file_tools_cannot_change_a_kit_file(self) -> None:
        kit = [".agents/skills/dev-loop/scripts/seal/check.py", ".agents/skills/dev-loop/SKILL.md",
               ".dev-loop/hooks/claude/git-guard.sh", ".dev-loop/MANIFEST.json", ".claude/skills/dev-loop/SKILL.md",
               ".codex/rules/dev-loop.rules", ".agents/skills/continue/SKILL.md", ".agents/skills/pause/agents/openai.yaml",
               ".claude/skills/pause/SKILL.md", ".claude/skills/continue", *[f"{folder}/{name}" for folder, name in KIT_AGENTS]]
        open_paths = ["skill/scripts/seal/check.py", "claude/hooks/git-guard.sh", ".claude/agents/my-reviewer.md",
                      ".codex/agents/mine.toml", ".codex/rules/mine.rules", "docs/dev-loop/x.md",
                      ".agents/skills/verify-shop/SKILL.md", ".claude/skills/pauses/SKILL.md", "skills/pause/SKILL.md",
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
            "Stop": [{"hooks": [{"type": "command", "command": "y"}, {"type": "command", "command": "z"}]}]}}))
        (self.repo / ".agents" / "skills").mkdir(parents=True)
        (self.repo / ".agents" / "skills" / "dev-loop").symlink_to(ROOT / "skill")
        git(self.repo, "init", "-q")

    def start(self, *events: str, hooks: int = 2) -> subprocess.CompletedProcess[str]:
        lines = [f'[projects."{self.repo}"]', 'trust_level = "trusted"']
        entries = json.loads((self.repo / ".codex" / "hooks.json").read_text())["hooks"]
        for name, groups in entries.items():
            if check_setup.snake_case(name) not in events:
                continue
            for j, hook in enumerate(groups[0]["hooks"][:hooks]):
                key = f"{self.repo}/.codex/hooks.json:{check_setup.snake_case(name)}:0:{j}"
                lines += [f'[hooks.state."{key}"]', f'trusted_hash = "{check_setup.codex_hook_hash(name, groups[0], hook)}"']
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

    def test_a_second_untrusted_hook_of_a_trusted_event_is_named(self) -> None:
        result = self.start("session_start", "stop", hooks=1)
        self.assertIn("Warning:", result.stdout)
        self.assertIn("stop", result.stdout.split("Warning:")[1])

    def test_no_kit_checker_or_no_dev_loop_adds_no_warning(self) -> None:
        (self.repo / ".agents" / "skills" / "dev-loop").unlink()
        result = self.start()
        self.assertEqual((result.returncode, result.stdout.count("\n")), (0, 1), result.stdout)
        (self.repo / "dev-loop.json").unlink()
        self.assertEqual(self.start().stdout, "")
