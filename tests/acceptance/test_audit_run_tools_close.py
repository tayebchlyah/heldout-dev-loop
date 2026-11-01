#!/usr/bin/env python3
"""Held-out oracle tests for `audit-run findings`, `report`, `status`, the abuse case and the kit text
(spec: Audit Run Tools, oracle 29 to 38). Fixtures: `audit_run_tools_support.py` (`full_run`: a standard run with
every step done but `findings`).

Fixture choices the spec leaves open:
- `open` names an item when its JSON text holds the agent id, the coverage id or the fingerprint; the missing
  final-clean critic is named by the word `critic` or `final`.
- Hardening notes: h01 gives two, h02 repeats one, so `## Hardening notes` has 2 bullets.
- Two final records have a title with a line break (one `confirmed`, one `needs_validation`).
- Step 4 of `skills/release-audit/SKILL.md`: the text from the line that starts `4. ` to the line that starts `5. `.
"""

from __future__ import annotations

import json
import re
import unittest

from audit_run_tools_support import (HARDENING, TITLE_CONF, TITLE_NV, AuditCase, check_of, entry, full_run,
                                     hunter_result, put, snapshot, unit_out)
from release_security_audit_support import AGENTS, AUDIT_SKILL, REPO_ROOT, section

REPORTS = ("REPORT.md", "FINDINGS-DETAIL.md", "NEEDS-VALIDATION.md")
SUMMARY = "SUMMARY-MARK: two subsystems were reviewed.\nNo finding blocks the release.\n"


class CloseTests(AuditCase):

    def summary(self) -> str:
        path = self.root / "summary.md"
        put(path, SUMMARY)
        return str(path)

    def test_o29_close_a_complete_run(self) -> None:
        run = full_run(self)
        payload = run.ok("findings")
        self.assertEqual(payload.get("run_status"), "complete", payload)
        records = run.json("findings.json")
        self.assertEqual([r.get("fingerprint") for r in records], ["fp-a", "fp-b", "fp-c"])
        self.assertEqual(run.metadata().get("run_status"), "complete")
        self.assertIn(records, run.repo.validator_runs("validate-findings"))
        self.assertIn(run.ledger(), run.repo.validator_runs("validate-coverage-ledger"))

    def test_o30_each_open_item_keeps_the_run_open(self) -> None:
        cases = {"open brief": (frozenset(), lambda run: run.brief("critic")),
                 "planned unit": (frozenset(), lambda run: run.add(entry("report"))[0]),
                 "candidate with no final record": (frozenset({"verify-b"}), lambda run: "fp-b"),
                 "last recheck a replace": (frozenset({"replace-a"}), lambda run: "fp-a"),
                 "no final-clean critic": (frozenset({"final"}), lambda run: "critic")}
        for case, (skip, make) in cases.items():
            with self.subTest(case=case):
                run = full_run(self, skip)
                token = make(run)
                code, payload = run.call("findings")
                self.assertEqual((code, payload.get("error")), (1, "open"), payload)
                self.assertEqual(payload.get("run_status"), "in_progress", payload)
                self.assertEqual(run.metadata().get("run_status"), "in_progress")
                text = json.dumps(payload.get("open"))
                tokens = (token, "final") if token == "critic" else (token,)
                self.assertTrue(any(t in text for t in tokens), payload)
                if token == "fp-b":
                    self.assertNotIn("fp-b", [r.get("fingerprint") for r in run.json("findings.json")])

    def test_o31_incomplete_close(self) -> None:
        run = self.start()
        app, admin = run.add(entry("app"), entry("admin"))
        h01 = run.hunter(app)
        run.finish(h01, hunter_result([unit_out(app, "covered", [check_of(h01, ["src/app.py"])])]))
        payload = run.ok("findings", "--incomplete", "validation_budget_exhausted")
        self.assertEqual(payload.get("run_status"), "incomplete", payload)
        meta = run.metadata()
        self.assertEqual((meta.get("run_status"), meta.get("incomplete_reason")),
                         ("incomplete", "validation_budget_exhausted"), meta)
        unit = run.unit(admin)
        self.assertEqual(unit.get("status"), "deferred", unit)
        self.assertTrue(any("validation_budget_exhausted" in str(x) for x in unit.get("unresolved") or []), unit)
        code, record = run.repo.devloop("release-record", "--run", str(run.path))
        self.assertEqual((code, record.get("error")), (1, "run-incomplete"), record)

    def test_o32_a_validator_fails(self) -> None:
        run = full_run(self)
        run.repo.validator_exit("validate-findings", 1)
        payload = run.fails("invalid-run", "findings")
        self.assertTrue(payload.get("tail"), payload)
        self.assertEqual(run.metadata().get("run_status"), "in_progress")

    def test_o33_report(self) -> None:
        run = full_run(self)
        run.fails("not-closed", "report", "--summary", self.summary())
        run.ok("findings")
        run.ok("report", "--summary", self.summary())
        report = (run.path / "REPORT.md").read_text()
        self.assertIn(SUMMARY.strip(), "\n".join(section(report, "## Security posture")))
        bullets = [line[2:].strip() for line in section(report, "## Hardening notes") if line.startswith("- ")]
        self.assertEqual(sorted(bullets), sorted(HARDENING))
        for name in REPORTS:
            with self.subTest(file=name):
                lines = (run.path / name).read_text().splitlines()
                whole = 0
                for title in (TITLE_NV, TITLE_CONF):
                    head, tail = title.split("\n")
                    for line in lines:
                        if head in line:
                            self.assertIn(tail, line, f"{name}: a title is split over lines")
                            whole += 1
                self.assertGreater(whole, 0, f"{name} holds no final record title")

    def test_o34_release_record_accepts_a_tool_run(self) -> None:
        run = full_run(self)
        run.ok("findings")
        run.ok("report", "--summary", self.summary())
        code, payload = run.repo.devloop("release-record", "--run", str(run.path))
        self.assertEqual(code, 0, payload)
        self.assertIn("2 hardening notes", str(payload.get("gardener")), payload)

    def test_o35_abuse_results_write_nothing(self) -> None:
        def dot_dot(run, h01, result, ids):
            for unit in result["units"]:
                unit["checks"] = [check_of(h01, ["../../x"])]
                unit["reviewed_paths"] = ["../../x"]
            run.write_result(h01, result)

        def other_agent(run, h01, result, ids):
            put(run.scratch(h01) / "a", "a")
            result["units"][1]["checks"][0]["artifact"] = "agents/h02/artifacts/a"
            run.write_result(h01, result)

        def link_out(run, h01, result, ids):
            target = self.root / "outside-result.json"
            put(target, json.dumps(result))
            (run.scratch(h01) / "result.json").symlink_to(target)

        for case, write in (("reviewed path ../../x", dot_dot), ("another agent's artifact", other_agent),
                            ("result.json a link outside the run", link_out)):
            with self.subTest(case=case):
                run, h01, result, ids = self.hunted()
                write(run, h01, result, ids)
                before = snapshot(self.root)
                code, payload = run.call("check", "--agent", h01)
                self.assertEqual((code, payload.get("ok"), payload.get("state")), (1, False, "invalid"), payload)
                run.call("promote", "--agent", h01)
                run.fails("invalid", "fold", "--agent", h01)
                self.assert_same(before, self.root)

    def test_o36_status(self) -> None:
        run = self.start()
        ids = run.add(entry("app"), entry("upload"), entry("admin"), entry("auth"), entry("export"))
        agents = [run.hunter(i) for i in ids]
        run.write_result(agents[1], {"units": []})
        for agent, coverage_id, path in ((agents[2], ids[2], "src/admin.py"), (agents[3], ids[3], "src/auth.py")):
            run.write_result(agent, hunter_result([unit_out(coverage_id, "covered", [check_of(agent, [path])])]))
        run.ok("fold", "--agent", agents[3])
        run.ok("fold", "--agent", agents[4], "--discard", "x")
        critic = run.brief("critic")
        payload = run.status()
        self.assertEqual(payload.get("run_status"), "in_progress", payload)
        briefs = {b.get("agent"): b for b in payload.get("briefs") or []}
        want = dict(zip(agents + [critic], ["missing", "invalid", "ready", "folded", "discarded", "missing"]))
        self.assertEqual({a: (briefs.get(a) or {}).get("state") for a in want}, want, payload)
        self.assertTrue(briefs[agents[1]].get("errors"), payload)
        self.assertEqual({k: v for k, v in (payload.get("units") or {}).items() if v},
                         {"in_progress": 3, "covered": 1, "planned": 1}, payload)
        self.assertEqual({k: v for k, v in (payload.get("counts") or {}).items() if v}, {"hunter": 5, "critic": 1},
                         payload)


def step_four() -> str:
    lines = AUDIT_SKILL.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("4. "))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("5. ")), len(lines))
    return "\n".join(lines[start:end])


class TextTests(unittest.TestCase):

    def test_o37_release_audit_step_four(self) -> None:
        text = step_four()
        pos = 0
        for name in (r"\bseed\b", r"units --add", r"\bbrief\b", r"\bcheck\b", r"\bpromote\b", r"\bfold\b",
                     r"\bstatus\b", r"\bfindings\b", r"\breport\b"):
            match = re.compile(name).search(text, pos)
            self.assertIsNotNone(match, f"{name} not named after offset {pos} in step 4")
            pos = match.end()
        for name in ("architecture.md", "context.md", "audit-run"):
            self.assertIn(name, text)
        self.assertRegex(text, re.compile(r"one agent per brief", re.I))
        self.assertRegex(text, re.compile(r"prompt", re.I))
        self.assertRegex(text, re.compile(r"notification.{0,200}?\bstatus\b|\bstatus\b.{0,200}?notification",
                                          re.I | re.S))
        self.assertRegex(text, re.compile(r"\bcheck\b.{0,200}?\bhands?[ -]back", re.I | re.S))

    def test_o38_reference_upstream_and_agents(self) -> None:
        ref = REPO_ROOT / "reference" / "cli" / "audit-run.md"
        self.assertTrue(ref.is_file(), "no reference/cli/audit-run.md")
        text = ref.read_text()
        for name in ("seed", "units", "--add", "--update", "brief", "check", "promote", "fold", "findings", "report",
                     "status", "no-plan", "already-seeded", "invalid", "duplicate", "not-planned", "unknown-unit",
                     "carried", "unknown-fingerprint", "no-architecture", "no-context", "skill-changed", "missing",
                     "unpromoted", "already-folded", "collision", "ledger-invalid", "open", "invalid-run",
                     "not-closed"):
            with self.subTest(name=name):
                self.assertIn(name, text)
        source = next(s for s in json.loads((REPO_ROOT / "upstream.json").read_text())["sources"]
                      if s.get("name") == "security-audit")
        self.assertIn(source["version"], text)
        self.assertIn("reference/cli/audit-run.md", source.get("local") or [])
        self.assertTrue([line for line in AGENTS.read_text().splitlines() if line.startswith("- `audit-run")],
                        "AGENTS.md has no audit-run index line")


if __name__ == "__main__":
    unittest.main()
