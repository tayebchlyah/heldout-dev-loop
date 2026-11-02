#!/usr/bin/env python3
"""Tests for `audit-run fold`: hunter, discard."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit_fixtures import AuditRunCase, validator_calls, validator_exit  # noqa: E402
from test_release import RECORD  # noqa: E402

UNCOVERED = {"surface": "new", "boundary": "B", "subsystem": "S", "attack_class": "ATTACK-CLASSES.md#Injection",
             "starting_paths": ["c.py"], "reason": "a parallel path"}


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditFoldTests(AuditRunCase):
    def fold(self, agent: str, *args: str) -> tuple[dict, int]:
        return self.step("fold", "--agent", agent, *args)

    def test_hunter_fold(self) -> None:
        agent, ids = self.hunter_brief("a.py", "b.py")
        result = self.hunter_result(agent, ids, hardening=["note one"])
        result["units"][1] = self.unit_result(ids[1], agent, "blocked", unresolved=["needs a fixture"], paths=("b.py",))
        self.write_result(agent, result)
        before = len(validator_calls(self.skill, "validate-coverage-ledger.cjs"))
        out, code = self.fold(agent)
        self.assertEqual((code, sorted(out["units"]), out["kind"]), (0, sorted(ids), "hunter"))
        units = {u["coverage_id"]: u for u in self.ledger()}
        self.assertEqual((units[ids[0]]["status"], units[ids[1]]["status"], units[ids[1]]["unresolved"]),
                         ("covered", "blocked", ["needs a fixture"]))
        self.assertEqual((units[ids[0]]["hardening"], units[ids[0]]["reviewed_paths"], units[ids[0]]["agent_id"]),
                         (["note one"], ["a.py"], agent))
        self.assertEqual(units[ids[0]]["local_checks"], result["units"][0]["checks"])
        self.assertEqual((self.state()["briefs"][agent]["state"], self.state()["briefs"][agent]["order"]), ("folded", 1))
        self.assertGreater(len(validator_calls(self.skill, "validate-coverage-ledger.cjs")), before)

    def test_candidate_unit_keeps_unresolved(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids, candidates=[self.candidate("f-one")])
        result["units"][0].update(disposition="candidate", candidate_fingerprints=["f-one"], unresolved=["open point"])
        self.write_result(agent, result)
        out, code = self.fold(agent)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.unit(ids[0])["status"], self.unit(ids[0])["unresolved"]), ("candidate", ["open point"]))

    def test_unpromoted_when_artifact_is_missing_or_a_link(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = [self.local_check(agent, "r.txt")]
        self.write_result(agent, result)
        self.promote_ok(agent)
        artifact = self.path(f"agents/{agent}/artifacts/r.txt")
        artifact.unlink()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"]), (1, "unpromoted"))
        artifact.symlink_to(self.path(f"agents/{agent}/scratch/r.txt"))
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"], out["paths"]), (1, "unpromoted", [f"agents/{agent}/artifacts/r.txt"]))
        self.assertEqual(self.snapshot(), before)

    def promote_ok(self, agent: str) -> None:
        self.assertEqual(self.step("promote", "--agent", agent)[1], 0)

    def test_refusals_write_nothing(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = [self.local_check(agent, "r.txt")]
        self.write_result(agent, result)
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"], out["paths"]), (1, "unpromoted", [f"agents/{agent}/artifacts/r.txt"]))
        self.assertEqual(self.snapshot(), before)
        self.write_result(agent, {"units": []})
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"], out["state"]), (1, "invalid", "invalid"))
        self.assertTrue(out["errors"])
        self.assertEqual(self.snapshot(), before)
        self.write_result(agent, result)
        self.step("promote", "--agent", agent)
        validator_exit(self.skill, "validate-coverage-ledger.cjs", 1)
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"]), (1, "ledger-invalid"))
        self.assertEqual(self.snapshot(), before)
        validator_exit(self.skill, "validate-coverage-ledger.cjs", 0)
        self.assertEqual(self.fold(agent)[1], 0)
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"]), (1, "already-folded"))
        self.assertEqual(self.snapshot(), before)

    def test_uncovered_becomes_a_unit(self) -> None:
        self.release_plan("--profile", "standard")
        self.folded_hunter("a.py", uncovered=[UNCOVERED])
        new = [u for u in self.ledger() if u["surface"] == "new"]
        self.assertEqual([(u["status"], u["agent_id"]) for u in new], [("planned", None)])

    def test_uncovered_in_quick_and_diff_scope(self) -> None:
        self.folded_hunter("a.py", uncovered=[UNCOVERED])
        (new,) = [u for u in self.ledger() if u["surface"] == "new"]
        self.assertEqual((new["status"], new["unresolved"], new["subsystem"]),
                         ("deferred", ["quick_profile_final_critic"], "S"))
        self.assertEqual(new["canonical_refs"]["subsystem"], "profile/quick/all-in-scope-subsystems")

    def test_uncovered_out_of_diff_scope(self) -> None:
        base = self.git("rev-parse", "HEAD")
        (self.notes / "Project Note.md").write_text(f"## Release audits\n{RECORD.format(sha=base)}\n")
        self.commit("a.py", "changed")
        self.release_plan()
        self.folded_hunter("a.py", uncovered=[UNCOVERED])
        (new,) = [u for u in self.ledger() if u["surface"] == "new"]
        self.assertEqual((new["status"], new["unresolved"]), ("out_of_scope", ["a parallel path", "outside the run scope"]))

    def test_discard(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        out, code = self.fold(agent, "--discard", "x")
        self.assertEqual((code, out), (0, {"agent": agent, "discarded": "x", "units": ids}))
        u = self.ledger()[0]
        self.assertEqual((self.state()["briefs"][agent]["state"], u["status"], u["agent_id"], u["local_checks"]),
                         ("discarded", "planned", None, []))
        self.assertEqual(self.fold(agent)[0]["error"], "already-folded")
        self.assertEqual(self.step("brief", "--kind", "hunter", "--units", *ids)[0]["agent"], "h02")

    def test_same_fingerprint_from_two_hunters(self) -> None:
        a, _ = self.folded_hunter("a.py", candidates=[self.candidate("f-1", title="first")])
        b, _ = self.folded_hunter("b.py", candidates=[self.candidate("f-1", title="second")])
        c = self.state()["candidates"]["f-1"]
        self.assertEqual((c["body"]["title"], c["hunter"], c["later"]), ("first", a, [b]))
        prompt = Path(self.step("brief", "--kind", "verifier", "--fingerprint", "f-1")[0]["prompt"]).read_text()
        self.assertIn("first", prompt)
        self.assertNotIn("second", prompt)

    def test_brief_json_edit_changes_nothing(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids))
        self.path(f"agents/{agent}/brief.json").write_text(json.dumps({"kind": "verifier", "fingerprint": "x"}))
        out, code = self.fold(agent)
        self.assertEqual((code, out["kind"], out["units"]), (0, "hunter", ids))

    def test_discarded_verifier_puts_carried_unit_back(self) -> None:
        self.carried_run()
        self.write_context()
        agent = self.step("brief", "--kind", "verifier", "--fingerprint", "f-c")[0]["agent"]
        out, code = self.fold(agent, "--discard", "budget")
        self.assertEqual((code, out["units"]), (0, [self.U1]))
        u1 = self.unit(self.U1)
        self.assertEqual((u1["status"], u1["agent_id"]), ("planned", None))

    def test_reviewed_path_escape_writes_nothing(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["reviewed_paths"] = ["../../x"]
        result["units"][0]["checks"][0]["reviewed_paths"] = ["../../x"]
        self.write_result(agent, result)
        outside = sorted(p.name for p in self.root.iterdir())
        before = self.snapshot()
        out, code = self.fold(agent)
        self.assertEqual((code, out["error"]), (1, "invalid"))
        self.assertEqual((self.snapshot(), sorted(p.name for p in self.root.iterdir())), (before, outside))


if __name__ == "__main__":
    unittest.main()
