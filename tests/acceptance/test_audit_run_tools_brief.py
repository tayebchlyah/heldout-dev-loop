#!/usr/bin/env python3
"""Held-out oracle tests for `audit-run brief` (spec: Audit Run Tools, oracle 9 to 13).
Fixtures: `audit_run_tools_support.py`.

Fixture choices the spec leaves open:
- "In this order" (oracle 9): `context.md`, then `architecture.md`, then the unit ids and block texts (each id of
  a unit before that unit's blocks may hold: the first id comes before every block), then the three `HUNTING.md`
  fences, the `## Structured hunter result` section, both schema branches, the result path, the `check` line.
- The `check` line is a line that holds `audit-run check` and `--agent h01`.
- "Holds a block" means its text from the bold line or `## ` heading to the next block, verbatim.
- A carried exclusion is matched by its fingerprint text in `prompt.md`.
"""

from __future__ import annotations

import json
import unittest

from audit_run_tools_support import (ACCESS_BLOCK, AI_CORE, AI_CORE_BLOCK, ARCHITECTURE, BRANCHES, CONTEXT,
                                     HUNT_FENCE, INJECTION, INJECTION_BLOCK, PROMOTION_FENCE, RULES_FENCE,
                                     VERIFIER_FENCE, AuditCase, candidate, check_of, cid, entry, hunter_result,
                                     labels, needs_validation, snapshot, unit_out, verifier_result, write_skill)


def uid(name: str) -> str:
    return cid(labels(name))


class BriefTests(AuditCase):

    def after(self, text: str, part: str, start: int) -> int:
        """The end of the first `part` at or after `start`; fails when absent."""
        at = text.find(part, start)
        self.assertGreaterEqual(at, 0, f"{part[:60]!r} not found after offset {start}")
        return at + len(part)

    def test_o09_hunter_brief_on_two_units(self) -> None:
        run = self.start()
        a, b, spare = run.add(entry("auth", companions=(AI_CORE,)), entry("upload", attack=INJECTION),
                              entry("export"))
        payload = run.ok("brief", "--kind", "hunter", "--units", a, b)
        self.assertEqual(payload.get("agent"), "h01", payload)
        self.assertTrue(str(payload.get("prompt")).endswith("agents/h01/prompt.md"), payload)
        self.assertTrue(str(payload.get("result")).endswith("agents/h01/scratch/result.json"), payload)
        for coverage_id in (a, b):
            unit = run.unit(coverage_id)
            self.assertEqual((unit.get("status"), unit.get("agent_id")), ("in_progress", "h01"), unit)
        text = run.prompt("h01")
        pos = self.after(text, CONTEXT.strip(), 0)
        pos = self.after(text, ARCHITECTURE.strip(), pos)
        ids = [text.find(i, pos) for i in (a, b)]
        blocks = [text.find(block, pos) for block in (ACCESS_BLOCK, INJECTION_BLOCK, AI_CORE_BLOCK)]
        self.assertTrue(min(ids) >= 0 and min(blocks) >= 0, (ids, blocks))
        self.assertLess(min(ids), min(blocks), "a block comes before the unit ids")
        pos = max(ids + blocks)
        for part in (HUNT_FENCE, PROMOTION_FENCE, RULES_FENCE, "RESULT-CONTRACT-MARK"):
            pos = self.after(text, part, pos)
        marks = [text.find(BRANCHES[v], pos) for v in ("confirmed", "needs_validation")]
        self.assertTrue(min(marks) >= 0, "a schema branch is missing after the result section")
        pos = self.after(text, "agents/h01/scratch/result.json", max(marks))
        lines = [line for line in text[pos:].splitlines() if "audit-run check" in line and "--agent h01" in line]
        self.assertTrue(lines, "no check line after the result path")
        self.assertEqual(run.hunter(spare), "h02")
        self.assertEqual(run.brief("critic"), "c01")

    def test_o10_not_planned_carried_and_the_verifier_owner(self) -> None:
        with self.subTest(case="not planned"):
            run = self.start()
            (busy,) = run.add(entry("app"))
            run.hunter(busy)
            before = snapshot(run.path)
            run.fails("not-planned", "brief", "--kind", "hunter", "--units", busy)
            self.assert_same(before, run.path)
        with self.subTest(case="carried"):
            run = self.start(r=self.prior_repo(("b.py",)))
            for name in ("u1", "u2"):
                before = snapshot(run.path)
                run.fails("carried", "brief", "--kind", "hunter", "--units", uid(name))
                self.assert_same(before, run.path)
            self.assertIn("fp-conf", run.prompt(run.hunter(uid("u5"))))
            self.assertEqual(run.brief("verifier", "--fingerprint", "fp-conf"), "v01")
            unit = run.unit(uid("u1"))
            self.assertEqual((unit.get("status"), unit.get("agent_id")), ("in_progress", "v01"), unit)
        with self.subTest(case="changed source"):
            run = self.start(r=self.prior_repo(("a.py", "b.py")))
            self.assertNotIn("fp-conf", run.prompt(run.hunter(uid("u5"))))

    def test_o10a_quick_verifier_brief_holds_phase_5(self) -> None:
        run = self.start(profile="quick")
        (a,) = run.add(entry("upload"))
        h01 = run.hunter(a)
        run.finish(h01, hunter_result([unit_out(a, "candidate", [check_of(h01, ["src/upload.py"])], fps=("fp-q",))],
                                      candidates=(candidate(needs_validation("fp-q", "Join may escape")),)))
        text = run.prompt(run.brief("verifier", "--fingerprint", "fp-q"))
        self.after(text, "PHASE5-MARK", self.after(text, "VERIFIER-PROMPT-MARK", 0))

    def test_o10b_brief_json_is_never_read_back(self) -> None:
        run = self.start()
        a, b = run.add(entry("app"), entry("auth"))
        h01 = run.hunter(a)
        brief = run.path / "agents" / h01 / "brief.json"
        brief.write_text(json.dumps({"kind": "critic", "units": [b], "fingerprint": "fp-x", "final": True}) + "\n")
        run.write_result(h01, hunter_result([unit_out(a, "covered", [check_of(h01, ["src/app.py"])])]))
        self.assertEqual(run.ok("check", "--agent", h01).get("ok"), True)
        state = run.brief_state(h01)
        self.assertEqual((state.get("kind"), state.get("state")), ("hunter", "ready"), state)
        run.ok("fold", "--agent", h01)
        self.assertEqual(run.unit(a).get("status"), "covered")
        self.assertEqual(run.unit(b).get("status"), "planned")
        self.assertEqual(run.brief_state(h01).get("state"), "folded")

    def test_o11_skill_text_missing(self) -> None:
        run = self.start()
        (a,) = run.add(entry("app"))
        for case, drop, name in (("block", f"ATTACK-CLASSES.md:{ACCESS_BLOCK}", "ATTACK-CLASSES.md"),
                                 ("heading", "HUNTING.md:#### Promotion procedure\n\n", "HUNTING.md"),
                                 ("schema branch", "schema:needs_validation", "report-schema.json")):
            with self.subTest(case=case):
                write_skill(run.repo.skill, drop=(drop,))
                before = snapshot(run.path)
                payload = run.fails("skill-changed", "brief", "--kind", "hunter", "--units", a)
                self.assertTrue(str(payload.get("file")).endswith(name), payload)
                self.assertTrue(payload.get("section"), payload)
                self.assert_same(before, run.path)
                write_skill(run.repo.skill)

    def test_o12_parent_files_missing(self) -> None:
        for case, error in (("architecture", "no-architecture"), ("context", "no-context")):
            with self.subTest(case=case):
                run = self.start(parent=False)
                run.write_parent(context=case != "context", architecture=case != "architecture")
                (a,) = run.add(entry("app"))
                run.fails(error, "brief", "--kind", "hunter", "--units", a)

    def test_o13_verifier_and_recheck_briefs(self) -> None:
        run = self.start()
        a, b = run.add(entry("upload"), entry("export"))
        h01 = run.hunter(a, b)
        one, two = needs_validation("fp-one", "Join may escape"), needs_validation("fp-two", "Export leaks rows",
                                                                                   file="src/export.py")
        run.finish(h01, hunter_result([unit_out(a, "candidate", [check_of(h01, ["src/upload.py"])], fps=("fp-one",)),
                                       unit_out(b, "candidate", [check_of(h01, ["src/export.py"])], fps=("fp-two",))],
                                      candidates=(candidate(one), candidate(two))))
        run.fails("unknown-fingerprint", "brief", "--kind", "verifier", "--fingerprint", "fp-none")
        run.fails("unknown-fingerprint", "brief", "--kind", "recheck", "--fingerprint", "fp-one")
        v01 = run.brief("verifier", "--fingerprint", "fp-one")
        secret = dict(one, description="V01-RESULT-MARK: the join is contained by the caller.")
        run.finish(v01, verifier_result(secret, v01, "src/upload.py"))
        text = run.prompt(run.brief("verifier", "--fingerprint", "fp-two"))
        for part in (VERIFIER_FENCE, *BRANCHES.values()):
            self.assertIn(part, text)
        self.assertNotIn("V01-RESULT-MARK", text)


if __name__ == "__main__":
    unittest.main()
