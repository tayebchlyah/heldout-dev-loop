#!/usr/bin/env python3
"""Held-out tests of a waived item in `release-record`, the git guards and the kit text (spec: Scoped Rulings,
issue #241, oracle 12 to 15, flows 1 and 4). Oracle 12 is proved inside flow 4: alone, the skip of an open item
holds before the feature too (Release Security Audit oracle 24), so its test must also show the waive at work.

Seams: `devloop.py release-record` and `release-check` (the kit source) as processes on a scratch git repo and
notes folder (seam 1); the git guard of each harness, installed by `bin/install.mjs init` from the source tree and
fed hook JSON on stdin (seam 2, the seam of the Release Security Audit guard tests); the kit source text. Repo and
note helpers: `release_security_audit_support.py`; oracle 1 to 11, flows 2 and 3: `test_scoped_rulings.py`.

Fixture choices the spec leaves open:
- Oracle 12: commit A2 adds `src/a.py`, commit B2 changes only `src/app.py`; the waived item names `Run 9`, the
  record line is `Run 9 at <A2>` with its run folder; the new run `Run 10` at B2 holds one `needs_validation`
  record `f-a` whose trace is two lines of `src/a.py` (unchanged since A2); `release-record --base <A2>`. The
  record line of Run 10 is then current for `HEAD`, so flow 4 checks the next tags with `--sha HEAD --for`.
- The guard repo: the kit installed by `init`, commit A, annotated tags `v3.0.0-rc.5` and `v3.0.0` at A, the
  record `Run 1 at <A>`, `origin` a bare repo holding `main`. So both commands of a tag check commit A.
- "Asks": the Claude main session gives `ask`; the Codex guard refuses a tag push in its main session (Release
  Security Audit oracle 49), so a Codex decision is `ask` or `deny`. Either way the reason holds the
  `release-check` suffix.
- A skill step `n` is the text from a line `n. ` to the next line `n+1. ` or heading. "The check after step 2" is
  a line of step 2 or step 3 that runs `release-check --sha HEAD --for`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from release_security_audit_support import (base_env, Case, DEVLOOP, GitRepo, hook_decision, INSTALLER, item_text,
                                            note_text, NOTES_NAME, put, reasons_named, record_line, REPO_ROOT,
                                            REPORT_NO_HARDENING, section)


ITEM_9 = "- 2026-11-01 (security, needs-validation, Run 9): x. Fingerprint `f-a`."
RC5, RC6, FINAL = "v3.0.0-rc.5", "v3.0.0-rc.6", "v3.0.0"
WAIVE_RC5 = "  - Ruling 2026-11-03: waive v3.0.0-rc.5 needs a live check"
A_PY = "def media_path(root, name):\n    return root + '/' + name\n"
APP_THIRD = "def colour(theme):\n    return 'green'\n"
TRACE = [{"file": "src/a.py", "line": 1}, {"file": "src/a.py", "line": 2}]

RELEASE_SKILL = REPO_ROOT / "skills" / "release" / "SKILL.md"
AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
CHECK_REF = REPO_ROOT / "reference" / "cli" / "release-check.md"
DEFERRED_REF = REPO_ROOT / "skill" / "references" / "deferred.md"


def step(text: str, n: int) -> str:
    match = re.search(rf"(?ms)^{n}\. .*?(?=^{n + 1}\. |^#|\Z)", text)
    return match.group(0) if match else ""


def open_items(payload: dict) -> list[str]:
    return [item_text(i) for reason in reasons_named(payload, "open-security") for i in reason.get("items") or []]


# ---------------------------------------------------------------------------------------------------------------
# release-record (O12, flow 4)
# ---------------------------------------------------------------------------------------------------------------

class RecordTests(Case):

    def setup_waived(self) -> tuple[GitRepo, Path, str]:
        r = self.repo()
        a = r.commit({"src/a.py": A_PY}, "A2")
        b = r.commit({"src/app.py": APP_THIRD}, "B2")
        r.run_folder(9, a, profile="standard")
        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, "process.exit(0)\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)
        r.write_note(note_text([record_line(9, a, date="2026-11-01", profile="standard", needs=1)],
                               [ITEM_9, WAIVE_RC5], []))
        run = r.notes / "Security Audit" / "Run 10"
        put(run / "run-metadata.json", json.dumps({"source_ref": {"commit": b}, "profile": "standard",
                                                   "scope_paths": [], "run_status": "complete"}) + "\n")
        put(run / "findings.json", json.dumps([{"verdict": "needs_validation", "fingerprint": "f-a",
                                                "title": "x", "blockers": ["fact one"],
                                                "trace": [dict(t) for t in TRACE]}], indent=2) + "\n")
        put(run / "coverage-ledger.json", "{}\n")
        put(run / "REPORT.md", REPORT_NO_HARDENING)
        put(run / "FINDINGS-DETAIL.md", "# Findings\n")
        put(run / "NEEDS-VALIDATION.md", "# Needs validation\n")
        return r, run, a

    def fa_lines(self, r: GitRepo) -> list[str]:
        return [line for line in section(r.note_path().read_text(), "## Deferred") if "f-a" in line or "Ruling" in line]

    def record(self, r: GitRepo, run: Path, a: str) -> dict:
        before = self.fa_lines(r)
        code, payload = r.devloop("release-record", "--run", str(run), "--base", a)
        self.assertEqual(code, 0, payload)
        self.assertIn("f-a", payload.get("skipped") or [], payload)
        self.assertEqual(payload.get("carried"), [], payload)
        self.assertEqual(self.fa_lines(r), before, r.note_path().read_text())
        return payload

    def test_f4_reaudit_keeps_the_waived_item_open_for_the_next_tag(self) -> None:
        """F4 (oracle 12, 2): the next run re-finds the waived item; `release-record` skips it and carries no
        ruling; the new record is current, and the item is still open for the next tags, closed for its rc."""
        r, run, a = self.setup_waived()
        self.record(r, run, a)
        for tag in (RC6, FINAL):
            with self.subTest(next_tag=tag):
                code, payload = r.devloop("release-check", "--sha", "HEAD", "--for", tag)
                self.assertEqual(code, 1, payload)
                self.assertFalse(reasons_named(payload, "stale-audit"), payload)
                self.assertTrue([i for i in open_items(payload) if ITEM_9[2:] in i], payload)
        with self.subTest(tag=RC5):
            code, payload = r.devloop("release-check", "--sha", "HEAD", "--for", RC5)
            self.assertEqual((code, payload.get("reasons")), (0, []), payload)


# ---------------------------------------------------------------------------------------------------------------
# git guards (O13, flow 1)
# ---------------------------------------------------------------------------------------------------------------

class GuardTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.notes = self.root / "notes" / NOTES_NAME
        for folder in (self.root / "home", self.root / "tmp", self.notes):
            folder.mkdir(parents=True)
        self.env = base_env(self.root / "home", self.root / "tmp")
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}"],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        put(self.repo / "src" / "app.py", "def colour(theme):\n    return 'red'\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "A")
        self.A = self.git("rev-parse", "HEAD")
        for tag in (RC5, FINAL):
            self.git("tag", "-a", "-m", f"Release {tag}", tag)
        origin = self.root / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")
        self.git("fetch", "-q", "origin")
        put(self.notes / "Security Audit" / "Run 1" / "run-metadata.json",
            json.dumps({"source_ref": {"commit": self.A}, "profile": "quick", "scope_paths": [],
                        "run_status": "complete"}))
        put(self.notes / "Project Note.md", note_text([record_line(1, self.A)], [ITEM_9, WAIVE_RC5], []))

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def guard(self, command: str, harness: str) -> tuple[str | None, str]:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        shell = "bash" if harness == "claude" else "sh"
        script = self.repo / ".dev-loop" / "hooks" / harness / "git-guard.sh"
        result = subprocess.run([shell, str(script)], cwd=self.repo, input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=60)
        return hook_decision(result)

    def check(self, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), "release-check", *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"unparsed_stdout": result.stdout, "_stderr": result.stderr}
        return result.returncode, payload

    def assert_guard(self, command: str, harness: str, waived: bool) -> None:
        decision, reason = self.guard(command, harness)
        self.assertIn(decision, ("ask",) if harness == "claude" else ("ask", "deny"), reason)
        if waived:
            self.assertIn("release-check: ok", reason, command)
        else:
            self.assertIn("release-check:", reason, command)
            self.assertNotIn("release-check: ok", reason, command)
            self.assertIn("open-security", reason, command)

    def test_o13_guards_check_the_tag_they_push_or_create(self) -> None:
        for harness in ("claude", "codex"):
            for command, waived in ((f"git push origin {RC5}", True), (f"git push origin {FINAL}", False),
                                    (f"gh release create {RC5}", True), (f"gh release create {FINAL}", False)):
                with self.subTest(harness=harness, command=command):
                    self.assert_guard(command, harness, waived)

    def test_f1_rc_only_waive_passes_the_rc_and_blocks_the_final(self) -> None:
        """F1 (oracle 1, 2, 13): the owner waives the item for v3.0.0-rc.5; `release-check` passes for that rc by
        `--tag`, by `--sha --for` and in the tag-push guard, and fails `open-security` for the final."""
        for args in (("--tag", RC5), ("--sha", "HEAD", "--for", RC5)):
            with self.subTest(rc=" ".join(args)):
                code, payload = self.check(*args)
                self.assertEqual((code, payload.get("ok"), payload.get("reasons")), (0, True, []), payload)
        for args in (("--tag", FINAL), ("--sha", "HEAD", "--for", FINAL)):
            with self.subTest(final=" ".join(args)):
                code, payload = self.check(*args)
                self.assertEqual(code, 1, payload)
                self.assertTrue([i for i in open_items(payload) if ITEM_9[2:] in i], payload)
        with self.subTest(guard="tag push"):
            self.assert_guard(f"git push origin {RC5}", "claude", True)
            self.assert_guard(f"git push origin {FINAL}", "claude", False)


# ---------------------------------------------------------------------------------------------------------------
# Kit text (O14, O15)
# ---------------------------------------------------------------------------------------------------------------

class TextTests(unittest.TestCase):

    def test_o14_release_skills_pass_the_tag(self) -> None:
        text = RELEASE_SKILL.read_text()
        first, second, third, seventh = (step(text, n) for n in (1, 2, 3, 7))
        for n, body in ((1, first), (2, second), (3, third), (7, seventh)):
            self.assertTrue(body, f"skills/release/SKILL.md has no step {n}")
        self.assertNotIn("release-check", first, "skills/release/SKILL.md step 1")
        self.assertRegex(second + third, r"release-check[^\n]*--sha HEAD --for",
                         "skills/release/SKILL.md: the check after step 2")
        self.assertIn("--for", third, "skills/release/SKILL.md step 3")
        self.assertIn("--for", seventh, "skills/release/SKILL.md step 7")
        sixth = step(AUDIT_SKILL.read_text(), 6)
        self.assertTrue(sixth, "skills/release-audit/SKILL.md has no step 6")
        self.assertIn("--for", sixth, "skills/release-audit/SKILL.md step 6")

    def test_o15_references_name_waive_and_fences(self) -> None:
        check = CHECK_REF.read_text()
        for pattern in (r"--for\b", r"\bwaive\b", r"unparsed-security", r"(?i)\bruling\b", r"(?i)\bfence"):
            with self.subTest(file="reference/cli/release-check.md", pattern=pattern):
                self.assertRegex(check, pattern)
        deferred = DEFERRED_REF.read_text()
        for pattern in (r"\bwaive\b", r"(?i)fenced\s+block", r"(?i)check\s+output"):
            with self.subTest(file="skill/references/deferred.md", pattern=pattern):
                self.assertRegex(deferred, pattern)


if __name__ == "__main__":
    unittest.main()
