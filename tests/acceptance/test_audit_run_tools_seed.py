#!/usr/bin/env python3
"""Held-out oracle tests for `release-plan`'s `plan.json`, `audit-run seed` and `audit-run units`
(spec: Audit Run Tools, oracle 1 to 8a). Fixtures: `audit_run_tools_support.py`.

Fixture choices the spec leaves open:
- Prior runs are written by the test (`run-metadata.json`, `coverage-ledger.json`, `findings.json`) at commit C;
  the units U1 to U6 of oracle 3 start at `a.py`, `b.py`, `c.py`, `d.py`; fingerprints are `fp-conf` (`f-c`),
  `fp-nv` (`f-n`), `fp-rej` (`f-r`), `fp-open` (`f-u`).
- A scoped run (oracle 4b) comes from a `## Release audits` record line at C in the project note.
- "names the entry": the `invalid` JSON has a non-null `entry`.
"""

from __future__ import annotations

import json
import unittest

from audit_run_tools_support import (ACCESS, AI_CORE, INJECTION, SCOPE_REASON, AuditCase, cid, entry, labels,
                                     ledger_unit, snapshot)


EMPTY = {"agent_id": None, "reviewed_paths": [], "local_checks": [], "result_fingerprints": [], "unresolved": []}


def uid(name: str) -> str:
    return cid(labels(name))


class PlanAndSeedTests(AuditCase):

    def test_o01_release_plan_writes_plan_json(self) -> None:
        r = self.repo()
        run = self.plan(r)
        self.assertTrue((run.path / "plan.json").is_file(), "release-plan wrote no plan.json")
        self.assertEqual(json.loads((run.path / "plan.json").read_text()), run.plan)

    def test_o02_seed_with_no_prior_run(self) -> None:
        run = self.plan(self.repo())
        payload = run.ok("seed")
        self.assertIsNone(payload.get("prior"), payload)
        self.assertIn("prior", payload)
        meta = run.metadata()
        self.assertEqual(meta.get("run_id"), "Run 1", meta)
        self.assertEqual((meta.get("source_ref") or {}).get("commit"), run.plan["head"], meta)
        self.assertEqual(meta.get("run_status"), "in_progress", meta)
        self.assertEqual((meta.get("profile"), meta.get("budget")), (run.plan["profile"], run.plan["budget"]), meta)
        self.assertEqual(run.ledger(), [])

    def assert_carried(self, unit: dict, prior_status: str) -> None:
        self.assertEqual(unit.get("prior_status"), prior_status, unit)
        self.assertEqual((unit.get("status"), unit.get("wave")), ("planned", 1), unit)
        self.assertEqual({k: unit.get(k) for k in EMPTY}, EMPTY, unit)

    def test_o03_seed_carries_the_open_units_of_the_last_finished_run(self) -> None:
        run = self.plan(self.prior_repo(("b.py",)))
        self.assertEqual(run.plan["run"], "Run 3", run.plan)
        payload = run.ok("seed")
        self.assertEqual(payload.get("prior"), "Run 1", payload)
        self.assertEqual(sorted(payload.get("carried") or []), sorted([uid("u1"), uid("u2"), uid("u5")]), payload)
        self.assertEqual(payload.get("touched"), [{"coverage_id": uid("u3"), "paths": ["b.py"]}], payload)
        ids = [u.get("coverage_id") for u in run.ledger()]
        self.assertEqual(sorted(ids), sorted([uid("u1"), uid("u2"), uid("u5")]), ids)
        self.assert_carried(run.unit(uid("u1")), "prior_confirmed_same_source")
        self.assert_carried(run.unit(uid("u2")), "prior_needs_validation")
        self.assert_carried(run.unit(uid("u5")), "prior_deferred")

    def test_o04_changed_trace_file_is_changed_source(self) -> None:
        run = self.plan(self.prior_repo(("a.py", "b.py")))
        run.ok("seed")
        self.assert_carried(run.unit(uid("u1")), "prior_confirmed_changed_source")

    def test_o04a_incomplete_prior_and_an_unvalidated_candidate(self) -> None:
        r = self.repo()
        r.prior_run(1, r.C, "incomplete", [ledger_unit("u7", "candidate", ["c.py"], reviewed=("c.py",),
                                                        fps=("fp-open",))], [])
        r.commit({"b.py": "def b():\n    return 3\n"}, "D")
        run = self.plan(r)
        payload = run.ok("seed")
        self.assertEqual(payload.get("prior"), "Run 1", payload)
        self.assert_carried(run.unit(uid("u7")), "prior_deferred")

    def test_o04b_scoped_run_carries_an_unchanged_unit_out_of_scope(self) -> None:
        run = self.plan(self.prior_repo(("a.py",), scoped=True))
        self.assertEqual(run.plan.get("scope"), f"{run.repo.C}..{run.plan['head']}", run.plan)
        run.ok("seed")
        unit = run.unit(uid("u5"))
        self.assertEqual(unit.get("status"), "out_of_scope", unit)
        self.assertTrue(any(SCOPE_REASON in str(x) for x in unit.get("unresolved") or []), unit)

    def test_o05_seed_twice_or_with_no_plan(self) -> None:
        with self.subTest(case="already-seeded"):
            run = self.plan(self.repo())
            run.ok("seed")
            before = snapshot(run.path)
            run.fails("already-seeded", "seed")
            self.assert_same(before, run.path)
        with self.subTest(case="no-plan"):
            run = self.plan(self.repo())
            (run.path / "plan.json").unlink(missing_ok=True)
            before = snapshot(run.path)
            run.fails("no-plan", "seed")
            self.assert_same(before, run.path)


class UnitsTests(AuditCase):

    def test_o06_coverage_id_and_sorted_ledger(self) -> None:
        run = self.start()
        refs = {"surface": "a/b.py#x y", "boundary": "B", "subsystem": "S", "attack_class": ACCESS}
        later = entry("zz", refs={"surface": "z.py#z", "boundary": "B", "subsystem": "S", "attack_class": ACCESS},
                      paths=["a.py"])
        first = entry("x", refs=refs, paths=["a.py"])
        payload = run.ok("units", "--add", str(run.repo.input([later, first])))
        want = "a%2Fb.py%23x%20y::B::S::ATTACK-CLASSES.md%23Access%20control"
        self.assertIn(want, payload.get("added") or [], payload)
        ids = [u.get("coverage_id") for u in run.ledger()]
        self.assertIn(want, ids)
        self.assertEqual(ids, sorted(ids))

    def test_o07_bad_refs_and_duplicates(self) -> None:
        run = self.start()
        run.add(entry("known"))
        bad = {"not NFC": "src/café.py#entry", "leading space": " src/x.py#entry",
               "control character": "src/x\u0007.py#entry"}
        for case, surface in bad.items():
            with self.subTest(case=case):
                refs = dict(labels("x"), surface=surface)
                before = snapshot(run.path)
                payload = run.fails("invalid", "units", "--add", str(run.repo.input([entry("ok"), entry("x", refs=refs)])))
                self.assertIsNotNone(payload.get("entry"), payload)
                self.assert_same(before, run.path)
        for case, entries in (("one id twice", [entry("twice"), entry("twice")]),
                              ("id in the ledger", [entry("fresh"), entry("known")])):
            with self.subTest(case=case):
                before = snapshot(run.path)
                run.fails("duplicate", "units", "--add", str(run.repo.input(entries)))
                self.assert_same(before, run.path)

    def test_o08_selected_companion_files(self) -> None:
        run = self.start()
        run.add(entry("chat", companions=(AI_CORE,)))
        self.assertEqual(run.metadata().get("selected_companion_files"), ["AI-AND-LLM.md"])

    def test_o08a_update_a_planned_unit_only(self) -> None:
        run = self.start()
        target, busy = run.add(entry("app"), entry("auth"))
        old = run.unit(target)
        change = {"coverage_id": target, "starting_paths": ["src/app.py"], "ordinary_attack_class_block": INJECTION,
                  "selected_companion_blocks": [], "excluded_blocks": []}
        payload = run.ok("units", "--update", str(run.repo.input([change])))
        self.assertEqual(payload.get("updated"), [target], payload)
        new = run.unit(target)
        self.assertEqual(new.get("ordinary_attack_class_block"), INJECTION, new)
        self.assertEqual((new.get("coverage_id"), new.get("canonical_refs")),
                         (old.get("coverage_id"), old.get("canonical_refs")), new)
        run.hunter(busy)
        before = snapshot(run.path)
        run.fails("not-planned", "units", "--update", str(run.repo.input([dict(change, coverage_id=busy)])))
        self.assert_same(before, run.path)


if __name__ == "__main__":
    unittest.main()
