#!/usr/bin/env python3
"""Tests for `audit-run units`: `--add`, `--update`, the new-unit builder of fold."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit import common, units  # noqa: E402
from audit_fixtures import AuditRunCase  # noqa: E402

ID = "a%2Fb.py%23x%20y::B::S::ATTACK-CLASSES.md%23Access%20control"
STATE = {"profile": "standard", "prior": None, "scope": {"kind": "whole", "paths": None, "exclude": []}}
UNCOVERED = {"surface": "s", "boundary": "b", "subsystem": "x", "attack_class": "AI-AND-LLM.md#Tool",
             "starting_paths": ["a.py"], "reason": "r"}


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditUnitsTests(AuditRunCase):
    def test_add_builds_the_id_and_sorts(self) -> None:
        ids = self.add_units(self.entry("z.py"), self.entry("a/b.py#x y"))
        self.assertIn(ID, ids)
        got = [u["coverage_id"] for u in self.ledger()]
        self.assertEqual(got, sorted(got))
        u = self.ledger()[0]
        self.assertEqual((u["status"], u["wave"], u["attempts"], u["agent_id"], u["unresolved"]), ("planned", 1, [], None, []))

    def test_bad_refs_are_invalid(self) -> None:
        self.seed()
        for bad in ["é", " a", "a\u0007"]:
            before = self.snapshot()
            out, code = self.step("units", "--add", self.file([self.entry("ok.py"), self.entry(bad)]))
            self.assertEqual((code, out["error"], out["entry"]), (1, "invalid", 1), bad)
            self.assertTrue(out["errors"])
            self.assertEqual(self.snapshot(), before)

    def test_duplicates(self) -> None:
        self.add_units(self.entry("a.py"))
        before = self.snapshot()
        for entries in ([self.entry("b.py"), self.entry("b.py")], [self.entry("a.py")]):
            out, code = self.step("units", "--add", self.file(entries))
            self.assertEqual((code, out["error"]), (1, "duplicate"))
            self.assertIn("coverage_id", out)
            self.assertEqual(self.snapshot(), before)

    def test_companion_files_follow_the_ledger(self) -> None:
        self.add_units(self.entry("a.py", selected_companion_blocks=["AI-AND-LLM.md#Core discipline"]))
        self.assertEqual(self.metadata()["selected_companion_files"], ["AI-AND-LLM.md"])

    def test_status_and_reason_from_the_parent(self) -> None:
        (uid,) = self.add_units(self.entry("a.py", status="deferred", reason="budget"))
        (unit,) = self.ledger()
        self.assertEqual((unit["coverage_id"], unit["status"], unit["unresolved"]), (uid, "deferred", ["budget"]))
        out, code = self.step("units", "--add", self.file([self.entry("b.py", status="deferred")]))
        self.assertEqual((code, out["error"], out["entry"]), (1, "invalid", 0))

    def test_block_refs_name_a_bare_skill_file(self) -> None:
        self.add_units(self.entry("a.py"))
        for change in ({"attack": "../outside.md#H"}, {"selected_companion_blocks": ["/abs/outside.md#H"]},
                       {"ordinary_attack_class_block": "sub/x.md#H"},
                       {"excluded_blocks": [{"block": "../x.md#H", "reason": "r"}]}):
            before = self.snapshot()
            out, code = self.step("units", "--add", self.file([self.entry("b.py", **change)]))
            self.assertEqual((code, out["error"]), (1, "invalid"), change)
            self.assertEqual(self.snapshot(), before)
        (uid,) = [u["coverage_id"] for u in self.ledger()]
        out, code = self.step("units", "--update", self.file([{"coverage_id": uid, "selected_companion_blocks": ["../x.md#H"]}]))
        self.assertEqual((code, out["error"]), (1, "invalid"))

    def test_fold_derives_the_ordinary_block_from_the_attack_class_only(self) -> None:
        unit = units.build_unit({**UNCOVERED, "attack_class": "AI-AND-LLM.md#Core discipline",
                                 "ordinary_attack_class_block": "ATTACK-CLASSES.md#Access control"}, STATE)
        self.assertIsNone(unit["ordinary_attack_class_block"])
        self.assertEqual(unit["selected_companion_blocks"], ["AI-AND-LLM.md#Core discipline"])

    def test_update_planned_unit(self) -> None:
        (uid,) = self.add_units(self.entry("a.py"))
        out, code = self.step("units", "--update", self.file(
            [{"coverage_id": uid, "ordinary_attack_class_block": "ATTACK-CLASSES.md#Injection", "starting_paths": ["b.py"]}]))
        self.assertEqual((code, out), (0, {"updated": [uid]}))
        u = self.ledger()[0]
        self.assertEqual((u["coverage_id"], u["ordinary_attack_class_block"], u["starting_paths"]),
                         (uid, "ATTACK-CLASSES.md#Injection", ["b.py"]))
        self.assertEqual(u["canonical_refs"]["attack_class"], "ATTACK-CLASSES.md#Access control")

    def test_update_refused(self) -> None:
        (uid,) = self.add_units(self.entry("a.py"))
        state = self.state()
        state["briefs"]["h01"] = {"kind": "hunter", "units": [uid], "state": "open"}
        self.path("audit-run.json").write_text(json.dumps(state))
        before = self.snapshot()
        out, code = self.step("units", "--update", self.file([{"coverage_id": uid, "starting_paths": ["b.py"]}]))
        self.assertEqual((code, out["error"]), (1, "not-planned"))
        out, code = self.step("units", "--update", self.file([{"coverage_id": "nope", "starting_paths": ["b.py"]}]))
        self.assertEqual((code, out["error"]), (1, "unknown-unit"))
        out, code = self.step("units", "--update", self.file([{"coverage_id": uid, "surface": "other"}]))
        self.assertEqual((code, out["error"]), (1, "invalid"))
        self.assertEqual(self.snapshot(), before)

    def test_not_a_json_array(self) -> None:
        self.seed()
        for path in (self.file({}), str(self.root / "missing.json")):
            out, code = self.step("units", "--add", path)
            self.assertEqual((code, out["error"], out["entry"]), (1, "invalid", None))

    def test_build_unit(self) -> None:
        u = units.build_unit(UNCOVERED, STATE)
        self.assertEqual((u["ordinary_attack_class_block"], u["selected_companion_blocks"], u["excluded_blocks"],
                          u["prior_status"], u["status"], u["wave"], u["attempts"]),
                         (None, ["AI-AND-LLM.md#Tool"], [], "none", "planned", 1, []))
        self.assertEqual(u["coverage_id"], common.coverage_id(u["canonical_refs"]))
        quick = units.build_unit(UNCOVERED, {**STATE, "profile": "quick", "prior": "Run 1"})
        self.assertEqual((quick["canonical_refs"]["subsystem"], quick["status"], quick["unresolved"], quick["prior_status"]),
                         (common.QUICK_SUBSYSTEM, "deferred", ["quick_profile_final_critic"], "new"))
        out = units.build_unit({**UNCOVERED, "starting_paths": ["c.py"]},
                               {**STATE, "scope": {"kind": "diff", "paths": ["a.py"], "exclude": []}})
        self.assertEqual((out["status"], out["unresolved"]), ("out_of_scope", ["r", "outside the run scope"]))

    def test_merge_new_collision(self) -> None:
        unit = units.build_unit(UNCOVERED, STATE)
        ledger = [dict(unit)]
        self.assertEqual(units.merge_new(ledger, [dict(unit)]), ([], [unit["coverage_id"]]))
        with self.assertRaises(common.StepError) as caught:
            units.merge_new(ledger, [{**unit, "starting_paths": ["b.py"]}])
        self.assertEqual(caught.exception.payload["error"], "collision")
        self.assertEqual(units.merge_new(ledger, [units.build_unit({**UNCOVERED, "surface": "t"}, STATE)])[0],
                         [units.build_unit({**UNCOVERED, "surface": "t"}, STATE)["coverage_id"]])
        self.assertEqual(len(ledger), 2)


if __name__ == "__main__":
    unittest.main()
