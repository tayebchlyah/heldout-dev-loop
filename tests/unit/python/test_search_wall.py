"""`search-wall`: the search rule engine of the walls, on `Grep`, `Glob` and `Bash` hook calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_devloop import DEVLOOP, git, put
from test_seal_hooks import ROOT

from seal.search import decide  # noqa: E402

HELD = "tests/" + "acceptance"
D8 = ("Blocked: this search reaches the held-out suite " + HELD + ". Search the source folders by name "
      "(grep -rn <name> src lib, Grep path src), or exclude the suite (grep --exclude-dir=acceptance, "
      "rg -g '!" + HELD + "/**', git grep <name> -- . ':!" + HELD + "', find . -path ./" + HELD
      + " -prune -o ..., Grep glob !" + HELD + "/**).")
DENY = {"deny": True, "reason": D8}
ALLOW = {"deny": False, "reason": None}


def search_repo(root: Path, *, marker: bool = True, suite: bool = True, cli: str | None = "link") -> Path:
    """A git repo with `src`, unit tests, and by default `dev-loop.json`, the held-out suite and the kit CLI."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    put(root / "README.md", "# x\n")
    put(root / "src" / "app.py", "def make(x):\n    return x\n")
    put(root / "tests" / "unit" / "test_app.py", "from app import make\n")
    if marker:
        put(root / "dev-loop.json", '{"version": "3.0.0"}\n')
    if suite:
        put(root / HELD / "test_x.py", "from app import make\n\nr = make(1)\n")
    scripts = root / ".agents" / "skills" / "dev-loop" / "scripts"
    scripts.parent.mkdir(parents=True)
    if cli == "link":
        scripts.symlink_to(ROOT / "skill" / "scripts")
    elif cli is not None:
        put(scripts / "devloop.py", {"stub-exit": "import sys\nsys.exit(1)\n", "stub-text": "print('not json')\n"}[cli])
    return root.resolve()


def payload(top: Path, tool: str, tool_input: dict, **caller: str) -> dict:
    who = caller or {"agent_id": "a", "agent_type": "implementer"}
    return {**who, "cwd": str(top), "tool_name": tool, "tool_input": tool_input}


class SearchWallTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.top = search_repo(self.tmp / "repo")

    def test_grep_and_glob_tools(self) -> None:
        top = self.top
        grep = lambda **extra: ("Grep", {"pattern": "make", **extra})  # noqa: E731
        glob = lambda pattern, **extra: ("Glob", {"pattern": pattern, **extra})  # noqa: E731
        rows = [(grep(), DENY), (grep(path="."), DENY), (grep(path="tests"), DENY), (grep(path=str(top)), DENY),
                (grep(path="./"), DENY), (grep(path="TESTS"), DENY), (grep(path="tests/../tests"), DENY),
                (grep(path=".//"), DENY), (grep(output_mode="content"), DENY),
                (grep(path=".", glob="*.py"), DENY), (grep(path=".", type="py"), DENY),
                (glob("**/*.py"), DENY), (glob("**/*.py", path="tests"), DENY), (glob("tests/*/*.py"), DENY),
                (glob("*/*/*.py"), DENY), (glob("tests/{unit,acceptance}/*.py"), DENY),
                (grep(path="src"), ALLOW), (grep(path=".", glob="!" + HELD + "/**"), ALLOW),
                (grep(path=".", glob="!**/acceptance/**"), ALLOW), (grep(path=".", glob="!/" + HELD + "/"), ALLOW),
                (glob("src/**/*.py"), ALLOW), (glob("**/*.py", path="tests/unit"), ALLOW), (glob("*.md"), ALLOW),
                (("Read", {"file_path": str(top / HELD / "test_x.py")}), ALLOW)]
        if not str(top).startswith("/private/"):
            rows.append((grep(path="/private" + str(top)), DENY))
        for (tool, tool_input), expected in rows:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(decide(payload(top, tool, tool_input), {"HOME": "/nonexistent"}), expected)

    def test_roles_and_suites(self) -> None:
        top, other = self.top, self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q")
        no_marker = search_repo(self.tmp / "no-marker", marker=False)
        no_suite = search_repo(self.tmp / "no-suite", suite=False)
        outside = self.tmp / "plain"
        outside.mkdir()
        no_path = ("Grep", {"pattern": "make"})
        rows = [
            ("general-purpose", payload(top, *no_path, agent_id="a", agent_type="general-purpose"), {}, DENY),
            ("main session", {k: v for k, v in payload(top, *no_path).items() if k != "agent_id"}, {}, ALLOW),
            ("spec-tester", payload(top, *no_path, agent_id="a", agent_type="spec-tester"), {}, ALLOW),
            ("spec_tester", payload(top, *no_path, agent_id="a", agent_type="spec_tester"), {}, ALLOW),
            ("project dir, top", payload(other, "Grep", {"pattern": "make", "path": str(top)}),
             {"CLAUDE_PROJECT_DIR": str(top)}, DENY),
            ("project dir, cwd", payload(other, "Grep", {"pattern": "make", "path": "."}),
             {"CLAUDE_PROJECT_DIR": str(top)}, ALLOW),
            ("no dev-loop.json", payload(no_marker, *no_path), {}, ALLOW),
            ("no suite folder", payload(no_suite, *no_path), {}, ALLOW),
            ("outside git", payload(outside, *no_path), {}, ALLOW),
        ]
        for name, hook, env, expected in rows:
            with self.subTest(name):
                self.assertEqual(decide(hook, {"HOME": "/nonexistent", **env}), expected)

    def test_cli(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        rows = [(json.dumps(payload(self.top, "Grep", {"pattern": "make"})), (), 0, DENY),
                (json.dumps(payload(self.top, "Grep", {"pattern": "make", "path": "src"})), (), 0, ALLOW),
                ("not json", (), 0, {"deny": True, "reason": "Blocked: guard error: hook input is not a JSON object"}),
                ("{}", ("x",), 2, None)]
        for stdin, extra, code, expected in rows:
            with self.subTest(stdin=stdin, extra=extra):
                result = subprocess.run([sys.executable, str(DEVLOOP), "search-wall", *extra], input=stdin, env=env,
                                        cwd=self.top, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, code, result.stderr)
                if expected is not None:
                    self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
