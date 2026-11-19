#!/usr/bin/env python3
"""Held-out tests of the spec Optional Release Candidate: one test per `## Acceptance flows` item (F<n>) and per
`## Acceptance oracle` line (O<n>) no flow proves; a test that proves more lines names them.

Seams: `scripts/repo_rules.py promoted` (stdout JSON, exit code) on a scratch git repo with tags;
`devloop.py release-check` on the scratch wave repo and notes folder of `one_audit_per_release_support.py`; the
text of `reference/release.md` and `reference/cli/release-check.md`. Every name in the scratch repos is invented.

Oracles 4 and 10 (earlier oracles hold unchanged) are the earlier held-out tests:
`test_release_candidates_release.py` (O36 to O41, O39 as superseded) and the `test_one_audit_per_release*.py`
files (oracle 9 and F2 as superseded).

Fixture choices the spec leaves open:
- The promoted repo: a base commit with kit source paths, `package.json`, `CHANGELOG.md` and a kit copy, tagged
  with the last final release; a release commit changes `package.json`, `CHANGELOG.md` and the kit copy only.
- A fix wave release commit: `WaveRepo.release`, a new `## [<version>]` section of `CHANGELOG.md` and
  `package.json` at that version (the wave repo starts at 1.0.0, so a `1.0.0` release changes `CHANGELOG.md`
  only); a commit T with version files only changes `package.json` alone.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from one_audit_per_release_support import package_json, WaveCase
from release_candidates_support import paragraphs, REPO_ROOT, REPO_RULES, Scratch
from release_security_audit_support import reasons_named

RELEASE_MD = REPO_ROOT / "reference" / "release.md"
CHECK_MD = REPO_ROOT / "reference" / "cli" / "release-check.md"

KIT_SOURCE = {"skill/SKILL.md": "# skill one\n", "skills/pause/SKILL.md": "# pause one\n",
              "claude/agents/spec-tester.md": "agent one\n", "codex/agents/spec-tester.toml": "agent = 1\n",
              "bin/install.mjs": "// one\n", "lib/kit.mjs": "export const one = 1\n"}


# ---------------------------------------------------------------------------------------------------------------
# Promoted check with no release candidate (F1, O1 to O3)
# ---------------------------------------------------------------------------------------------------------------

class PromotedNoCandidate(Scratch):

    def kit_repo(self, last_final: str) -> Path:
        """A copy of the kit with one final release `last_final` tagged on its base commit."""
        repo = self.new_repo(f"kit-{len(list(self.root.iterdir()))}")
        self.commit(repo, {**KIT_SOURCE, "package.json": json.dumps({"version": last_final.lstrip("v")}) + "\n",
                           "README.md": "# kit\n", "CHANGELOG.md": "# Changelog\n",
                           ".agents/skills/dev-loop/SKILL.md": "# copy 1\n"}, "base")
        self.tag(repo, last_final)
        return repo

    def release_commit(self, repo: Path, version: str) -> str:
        return self.commit(repo, {"package.json": json.dumps({"version": version}) + "\n",
                                  "CHANGELOG.md": f"# Changelog\n\n## [{version}]\n",
                                  ".agents/skills/dev-loop/SKILL.md": f"# copy {version}\n"}, f"release {version}")

    def promoted(self, repo: Path, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(REPO_RULES), "promoted", *args, "--repo", str(repo)],
                                cwd=repo, env=self.env, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"unparsed_stdout": result.stdout, "stderr": result.stderr}
        return result.returncode, payload if isinstance(payload, dict) else {"unparsed_stdout": result.stdout}

    def assert_skipped(self, code: int, payload: dict, tag: str) -> None:
        self.assertEqual(code, 0, payload)
        self.assertIn("candidate", payload)
        self.assertEqual({key: payload.get(key) for key in ("ok", "tag", "candidate", "violations", "skipped")},
                         {"ok": True, "tag": tag, "candidate": None, "violations": [],
                          "skipped": "no-release-candidate"}, payload)

    def test_f1_final_release_with_no_rc(self) -> None:
        """F1: a repo with release history v1.0.0 and no v1.1.0-rc.* tag: `promoted --sha HEAD --tag v1.1.0`
        before the tag passes, skipped `no-release-candidate`."""
        repo = self.kit_repo("v1.0.0")
        self.commit(repo, {"lib/kit.mjs": "export const one = 2\n"}, "minor feature")
        self.release_commit(repo, "1.1.0")
        code, payload = self.promoted(repo, "--sha", "HEAD", "--tag", "v1.1.0")
        self.assert_skipped(code, payload, "v1.1.0")

    def test_o01_to_o03_final_with_no_rc_of_its_version(self) -> None:
        """O1 to O3: no v3.0.0-rc.* tag: `promoted --tag v3.0.0` (O1), with `--sha` and no v3.0.0 tag (O2), and
        with only v2.9.0-rc.1 (O3): exit 0, ok true, candidate null, violations [], skipped
        no-release-candidate."""
        for case in ("O1 tag", "O2 sha before the tag", "O3 rc of another version"):
            with self.subTest(case=case):
                repo = self.kit_repo("v2.1.1")
                if case.startswith("O3"):
                    self.commit(repo, {"lib/kit.mjs": "export const nine = 9\n"}, "2.9.0 work")
                    self.tag(repo, "v2.9.0-rc.1")
                head = self.release_commit(repo, "3.0.0")
                if case.startswith("O2"):
                    code, payload = self.promoted(repo, "--tag", "v3.0.0", "--sha", head)
                else:
                    self.tag(repo, "v3.0.0")
                    code, payload = self.promoted(repo, "--tag", "v3.0.0")
                self.assert_skipped(code, payload, "v3.0.0")


# ---------------------------------------------------------------------------------------------------------------
# Fix wave and a release tag (F2, O5 to O9)
# ---------------------------------------------------------------------------------------------------------------

class WaveAndReleaseTag(WaveCase):

    def assert_ok_with_wave(self, args: tuple[str, ...], r, line: str) -> None:
        code, check = r.check(*args)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)
        self.assertEqual(check.get("reasons"), [], check)
        self.assert_wave(check, line)
        self.assertIn("wave_ignored", check)
        self.assertIsNone(check["wave_ignored"], check)

    def assert_stale(self, args: tuple[str, ...], r, line: str | None, ignored: str | None) -> dict:
        code, check = r.check(*args)
        self.assertEqual(code, 1, check)
        self.assertIs(check.get("ok"), False, check)
        stale = reasons_named(check, "stale-audit")
        self.assertTrue(stale, check)
        self.assert_wave(check, line)
        self.assertIn("wave_ignored", check)
        self.assertEqual(check["wave_ignored"], ignored, check)
        return stale[0]

    def test_f2_final_promotion_of_an_rc_cut_on_a_fix_wave(self) -> None:
        """F2 (O5, O6, O7), with the fix wave of One Audit Per Release: record R at A, wave W at C, tag
        v1.0.0-rc.1 on C, release commit E: `release-check --sha E` ok with the wave (O5); a code commit F after E
        is stale, wave ended by the rc tag (O6); tag v1.0.0 on E: `--tag v1.0.0` and `--sha E` ok with the wave
        (O7)."""
        r, line = self.waved()
        r.tag("v1.0.0-rc.1", r.C)
        self.assert_ok_with_wave(("--tag", "v1.0.0-rc.1"), r, line)

        e = r.release("1.0.0")
        self.assert_ok_with_wave(("--sha", e), r, line)

        f = r.code("#9: later work")
        stale = self.assert_stale(("--sha", f), r, None, "released: v1.0.0-rc.1")
        self.assertIn("src/app.py", stale.get("paths") or [], stale)

        r.tag("v1.0.0", e)
        for args in (("--tag", "v1.0.0"), ("--sha", e)):
            with self.subTest(args=args):
                self.assert_ok_with_wave(args, r, line)

    def test_o08_o09_tag_ends_the_wave_only_off_the_line_or_with_code_after_it(self) -> None:
        """O8: rc tag on T (version files, after C), release commit E after C, a sibling of T: stale-audit, wave
        null, `released: v1.0.0-rc.1`. O9: rc tag on T that changes `src/app.py` after C, release commit E after T:
        stale-audit with `src/app.py`, wave the line, wave_ignored null."""
        with self.subTest(oracle="O8 sibling of the tagged commit"):
            r, _ = self.waved()
            t = r.side_commit(r.C, {"package.json": package_json("1.0.0-rc.1")}, "🔖(release) acme-widgets 1.0.0-rc.1")
            r.tag("v1.0.0-rc.1", t)
            e = r.release("1.0.0")
            self.assertNotEqual(r.git("merge-base", t, e), t)
            self.assert_stale(("--sha", e), r, None, "released: v1.0.0-rc.1")
        with self.subTest(oracle="O9 code in the tagged commit"):
            r, line = self.waved()
            t = r.code("#9: later work")
            r.tag("v1.0.0-rc.1", t)
            e = r.release("1.0.0")
            stale = self.assert_stale(("--sha", e), r, line, None)
            self.assertIn("src/app.py", stale.get("paths") or [], stale)


# ---------------------------------------------------------------------------------------------------------------
# Text (O11, O12)
# ---------------------------------------------------------------------------------------------------------------

RC = r"(?:release candidates?|\brcs?\b)"


class TextTests(unittest.TestCase):

    def test_o11_release_md_rc_is_optional_and_promoted_skips(self) -> None:
        """O11: reference/release.md says a release candidate is optional and that `promoted` skips a final with
        no rc."""
        blocks = paragraphs(RELEASE_MD.read_text())
        optional = [b for b in blocks if re.search(r"\boptional(?:ly)?\b", b, re.I) and re.search(RC, b, re.I)]
        self.assertTrue(optional, "reference/release.md does not say a release candidate is optional")
        skips = [b for b in blocks if "promoted" in b and re.search(r"\bskip", b, re.I)
                 and re.search(rf"(?:\bno\b|\bwithout\b)[^.\n]{{0,40}}{RC}|no-release-candidate", b, re.I)]
        self.assertTrue(skips, "reference/release.md does not say `promoted` skips a final with no rc")

    def test_o12_release_check_md_fix_wave_tag_rule(self) -> None:
        """O12, D3: the Fix wave paragraph of reference/cli/release-check.md says a tag ends the wave only when the
        target does not descend from its commit or code changed after it."""
        fix = [b for b in paragraphs(CHECK_MD.read_text()) if b.lstrip().startswith("Fix wave")]
        self.assertEqual(len(fix), 1, "reference/cli/release-check.md needs one Fix wave paragraph")
        sentences = re.split(r"(?<=[.!?])\s+", fix[0])
        rule = [s for s in sentences if re.search(r"\btags?\b", s) and re.search(r"descend", s, re.I)
                and re.search(r"\bafter\b", s, re.I) and re.search(r"\bcode\b|\bpaths?\b|stale-audit", s, re.I)]
        self.assertTrue(rule, "the Fix wave paragraph does not state when a tag ends the wave (D3)")


if __name__ == "__main__":
    unittest.main()
