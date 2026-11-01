#!/usr/bin/env python3
"""Tests for `audit-run brief`: ids, prompt parts, refusals."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit_fixtures import AuditRunCase  # noqa: E402


def ordered(test: unittest.TestCase, text: str, marks: list[str]) -> None:
    at = [text.index(m) for m in marks]
    test.assertEqual(at, sorted(at), marks)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditBriefTests(AuditRunCase):
    def brief(self, *args: str) -> dict:
        out, code = self.step("brief", *args)
        self.assertEqual(code, 0, out)
        return out

    def test_hunter_brief(self) -> None:
        a, b = self.add_units(self.entry("a.py"), self.entry("b.py", selected_companion_blocks=["AI-AND-LLM.md#Core discipline"]))
        self.write_context()
        out = self.brief("--kind", "hunter", "--units", a, b)
        self.assertEqual(out["agent"], "h01")
        self.assertEqual(out["result"], str(self.path("agents/h01/scratch/result.json").resolve()))
        for u in self.ledger():
            self.assertEqual((u["status"], u["agent_id"]), ("in_progress", "h01"))
        prompt = Path(out["prompt"]).read_text()
        ordered(self, prompt, ["CONTEXT-TEXT", "ARCHITECTURE-TEXT", a, b, "ACCESS-CONTROL-TEXT", "AI-CORE-TEXT",
                               "CORE-HUNTING-TEXT", "PROMOTION-TEXT", "VALIDATION-RULES-TEXT", "STRUCTURED-RESULT-TEXT",
                               "BRANCH-confirmed", "BRANCH-needs_validation", "agents/h01/scratch/result.json",
                               "audit-run check"])
        self.assertNotIn("BRANCH-rejected", prompt)
        self.assertEqual(prompt.count("ACCESS-CONTROL-TEXT"), 1)
        agent = self.path("agents/h01")
        self.assertEqual(json.loads((agent / "brief.json").read_text()), {"kind": "hunter", "units": [a, b], "final": False})
        self.assertEqual((list((agent / "scratch").iterdir()), list((agent / "artifacts").iterdir())), ([], []))
        self.assertEqual(self.state()["briefs"]["h01"]["state"], "open")
        (c,) = self.add_units(self.entry("c.py"))
        second = Path(self.brief("--kind", "hunter", "--units", c)["prompt"]).read_text()
        self.assertEqual(self.state()["briefs"]["h02"]["units"], [c])
        self.assertIn(a, second.split("Peer coverage")[1])
        self.assertEqual(self.brief("--kind", "critic")["agent"], "c01")

    def test_refusals_write_nothing(self) -> None:
        a, b = self.add_units(self.entry("a.py"), self.entry("b.py"))
        self.write_context()
        self.brief("--kind", "hunter", "--units", a)
        before = self.snapshot()
        for units in ([a], ["nope"], [b, a]):
            out, code = self.step("brief", "--kind", "hunter", "--units", *units)
            self.assertEqual((code, out["error"]), (1, "not-planned"), units)
            self.assertEqual(self.snapshot(), before)
        for name, error in (("context.md", "no-context"), ("architecture.md", "no-architecture")):
            self.write_context()
            self.path(name).unlink()
            before = self.snapshot()
            out, code = self.step("brief", "--kind", "hunter", "--units", b)
            self.assertEqual((code, out["error"]), (1, error))
            self.assertEqual(self.snapshot(), before)

    def test_usage_errors(self) -> None:
        self.seed()
        for args in (["--kind", "hunter"], ["--kind", "critic", "--fingerprint", "f"], ["--kind", "verifier"],
                     ["--kind", "recheck", "--fingerprint", "f", "--final"]):
            self.assertEqual(self.step("brief", *args)[1], 2, args)

    def test_carried_units(self) -> None:
        self.carried_run()
        self.write_context()
        for uid in (self.U1, self.U2):
            out, code = self.step("brief", "--kind", "hunter", "--units", uid)
            self.assertEqual((code, out["error"]), (1, "carried"))
        out = self.brief("--kind", "verifier", "--fingerprint", "f-c")
        u1 = next(u for u in self.ledger() if u["coverage_id"] == self.U1)
        self.assertEqual((u1["status"], u1["agent_id"], out["agent"]), ("in_progress", "v01", "v01"))
        self.assertIn("Title f-c", Path(out["prompt"]).read_text())
        hunter = Path(self.brief("--kind", "hunter", "--units", self.U5)["prompt"]).read_text()
        self.assertIn("f-c", hunter)
        self.assertNotIn("f-n", hunter)

    def test_changed_source_is_no_exclusion(self) -> None:
        self.carried_run(change="a.py")
        self.write_context()
        hunter = Path(self.brief("--kind", "hunter", "--units", self.U1, self.U5)["prompt"]).read_text()
        self.assertNotIn("f-c", hunter)
        out, code = self.step("brief", "--kind", "verifier", "--fingerprint", "f-c")
        self.assertEqual((code, out["error"]), (1, "unknown-fingerprint"))

    def test_quick_verifier_holds_phase_5(self) -> None:
        self.release_plan("--profile", "quick")
        self.seed()
        self.write_context()
        self.put_candidate("f-1", {"proposed_verdict": "confirmed", "fingerprint": "f-1", "title": "T"})
        prompt = Path(self.brief("--kind", "verifier", "--fingerprint", "f-1")["prompt"]).read_text()
        ordered(self, prompt, ["VERIFIER-PROMPT-TEXT", "VERIFIER-PROMOTION-TEXT", "PHASE-5-TEXT"])

    def test_skill_changed(self) -> None:
        (a,) = self.add_units(self.entry("a.py"))
        self.write_context()
        hunting = self.skill / "HUNTING.md"
        text = hunting.read_text()
        hunting.write_text(text.replace("#### Core validation rules", "#### Other rules"))
        before = self.snapshot()
        out, code = self.step("brief", "--kind", "hunter", "--units", a)
        self.assertEqual((code, out["error"], out["file"], out["section"]),
                         (1, "skill-changed", "HUNTING.md", "#### Core validation rules"))
        self.assertEqual(self.snapshot(), before)
        hunting.write_text(text)
        (b,) = self.add_units(self.entry("b.py", attack="ATTACK-CLASSES.md#Gone"))
        before = self.snapshot()
        out, code = self.step("brief", "--kind", "hunter", "--units", b)
        self.assertEqual((code, out["error"], out["file"], out["section"]), (1, "skill-changed", "ATTACK-CLASSES.md", "Gone"))
        self.assertEqual(self.snapshot(), before)

    def test_unknown_fingerprint(self) -> None:
        self.carried_run()
        self.write_context()
        for kind, fp in (("verifier", "f-x"), ("recheck", "f-c")):
            out, code = self.step("brief", "--kind", kind, "--fingerprint", fp)
            self.assertEqual((code, out["error"]), (1, "unknown-fingerprint"), kind)

    def test_verifier_and_recheck_text(self) -> None:
        self.release_plan("--profile", "standard")
        self.seed()
        self.write_context()
        self.put_candidate("f-1", {"proposed_verdict": "confirmed", "fingerprint": "f-1", "title": "CANDIDATE-TITLE"})
        prompt = Path(self.brief("--kind", "verifier", "--fingerprint", "f-1")["prompt"]).read_text()
        ordered(self, prompt, ["CONTEXT-TEXT", "ARCHITECTURE-TEXT", "VERIFIER-PROMPT-TEXT", "VERIFIER-PROMOTION-TEXT",
                               "CANDIDATE-TITLE", "BRANCH-confirmed", "BRANCH-needs_validation", "BRANCH-rejected",
                               "agents/v01/scratch/result.json", "audit-run check"])
        self.assertNotIn("PHASE-5-TEXT", prompt)
        state = self.state()
        state["final"]["f-1"] = {"verdict": "confirmed", "fingerprint": "f-1", "title": "FINAL-TITLE"}
        self.path("audit-run.json").write_text(json.dumps(state))
        recheck = Path(self.brief("--kind", "recheck", "--fingerprint", "f-1")["prompt"]).read_text()
        ordered(self, recheck, ["CONTEXT-TEXT", "PHASE-5-TEXT", "FINAL-TITLE", "BRANCH-rejected",
                                "VERIFIER-PROMOTION-TEXT", "agents/r01/scratch/result.json", "audit-run check"])
        self.assertNotIn("ARCHITECTURE-TEXT", recheck)

    def test_check_line_quotes_a_run_path_with_spaces(self) -> None:
        (a,) = self.add_units(self.entry("a.py"))
        self.write_context()
        prompt = Path(self.brief("--kind", "hunter", "--units", a)["prompt"]).read_text()
        line = next(text for text in prompt.splitlines() if "audit-run check" in text)
        words = shlex.split(line)
        self.assertIn(str(self.path(".").resolve()), [str(Path(w).resolve()) for w in words])
        self.assertEqual(words[words.index("--agent") + 1], "h01")


if __name__ == "__main__":
    unittest.main()
