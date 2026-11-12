"""The `source_dirs` wall through the spec-tester hooks: Claude `deny-acceptance-to-subagents.sh` and `deny-path.sh --source-dirs`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_fixtures import key, set_branch, set_main, source_repo
from support import CLAUDE_HOOKS, ROOT

SUITE = "tests/" + "acceptance"
DEFAULT = ("src/", "app/", "backend/", "frontend/")
TWIN = (CLAUDE_HOOKS / "deny-acceptance-to-subagents.sh", (), {"agent_id": "a", "agent_type": "spec-tester"})
FRONTMATTER = (CLAUDE_HOOKS / "deny-path.sh", ("--source-dirs", *DEFAULT), {})
COMMAND = "~/.claude/hooks/deny-path.sh --source-dirs src/ app/ backend/ frontend/"


def hook(script: Path, args: tuple[str, ...], payload: dict, env: dict | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    shell = "sh" if script.name == "acceptance-wall.sh" else "bash"
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([shell, str(script), *args], input=json.dumps(payload), env={**base, **(env or {})},
                          cwd=cwd or tempfile.gettempdir(), capture_output=True, text=True, timeout=60)


def read(path: str) -> tuple[str, dict]:
    return "Read", {"file_path": path}


class ClaudeSourceHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.top = source_repo(self.tmp)

    def check(self, rows: list[tuple], hooks: tuple = (TWIN, FRONTMATTER), top: Path | None = None,
              cwd: Path | None = None, env: dict | None = None, payload_cwd: bool = True) -> None:
        top = top or self.top
        env = {"CLAUDE_PROJECT_DIR": str(top)} if env is None else env
        for (tool, tool_input), code, *said in rows:
            for script, args, caller in hooks:
                payload = {**caller, "tool_name": tool, "tool_input": tool_input,
                           **({"cwd": str(top)} if payload_cwd else {})}
                with self.subTest(hook=script.name, args=args, tool=tool, tool_input=tool_input):
                    done = hook(script, args, payload, env, cwd or self.tmp)
                    self.assertEqual(done.returncode, code, done.stderr)
                    for text in said:
                        self.assertIn(text, done.stderr)
                        self.assertEqual(done.stderr.count("\n"), 1, done.stderr)

    def test_main_key_walls_its_folders(self) -> None:
        set_main(self.top, key(["internal/"]))
        self.check([(read("internal/x.go"), 2, "internal/"), (("Bash", {"command": "cd internal && cat x.go"}), 2),
                    (read("src/app.py"), 0)])

    def test_no_key_keeps_the_four_folders(self) -> None:
        self.check([(read(path), 2) for path in ("src/app.py", "app/x.py", "backend/x.py", "frontend/x.ts", "pkg/src/y.py")]
                   + [(read("internal/x.go"), 0)])

    def test_a_branch_key_changes_nothing(self) -> None:
        set_branch(self.top, key(["internal/"]))
        self.check([(read("internal/x.go"), 0), (read("src/app.py"), 2)])
        other = source_repo(self.tmp / "b")
        set_main(other, key(["internal/"]))
        set_branch(other, json.dumps({"version": "0.0.1"}))
        self.check([(read("internal/x.go"), 2)], top=other)

    def test_bad_config_denies_path_tools_only(self) -> None:
        set_main(self.top, key("internal/"))
        self.check([(read("docs/guide.md"), 2, "source_dirs"),
                    (("Write", {"file_path": f"{SUITE}/test_x.py", "content": "x"}), 2, "source_dirs"),
                    (("SubagentHandback", {"message": "x"}), 0), (("ToolSearch", {"query": "x"}), 0)])
        set_main(self.top, "[]")
        self.check([(read("docs/guide.md"), 2, "source_dirs")])

    def test_other_callers_skip_the_source_wall(self) -> None:
        set_main(self.top, key("internal/"))
        script = TWIN[0]
        self.check([(read("docs/guide.md"), 0)],
                   hooks=((script, (), {"agent_id": "a", "agent_type": "implementer"}), (script, (), {})))

    def test_no_kit_cli(self) -> None:
        folder = self.tmp / "hooks"
        folder.mkdir()
        for name in ("deny-path.sh", "deny-acceptance-to-subagents.sh", "git-pin.sh"):
            shutil.copy(CLAUDE_HOOKS / name, folder / name)
        hooks = ((folder / TWIN[0].name, *TWIN[1:]), (folder / FRONTMATTER[0].name, *FRONTMATTER[1:]))
        self.check([(read("docs/guide.md"), 0), (read("src/app.py"), 2)], hooks=hooks)
        set_main(self.top, key(["internal/"]))
        self.check([(read("docs/guide.md"), 2, "source_dirs")], hooks=hooks)

    def test_explicit_fragments_unchanged(self) -> None:
        set_main(self.top, key(["internal/"]))
        self.check([(read("pkg/src/y.py"), 2), (read("internal/x.go"), 0)],
                   hooks=((FRONTMATTER[0], DEFAULT, {}),))

    def test_empty_key_opens_src(self) -> None:
        set_main(self.top, key([]))
        self.check([(read("src/app.py"), 0)])

    def test_project_dir_wins_over_cwd(self) -> None:
        set_main(self.top, key(["internal/"]))
        other = source_repo(self.tmp / "b")
        self.check([(read(f"{self.top}/internal/x.go"), 2, "internal/")], top=other,
                   env={"CLAUDE_PROJECT_DIR": str(self.top)})

    def test_no_cwd_and_no_project_dir_keep_the_default(self) -> None:
        set_main(self.top, key(["docs/"]))
        self.check([(read("docs/guide.md"), 0), (read("src/app.py"), 2)], cwd=self.top, env={}, payload_cwd=False)

    def test_frontmatter_entry(self) -> None:
        text = (ROOT / "claude" / "agents" / "spec-tester.md").read_text()
        head = text.split("\n---\n", 1)[0] + "\n"
        self.assertIn(f'command: "{COMMAND}"\n          onFailure: block\n', head)


if __name__ == "__main__":
    unittest.main()
