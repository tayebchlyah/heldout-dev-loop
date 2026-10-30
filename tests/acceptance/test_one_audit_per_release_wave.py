#!/usr/bin/env python3
"""Held-out oracle tests for `release-record --fix-wave` and the wave in `release-check` (spec: One Audit Per
Release, oracle 6 to 19).

Seams, fixture choices and shared helpers: `one_audit_per_release_support.py`.
"""

from __future__ import annotations

import unittest

from one_audit_per_release_support import bare, WaveCase
from release_security_audit_support import reasons_named, record_line


class FixWaveTests(WaveCase):

    def test_o06_fix_wave_line_and_check_from_the_wave(self) -> None:
        r = self.wave_repo()
        r.B = r.code("#7: fix")
        r.C = r.code("#8: fix")
        code, payload = r.fix_wave(8, 7)
        self.assertEqual(code, 0, payload)
        lines = r.audits()
        self.assert_today_wave(lines[-1], r.C, [7, 8])
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(bare(payload.get("wave")), bare(lines[-1]), payload)
        code, check = r.check("--sha", r.C)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)
        self.assert_wave(check, lines[-1])

    def test_o07_code_commit_after_the_wave_is_stale(self) -> None:
        r, line = self.waved()
        d = r.code("#9: other")
        code, check = r.check("--sha", d)
        self.assertEqual(code, 1, check)
        stale = reasons_named(check, "stale-audit")
        self.assertTrue(stale, check)
        self.assertEqual(stale[0].get("paths"), ["src/app.py"], check)
        self.assert_wave(check, line)

    def test_o08_release_commit_after_the_wave_passes(self) -> None:
        r, _ = self.waved()
        e = r.release("1.1.0")
        code, check = r.check("--sha", e)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)

    def test_o09_tag_on_the_wave_ends_it_for_a_later_commit(self) -> None:
        r, line = self.waved()
        r.tag("v1.0.0-rc.1", r.C)
        for args in (("--sha", r.C), ("--tag", "v1.0.0-rc.1")):
            with self.subTest(args=args):
                code, check = r.check(*args)
                self.assertEqual(code, 0, check)
                self.assertIs(check.get("ok"), True, check)
                self.assert_wave(check, line)
        e = r.release("1.0.0-rc.2")
        code, check = r.check("--sha", e)
        self.assertEqual(code, 1, check)
        stale = reasons_named(check, "stale-audit")
        self.assertTrue(stale, check)
        self.assertIn("src/app.py", stale[0].get("paths") or [], check)
        self.assert_wave(check, None)
        self.assertEqual(check.get("wave_ignored"), "released: v1.0.0-rc.1", check)

    def test_o10_wave_line_missing_an_issue_is_ignored(self) -> None:
        r, line = self.waved()
        r.write_note(r.note_path().read_text().replace(line, line.replace(": #7, #8", ": #7")))
        code, check = r.check("--sha", r.C)
        self.assertEqual(code, 1, check)
        self.assertTrue(reasons_named(check, "stale-audit"), check)
        self.assertEqual(check.get("wave_ignored"), "foreign-commit", check)

    def test_o11_wave_sha_off_the_line_is_ignored(self) -> None:
        for case in ("not an ancestor of the target", "not a strict descendant of A"):
            with self.subTest(case=case):
                r, line = self.waved()
                other = (r.side_commit(r.A, {"src/app.py": "SIDE = 1\n"}, "#7: fix")
                         if case.startswith("not an ancestor") else r.A)
                r.write_note(r.note_path().read_text().replace(line, line.replace(r.C, other)))
                code, check = r.check("--sha", r.C)
                self.assertEqual(code, 1, check)
                self.assert_wave(check, None)
                self.assertEqual(check.get("wave_ignored"), "not-ancestor", check)

    def test_o12_refusals_write_nothing(self) -> None:
        with self.subTest(case="no record"):
            r = self.wave_repo(recorded=False)
            r.code("#7: fix")
            self.assert_refused(r, "no-audit", 7)
        with self.subTest(case="second wave for Run 2"):
            r, _ = self.waved()
            r.code("#9: fix")
            self.assert_refused(r, "wave-exists", 9)
        with self.subTest(case="HEAD equal to A"):
            r = self.wave_repo()
            self.assert_refused(r, "not-descendant", 7)

    def test_o13_code_commit_naming_no_issue_is_foreign(self) -> None:
        r = self.wave_repo()
        r.code("#7: fix")
        c = r.code("✨(x) #12: feature")
        payload = self.assert_refused(r, "foreign-commit", 7)
        commits = [{key: item.get(key) for key in ("sha", "subject")} for item in payload.get("commits") or []]
        self.assertEqual(commits, [{"sha": c, "subject": "✨(x) #12: feature"}], payload)

    def test_o14_issue_no_commit_names_is_unfixed(self) -> None:
        r = self.wave_repo()
        r.code("#7: fix")
        r.code("✨(x) #12: feature")
        payload = self.assert_refused(r, "unfixed", 7, 12, 30)
        self.assertEqual(payload.get("issues"), [30], payload)

    def test_o15_test_and_doc_commit_is_not_foreign(self) -> None:
        r = self.wave_repo()
        r.code("#7: fix")
        t = r.commit({"tests/unit/test_x.py": "def test_x():\n    assert 2 == 2\n", "docs/x.md": "# X\n"},
                     "Tidy the tests and the docs")
        code, payload = r.fix_wave(7)
        self.assertEqual(code, 0, payload)
        self.assert_today_wave(r.audits()[-1], t, [7])

    def test_o16_longer_number_does_not_name_the_issue(self) -> None:
        r = self.wave_repo()
        r.code("#70: x")
        payload = self.assert_refused(r, "unfixed", 70, 7)
        self.assertEqual(payload.get("issues"), [7], payload)

    def test_o17_usage_errors(self) -> None:
        r = self.wave_repo()
        r.code("#7: fix")
        before = r.note_path().read_bytes()
        cases = {"--run": ("--issue", "7", "--run", "X"), "--base": ("--issue", "7", "--base", r.A),
                 "--harness": ("--issue", "7", "--harness", "claude"), "no --issue": (), "--issue 0": ("--issue", "0")}
        for name, args in cases.items():
            with self.subTest(case=name):
                code, payload = r.devloop("release-record", "--fix-wave", *args)
                self.assertEqual(code, 2, payload)
                self.assertEqual(r.note_path().read_bytes(), before)
        code, payload = r.fix_wave(7)
        self.assertEqual(code, 0, payload)

    def test_o18_release_plan_keeps_the_record_base(self) -> None:
        r, _ = self.waved()
        r.validators()
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan.get("base"), r.A, plan)
        self.assertEqual(plan.get("scope"), f"{r.A}..{r.C}", plan)

    def test_o19_newer_record_ignores_the_older_wave(self) -> None:
        r, _ = self.waved()
        r.run_folder(3, r.B)
        text = r.note_path().read_text()
        lines = r.audits()
        newer = record_line(3, r.B, scope=f"{r.A}..{r.B}")
        r.write_note(text.replace(lines[-1], lines[-1] + "\n" + newer, 1))
        self.assertEqual(r.audits()[-1], newer)
        code, check = r.check("--sha", r.C)
        self.assertEqual(code, 1, check)
        self.assertTrue(reasons_named(check, "stale-audit"), check)
        self.assert_wave(check, None)
        self.assertIn("wave_ignored", check)
        self.assertIsNone(check["wave_ignored"], check)


if __name__ == "__main__":
    unittest.main()
