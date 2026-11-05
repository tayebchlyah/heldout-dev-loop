#!/usr/bin/env python3
"""Held-out oracle tests for the tag of `release-check` and the ruling parser (spec: Scoped Rulings, issue #241,
oracle 1 to 11, flows 2 and 3).

One test per `## Acceptance oracle` line (O<n>), the variants a line lists as subtests, plus one test per
`## Acceptance flows` item (F<n>). Seam 1 of the spec: `devloop.py release-check` (the kit source) as a process on a
scratch git repo and a scratch notes folder. Repo and note helpers: `release_security_audit_support.py`. Oracle 12
to 15 and flows 1 and 4: `test_scoped_rulings_release.py`.

Fixture choices the spec leaves open:
- The oracle preamble: the item `ITEM` is the first line of `## Deferred`, its ruling lines follow it; the audit
  record is `Run 1 at <B>` (B is `HEAD`) with its complete run folder; the tags `v3.0.0-rc.5`, `v3.0.0-rc.6` and
  `v3.0.0` are annotated tags at B. So `--tag <t>` and `--sha HEAD` check the same audited commit.
- "Every tag": `--tag v3.0.0-rc.5`, `--tag v3.0.0-rc.6`, `--tag v3.0.0`, `--sha HEAD` and
  `--sha HEAD --for v3.0.0-rc.5`.
- An item of `open-security` is matched by the item line with its leading `- ` removed; a line of
  `unparsed-security` `lines` is compared whole (the spec: each unparsed line, `rstrip`).
- A fence under an item is indented by two spaces, as a block quoted under a list item.
- Oracle 3 also runs the valid `--sha HEAD --for <tag>` forms (the CLI line of the spec): no usage error, a verdict.
- Oracle 8 runs beside the waive of oracle 1, so the line is shown to be neither unparsed nor a ruling.
"""

from __future__ import annotations

import unittest

from release_security_audit_support import Case, GitRepo, item_text, note_text, reasons_named, record_line


ITEM = "- 2026-11-01 (security, needs-validation, Run 9): x. Fingerprint `f-a`."
NEXT_ITEM = "- 2026-11-02 (security, needs-validation, Run 9): y. Fingerprint `f-b`."
RC5, RC6, FINAL = "v3.0.0-rc.5", "v3.0.0-rc.6", "v3.0.0"
WAIVE_RC5 = "  - Ruling 2026-11-03: waive v3.0.0-rc.5 needs a live check"
ACCEPT_OK = "  - Ruling 2026-11-03: accept owner ok"
EVERY_TAG = (("--tag", RC5), ("--tag", RC6), ("--tag", FINAL), ("--sha", "HEAD"), ("--sha", "HEAD", "--for", RC5))


class ScopedCase(Case):

    def ruled(self, rulings: list[str], r: GitRepo | None = None) -> GitRepo:
        """The oracle preamble with `rulings` after the item (on a fresh repo, or a new note in `r`)."""
        if r is None:
            r = self.repo()
            for tag in (RC5, RC6, FINAL):
                r.tag(tag, r.B)
            r.run_folder(1, r.B)
        r.write_note(note_text([record_line(1, r.B)], [ITEM, *rulings], []))
        return r

    def check(self, r: GitRepo, *args: str) -> tuple[int, dict]:
        return r.devloop("release-check", *args)

    def open_items(self, payload: dict) -> list[str]:
        found = reasons_named(payload, "open-security")
        return [item_text(i) for reason in found for i in reason.get("items") or []]

    def unparsed(self, payload: dict) -> list[str]:
        found = reasons_named(payload, "unparsed-security")
        return [item_text(i) for reason in found for i in reason.get("lines") or []]

    def assert_item_open(self, code: int, payload: dict, line: str = ITEM) -> None:
        self.assertEqual(code, 1, payload)
        self.assertIs(payload.get("ok"), False, payload)
        self.assertTrue([i for i in self.open_items(payload) if line[2:] in i], payload)

    def assert_passes(self, code: int, payload: dict) -> None:
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("reasons"), [], payload)

    def assert_unparsed(self, code: int, payload: dict, line: str) -> None:
        self.assertEqual(code, 1, payload)
        self.assertIs(payload.get("ok"), False, payload)
        self.assertIn(line.rstrip(), self.unparsed(payload), payload)


class WaiveTests(ScopedCase):

    def test_o01_waive_closes_the_item_for_its_tag_only(self) -> None:
        r = self.ruled([WAIVE_RC5])
        with self.subTest(tag=RC5):
            self.assert_passes(*self.check(r, "--tag", RC5))
        for tag in (FINAL, RC6):
            with self.subTest(tag=tag):
                self.assert_item_open(*self.check(r, "--tag", tag))

    def test_o02_sha_with_for_takes_the_for_tag(self) -> None:
        r = self.ruled([WAIVE_RC5])
        with self.subTest(args="--sha HEAD --for v3.0.0-rc.5"):
            self.assert_passes(*self.check(r, "--sha", "HEAD", "--for", RC5))
        with self.subTest(args="--sha HEAD"):
            self.assert_item_open(*self.check(r, "--sha", "HEAD"))

    def test_o03_for_usage_errors(self) -> None:
        r = self.ruled([WAIVE_RC5])
        for args in (("--tag", FINAL, "--for", FINAL), ("--for", FINAL), ("--sha", "HEAD", "--for", "3.0.0"),
                     ("--sha", "HEAD", "--for", "rc.5")):
            with self.subTest(args=" ".join(args)):
                code, payload = self.check(r, *args)
                self.assertEqual(code, 2, payload)
        for args in (("--sha", "HEAD", "--for", FINAL), ("--sha", "HEAD", "--for", RC5)):
            with self.subTest(valid=" ".join(args)):
                code, payload = self.check(r, *args)
                self.assertNotEqual(code, 2, payload)
                self.assertIn("ok", payload)

    def test_o04_waive_without_a_full_tag_or_a_reason_is_unparsed(self) -> None:
        r = self.ruled([])
        for ruling in ("  - Ruling 2026-11-03: waive rc.5 x", "  - Ruling 2026-11-03: waive 3.0.0 x",
                       "  - Ruling 2026-11-03: waive v3.0.0-rc.5"):
            self.ruled([ruling], r)
            for args in EVERY_TAG:
                with self.subTest(ruling=ruling, args=" ".join(args)):
                    self.assert_unparsed(*self.check(r, *args), ruling)

    def test_o05_last_ruling_decides_with_a_waive(self) -> None:
        r = self.ruled([])
        with self.subTest(order="waive then accept"):
            self.ruled(["  - Ruling 2026-11-03: waive v3.0.0-rc.5 x", "  - Ruling 2026-11-04: accept owner ok"], r)
            code, payload = self.check(r, "--tag", FINAL)
            self.assertEqual(self.open_items(payload), [], payload)
            self.assert_passes(code, payload)
        with self.subTest(order="accept then waive"):
            self.ruled([ACCEPT_OK, "  - Ruling 2026-11-04: waive v3.0.0-rc.5 x"], r)
            self.assert_passes(*self.check(r, "--tag", RC5))
            self.assert_item_open(*self.check(r, "--tag", FINAL))


class LegacyAndShapeTests(ScopedCase):

    def test_o06_legacy_rc_scoped_reason_is_unparsed_and_not_a_ruling(self) -> None:
        r = self.ruled([])
        reasons = ("for rc.4 only. Validate later.", "for rc.2: check it", "x before the final release",
                   "not for the final x")
        for word in ("accept", "close"):
            for reason in reasons:
                ruling = f"  - Ruling 2026-11-03: {word} {reason}"
                self.ruled([ruling], r)
                for args in EVERY_TAG:
                    with self.subTest(ruling=ruling, args=" ".join(args)):
                        code, payload = self.check(r, *args)
                        self.assert_item_open(code, payload)
                        self.assert_unparsed(code, payload, ruling)
        with self.subTest(case="a later valid close"):
            self.ruled(["  - Ruling 2026-11-03: accept for rc.4 only x", "  - Ruling 2026-11-04: close fixed by #1"], r)
            code, payload = self.check(r, "--tag", FINAL)
            self.assertEqual(self.unparsed(payload), [], payload)
            self.assertEqual(self.open_items(payload), [], payload)
            self.assert_passes(code, payload)

    def test_o07_ruling_shaped_lines_that_do_not_parse(self) -> None:
        r = self.ruled([])
        for line in (" - Ruling 2026-11-05: fix #230 x", "  - Ruling 2026-11-05: reopen: x",
                     "  - Ruling 2026-11-5: fix #230 x", "  - Ruling 2026-11-05 fix #230 x",
                     "  - ruling 2026-11-05: fix #230 x", "  - **Ruling 2026-11-05**: fix #230 x",
                     "  * Ruling 2026-11-05: fix #230 x", "    - Ruling 2026-11-05: fix #230 x"):
            with self.subTest(line=line):
                self.ruled([ACCEPT_OK, line], r)
                self.assert_unparsed(*self.check(r, "--tag", FINAL), line)

    def test_o08_other_sub_bullet_naming_a_ruling_is_not_unparsed(self) -> None:
        """Oracle 8 beside the waive of oracle 1: the sub-bullet is neither unparsed nor a ruling."""
        r = self.ruled([WAIVE_RC5, "  - Verdict: the ruling of Run 3 stands"])
        with self.subTest(tag=RC5):
            self.assert_passes(*self.check(r, "--tag", RC5))
        with self.subTest(tag=FINAL):
            code, payload = self.check(r, "--tag", FINAL)
            self.assertFalse(reasons_named(payload, "unparsed-security"), payload)
            self.assert_item_open(code, payload)

    def test_o09_ruling_inside_a_fence_is_not_a_ruling(self) -> None:
        r = self.ruled([])
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                self.ruled(["  - Ruling 2026-11-03: fix #170 patch", f"  {fence}",
                            "  - Ruling 2026-11-04: accept old fixture", f"  {fence}"], r)
                code, payload = self.check(r, "--tag", FINAL)
                self.assertFalse(reasons_named(payload, "unparsed-security"), payload)
                self.assert_item_open(code, payload)

    def test_o10_fence_still_open_at_the_end_of_its_item_is_unparsed(self) -> None:
        r = self.ruled([ACCEPT_OK, "  ```", "  - Ruling 2026-11-05: fix #1 x", NEXT_ITEM])
        self.assert_unparsed(*self.check(r, "--tag", FINAL), "  ```")

    def test_o11_accept_with_no_reason_is_open_and_unparsed(self) -> None:
        ruling = "  - Ruling 2026-10-26: accept"
        r = self.ruled([ruling])
        code, payload = self.check(r, "--tag", FINAL)
        self.assert_item_open(code, payload)
        self.assert_unparsed(code, payload, ruling)


class FlowTests(ScopedCase):

    def test_f2_owner_typo_in_a_reopen_ruling_fails_closed(self) -> None:
        """F2 (oracle 7): an accept closes the item; the owner's reopen ruling has a date typo; `release-check`
        fails with `unparsed-security` instead of passing on the accept."""
        r = self.ruled([ACCEPT_OK])
        self.assert_passes(*self.check(r, "--tag", RC5))
        typo = "  - Ruling 2026-11-5: fix #230 the token still leaks"
        self.ruled([ACCEPT_OK, typo], r)
        for args in (("--tag", RC5), ("--sha", "HEAD", "--for", RC5)):
            with self.subTest(args=" ".join(args)):
                self.assert_unparsed(*self.check(r, *args), typo)

    def test_f3_quoted_check_output_keeps_the_item_open(self) -> None:
        """F3 (oracle 9, 2): after a `fix` ruling the owner quotes check output in a fence under the item; the
        quoted ruling line does not close it, for the final and with no tag."""
        r = self.ruled(["  - Ruling 2026-11-03: fix #170 patch the log call", "  ```text",
                        "  $ devloop.py release-check --tag v3.0.0-rc.4", '  {"ok": true, "reasons": []}',
                        "  - Ruling 2026-11-02: accept old fixture", "  ```"])
        for args in (("--tag", FINAL), ("--sha", "HEAD", "--for", FINAL), ("--sha", "HEAD")):
            with self.subTest(args=" ".join(args)):
                code, payload = self.check(r, *args)
                self.assertFalse(reasons_named(payload, "unparsed-security"), payload)
                self.assert_item_open(code, payload)


if __name__ == "__main__":
    unittest.main()
