#!/usr/bin/env python3
"""Held-out oracle tests for `release-plan --require-sandbox` and the `/release-audit` skill text
(spec: Audit Plan And Record Fixes, oracle 1 to 9 and 22, flows 1 and 2).

Seams: `devloop.py release-plan` as a process on a scratch git repo and a scratch notes folder, with `PATH` set to
control `docker`; the text of `skills/release-audit/SKILL.md`, `reference/cli/release-plan.md` and
`reference/cli/release-record.md`. Repo and note helpers: `release_security_audit_support.py`.

Fixture choices the spec leaves open:
- "no `docker` on `PATH`": `PATH` is a folder with a link to `git` only.
- A `docker` stub is a shell script that exits 0 for every argument (so `docker info` exits 0).
- The skill text is read as blocks split on blank lines; a step is the block that names its command.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from release_security_audit_support import (APP_B, AUDIT_SKILL, BASE_CONFIG, Case, GitRepo, note_text, put,
                                            REPO_ROOT, tools_dir)


PLAN_REF = REPO_ROOT / "reference" / "cli" / "release-plan.md"
RECORD_REF = REPO_ROOT / "reference" / "cli" / "release-record.md"


def blocks(text: str) -> list[str]:
    return [b for b in re.split(r"\n\s*\n", text) if b.strip()]


class SandboxPlanCase(Case):

    def audit_dir(self, r: GitRepo) -> Path:
        return r.notes / "Security Audit"

    def listing(self, r: GitRepo) -> list[str]:
        folder = self.audit_dir(r)
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def plan_repo(self, images: object = None, key: bool = True) -> GitRepo:
        """A clean repo, no `## Release audits` record; `security_audit.images` set when `key` and not None."""
        config = dict(BASE_CONFIG)
        if key and images is not None:
            config["security_audit"] = {"images": images}
        r = self.repo(config)
        r.write_note(note_text(None, []))
        return r

    def no_docker(self) -> str:
        return str(tools_dir(self.root, "git"))

    def docker_ok(self) -> str:
        stubs = self.root / "docker-ok"
        if not (stubs / "docker").exists():
            put(stubs / "docker", "#!/bin/sh\nexit 0\n")
            (stubs / "docker").chmod(0o755)
        return f"{stubs}:{tools_dir(self.root, 'git')}"

    def assert_no_sandbox(self, r: GitRepo, path: str, reason: str) -> None:
        before = self.listing(r)
        code, payload = r.devloop("release-plan", "--require-sandbox", env={"PATH": path})
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), "no-sandbox", payload)
        self.assertEqual(payload.get("reason"), reason, payload)
        self.assertEqual(self.listing(r), before, "release-plan created a folder under Security Audit/")


# ---------------------------------------------------------------------------------------------------------------
# release-plan --require-sandbox (O1 to O7)
# ---------------------------------------------------------------------------------------------------------------

class RequireSandboxTests(SandboxPlanCase):

    def test_o01_no_security_audit_key(self) -> None:
        r = self.plan_repo(key=False)
        self.assert_no_sandbox(r, self.docker_ok(), "no-images")

    def test_o02_empty_images(self) -> None:
        r = self.plan_repo({})
        self.assert_no_sandbox(r, self.docker_ok(), "no-images")

    def test_o03_no_docker_on_path(self) -> None:
        r = self.plan_repo({"t": "x"})
        self.assert_no_sandbox(r, self.no_docker(), "no-engine")

    def test_o04_no_images_comes_first(self) -> None:
        r = self.plan_repo(key=False)
        self.assert_no_sandbox(r, self.no_docker(), "no-images")

    def test_o05_sandbox_ready(self) -> None:
        r = self.plan_repo({"t": "x"})
        code, payload = r.devloop("release-plan", "--require-sandbox", env={"PATH": self.docker_ok()})
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("run"), "Run 1", payload)
        sandbox = payload.get("sandbox") or {}
        self.assertIs(sandbox.get("engine"), True, payload)
        self.assertEqual(sandbox.get("images"), {"t": "x"}, payload)
        self.assertTrue((self.audit_dir(r) / "Run 1").is_dir(), "the Run 1 folder was not created")

    def test_o06_no_flag_as_today(self) -> None:
        r = self.plan_repo(key=False)
        code, payload = r.devloop("release-plan", env={"PATH": self.no_docker()})
        self.assertEqual(code, 0, payload)
        self.assertNotIn("error", payload)
        self.assertEqual((payload.get("run"), payload.get("profile"), payload.get("scope")),
                         ("Run 1", "quick", "whole"), payload)
        self.assertEqual(payload.get("sandbox", {}).get("engine"), False, payload)
        self.assertTrue((self.audit_dir(r) / "Run 1").is_dir(), "the Run 1 folder was not created")

    def test_o07_existing_checks_come_first(self) -> None:
        with self.subTest(case="dirty"):
            r = self.plan_repo(key=False)
            put(r.repo / "src" / "app.py", APP_B + "# local edit\n")
            code, payload = r.devloop("release-plan", "--require-sandbox", env={"PATH": self.no_docker()})
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("error"), "dirty", payload)
            self.assertEqual(self.listing(r), [])
        with self.subTest(case="placeholder"):
            r = self.plan_repo(key=False)
            put(self.audit_dir(r) / "Run 1" / ".findings.json.icloud", b"\x00")
            before = self.listing(r)
            code, payload = r.devloop("release-plan", "--require-sandbox", env={"PATH": self.no_docker()})
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("error"), "placeholder", payload)
            self.assertEqual(self.listing(r), before)


# ---------------------------------------------------------------------------------------------------------------
# Skill and reference text (O8, O9, O22)
# ---------------------------------------------------------------------------------------------------------------

class SkillText:
    """The skill text checks a flow test shares with the oracle tests (no test of its own)."""

    def step_one(self) -> str:
        """The skill block that runs `release-plan` with `--require-sandbox`."""
        found = [b for b in blocks(AUDIT_SKILL.read_text()) if "release-plan" in b and "--require-sandbox" in b]
        self.assertTrue(found, "no skill block runs release-plan with --require-sandbox")
        return "\n".join(found)

    def assert_stop_rule(self) -> None:
        text = AUDIT_SKILL.read_text()
        step = self.step_one()
        self.assertIn("--no-sandbox", step)
        stop = [b for b in blocks(text) if "no-sandbox" in b.replace("--no-sandbox", "")
                and re.search(r"\bstop", b, re.I)]
        self.assertTrue(stop, "no skill block says to stop on no-sandbox")
        stop_text = "\n".join(stop)
        for name in ("no-images", "no-engine", "security_audit.images", "dev-loop.json",
                     "codex -c default_permissions=dev-docker"):
            self.assertIn(name, stop_text)
        self.assertRegex(stop_text, re.compile(r"\bstart\b[^.\n]*\bdocker\b", re.I))


class SkillTextTests(SkillText, unittest.TestCase):

    def test_o08_step_one_flag_and_stop_rule(self) -> None:
        self.assert_stop_rule()

    def test_o09_reference_files(self) -> None:
        plan = PLAN_REF.read_text()
        for name in ("--require-sandbox", "no-sandbox", "no-images", "no-engine"):
            self.assertIn(name, plan)
        record = RECORD_REF.read_text()
        for name in ("carried", "reopened"):
            self.assertIn(name, record)

    def test_o22_no_sandbox_only_drops_the_check(self) -> None:
        found = [b for b in blocks(AUDIT_SKILL.read_text()) if "--no-sandbox" in b]
        self.assertTrue([b for b in found if re.search(r"\bonly\b.*\bcheck", b, re.I | re.S)],
                        "no block says --no-sandbox only drops the check")
        self.assertTrue([b for b in found if re.search(r"\bstill\b.*\bsandbox", b, re.I | re.S)],
                        "no block says step 3 still uses the sandbox the plan has")


# ---------------------------------------------------------------------------------------------------------------
# Flows 1 and 2
# ---------------------------------------------------------------------------------------------------------------

class SandboxFlowTests(SandboxPlanCase, SkillText):

    def test_f1_no_sandbox_stops_before_the_audit(self) -> None:
        """F1: no image, the skill's step 1 call gives no-sandbox no-images; the skill stops and names the fix."""
        r = self.plan_repo(key=False)
        self.assert_no_sandbox(r, self.docker_ok(), "no-images")
        self.assertFalse((self.audit_dir(r) / "Run 1").exists())
        self.assert_stop_rule()

    def test_f2_no_sandbox_by_choice_runs_source_only(self) -> None:
        """F2: `--no-sandbox`, no image: release-plan without the flag plans Run 1; the run goes on source-only."""
        r = self.plan_repo(key=False)
        code, payload = r.devloop("release-plan", env={"PATH": self.no_docker()})
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("sandbox", {}).get("images"), {}, payload)
        self.assertTrue((self.audit_dir(r) / "Run 1").is_dir())
        found = [b for b in blocks(AUDIT_SKILL.read_text()) if "--no-sandbox" in b]
        self.assertTrue([b for b in found if "--require-sandbox" in b and re.search(r"\b(unless|without)\b", b, re.I)],
                        "no block says --no-sandbox runs release-plan without --require-sandbox")
        self.assertRegex(AUDIT_SKILL.read_text(), re.compile(r"source-only", re.I))


if __name__ == "__main__":
    unittest.main()
