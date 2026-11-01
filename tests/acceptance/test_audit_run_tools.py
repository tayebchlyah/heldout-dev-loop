#!/usr/bin/env python3
"""Held-out flow tests for `audit-run` (spec: Audit Run Tools, flows 1 to 4), one test per flow, each composing
`release-plan`, the `audit-run` steps and `release-record` (the earlier feature the flows name) end to end.
Oracle lines: `test_audit_run_tools_seed.py`, `_brief.py`, `_check.py`, `_fold.py`, `_close.py`.
Fixtures: `audit_run_tools_support.py` (`full_run`).

Fixture choices the spec leaves open:
- Flow 1: each agent result goes through `check` (as the agent does) before `promote` and `fold`.
- Flow 2: the prior run is flow 1's run, recorded by `release-record`; the next commit changes `src/extra.py`
  only, a starting path of the carried units and a reviewed path of the covered unit `app`. The parent plans
  `app` again with `units --add`; the carried units go to verifiers (D17a).
- Flow 4's reason is `validation_budget_exhausted`.
"""

from __future__ import annotations

import unittest

from audit_run_tools_support import (AuditCase, check_of, confirmed, critic_result, entry, full_run, hunter_result,
                                     needs_validation, put, TITLE_CONF, TITLE_NV, uid, unit_out, verifier_result)


SUMMARY = "Two subsystems were reviewed. One confirmed finding, one lead that needs an owner check.\n"


class FlowTests(AuditCase):

    def close_and_record(self, run, *record_args: str) -> dict:
        self.assertEqual(run.ok("findings").get("run_status"), "complete")
        summary = self.root / "summary.md"
        put(summary, SUMMARY)
        files = run.ok("report", "--summary", str(summary)).get("files")
        self.assertTrue(files, "report named no files")
        for name in ("REPORT.md", "FINDINGS-DETAIL.md", "NEEDS-VALIDATION.md"):
            self.assertTrue((run.path / name).is_file(), name)
        code, payload = run.repo.devloop("release-record", "--run", str(run.path), *record_args)
        self.assertEqual(code, 0, payload)
        return payload

    def first_audit(self):
        r = self.repo()
        run = self.plan(r)
        self.assertTrue((run.path / "plan.json").is_file())
        self.assertIsNone(run.ok("seed").get("prior"))
        run.write_parent()
        run.agent_check = True
        full_run(self, run=run)
        return r, run, self.close_and_record(run)

    def test_f1_first_audit(self) -> None:
        """F1: plan, seed with no prior, parent files and units, hunters that check, promote and fold, a critic and
        a final-clean critic, a verifier per candidate, a recheck per record, findings, report, release-record."""
        r, run, record = self.first_audit()
        self.assertIn("Run 1", str(record.get("record")), record)
        self.assertEqual(run.metadata().get("run_status"), "complete")
        self.assertEqual([x.get("fingerprint") for x in run.json("findings.json")], ["fp-a", "fp-b", "fp-c"])

    def test_f2_next_audit(self) -> None:
        """F2: a complete prior run; seed carries its open units and lists `touched`; the parent plans them again;
        the run closes and is recorded as in F1."""
        r, _, _ = self.first_audit()
        r.commit({"src/extra.py": "def extra():\n    return 2\n"}, "E")
        run = self.plan(r)
        self.assertEqual(run.plan.get("run"), "Run 2", run.plan)
        seed = run.ok("seed")
        self.assertEqual(seed.get("prior"), "Run 1", seed)
        auth, upload, app = uid("auth"), uid("upload"), uid("app")
        self.assertEqual(sorted(seed.get("carried") or []), sorted([auth, upload]), seed)
        self.assertEqual(seed.get("touched"), [{"coverage_id": app, "paths": ["src/extra.py"]}], seed)
        self.assertEqual(run.unit(auth).get("prior_status"), "prior_confirmed_same_source")
        self.assertEqual(run.unit(upload).get("prior_status"), "prior_needs_validation")
        run.write_parent()
        run.agent_check = True
        for fp, record, path in (("fp-c", confirmed("fp-c", TITLE_CONF), "src/auth.py"),
                                 ("fp-a", needs_validation("fp-a", TITLE_NV), "src/upload.py")):
            agent = run.brief("verifier", "--fingerprint", fp)
            run.finish(agent, verifier_result(record, agent, path), promote=True)
        run.add(entry("app", prior="prior_covered_changed_source"))
        h01 = run.hunter(app)
        run.finish(h01, hunter_result([unit_out(app, "covered", [check_of(h01, ["src/app.py", "src/extra.py"])])],
                                      hardening=("Pin the base image.",)), promote=True)
        run.finish(run.brief("critic"), critic_result())
        run.finish(run.brief("critic", "--final"), critic_result())
        for fp in ("fp-c", "fp-a"):
            run.finish(run.brief("recheck", "--fingerprint", fp), {"decision": "verified", "fingerprint": fp})
        self.assertEqual(run.unit(auth).get("status"), "candidate")
        self.close_and_record(run, "--base", run.plan["base"])
        self.assertEqual([x.get("fingerprint") for x in run.json("findings.json")], ["fp-a", "fp-c"])

    def test_f3_broken_result(self) -> None:
        """F3: a bad hunter result: check gives the errors, status shows invalid, fold refuses, fold --discard puts
        the units back to planned for a fresh hunter."""
        run = self.start()
        app, upload = run.add(entry("app"), entry("upload"))
        h01 = run.hunter(app, upload)
        run.write_result(h01, hunter_result([unit_out(app, "covered", [check_of(h01, ["src/app.py"])])]))
        code, payload = run.call("check", "--agent", h01)
        self.assertEqual((code, payload.get("ok"), payload.get("state")), (1, False, "invalid"), payload)
        self.assert_errors_name(payload, upload)
        state = run.brief_state(h01)
        self.assertEqual(state.get("state"), "invalid", state)
        self.assertTrue(state.get("errors"), state)
        run.fails("invalid", "fold", "--agent", h01)
        run.ok("fold", "--agent", h01, "--discard", "broken result")
        for coverage_id in (app, upload):
            unit = run.unit(coverage_id)
            self.assertEqual((unit.get("status"), unit.get("agent_id")), ("planned", None), unit)
        h02 = run.hunter(app, upload)
        self.assertEqual(h02, "h02")
        run.finish(h02, hunter_result([unit_out(app, "covered", [check_of(h02, ["src/app.py"])]),
                                       unit_out(upload, "covered", [check_of(h02, ["src/upload.py"])])]))
        self.assertEqual([run.unit(i).get("status") for i in (app, upload)], ["covered", "covered"])

    def test_f4_budget_stop(self) -> None:
        """F4: the parent stops early with findings --incomplete; release-record gives run-incomplete."""
        run = self.start()
        app, _ = run.add(entry("app"), entry("admin"))
        h01 = run.hunter(app)
        run.finish(h01, hunter_result([unit_out(app, "covered", [check_of(h01, ["src/app.py"])])]))
        payload = run.ok("findings", "--incomplete", "validation_budget_exhausted")
        self.assertEqual(payload.get("run_status"), "incomplete", payload)
        code, record = run.repo.devloop("release-record", "--run", str(run.path))
        self.assertEqual((code, record.get("error")), (1, "run-incomplete"), record)


if __name__ == "__main__":
    unittest.main()
