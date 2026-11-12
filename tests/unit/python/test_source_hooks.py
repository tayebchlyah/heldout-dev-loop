"""The `source_dirs` wall through the spec-tester hooks: Claude `deny-acceptance-to-subagents.sh` and
`deny-path.sh --source-dirs`, Codex `acceptance-wall.sh`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_fixtures import key, set_branch, set_main, source_repo
from support import CLAUDE_HOOKS, CODEX_ASSETS, ROOT, git_in

SUITE = "tests/" + "acceptance"
DEFAULT = ("src/", "app/", "backend/", "frontend/")
TWIN = (CLAUDE_HOOKS / "deny-acceptance-to-subagents.sh", (), {"agent_id": "a", "agent_type": "spec-tester"})
FRONTMATTER = (CLAUDE_HOOKS / "deny-path.sh", ("--source-dirs", *DEFAULT), {})
COMMAND = "~/.claude/hooks/deny-path.sh --source-dirs src/ app/ backend/ frontend/"
CODEX_WALL = CODEX_ASSETS / "hooks" / "acceptance-wall.sh"
TESTER = {"agent_id": "b", "agent_type": "spec_tester"}


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


def bash(command: str) -> tuple[str, dict]:
    return "Bash", {"command": command}


def add_patch(header: str, body: str = "+x") -> dict:
    return {"command": "\n".join(["*** Begin Patch", header, body, "*** End Patch"])}


class CodexSourceHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.top = source_repo(self.tmp)

    def check(self, rows: list[tuple], script: Path = CODEX_WALL, caller: dict = TESTER,
              cwd: Path | None = None) -> None:
        for (tool, tool_input), code, *said in rows:
            payload = {**caller, "tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or self.top)}
            with self.subTest(script=str(script), caller=caller, tool=tool, tool_input=tool_input):
                done = hook(script, (), payload, cwd=self.tmp)
                self.assertEqual(done.returncode, code, done.stderr)
                for text in said:
                    self.assertIn(text, done.stderr)
                    self.assertEqual(done.stderr.count("\n"), 1, done.stderr)

    def test_main_key_walls_its_folders(self) -> None:
        set_main(self.top, key(["internal/", "cmd/"]))
        self.check([(bash("cat internal/x.go"), 2, "internal/"), (bash("cat cmd/app/main.go"), 2),
                    (("view_image", {"path": "internal/a.png"}), 2), (bash("cat src/app.py"), 0)])

    def test_no_key_keeps_the_four_folders(self) -> None:
        self.check([(bash("cat src/app.py"), 2), (bash("cat pkg/src/y.py"), 2), (bash("cat internal/x.go"), 0)])

    def test_bad_config(self) -> None:
        set_main(self.top, key("internal/"))
        self.check([(bash("cat docs/guide.md"), 2, "source_dirs"),
                    (("apply_patch", add_patch(f"*** Add File: {SUITE}/test_x.py")), 2, "source_dirs"),
                    (("update_plan", {"plan": []}), 0)])
        self.check([(bash("cat docs/guide.md"), 0)], caller={"agent_id": "a", "agent_type": "implementer"})
        self.check([(bash("cat docs/guide.md"), 0)], caller={})

    def test_patch_body_is_not_a_path(self) -> None:
        set_main(self.top, key(["internal/"]))
        self.check([(("apply_patch", add_patch(f"*** Add File: {SUITE}/test_x.py", '+PATH = "internal/x.go"')), 0)])

    def test_kit_file_rule_still_first(self) -> None:
        set_main(self.top, key([]))
        self.check([(("apply_patch", add_patch("*** Update File: .dev-loop/hooks/codex/x.sh")), 2,
                     "installed dev-loop kit file")])

    def test_main_worktree_of_cwd(self) -> None:
        set_main(self.top, key(["internal/"]))
        linked = self.tmp / "w"
        git_in(self.top, "worktree", "add", "-q", "--detach", str(linked))
        self.check([(bash(f"cat {self.top}/internal/x.go"), 2, "internal/")], cwd=linked)

    def test_no_kit_cli(self) -> None:
        folder = self.tmp / "hooks"
        folder.mkdir()
        for name in ("acceptance-wall.sh", "deny-path.sh", "git-pin.sh"):
            shutil.copy(CODEX_ASSETS / "hooks" / name, folder / name)
        script = folder / CODEX_WALL.name
        self.check([(bash("cat docs/guide.md"), 0), (bash("cat src/app.py"), 2)], script=script)
        set_main(self.top, key(["internal/"]))
        self.check([(bash("cat docs/guide.md"), 2, "source_dirs")], script=script)


if __name__ == "__main__":
    unittest.main()
