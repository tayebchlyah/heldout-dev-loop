#!/usr/bin/env python3
"""Tests for `audit-run findings` and `audit-run status`."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from audit_fixtures import AuditRunCase, validator_calls, validator_exit  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditCloseTests(AuditRunCase):
    def open_items(self) -> list[dict]:
        out, code = self.step("findings")
        self.assertEqual((code, out["error"], out["run_status"]), (1, "open", "in_progress"), out)
        self.assertEqual(self.metadata()["run_status"], "in_progress")
        return out["open"]

    def test_all_done_is_complete(self) -> None:
        self.closed_flow()
        out, code = self.step("findings")
        self.assertEqual((code, out, self.metadata()["run_status"]),
                         (0, {"run_status": "complete", "records": 1, "open": []}, "complete"))
        records = json.loads(self.path("findings.json").read_text())
        self.assertEqual([r["fingerprint"] for r in records], ["f-1"])
        self.assertTrue(validator_calls(self.skill, "validate-findings.cjs")[-1].endswith("findings.json"))
        self.assertTrue(validator_calls(self.skill, "validate-coverage-ledger.cjs")[-1].endswith("coverage-ledger.json"))

    def test_open_brief_and_planned_unit(self) -> None:
        self.closed_flow()
        (uid,) = self.add_units(self.entry("b.py", paths=("b.py",)))
        self.assertIn({"kind": "unit", "coverage_id": uid, "status": "planned"}, self.open_items())
        agent = self.step("brief", "--kind", "hunter", "--units", uid)[0]["agent"]
        self.assertIn({"kind": "brief", "agent": agent}, self.open_items())

    def test_candidate_with_no_final_record(self) -> None:
        self.closed_flow()
        self.folded_hunter("b.py", candidates=[self.candidate("f-2")])
        self.assertIn({"kind": "candidate", "fingerprint": "f-2"}, self.open_items())
        self.assertNotIn("f-2", self.path("findings.json").read_text())

    def test_replace_last_needs_a_new_recheck(self) -> None:
        self.closed_flow(verdict="needs_validation")
        record = {**self.record("f-1", "needs_validation"), "title": "Sharper"}
        self.folded("recheck", lambda r: {"decision": "replace", "reason": "sharper", "record": record}, "--fingerprint", "f-1")
        self.assertIn({"kind": "recheck", "fingerprint": "f-1"}, self.open_items())

    def test_no_final_clean_critic(self) -> None:
        self.closed_flow()
        self.folded("critic", lambda c: self.critic_result(stop=True))
        self.assertEqual([i["kind"] for i in self.open_items()], ["critic"])

    def test_carried_fingerprint_needs_a_final_record(self) -> None:
        self.carried_run(profile=("--profile", "standard"))
        self.assertIn({"kind": "candidate", "fingerprint": "f-c"}, self.open_items())

    def test_incomplete(self) -> None:
        self.release_plan("--profile", "standard")
        self.seed()
        (uid,) = self.add_units(self.entry("a.py"))
        out, code = self.step("findings", "--incomplete", "validation_budget_exhausted")
        self.assertEqual((code, out["run_status"]), (0, "incomplete"))
        meta = self.metadata()
        self.assertEqual((meta["run_status"], meta["incomplete_reason"]), ("incomplete", "validation_budget_exhausted"))
        unit = self.unit(uid)
        self.assertEqual((unit["status"], unit["unresolved"]), ("deferred", ["validation_budget_exhausted"]))
        out, code = self.devloop("release-record", "--run", str(self.path(".")), "--repo", str(self.repo))
        self.assertEqual((code, out["error"]), (1, "run-incomplete"))

    def test_validator_failure(self) -> None:
        self.closed_flow()
        validator_exit(self.skill, "validate-findings.cjs", 1)
        out, code = self.step("findings")
        self.assertEqual((code, out["error"], self.metadata()["run_status"]), (1, "invalid-run", "in_progress"))
        self.assertIn("stub failure", out["tail"])

    def test_incomplete_validator_failure_keeps_the_ledger(self) -> None:
        self.release_plan("--profile", "standard")
        self.seed()
        self.add_units(self.entry("a.py"))
        ledger = self.path("coverage-ledger.json")
        before = ledger.read_bytes()
        validator_exit(self.skill, "validate-findings.cjs", 1)
        out, code = self.step("findings", "--incomplete", "x")
        self.assertEqual((code, out["error"], self.metadata()["run_status"]), (1, "invalid-run", "in_progress"))
        self.assertIn("stub failure", out["tail"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_quick_needs_one_critic_and_no_recheck(self) -> None:
        self.closed_flow(profile="quick")
        self.assertEqual(self.step("findings")[0]["run_status"], "complete")

    def test_status_lists_every_brief(self) -> None:
        ids = self.add_units(*(self.entry(p, paths=(p,)) for p in ("a.py", "b.py", "c.py", "d.py", "e.py")))
        self.write_context()
        agents = [self.step("brief", "--kind", "hunter", "--units", uid)[0]["agent"] for uid in ids]
        self.write_result(agents[1], {"units": []})
        for agent, uid in zip(agents[2:4], ids[2:4]):
            self.write_result(agent, self.hunter_result(agent, [uid]))
        self.step("fold", "--agent", agents[3])
        self.step("fold", "--agent", agents[4], "--discard", "x")
        before = self.snapshot()
        out, code = self.step("status")
        self.assertEqual(code, 0, out)
        self.assertEqual([(b["agent"], b["state"]) for b in out["briefs"]],
                         list(zip(agents, ["missing", "invalid", "ready", "folded", "discarded"])))
        self.assertTrue(out["briefs"][1]["errors"])
        self.assertEqual(out["briefs"][2]["errors"], [])
        self.assertEqual((out["units"], out["counts"], out["run_status"]),
                         ({"covered": 1, "in_progress": 3, "planned": 1}, {"hunter": 5}, "in_progress"))
        self.assertEqual(self.snapshot(), before)

    def test_status_ignores_brief_json(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids))
        self.path(f"agents/{agent}/brief.json").write_text(json.dumps({"kind": "recheck", "fingerprint": "x"}))
        out, _ = self.step("status")
        self.assertEqual((out["briefs"][0]["kind"], out["briefs"][0]["state"]), ("hunter", "ready"))


if __name__ == "__main__":
    unittest.main()
