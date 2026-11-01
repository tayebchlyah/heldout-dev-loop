#!/usr/bin/env python3
"""Tests for `audit-run seed`: prior run, carry, `touched`, metadata."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit import common  # noqa: E402
from audit_fixtures import AuditRunCase, validator_exit  # noqa: E402
from test_release import RECORD  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditSeedTests(AuditRunCase):
    def carry_setup(self, status: str = "complete", change: str = "b.py") -> dict[str, str]:
        start = self.git("rev-parse", "HEAD")
        units = {
            "U1": self.prior_unit("u1", "candidate", fps=("f-c",)),
            "U2": self.prior_unit("u2", "candidate", fps=("f-n",)),
            "U3": self.prior_unit("u3", "covered", paths=("b.py",)),
            "U4": self.prior_unit("u4", "covered", paths=("c.py",)),
            "U5": self.prior_unit("u5", "deferred", paths=("c.py",)),
            "U6": self.prior_unit("u6", "candidate", fps=("f-r",)),
        }
        findings = [self.record("f-c", "confirmed"), self.record("f-n", "needs_validation"), self.record("f-r", "rejected")]
        self.prior_run(1, status, list(units.values()), findings, start)
        (self.notes / "Security Audit" / "Run 2").mkdir()
        self.commit(change, "changed")
        self.release_plan()
        return {name: u["coverage_id"] for name, u in units.items()}

    def test_no_prior(self) -> None:
        plan, _ = self.release_plan()
        out = self.seed()
        meta = self.metadata()
        self.assertEqual((meta["run_id"], meta["source_ref"]["commit"], meta["run_status"], meta["profile"], meta["budget"]),
                         ("Run 1", plan["head"], "in_progress", plan["profile"], plan["budget"]))
        self.assertEqual((self.ledger(), out["prior"], out["units"]), ([], None, 0))
        self.assertEqual(meta["execution_policy"], "sandboxed-source-and-local-only")
        self.assertEqual(meta["result_transport"], "file: agents/<agent-id>/scratch/result.json")
        self.assertEqual(self.state()["briefs"], {})

    def test_carry_rules(self) -> None:
        ids = self.carry_setup()
        out = self.seed()
        self.assertEqual(out["prior"], "Run 1")
        units = {u["coverage_id"]: u for u in self.ledger()}
        self.assertEqual({i: units[i]["prior_status"] for i in out["carried"]},
                         {ids["U1"]: "prior_confirmed_same_source", ids["U2"]: "prior_needs_validation",
                          ids["U5"]: "prior_deferred"})
        for name in ("U1", "U2", "U5"):
            u = units[ids[name]]
            self.assertEqual((u["status"], u["wave"], u["agent_id"], u["reviewed_paths"], u["local_checks"],
                              u["result_fingerprints"], u["unresolved"]), ("planned", 1, None, [], [], [], []))
        self.assertEqual(units[ids["U1"]]["prior_fingerprints"], ["f-c"])
        self.assertEqual(out["touched"], [{"coverage_id": ids["U3"], "paths": ["b.py"]}])
        self.assertEqual(set(self.state()["prior_records"]), {"f-c", "f-n", "f-r"})

    def test_changed_trace_is_changed_source(self) -> None:
        ids = self.carry_setup(change="a.py")
        self.seed()
        units = {u["coverage_id"]: u for u in self.ledger()}
        self.assertEqual(units[ids["U1"]]["prior_status"], "prior_confirmed_changed_source")

    def test_incomplete_prior_unvalidated_candidate(self) -> None:
        start = self.git("rev-parse", "HEAD")
        u7 = self.prior_unit("u7", "candidate", fps=("f-u",))
        self.prior_run(1, "incomplete", [u7], [], start)
        self.release_plan()
        out = self.seed()
        self.assertEqual(out["prior"], "Run 1")
        (unit,) = self.ledger()
        self.assertEqual((unit["coverage_id"], unit["status"], unit["prior_status"]), (u7["coverage_id"], "planned", "prior_deferred"))

    def test_diff_scope_carries_out_of_scope(self) -> None:
        base = self.git("rev-parse", "HEAD")
        (self.notes / "Project Note.md").write_text(f"## Release audits\n{RECORD.format(sha=base)}\n")
        u5 = self.prior_unit("u5", "deferred", paths=("c.py",))
        self.prior_run(1, "complete", [u5], [], base)
        self.commit("a.py", "changed")
        plan, _ = self.release_plan()
        self.assertEqual(plan["scope"], f"{base}..{plan['head']}")
        self.seed()
        (unit,) = self.ledger()
        self.assertEqual((unit["status"], unit["unresolved"]), ("out_of_scope", ["outside the run scope"]))

    def test_touched_path_with_space_and_non_ascii(self) -> None:
        start = self.commit("d i/é.py", "1")
        u8 = self.prior_unit("u8", "covered", paths=("d i/é.py",))
        self.prior_run(1, "complete", [u8], [], start)
        self.commit("d i/é.py", "2")
        self.release_plan()
        out = self.seed()
        self.assertEqual(out["touched"], [{"coverage_id": u8["coverage_id"], "paths": ["d i/é.py"]}])

    def test_seed_twice_and_no_plan(self) -> None:
        self.seed()
        before = self.snapshot()
        out, code = self.step("seed")
        self.assertEqual((code, out["error"]), (1, "already-seeded"))
        self.assertEqual(self.snapshot(), before)
        self.run_dir = self.notes / "Security Audit" / "Run 5"
        self.run_dir.mkdir()
        out, code = self.step("seed")
        self.assertEqual((code, out["error"]), (1, "no-plan"))
        self.assertEqual(list(self.run_dir.iterdir()), [])

    def test_bad_run_and_not_seeded(self) -> None:
        self.release_plan()
        outside = self.root / "Run 1"
        outside.mkdir()
        out, code = self.devloop("audit-run", "seed", "--run", str(outside), "--repo", str(self.repo))
        self.assertEqual((code, out["error"]), (1, "bad-run"))
        args = argparse.Namespace(repo=str(self.repo), run_folder=str(self.run_dir))
        printed = io.StringIO()
        with unittest.mock.patch.dict("os.environ", self.env), contextlib.redirect_stdout(printed):
            code = common.run_step(args, lambda run, state: {"ok": True})
        self.assertEqual((code, json.loads(printed.getvalue())["error"]), (1, "not-seeded"))

    def test_ledger_validator_failure_writes_nothing(self) -> None:
        self.release_plan()
        validator_exit(self.skill, "validate-coverage-ledger.cjs", 1)
        before = self.snapshot()
        out, code = self.step("seed")
        self.assertEqual((code, out["error"]), (1, "ledger-invalid"))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
