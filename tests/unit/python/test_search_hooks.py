"""The search rule through the walls: Claude `deny-acceptance-to-subagents.sh` and `deny-path.sh`, Codex `acceptance-wall.sh`."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_devloop import git
from test_search_wall import D8, HELD, search_repo
from test_seal_hooks import ROOT

IMPLEMENTER = {"agent_id": "a", "agent_type": "implementer"}
CLAUDE = (("bash", "claude/hooks/deny-acceptance-to-subagents.sh"), ("bash", "claude/hooks/deny-path.sh", HELD))
CODEX = (("sh", "codex/hooks/acceptance-wall.sh"),)
GUARD = "Blocked: guard error: search check failed\n"


def run_wall(wall: tuple[str, ...], payload: dict, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    shell, script, *args = wall
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([shell, str(ROOT / script), *args], input=json.dumps(payload), env={**base, **env},
                          capture_output=True, text=True, timeout=60)


class SearchHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def check(self, rows: list[tuple]) -> None:
        for walls, caller, tool, tool_input, cwd, env, outcome in rows:
            for wall in walls:
                payload = {**caller, "tool_name": tool, "tool_input": tool_input, **({"cwd": str(cwd)} if cwd else {})}
                with self.subTest(wall=wall[1], caller=caller.get("agent_type", "main"), tool_input=tool_input,
                                  cwd=str(cwd), outcome=outcome):
                    result = run_wall(wall, payload, env)
                    if outcome == "ok":
                        self.assertEqual((result.returncode, result.stderr), (0, ""))
                    elif outcome == "d8":
                        self.assertEqual((result.returncode, result.stderr), (2, D8 + "\n"))
                    elif outcome == "guard":
                        self.assertEqual((result.returncode, result.stderr), (2, GUARD))
                    elif outcome == "name":
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertNotIn(D8, result.stderr)
                    else:
                        self.assertNotIn(D8, result.stderr)

    def test_search_rule_through_each_wall(self) -> None:
        top = search_repo(self.tmp / "repo")
        both, home = CLAUDE + CODEX, {"HOME": str(top.parent)}
        bash = lambda walls, command, outcome, caller=IMPLEMENTER: (  # noqa: E731
            walls, caller, "Bash", {"command": command}, top, home, outcome)
        tool = lambda name, tool_input, outcome, caller=IMPLEMENTER, walls=CLAUDE: (  # noqa: E731
            walls, caller, name, tool_input, top, home, outcome)
        rows = [bash(both, c, "d8") for c in (
            "grep -rn make .", "grep -Rn make", "grep -rn make tests", "egrep -r make .", "rgrep make .", "rg -n make",
            "rg make .", "rg --files", "git grep -n make", "git -C tests grep make", "find . -name '*.py'",
            "find tests -name x", "find", "cd tests && grep -rn make .", "grep -rn make ..", "grep -rn make ~")]
        rows += [tool("Grep", {"pattern": "make"}, "d8"), tool("Grep", {"pattern": "make", "path": "TESTS"}, "d8"),
                 tool("Glob", {"pattern": "**/*.py"}, "d8"), tool("Glob", {"pattern": "tests/{unit,acceptance}/*.py"}, "d8")]
        rows += [bash(both, c, "ok") for c in (
            "grep -rn make src tests/unit", "rg make src", "git grep make -- src", "find src -name x",
            "cd src && grep -rn make .", "grep -n make src/app.py", "grep make README.md",
            f"grep -rn make {HELD}/../../src", "grep -rn --exclude-dir=acceptance make .",
            "grep -rn --exclude-dir acceptance make tests", f"rg -g '!{HELD}/**' make", "rg --glob='!**/acceptance/**' make .",
            f"git grep make -- . ':!{HELD}'", f"git grep make -- ':(exclude){HELD}'",
            f"find . -path ./{HELD} -prune -o -name '*.py' -print", f"find . -not -path '*/{HELD}/*' -name '*.py'")]
        rows += [tool("Grep", {"pattern": "make", "path": "src"}, "ok"),
                 tool("Glob", {"pattern": "src/**/*.py"}, "ok"),
                 tool("Glob", {"pattern": "**/*.py", "path": "tests/unit"}, "ok"),
                 tool("Glob", {"pattern": "*.md"}, "ok"),
                 tool("Grep", {"pattern": "make", "path": ".", "glob": f"!{HELD}/**"}, "ok")]
        rows += [bash(both, c, "d8") for c in (
            f"grep -rn --exclude-dir={HELD} make .", f"grep -rn make . | grep -v {HELD}", "grep -rn --include='*.py' make .",
            "rg -g '*.py' make", "rg -t py make", "find . -path ./tests -o -name x", "find . -name acceptance -o -name '*.py'")]
        rows += [tool("Grep", {"pattern": "make", "path": ".", "glob": "*.py"}, "d8")]
        rows += [bash(both, f"cat {HELD}/test_x.py", "name"), bash(both, f"rg make {HELD}", "name")]
        rows += [bash(CLAUDE[:1], "grep -rn make .", "d8", {"agent_id": "a", "agent_type": "general-purpose"}),
                 bash(CODEX, "grep -rn make .", "d8", {"agent_id": "a", "agent_type": "task-reviewer"})]
        rows += [tool("Grep", {"pattern": "make"}, "ok", {}, CLAUDE[:1]), tool("Glob", {"pattern": "**/*.py"}, "ok", {}, CLAUDE[:1]),
                 bash(CLAUDE[:1], "grep -rn make .", "ok", {}), bash(CODEX, "rg make", "ok", {})]
        rows += [bash(CLAUDE[:1], "grep -rn make .", "no-d8", {"agent_id": "a", "agent_type": "spec-tester"}),
                 tool("Grep", {"pattern": "make"}, "no-d8", {"agent_id": "a", "agent_type": "spec-tester"}, CLAUDE[:1]),
                 bash(CODEX, "grep -rn make .", "no-d8", {"agent_id": "a", "agent_type": "spec_tester"})]
        self.check(rows)

    def test_cli_location_and_failure(self) -> None:
        top = search_repo(self.tmp / "repo")
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q")
        no_cli = search_repo(self.tmp / "no-cli", cli=None)
        stub_exit = search_repo(self.tmp / "stub-exit", cli="stub-exit")
        stub_text = search_repo(self.tmp / "stub-text", cli="stub-text")
        no_marker = search_repo(self.tmp / "no-marker", marker=False)
        no_suite = search_repo(self.tmp / "no-suite", suite=False)
        project = {"CLAUDE_PROJECT_DIR": str(top)}
        bash = lambda walls, command, cwd, outcome, env=None: (  # noqa: E731
            walls, IMPLEMENTER, "Bash", {"command": command}, cwd, env or {}, outcome)
        rows = [bash(CLAUDE, f"grep -rn make {top}", other, "d8", project),
                bash(CLAUDE, "grep -rn make .", other, "ok", project),
                bash(CLAUDE, "grep -rn make .", no_cli, "ok"), bash(CLAUDE, f"cat {HELD}/test_x.py", no_cli, "name"),
                bash(CLAUDE + CODEX, "grep -rn make .", stub_exit, "guard"),
                bash(CLAUDE + CODEX, "grep -rn make .", stub_text, "guard"),
                bash(CLAUDE + CODEX, "cat src/app.py", stub_exit, "ok"),
                bash(CLAUDE + CODEX, "grep -rn make .", no_marker, "ok"),
                bash(CLAUDE + CODEX, f"cat {HELD}/test_x.py", no_marker, "name"),
                bash(CLAUDE + CODEX, "grep -rn make .", no_suite, "ok"),
                bash(CLAUDE, "grep -rn make .", None, "ok")]
        self.check(rows)


if __name__ == "__main__":
    unittest.main()
