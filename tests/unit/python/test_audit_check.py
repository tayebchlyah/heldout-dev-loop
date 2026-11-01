#!/usr/bin/env python3
"""Tests for `audit-run check`: result file, kind rules."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit_fixtures import AuditRunCase, validator_calls, validator_exit  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditCheckTests(AuditRunCase):
    def invalid(self, agent: str, *names: str) -> list[str]:
        out, code = self.check(agent)
        self.assertEqual((code, out.get("ok"), out.get("state")), (1, False, "invalid"), out)
        joined = "\n".join(out["errors"])
        for name in names:
            self.assertIn(name, joined)
        return out["errors"]

    def test_valid_hunter_result(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids, hardening=["note"]))
        self.assertEqual(self.check(agent), ({"ok": True}, 0))

    def test_each_broken_field(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        uid = ids[0]
        cases = {
            "missing id": (lambda r: r["units"].pop(), uid),
            "unassigned id": (lambda r: r["units"].append({**r["units"][0], "coverage_id": "other"}), "other"),
            "id twice": (lambda r: r["units"].append(dict(r["units"][0])), uid),
            "covered with unresolved": (lambda r: r["units"][0].update(unresolved=["x"]), "unresolved"),
            "candidate without fingerprint": (lambda r: r["units"][0].update(disposition="candidate"),
                                              "candidate_fingerprints"),
            "other check agent": (lambda r: r["units"][0]["checks"][0].update(agent_id="h09"), "agent_id"),
            "source with artifact": (lambda r: r["units"][0]["checks"][0].update(artifact=f"agents/{agent}/artifacts/x"),
                                     "artifact"),
            "artifact outside": (lambda r: r["units"][0]["checks"].append(
                {**self.local_check(agent, "x.txt"), "artifact": f"agents/{agent}/x.txt"}), "artifact"),
            "artifact not in scratch": (lambda r: r["units"][0]["checks"].append(
                {**self.local_check(agent, "y.txt"), "artifact": f"agents/{agent}/artifacts/none.txt"}), "none.txt"),
            "paths not the union": (lambda r: r["units"][0].update(reviewed_paths=["a.py", "b.py"]), "reviewed_paths"),
            "absolute path": (lambda r: r["units"][0].update(reviewed_paths=["/etc/passwd"]), "/etc/passwd"),
            "dotdot path": (lambda r: r["units"][0]["checks"][0].update(reviewed_paths=["../../x"]), "../../x"),
            "bad fingerprint": (lambda r: (r["candidates"].append(self.candidate("-bad")),
                                           r["units"][0].update(disposition="candidate", candidate_fingerprints=["-bad"])),
                                "-bad"),
            "unknown fingerprint": (lambda r: r["units"][0].update(disposition="candidate",
                                                                   candidate_fingerprints=["f-zz"]), "f-zz"),
        }
        for name, (change, named) in cases.items():
            with self.subTest(name):
                result = self.hunter_result(agent, ids)
                change(result)
                self.write_result(agent, result)
                self.invalid(agent, named)

    def test_block_refs_name_a_bare_skill_file(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        base = {"surface": "n", "boundary": "B", "subsystem": "S", "attack_class": "AI-AND-LLM.md#Tool",
                "starting_paths": ["a.py"], "reason": "r"}
        bad = {"dotdot attack class": {"attack_class": "../outside.md#H"},
               "absolute companion block": {"selected_companion_blocks": ["/abs/outside.md#H"]},
               "folder ordinary block": {"ordinary_attack_class_block": "sub/x.md#H"},
               "excluded block": {"excluded_blocks": [{"block": "..\\x.md#H", "reason": "r"}]},
               "not a markdown file": {"selected_companion_blocks": ["x.txt#H"]}}
        for name, change in bad.items():
            with self.subTest(name):
                self.write_result(agent, self.hunter_result(agent, ids, uncovered=[{**base, **change}]))
                self.invalid(agent, "uncovered[0]")
        self.write_result(agent, self.hunter_result(agent, ids, uncovered=[base]))
        self.assertEqual(self.check(agent), ({"ok": True}, 0))

    def test_candidate_through_the_findings_validator(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids, candidates=[self.candidate("f-1")])
        result["units"][0].update(disposition="candidate", candidate_fingerprints=["f-1"])
        self.write_result(agent, result)
        self.assertEqual(self.check(agent)[1], 0)
        self.assertEqual(len(validator_calls(self.skill, "validate-findings.cjs")), 1)
        before = set(self.snapshot())
        validator_exit(self.skill, "validate-findings.cjs", 1)
        self.invalid(agent, "candidates")
        self.assertEqual(set(self.snapshot()), before)

    def test_missing_and_invalid_files(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        out, code = self.check(agent)
        self.assertEqual((code, out), (1, {"ok": False, "state": "missing", "errors": []}))
        for value in (b"nope", b"[]", b" " * (5 * 1024 * 1024 + 1)):
            self.write_result(agent, value)
            self.invalid(agent)
        result = self.path(f"agents/{agent}/scratch/result.json")
        result.unlink()
        outside = self.root / "outside.json"
        outside.write_text(json.dumps({"units": [], "secret": "OUTSIDE-TEXT"}))
        result.symlink_to(outside)
        before = self.snapshot()
        errors = self.invalid(agent)
        self.assertNotIn("OUTSIDE-TEXT", "\n".join(errors))
        self.assertEqual(self.snapshot(), before)
        out, code = self.check("h09")
        self.assertEqual((code, out["error"]), (1, "unknown-agent"))

    def test_critic_rules(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids))
        state = self.state()
        state["briefs"][agent]["state"] = "folded"
        self.path("audit-run.json").write_text(json.dumps(state))
        ledger = self.ledger()
        ledger[0].update(status="covered", reviewed_paths=["a.py"], local_checks=[self.src_check(agent)])
        self.path("coverage-ledger.json").write_text(json.dumps(ledger))
        critic, code = self.step("brief", "--kind", "critic")
        self.assertEqual(code, 0, critic)
        c = critic["agent"]
        missing = {"surface": "s", "boundary": "b", "subsystem": "x", "attack_class": "ATTACK-CLASSES.md#Injection",
                   "starting_paths": ["b.py"], "reason": "gap"}
        good = {"missing_units": [], "reassign_ids": [ids[0]], "resolved_prior_leads": [], "stop": False,
                "notes": {ids[0]: "closed without the parallel path"}}
        self.write_result(c, good)
        self.assertEqual(self.check(c)[1], 0)
        for name, result, named in (
                ("stop with a missing unit", {**good, "reassign_ids": [], "notes": {}, "missing_units": [missing],
                                              "stop": True}, "stop"),
                ("unknown reassign id", {**good, "reassign_ids": ["nope"], "notes": {"nope": "r"}}, "nope"),
                ("reassign with no note", {**good, "notes": {}}, ids[0]),
                ("resolved lead with no note", {**good, "reassign_ids": [], "notes": {},
                                                "resolved_prior_leads": ["f-c"]}, "f-c")):
            with self.subTest(name):
                self.write_result(c, result)
                self.invalid(c, named)

    def critic_agent(self) -> tuple[str, str]:
        self.release_plan("--profile", "standard")
        _, (uid,) = self.folded_hunter("a.py")
        out, code = self.step("brief", "--kind", "critic")
        self.assertEqual(code, 0, out)
        return out["agent"], uid

    def test_critic_result_with_a_missing_or_odd_field_is_invalid(self) -> None:
        c, uid = self.critic_agent()
        full = self.critic_result(reassign_ids=[uid], notes={uid: "why"})
        cases = (
            ("no reassign_ids", {k: v for k, v in full.items() if k != "reassign_ids"}, "reassign_ids"),
            ("no missing_units", {k: v for k, v in full.items() if k != "missing_units"}, "missing_units"),
            ("no resolved_prior_leads", {k: v for k, v in full.items() if k != "resolved_prior_leads"},
             "resolved_prior_leads"),
            ("list lead", {**full, "resolved_prior_leads": [["x"]]}, "resolved_prior_leads"),
            ("repeated reassign id", {**full, "reassign_ids": [uid, uid]}, f"{uid} repeated"),
        )
        for name, result, named in cases:
            with self.subTest(name):
                self.write_result(c, result)
                self.invalid(c, named)

    def test_unusable_checks_do_not_crash_fold_or_promote(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, {**self.hunter_result(agent, ids), "checks": 1})
        for name in ("promote", "fold"):
            out, code = self.step(name, "--agent", agent)
            self.assertEqual(code, 0, out)
        other, ids = self.hunter_brief("b.py")
        result = self.hunter_result(other, ids)
        result["units"][0]["checks"] = 1
        self.write_result(other, result)
        self.assertEqual(self.step("promote", "--agent", other)[1], 0)

    def test_verifier_and_recheck_rules(self) -> None:
        self.carried_run()
        self.write_context()
        v = self.step("brief", "--kind", "verifier", "--fingerprint", "f-c")[0]["agent"]
        record = {**self.record("f-c", "confirmed")}
        self.write_result(v, {"decision": "confirmed", "record": record, "checks": [self.src_check(v)]})
        self.assertEqual(self.check(v)[1], 0)
        self.write_result(v, {"decision": "rejected", "record": record, "checks": [self.src_check(v)]})
        self.invalid(v, "verdict")
        state = self.state()
        state["final"]["f-c"] = record
        self.path("audit-run.json").write_text(json.dumps(state))
        r = self.step("brief", "--kind", "recheck", "--fingerprint", "f-c")[0]["agent"]
        self.write_result(r, {"decision": "verified", "fingerprint": "f-c"})
        self.assertEqual(self.check(r)[1], 0)
        self.write_result(r, {"decision": "replace", "reason": "why", "record": {**record, "fingerprint": "f-other"}})
        self.invalid(r, "fingerprint")

    def test_kind_comes_from_the_state(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids))
        self.path(f"agents/{agent}/brief.json").write_text(json.dumps({"kind": "critic", "units": []}))
        self.assertEqual(self.check(agent)[1], 0)

    def test_errors_capped(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        self.write_result(agent, self.hunter_result(agent, ids, uncovered=[{"surface": " bad"}] * 60))
        self.assertEqual(len(self.invalid(agent)), 50)

    def test_hunter_artifact_of_another_agent(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"].append({**self.local_check(agent, "a"), "artifact": "agents/h02/artifacts/a"})
        result["units"][0]["reviewed_paths"] = ["a.py"]
        self.write_result(agent, result)
        self.invalid(agent, "agents/h02/artifacts/a")


if __name__ == "__main__":
    unittest.main()
