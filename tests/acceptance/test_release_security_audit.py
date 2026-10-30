#!/usr/bin/env python3
"""Held-out oracle tests for `release-check` and `release-plan` (spec: Release Security Audit).

Seams, fixture choices and shared helpers: `release_security_audit_support.py`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from release_security_audit_support import (APP_B, BASE_CONFIG, Case, GitRepo, IMAGE, item_text, note_text, NOTES_NAME,
                                            put, reasons_named, record_line, tools_dir)


# ---------------------------------------------------------------------------------------------------------------
# release-check (O1 to O15)
# ---------------------------------------------------------------------------------------------------------------

class ReleaseCheckTests(Case):

    def check(self, r: GitRepo, *args: str) -> tuple[int, dict]:
        return r.devloop("release-check", *args)

    def test_o01_audited_tag_passes(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("reasons"), [], payload)
        self.assertEqual(payload.get("target"), r.A, payload)
        self.assertEqual(payload.get("tag"), "v1.0.0", payload)
        self.assertIn(f"Run 1 at {r.A}", payload.get("record") or "", payload)

    def test_o02_no_release_audits_section(self) -> None:
        r = self.repo()
        r.tag("v1.0.0", r.A)
        r.run_folder(1, r.A)
        r.write_note(note_text(None, []))
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 1, payload)
        self.assertIs(payload.get("ok"), False, payload)
        self.assertTrue(reasons_named(payload, "no-audit"), payload)

    def test_o03_test_and_doc_changes_after_the_record_pass(self) -> None:
        r = self.audited()
        c = r.side_commit(r.A, {"tests/unit/test_x.py": "def test_x():\n    assert 2 == 2\n",
                                "README.md": "# Acme widgets\n\nNow with docs.\n"}, "C")
        r.tag("v1.1.0", c)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)

    def test_o04_code_change_after_the_record_is_stale(self) -> None:
        r = self.audited()
        r.tag("v1.1.0", r.B)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 1, payload)
        stale = reasons_named(payload, "stale-audit")
        self.assertTrue(stale, payload)
        self.assertEqual(stale[0].get("paths"), ["src/app.py"], payload)

    def test_o05_record_not_an_ancestor_is_stale_with_every_path(self) -> None:
        r = self.repo()
        x = r.side_commit(r.A, {"lib/side.py": "SIDE = 1\n"}, "X")
        r.run_folder(1, x)
        r.write_note(note_text([record_line(1, x)], []))
        r.tag("v1.1.0", r.B)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 1, payload)
        stale = reasons_named(payload, "stale-audit")
        self.assertTrue(stale, payload)
        self.assertEqual(sorted(stale[0].get("paths") or []), ["lib/side.py", "src/app.py"], payload)

    def test_o06_last_record_line_is_current(self) -> None:
        r = self.repo()
        x = r.side_commit(r.Z, {"lib/old.py": "OLD = 1\n"}, "old")
        r.run_folder(1, x)
        r.run_folder(2, r.A, profile="standard")
        r.write_note(note_text([record_line(1, x), record_line(2, r.A, scope=f"{x}..{r.A}", profile="standard")], []))
        r.tag("v1.0.0", r.A)
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertIn("Run 2", payload.get("record") or "", payload)

    def test_o07_record_without_a_matching_complete_run_folder(self) -> None:
        for case in ("other commit", "incomplete", "no run folder"):
            with self.subTest(case=case):
                r = self.repo()
                r.tag("v1.0.0", r.A)
                if case == "other commit":
                    r.run_folder(1, r.B)
                elif case == "incomplete":
                    r.run_folder(1, r.A, status="incomplete")
                r.write_note(note_text([record_line(1, r.A)], []))
                code, payload = self.check(r, "--tag", "v1.0.0")
                self.assertEqual(code, 1, payload)
                self.assertTrue(reasons_named(payload, "record-mismatch"), payload)

    def assert_open(self, r: GitRepo, line: str | None) -> None:
        code, payload = self.check(r, "--tag", "v1.0.0")
        found = reasons_named(payload, "open-security")
        if line is None:
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("reasons"), [], payload)
        else:
            self.assertEqual(code, 1, payload)
            self.assertTrue(found, payload)
            items = [item_text(i) for i in found[0].get("items") or []]
            self.assertTrue([i for i in items if line.lstrip("- ").strip() in i], payload)

    def test_o08_needs_validation_rulings(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, Run 1): x"
        for ruling, still_open in ((None, True), ("  - Ruling 2026-10-26: accept the risk", False),
                                   ("  - Ruling 2026-10-26: close not reachable", False),
                                   ("  - Ruling 2026-10-26: fix in a slice", True)):
            with self.subTest(ruling=ruling):
                r = self.audited([item] + ([ruling] if ruling else []))
                self.assert_open(r, item if still_open else None)

    def test_o09_confirmed_item_closes_with_a_ruling_and_its_reason(self) -> None:
        """Superseded by One Audit Per Release oracle 5: `accept <reason>` or `close <reason>` closes a confirmed
        item; a ruling word with no reason keeps it open."""
        item = "- 2026-10-25 (security, confirmed, Run 1): x"
        for ruling, still_open in (("  - Ruling 2026-10-26: accept the risk", False),
                                   ("  - Ruling 2026-10-26: close not reachable", False),
                                   ("  - Ruling 2026-10-26: accept", True)):
            with self.subTest(ruling=ruling):
                r = self.audited([item, ruling])
                self.assert_open(r, item if still_open else None)

    def test_o10_checkbox_item_under_a_subsection(self) -> None:
        item = "- [ ] 2026-10-25 (security, needs-validation, review-wave-measurement): y"
        r = self.audited(["### Some gate", "", item])
        self.assert_open(r, "2026-10-25 (security, needs-validation, review-wave-measurement): y")

    def test_o11_unparsed_security_bullet_blocks(self) -> None:
        line = "- Security: the token is logged"
        with self.subTest(case="in Deferred"):
            r = self.audited([line])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            unparsed = reasons_named(payload, "unparsed-security")
            self.assertTrue(unparsed, payload)
            self.assertTrue([i for i in unparsed[0].get("lines") or [] if "the token is logged" in item_text(i)],
                            payload)
        with self.subTest(case="in Gardener"):
            r = self.audited([], [line])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("reasons")), (0, []), payload)
        with self.subTest(case="word boundary"):
            r = self.audited(["- Insecurely named thing"])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("reasons")), (0, []), payload)

    def test_o12_other_tag_name_is_skipped(self) -> None:
        r = self.audited()
        r.tag("release-1", r.B)
        code, payload = self.check(r, "--tag", "release-1")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("skipped"), "not-a-release-tag", payload)

    def test_o13_sha_without_a_tag(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--sha", r.A)
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("reasons"), [], payload)
        self.assertEqual(payload.get("target"), r.A, payload)
        self.assertIn("tag", payload)
        self.assertIsNone(payload.get("tag"), payload)

    def test_o14_project_note_rule(self) -> None:
        good = note_text([record_line(1, "{sha}")], [])
        bare = note_text(None, [])
        with self.subTest(case="folder-named note only"):
            r = self.audited()
            r.note_path().unlink()
            r.write_note(good.replace("{sha}", r.A), f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("ok")), (0, True), payload)
        with self.subTest(case="both: Project Note.md wins"):
            r = self.audited()
            r.write_note(bare, f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("ok")), (0, True), payload)
            r.write_note(bare)
            r.write_note(good.replace("{sha}", r.A), f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            self.assertTrue(reasons_named(payload, "no-audit"), payload)
        with self.subTest(case="neither"):
            r = self.audited()
            r.note_path().unlink()
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("error"), "no-project-note", payload)
            self.assertNotIn("ok", payload)

    def test_o15_bad_tag_and_usage(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--tag", "v9")
        self.assertEqual(code, 1, payload)
        self.assertTrue(payload.get("error"), payload)
        self.assertNotIn("ok", payload)
        code, payload = self.check(r, "--tag", "v1.0.0", "--sha", r.A)
        self.assertEqual(code, 2, payload)
        code, payload = self.check(r)
        self.assertEqual(code, 2, payload)


# ---------------------------------------------------------------------------------------------------------------
# release-plan (O16 to O20)
# ---------------------------------------------------------------------------------------------------------------

class ReleasePlanTests(Case):

    def audit_dir(self, r: GitRepo) -> Path:
        return r.notes / "Security Audit"

    def listing(self, r: GitRepo) -> list[str]:
        folder = self.audit_dir(r)
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_o16_first_audit(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        expected = (self.audit_dir(r) / "Run 1").resolve()
        self.assertEqual(payload.get("run"), "Run 1", payload)
        self.assertIs(payload.get("first"), True, payload)
        self.assertEqual(payload.get("profile"), "quick", payload)
        self.assertEqual(payload.get("budget"), 100, payload)
        self.assertEqual(payload.get("scope"), "whole", payload)
        self.assertIn("base", payload)
        self.assertIsNone(payload.get("base"), payload)
        self.assertEqual(payload.get("prior_runs"), [], payload)
        self.assertEqual(payload.get("head"), r.B, payload)
        self.assertTrue(Path(payload.get("output") or "").is_absolute(), payload)
        self.assertEqual(Path(payload["output"]).resolve(), expected, payload)
        self.assertTrue(expected.is_dir(), "the Run 1 folder was not created")

    def test_o17_later_audit(self) -> None:
        r = self.repo()
        (self.audit_dir(r) / "Run 1").mkdir(parents=True)
        r.run_folder(3, r.A)
        r.write_note(note_text([record_line(3, r.A)], []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("run"), "Run 4", payload)
        self.assertIs(payload.get("first"), False, payload)
        self.assertEqual(payload.get("profile"), "standard", payload)
        self.assertIn("budget", payload)
        self.assertIsNone(payload.get("budget"), payload)
        self.assertEqual(payload.get("scope"), f"{r.A}..{r.B}", payload)
        self.assertEqual(payload.get("base"), r.A, payload)
        self.assertEqual([Path(p).resolve() for p in payload.get("prior_runs") or []],
                         [(self.audit_dir(r) / "Run 1").resolve(), (self.audit_dir(r) / "Run 3").resolve()], payload)
        self.assertTrue((self.audit_dir(r) / "Run 4").is_dir())

    def test_o17a_merge_base_and_overrides(self) -> None:
        r = self.repo()
        rewritten = r.side_commit(r.A, {"src/other.py": "OTHER = 1\n"}, "R")
        r.run_folder(1, rewritten)
        r.write_note(note_text([record_line(1, rewritten)], []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("base"), r.A, payload)
        self.assertEqual(payload.get("scope"), f"{r.A}..{r.B}", payload)
        code, payload = r.devloop("release-plan", "--profile", "deep", "--budget", "40")
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("profile"), payload.get("budget")), ("deep", 40), payload)
        code, payload = r.devloop("release-plan", "--budget", "0")
        self.assertEqual(code, 2, payload)

    def test_o18_icloud_placeholder(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        put(self.audit_dir(r) / "Run 1" / ".findings.json.icloud", b"\x00")
        before = self.listing(r)
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), "placeholder", payload)
        self.assertTrue([f for f in payload.get("files") or [] if ".findings.json.icloud" in str(f)], payload)
        self.assertEqual(self.listing(r), before)

    def test_o19_dirty_worktree(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        put(r.repo / "src" / "app.py", APP_B + "# local edit\n")
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), "dirty", payload)
        self.assertTrue([p for p in payload.get("paths") or [] if "src/app.py" in str(p)], payload)
        self.assertEqual(self.listing(r), [])
        r.git("checkout", "--", "src/app.py")
        put(r.repo / "scratch-notes.txt", "untracked\n")
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)

    def test_o20_sandbox_block(self) -> None:
        for images in (None, {"t": IMAGE}):
            with self.subTest(images=images):
                config = dict(BASE_CONFIG)
                if images is not None:
                    config["security_audit"] = {"images": images}
                r = self.repo(config)
                r.write_note(note_text(None, []))
                path = tools_dir(self.root, "git")
                code, payload = r.devloop("release-plan", env={"PATH": str(path)})
                self.assertEqual(code, 0, payload)
                sandbox = payload.get("sandbox") or {}
                self.assertIs(sandbox.get("engine"), False, payload)
                self.assertEqual(sandbox.get("images"), images or {}, payload)
                runner = sandbox.get("runner") or ""
                self.assertTrue(Path(runner).is_absolute(), payload)
                self.assertTrue(runner.endswith("scripts/sandbox-run.py"), payload)


if __name__ == "__main__":
    unittest.main()
