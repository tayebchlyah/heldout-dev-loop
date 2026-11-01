#!/usr/bin/env python3
"""Tests for `audit-run fold`: critic, verifier, recheck."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit_fixtures import AuditRunCase, validator_exit  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditFoldReviewTests(AuditRunCase):
    def missing(self, surface: str = "n", paths: tuple[str, ...] = ("c.py",)) -> dict:
        return {"surface": surface, "boundary": "B", "subsystem": "S", "attack_class": "ATTACK-CLASSES.md#Injection",
                "starting_paths": list(paths), "reason": "gap"}

    def test_critic_adds_and_reassigns(self) -> None:
        self.release_plan("--profile", "standard")
        hunter, (uid,) = self.folded_hunter("a.py")
        critic, out = self.folded("critic", lambda c: self.critic_result(
            missing_units=[self.missing()], reassign_ids=[uid], notes={uid: "path x unchecked"}))
        u = self.unit(uid)
        self.assertEqual((out["stop"], out["reassigned"], u["status"], u["wave"], u["agent_id"], u["local_checks"]),
                         (False, [uid], "planned", 2, None, []))
        self.assertEqual((u["attempts"][0]["status"], u["attempts"][0]["agent_id"], u["attempts"][0]["wave"],
                          u["attempts"][0]["reassignment_reason"]), ("covered", hunter, 1, "path x unchecked"))
        (added,) = out["added"]
        self.assertEqual(self.unit(added)["status"], "planned")
        self.assertEqual(self.state()["critics"], [{"agent": critic, "final": False, "added": 1, "reassigned": 1}])

    def test_critic_resolved_lead_on_its_unit(self) -> None:
        self.carried_run(profile=("--profile", "standard"))
        self.write_context()
        _, out = self.folded("critic", lambda c: self.critic_result(
            resolved_prior_leads=["f-n"], stop=True, notes={"f-n": "fixed in b.py"}), "--final")
        u2 = self.unit(self.U2)
        self.assertEqual((out["resolved"], out["stop"], u2["resolved_prior_leads"], u2["resolved_prior_lead_reasons"]),
                         (["f-n"], True, ["f-n"], {"f-n": "fixed in b.py"}))
        self.assertEqual(self.state()["critics"][0]["final"], True)

    def test_quick_critic_defers(self) -> None:
        _, (uid,) = self.folded_hunter("a.py")
        _, out = self.folded("critic", lambda c: self.critic_result(
            missing_units=[self.missing()], reassign_ids=[uid], notes={uid: "why"}))
        for unit in (self.unit(uid), self.unit(out["added"][0])):
            self.assertEqual((unit["status"], unit["unresolved"], unit["agent_id"]),
                             ("deferred", ["quick_profile_final_critic"], None))
        self.assertEqual(self.state()["critics"][0]["added"], 0)

    def test_new_unit_duplicate_and_collision(self) -> None:
        self.release_plan("--profile", "standard")
        self.folded_hunter("a.py")
        _, out = self.folded("critic", lambda c: self.critic_result(missing_units=[self.missing()]))
        _, again = self.folded("critic", lambda c: self.critic_result(missing_units=[self.missing()]))
        self.assertEqual((again["added"], again["duplicates"]), ([], out["added"]))
        agent = self.step("brief", "--kind", "critic")[0]["agent"]
        self.write_result(agent, self.critic_result(missing_units=[self.missing(paths=("b.py",))]))
        before = self.snapshot()
        result, code = self.step("fold", "--agent", agent)
        self.assertEqual((code, result["error"], result["coverage_id"]), (1, "collision", out["added"][0]))
        self.assertEqual(self.snapshot(), before)

    def test_verifier_on_carried_unit(self) -> None:
        self.carried_run()
        self.write_context()
        verifier, out = self.folded("verifier", lambda v: self.verifier_result(v, "f-c"), "--fingerprint", "f-c")
        u1 = self.unit(self.U1)
        self.assertEqual((u1["status"], u1["agent_id"], u1["result_fingerprints"], u1["local_checks"][0]["agent_id"]),
                         ("candidate", "v01", ["f-c"], "v01"))
        self.assertEqual((out["units"], out["fingerprint_changed"], self.state()["final"]["f-c"]["verdict"]),
                         ([self.U1], None, "confirmed"))

    def test_verifier_changes_the_fingerprint(self) -> None:
        hunter, (uid,) = self.folded_hunter("a.py", candidates=[self.candidate("f-1")])
        _, out = self.folded("verifier", lambda v: self.verifier_result(v, "f-2"), "--fingerprint", "f-1")
        u = self.unit(uid)
        self.assertEqual((out["fingerprint_changed"], u["result_fingerprints"], u["agent_id"]),
                         ({"from": "f-1", "to": "f-2"}, ["f-2"], hunter))
        self.assertEqual(sorted(c["agent_id"] for c in u["local_checks"]), sorted([hunter, "v01"]))
        state = self.state()
        self.assertEqual((state["renamed"], list(state["final"])), ({"f-1": "f-2"}, ["f-2"]))

    def test_recheck_verified_and_replace(self) -> None:
        self.folded_hunter("a.py", candidates=[self.candidate("f-1")])
        self.folded("verifier", lambda v: self.verifier_result(v, "f-1"), "--fingerprint", "f-1")
        _, out = self.folded("recheck", lambda r: {"decision": "verified", "fingerprint": "f-1"}, "--fingerprint", "f-1")
        self.assertEqual((out["decision"], out["diff"]), ("verified", []))
        record = {**self.record("f-1", "confirmed"), "title": "New title"}
        _, out = self.folded("recheck", lambda r: {"decision": "replace", "reason": "sharper", "record": record},
                             "--fingerprint", "f-1")
        state = self.state()
        self.assertEqual((out["diff"], state["final"]["f-1"]["title"], state["rechecks"]["f-1"]),
                         (["title"], "New title", ["verified", "replace"]))

    def test_recheck_replace_invalid_writes_nothing(self) -> None:
        self.folded_hunter("a.py", candidates=[self.candidate("f-1")])
        self.folded("verifier", lambda v: self.verifier_result(v, "f-1"), "--fingerprint", "f-1")
        agent = self.step("brief", "--kind", "recheck", "--fingerprint", "f-1")[0]["agent"]
        self.write_result(agent, {"decision": "replace", "reason": "x", "record": self.record("f-1", "rejected")})
        validator_exit(self.skill, "validate-findings.cjs", 1)
        before = self.snapshot()
        out, code = self.step("fold", "--agent", agent)
        self.assertEqual((code, out["error"]), (1, "invalid"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(self.path("audit-run.json").read_text())["final"]["f-1"]["verdict"], "confirmed")


if __name__ == "__main__":
    unittest.main()
