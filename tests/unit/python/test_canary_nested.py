"""The nested route canary of `scripts/canary.sh` (#273): its verdict, its run, the upgrade note."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

DENIAL = "Blocked: skill probe-fork is closed to this agent: the open skills are x."
CODEX_DENIAL = "Blocked: x is closed to this agent: y"


def skill(cid, name="Skill"):
    return {"message": {"content": [{"type": "tool_use", "id": cid, "name": name, "input": {"skill": "probe-fork"}}]}}


def skill_result(cid, text):
    return {"message": {"content": [{"type": "tool_result", "tool_use_id": cid, "content": text}]}}


def spawn(cid, name="collaborationspawn_agent", text='{"message": "x"}', key="arguments", kind="function_call"):
    return {"payload": {"type": kind, "call_id": cid, "name": name, key: text}}


def spawn_result(cid, text, kind="function_call_output"):
    return {"payload": {"type": kind, "call_id": cid, "output": text}}


CODE_CALL = ("exec", "await tools.spawn_agent({})", "input", "custom_tool_call")
CLAUDE_BLOCKED = [skill("t1"), skill_result("t1", DENIAL)]
CODEX_BLOCKED = [spawn("c1"), spawn_result("c1", CODEX_DENIAL)]

FORK = {"type": "session_meta", "payload": {"forked_from_id": "parent"}}
PARENT_META = {"type": "session_meta", "payload": {"id": "parent"}}
ABORTED = {"type": "event_msg", "payload": {"type": "turn_aborted"}}
BOUNDARY = {"type": "event_msg", "payload": {"type": "thread_settings_applied"}}
INHERITED = [FORK, PARENT_META, spawn("c0"), ABORTED]

CLAUDE_ROWS = [
    ("blocked", CLAUDE_BLOCKED, 0, 0, "blocked"),
    ("no-denial", [skill("t1"), skill_result("t1", "forked")], 0, 1, "LEAK"),
    ("second-call", CLAUDE_BLOCKED + [skill("t2"), skill_result("t2", "launched")], 0, 1, "LEAK"),
    ("no-result", [skill("t1")], 0, 1, "LEAK"),
    ("other-tool", [skill("t1", "Bash"), skill_result("t1", "ok")], 0, 0, "refused"),
    ("harness-failed", CLAUDE_BLOCKED, 1, 1, "ERROR"),
    ("no-transcript", None, 0, 1, "ERROR"),
]

CODEX_ROWS = [
    ("blocked", CODEX_BLOCKED, 0, 0, "blocked"),
    ("bare-name", [spawn("c1", name="spawn_agent"), spawn_result("c1", CODEX_DENIAL)], 0, 0, "blocked"),
    (
        "code-mode",
        [spawn("c1", *CODE_CALL), spawn_result("c1", CODEX_DENIAL, "custom_tool_call_output")],
        0,
        0,
        "blocked",
    ),
    ("no-denial", [spawn("c1"), spawn_result("c1", "started")], 0, 1, "LEAK"),
    (
        "second-call",
        CODEX_BLOCKED + [spawn("c2", *CODE_CALL), spawn_result("c2", "started", "custom_tool_call_output")],
        0,
        1,
        "LEAK",
    ),
    (
        "shell-text",
        [spawn("c1", "exec_command", '{"cmd": "rg spawn_agent"}'), spawn_result("c1", "no match")],
        0,
        0,
        "refused",
    ),
    ("harness-failed", CODEX_BLOCKED, 1, 1, "ERROR"),
    ("no-transcript", None, 0, 1, "ERROR"),
    ("crash", [{"call_id": [1], "output": "x"}], 0, 1, "ERROR"),
    ("forked-blocked", INHERITED + [BOUNDARY, spawn("c1"), spawn_result("c1", CODEX_DENIAL)], 0, 0, "blocked"),
    ("forked-own-leak", INHERITED + [BOUNDARY, spawn("c1"), spawn_result("c1", "started")], 0, 1, "LEAK"),
    ("forked-no-boundary", INHERITED, 0, 1, "LEAK"),
    ("forked-refused", INHERITED + [BOUNDARY], 0, 0, "refused"),
]


class CanaryNestedTest(unittest.TestCase):
    def run_rows(self, harness, role, rows):
        for name, objects, rc, code, verdict in rows:
            with self.subTest(name):
                work = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, work, True)
                if objects is not None:
                    Path(work, "agent.jsonl").write_text("".join(json.dumps(o) + "\n" for o in objects))
                done = subprocess.run(
                    ["/bin/bash", str(ROOT / "scripts/canary.sh"), "--self-test-nested-verdict", harness, str(rc), work],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(done.stdout, f"{harness} nested {role} {verdict}\n")
                self.assertEqual(done.returncode, code)

    def test_claude_nested_verdict(self):
        self.run_rows("claude", "implementer", CLAUDE_ROWS)

    def test_codex_nested_verdict(self):
        self.run_rows("codex", "spec_tester", CODEX_ROWS)

    def test_each_harness_runs_the_nested_canary(self):
        text = (ROOT / "scripts/canary.sh").read_text()
        self.assertIn('nested_canary "$h" "$scratch/nested-$h"', text)
        self.assertIn("context: fork", text)
        self.assertIn("make no other call", text)
        body = text.split("nested_canary() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("--permission-mode bypassPermissions", body)
        self.assertIn("--dangerously-bypass-hook-trust --sandbox workspace-write", body)
        self.assertEqual(text.count('"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/'), 2)

    def test_help_names_the_nested_line(self):
        done = subprocess.run(["/bin/bash", str(ROOT / "scripts/canary.sh"), "--help"], capture_output=True, text=True)
        self.assertIn("claude nested implementer", done.stdout)
        self.assertIn("--self-test-nested-verdict", done.stdout)

    def test_the_upgrade_note_names_the_nested_route_matcher(self):
        text = (ROOT / "reference/kit.md").read_text()
        note = text.split("Walled agents nested routes upgrade (#273)", 1)[1].split("\n\n", 1)[0]
        for word in ("/hooks", "nested-route matcher", "collaboration.*"):
            self.assertIn(word, note)


if __name__ == "__main__":
    unittest.main()
