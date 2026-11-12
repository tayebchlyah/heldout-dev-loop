"""`audit` of a spec-tester transcript against the `source_dirs` folders at the main ref (spec D9, oracle 13)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from source_fixtures import key, set_main, source_repo
from support import git_in, write
from test_seal import DEVLOOP, claude_call, claude_line

L1 = "func internalTotal(xs []int) int { // source line one"
L2 = "return sumOfInternalValues(xs) // source line two, distinct"
SOURCE = f"package x\n\n{L1}\n\t{L2}\n}}\n"


class SourceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = source_repo(self.root)
        write(self.repo / "internal/x.go", SOURCE)
        git_in(self.repo, "add", "-A")
        git_in(self.repo, "commit", "-q", "-m", "source")

    def audit(self, shown: str = SOURCE) -> dict:
        git_dir = Path(git_in(self.repo, "rev-parse", "--absolute-git-dir").strip())
        write(git_dir / "dev-loop-subagents" / "s1.head", git_in(self.repo, "rev-parse", "HEAD"))
        write(self.repo / "docs/new.md", "new\n")
        git_in(self.repo, "add", "-A")
        git_in(self.repo, "commit", "-q", "--allow-empty", "-m", "one commit")
        transcript = self.root / "t.jsonl"
        lines = [claude_call("Read", {"file_path": "internal/x.go"}, "r1"), claude_line(shown, "r1")]
        transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
        hook = {"hook_event_name": "SubagentStop", "session_id": "s", "cwd": str(self.repo), "agent_id": "s1",
                "agent_type": "spec-tester", "agent_transcript_path": str(transcript)}
        env = {k: v for k, v in os.environ.items() if k != "DEV_LOOP_NOTES"}
        result = subprocess.run([sys.executable, str(DEVLOOP), "audit"], cwd=self.repo, env=env, input=json.dumps(hook),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_taint(self, out: dict) -> None:
        self.assertEqual(out["taint"]["set"], "source", out)
        self.assertEqual(out["taint"]["lines"], [L1, L2])

    def test_configured_folder_taints(self) -> None:
        set_main(self.repo, key(["internal/"]))
        self.assert_taint(self.audit())

    def test_no_key_keeps_the_default_set(self) -> None:
        self.assertEqual(self.audit(), {"taint": None, "skipped": []})

    def test_kit_copy_does_not_hide_a_read(self) -> None:
        set_main(self.repo, key(["internal/", ".agents/skills/"]))
        write(self.repo / ".agents/skills/k/x.go", SOURCE)
        git_in(self.repo, "add", "-A")
        git_in(self.repo, "commit", "-q", "-m", "kit copy")
        self.assert_taint(self.audit())

    def test_tests_entry_keeps_the_suite_out(self) -> None:
        set_main(self.repo, key(["tests/"]))
        for name, taint in (("tests/" + "acceptance/test_gizmo.py", False), ("tests/conftest.py", True)):
            with self.subTest(name=name):
                own = f"def gizmo_total() -> None:  # line one of {name}\n    assert gizmo_total() == 3  # line two of {name}\n"
                write(self.repo / name, own)
                git_in(self.repo, "add", "-A")
                git_in(self.repo, "commit", "-q", "-m", name)
                self.assertEqual(self.audit(own)["taint"] is not None, taint)

    def test_case_folded_path(self) -> None:
        set_main(self.repo, key(["Internal/"]))
        self.assert_taint(self.audit())


if __name__ == "__main__":
    unittest.main()
