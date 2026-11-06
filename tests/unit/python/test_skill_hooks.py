"""The skill rule through the wall: arm `implementer:Skill` of `deny-acceptance-to-subagents.sh`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_devloop import put
from test_search_hooks import IMPLEMENTER, run_wall
from test_seal_hooks import ROOT
from test_skill_wall import PLAIN, SKILL, skill_repo, write

WALL = ("bash", "claude/hooks/deny-acceptance-to-subagents.sh")
FIXED = ("mattpocock-skills:tdd", "mattpocock-skills:diagnosing-bugs", "mattpocock-skills:codebase-design")
HELD = "tests/" + "acceptance/x.py"
CLOSED ="is closed to this agent"
HOOK_HEADER = (ROOT / WALL[1]).read_text().split('. "${0%/*}/git-pin.sh"')[0]


def call(top: Path, tool_input: dict, caller: dict | None = None):
    payload = {**(IMPLEMENTER if caller is None else caller), "tool_name": "Skill", "tool_input": tool_input, "cwd": str(top)}
    return run_wall(WALL, payload, {})


class SkillHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.count = 0

    def repo(self, **kwargs) -> Path:
        self.count += 1
        return skill_repo(self.tmp / f"repo{self.count}", **kwargs)

    def denied(self, result, *held: str) -> None:
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        for text in held:
            self.assertIn(text, result.stderr)

    def stubbed(self, body: str) -> Path:
        top = self.repo(cli="stub-text")
        put(top / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py", body)
        return top

    def test_a_listed_skill_passes(self) -> None:
        result = call(self.repo(cli="link"), {"skill": "pg-guide"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_each_reason_keeps_the_denial_text(self) -> None:
        no_key = self.repo(config='{"version": "3.0.0"}\n')
        no_copy = self.repo(text=None)
        stale = self.repo()
        write(stale / SKILL, PLAIN + "x")
        fork = self.repo(text="---\ncontext: fork\n---\n")
        shell = self.repo(text="Body !`date`\n")
        hooks = self.repo(text="---\nhooks: x\n---\n")
        rows = [(no_key, "not in walled_skills"), (no_copy, "no copy at the main ref"), (stale, "differs from the main ref"),
                (fork, "context: fork"), (shell, "shell line"), (hooks, "hooks key")]
        for top, reason in rows:
            with self.subTest(reason):
                self.denied(call(top, {"skill": "pg-guide"}), CLOSED, *FIXED, "go on without the skill", "SKILL.md", reason)

    def test_fixed_names_need_no_check(self) -> None:
        no_key = self.repo(config='{"version": "3.0.0"}\n')
        no_ref = self.tmp / "empty"
        no_ref.mkdir()
        for top in (no_key, no_ref):
            for name in FIXED:
                with self.subTest(top=top.name, name=name):
                    self.assertEqual(call(top, {"skill": name}).returncode, 0)

    def test_empty_input_is_denied(self) -> None:
        self.denied(call(self.repo(), {}), CLOSED)

    def test_spec_tester_stays_denied(self) -> None:
        self.denied(call(self.repo(), {"skill": "pg-guide"}, {"agent_id": "a", "agent_type": "spec-tester"}),
                    "Skill is closed to this agent")

    def test_other_callers_keep_skill(self) -> None:
        for caller in ({}, {"agent_id": "a", "agent_type": "general-purpose"}):
            with self.subTest(caller=caller):
                self.assertEqual(call(self.repo(), {"skill": "other"}, caller).returncode, 0)

    def test_fails_closed(self) -> None:
        rows = [self.repo(cli=None), self.repo(cli="stub-exit"), self.repo(cli="stub-text"), self.stubbed("print('{}')\n"),
                self.stubbed("""print('{"deny": "no"}')\n"""), self.stubbed("print('[]')\n"),
                self.stubbed("pass\n"), self.stubbed("""print('{"deny": true, "reason": ""}')\n"""),
                self.stubbed("""print('{"reason": ""}')\n"""),
                self.stubbed("""print('{"deny": false}')\nprint('{"deny": false}')\n""")]
        for top in rows:
            with self.subTest(top=top.name):
                self.denied(call(top, {"skill": "pg-guide"}), CLOSED)
        open_stub = self.stubbed("""print('{"deny": false, "reason": null}')\n""")
        self.assertEqual(call(open_stub, {"skill": "pg-guide"}).returncode, 0)

    def test_args_with_an_at_are_denied(self) -> None:
        top = self.repo(cli="link")
        for name in ("pg-guide", "mattpocock-skills:tdd"):
            with self.subTest(name=name):
                self.denied(call(top, {"skill": name, "args": "@x.txt"}), "Skill args")
                self.assertEqual(call(top, {"skill": name, "args": "plain"}).returncode, 0)
        other = {"agent_id": "a", "agent_type": "general-purpose"}
        self.assertEqual(call(top, {"skill": "other", "args": "@" + HELD}, other).returncode, 2)
        self.assertEqual(call(top, {"skill": "other", "args": "@x.txt"}, other).returncode, 0)

    def test_the_texts_name_the_route(self) -> None:
        route = (ROOT / "skill" / "references" / "claude.md").read_text()
        for text in (HOOK_HEADER, route):
            for word in ("walled_skills", "context: fork", "shell line", "personal skill"):
                self.assertIn(word, text)
        self.assertIn("walled_skills", (ROOT / "skill" / "references" / "codex.md").read_text())


if __name__ == "__main__":
    unittest.main()
