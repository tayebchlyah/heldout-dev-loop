#!/usr/bin/env python3
"""Held-out oracle tests for `audit-run fold` (spec: Audit Run Tools, oracle 21 to 28).
Fixtures: `audit_run_tools_support.py` (the `hunted` run: units app and upload in brief h01, admin planned).

Fixture choices the spec leaves open:
- The result's `hardening` is kept on each unit in a field named `hardening` (oracle 21 names it).
- "The stub ledger validator ran on the new ledger": one logged input of `validate-coverage-ledger.cjs` equals,
  as JSON, the ledger file after the fold.
- A unit made in profile `quick` is found by its `surface` label (D14 changes its subsystem ref).
- "Same fields" (oracle 26): the D14 fields (refs, labels, starting paths, block fields) of a planned unit.
- The resolved lead (oracle 25) is the prior `needs_validation` fingerprint of carried U2 (oracle 3 fixture).
- Final records are read from `findings.json` after a `findings` call that exits `open`.
"""

from __future__ import annotations

import json
import unittest

from audit_run_tools_support import (QUICK_REASON, SCOPE_REASON, AuditCase, check_of, cid, confirmed,
                                     critic_result, entry, hunter_result, labels, needs_validation, snapshot,
                                     unit_out, verifier_result)


EMPTY = {"agent_id": None, "reviewed_paths": [], "local_checks": [], "result_fingerprints": [], "unresolved": []}


def gap(name: str, paths: list[str] | None = None) -> dict:
    return {**labels(name), "starting_paths": list(paths or [f"src/{name}.py"]), "selected_companion_blocks": [],
            "excluded_blocks": [], "reason": f"No unit reads the {name} route."}


def uncovered(name: str, paths: list[str] | None = None) -> dict:
    out = gap(name, paths)
    for key in ("selected_companion_blocks", "excluded_blocks"):
        out.pop(key)
    return out


class FoldCase(AuditCase):

    def by_surface(self, run, surface: str) -> dict:
        found = [u for u in run.ledger() if u.get("surface") == surface]
        self.assertEqual(len(found), 1, f"one unit with surface {surface}: {found}")
        return found[0]

    def assert_reason(self, unit: dict, status: str, reason: str) -> None:
        self.assertEqual(unit.get("status"), status, unit)
        self.assertTrue(any(reason in str(x) for x in unit.get("unresolved") or []), unit)

    def assert_reopened(self, unit: dict, status: str) -> None:
        self.assertEqual((unit.get("status"), unit.get("wave")), (status, 2), unit)
        self.assertEqual({k: unit.get(k) for k in EMPTY if k != "unresolved"},
                         {k: v for k, v in EMPTY.items() if k != "unresolved"}, unit)


class FoldTests(FoldCase):

    def test_o21_hunter_fold(self) -> None:
        run, h01, result, _ = self.hunted()
        run.write_result(h01, result)
        run.ok("promote", "--agent", h01)
        run.ok("fold", "--agent", h01)
        for out in result["units"]:
            unit = run.unit(out["coverage_id"])
            self.assertEqual((unit.get("status"), unit.get("agent_id")), (out["disposition"], h01), unit)
            self.assertEqual(unit.get("local_checks"), out["checks"], unit)
            self.assertEqual(sorted(unit.get("reviewed_paths") or []), sorted(out["reviewed_paths"]), unit)
            self.assertEqual(unit.get("result_fingerprints"), out["candidate_fingerprints"], unit)
            self.assertEqual(unit.get("unresolved"), out["unresolved"], unit)
            self.assertEqual(unit.get("hardening"), result["hardening"], unit)
        self.assertEqual(run.brief_state(h01).get("state"), "folded")
        self.assertIn(run.ledger(), run.repo.validator_runs("validate-coverage-ledger"))

    def test_o22_fold_refusals(self) -> None:
        run, h01, result, (app, upload, admin) = self.hunted()
        run.write_result(h01, result)
        with self.subTest(case="unpromoted"):
            before = snapshot(run.path)
            run.fails("unpromoted", "fold", "--agent", h01)
            self.assert_same(before, run.path)
        with self.subTest(case="invalid"):
            h02 = run.hunter(admin)
            run.write_result(h02, {"units": []})
            before = snapshot(run.path)
            self.assertTrue(run.fails("invalid", "fold", "--agent", h02).get("errors"))
            self.assert_same(before, run.path)
        run.ok("promote", "--agent", h01)
        with self.subTest(case="ledger-invalid"):
            before = snapshot(run.path)
            run.repo.validator_exit("validate-coverage-ledger", 1)
            self.assertIn("tail", run.fails("ledger-invalid", "fold", "--agent", h01))
            run.repo.validator_exit("validate-coverage-ledger", 0)
            self.assert_same(before, run.path)
        run.ok("fold", "--agent", h01)
        run.fails("already-folded", "fold", "--agent", h01)

    def hunt_with_gap(self, run, unit: str, path: str) -> None:
        h01 = run.hunter(unit)
        run.finish(h01, hunter_result([unit_out(unit, "covered", [check_of(h01, [path])])],
                                      uncovered=(uncovered("report"),)))

    def test_o23_uncovered_entry_becomes_a_unit(self) -> None:
        with self.subTest(profile="standard"):
            run = self.start()
            self.hunt_with_gap(run, run.add(entry("app"))[0], "src/app.py")
            self.assertEqual(run.unit(cid(labels("report"))).get("status"), "planned")
        with self.subTest(profile="quick"):
            run = self.start(profile="quick")
            self.hunt_with_gap(run, run.add(entry("app"))[0], "src/app.py")
            self.assert_reason(self.by_surface(run, "src/report.py#entry"), "deferred", QUICK_REASON)
        with self.subTest(scope="diff"):
            r = self.repo()
            r.prior_run(1, r.C, "complete", [], [])
            r.record_at(1, r.C)
            r.commit({"a.py": "def a():\n    return 2\n"}, "D")
            run = self.start(r=r)
            self.assertEqual(run.plan.get("scope"), f"{r.C}..{run.plan['head']}", run.plan)
            self.hunt_with_gap(run, run.add(entry("aa", paths=["a.py"]))[0], "a.py")
            self.assert_reason(run.unit(cid(labels("report"))), "out_of_scope", SCOPE_REASON)

    def test_o24_discard_a_hunter(self) -> None:
        run, h01, result, (app, upload, _) = self.hunted()
        run.write_result(h01, result)
        run.ok("fold", "--agent", h01, "--discard", "x")
        self.assertEqual(run.brief_state(h01).get("state"), "discarded")
        for coverage_id in (app, upload):
            unit = run.unit(coverage_id)
            self.assertEqual(unit.get("status"), "planned", unit)
            self.assertEqual({k: unit.get(k) for k in EMPTY}, EMPTY, unit)


class CriticAndVerifierFoldTests(FoldCase):

    def covered_app(self, run) -> tuple[str, dict]:
        (app,) = run.add(entry("app", prior="new"))
        h01 = run.hunter(app)
        run.finish(h01, hunter_result([unit_out(app, "covered", [check_of(h01, ["src/app.py"])])]))
        return app, run.unit(app)

    def test_o25_critic_fold(self) -> None:
        reassign_note = "REASSIGN-NOTE: the export branch of src/app.py was not read."
        with self.subTest(profile="standard"):
            run = self.start(r=self.prior_repo(("c.py",)))
            app, live = self.covered_app(run)
            lead_note = "LEAD-NOTE: b.py checks the media root since commit D."
            payload = run.finish(run.brief("critic"), critic_result(
                missing=(gap("report"),), reassign=(app,), leads=("fp-nv",), stop=False,
                notes={app: reassign_note, "fp-nv": lead_note}))
            self.assertIs(payload.get("stop"), False, payload)
            self.assertEqual(run.unit(cid(labels("report"))).get("status"), "planned")
            unit = run.unit(app)
            self.assert_reopened(unit, "planned")
            self.assertEqual(unit.get("unresolved"), [], unit)
            archived = (unit.get("attempts") or [{}])[-1]
            for key in ("wave", "status", "agent_id", "reviewed_paths", "local_checks", "result_fingerprints",
                        "unresolved"):
                self.assertEqual(archived.get(key), live.get(key), f"attempts[-1].{key}: {archived}")
            self.assertEqual(archived.get("reassignment_reason"), reassign_note, archived)
            carried = run.unit(cid(labels("u2")))
            self.assertIn("fp-nv", json.dumps(carried.get("resolved_prior_leads")), carried)
            self.assertIn(lead_note, json.dumps(carried), carried)
        with self.subTest(profile="quick"):
            run = self.start(profile="quick")
            app, _ = self.covered_app(run)
            run.finish(run.brief("critic"), critic_result(missing=(gap("report"),), reassign=(app,), stop=False,
                                                          notes={app: reassign_note}))
            self.assert_reason(self.by_surface(run, "src/report.py#entry"), "deferred", QUICK_REASON)
            unit = run.unit(app)
            self.assert_reopened(unit, "deferred")
            self.assert_reason(unit, "deferred", QUICK_REASON)

    def test_o26_known_unit_from_a_critic(self) -> None:
        run = self.start()
        (known,) = run.add(entry("report"))
        payload = run.finish(run.brief("critic"), critic_result(missing=(gap("report"),), stop=False))
        self.assertIn(known, payload.get("duplicates") or [], payload)
        c02 = run.brief("critic")
        run.write_result(c02, critic_result(missing=(gap("report", ["src/report.py", "src/app.py"]),), stop=False))
        before = snapshot(run.path)
        run.fails("collision", "fold", "--agent", c02)
        self.assert_same(before, run.path)

    def test_o27_verifier_fold_on_a_carried_unit(self) -> None:
        run = self.start(r=self.prior_repo(("b.py",)))
        v01 = run.brief("verifier", "--fingerprint", "fp-conf")
        record = confirmed("fp-conf", "Token in the log", file="a.py")
        result = verifier_result(record, v01, "a.py")
        run.finish(v01, result, promote=True)
        unit = run.unit(cid(labels("u1")))
        self.assertEqual((unit.get("status"), unit.get("agent_id"), unit.get("result_fingerprints")),
                         ("candidate", "v01", ["fp-conf"]), unit)
        self.assertEqual(unit.get("local_checks"), result["checks"], unit)
        run.call("findings")
        self.assertEqual([r for r in run.json("findings.json") if r.get("fingerprint") == "fp-conf"], [record])

    def test_o28_recheck_replace(self) -> None:
        run, h01, result, _ = self.hunted()
        run.finish(h01, result, promote=True)
        v01 = run.brief("verifier", "--fingerprint", "fp-one")
        record = needs_validation("fp-one", "Join may escape")
        run.finish(v01, verifier_result(record, v01, "src/upload.py"))
        new = dict(record, title="Upload join may leave the media root")
        payload = run.finish(run.brief("recheck", "--fingerprint", "fp-one"),
                             {"decision": "replace", "reason": "A clearer title.", "record": new})
        self.assertIn("title", json.dumps(payload.get("diff")), payload)
        self.assertNotIn("blockers", json.dumps(payload.get("diff")), payload)
        run.call("findings")
        self.assertEqual([r for r in run.json("findings.json") if r.get("fingerprint") == "fp-one"], [new])
        r02 = run.brief("recheck", "--fingerprint", "fp-one")
        run.write_result(r02, {"decision": "replace", "reason": "Reworded.",
                               "record": dict(new, description="STUB-FAIL: a record the validator refuses.")})
        before = snapshot(run.path)
        run.fails("invalid", "fold", "--agent", r02)
        self.assert_same(before, run.path)


if __name__ == "__main__":
    unittest.main()
