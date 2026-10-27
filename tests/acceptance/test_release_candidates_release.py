#!/usr/bin/env python3
"""Held-out oracle tests of the release side: the release notes body, `release.yml`, `scripts/repo_rules.py
promoted` and the text contracts (spec: Release Candidates). Seams, fixture choices and shared helpers:
`release_candidates_support.py`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from release_candidates_support import (AGENTS, AUDIT_SKILL, PLAN_MD, RELEASE_MD, REPO_ROOT, REPO_RULES, SKILL,
                                        STATUS_MD, Scratch, Workflow, condition, headings, interpolate, paragraphs,
                                        put, section_of)

DEV_LOOP_JSON = REPO_ROOT / "dev-loop.json"
RELEASE_CHECK_SH = REPO_ROOT / "scripts" / "release-check.sh"
RELEASE_VERIFY_SH = REPO_ROOT / "scripts" / "release-verify.sh"


def is_notes(step: dict) -> bool:
    return bool(re.search(r"devloop\.py release-notes\b", str(step.get("run", ""))))


# ---------------------------------------------------------------------------------------------------------------
# Release notes (O30 to O33, superseded by spec Written Release Notes: the body is the written section)
# ---------------------------------------------------------------------------------------------------------------

RC2_CHANGELOG = """# Changelog

## [3.0.0-rc.2] - 2026-10-30

### Highlights

**Gadget dials.** Marker mkrc2 gadget dials turn by themselves, so a user sets them once.

**Widget trays.** Marker mkrc1 widget trays hold every widget, so a user finds them in one place.

## [2.1.1] - 2026-10-16

### Fixes

- Marker mka1 widget colour no longer resets.
"""

FINAL_CHANGELOG = """# Changelog

## [3.0.0] - 2026-11-01

### Highlights

**Gadget dials.** Marker mkfinal gadget dials turn by themselves, so a user sets them once.

### Upgrade notes

- Marker mkupgrade run the widget migration once.

## [2.1.1] - 2026-10-16

### Fixes

- Marker mka1 widget colour no longer resets.
"""


class NotesCase(Scratch):
    """A scratch repo with the kit source installed at `.agents/skills/dev-loop` and a written `CHANGELOG.md`."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.new_repo("notes-repo")
        shutil.copytree(REPO_ROOT / "skill", self.repo / ".agents" / "skills" / "dev-loop", symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.commit(self.repo, {"package.json": '{"version": "2.1.1"}\n', "CHANGELOG.md": "# Changelog\n"}, "base")

    def workflow_body(self, tag: str) -> str:
        """The release body file of `release.yml` after its `release-notes` step ran for `tag`."""
        flow = Workflow()
        ran = flow.index(is_notes)
        self.assertEqual(len(ran), 1, "release.yml needs one step that runs devloop.py release-notes")
        n = ran[0]
        context, env = flow.context(tag, self.repo, self.env, n)
        step = flow.steps[n]
        self.assertTrue(condition(step, context), f"the release-notes step does not run for {tag}")
        for key, value in (step.get("env") or {}).items():
            env[key] = interpolate(value, context)
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                                 interpolate(step["run"], context)], cwd=self.repo, env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        release = flow.steps[flow.index(flow.is_release)[0]]
        body_file = interpolate((release.get("with") or {}).get("bodyFile") or "", context)
        self.assertTrue(body_file and (self.repo / body_file).is_file(), f"no release body file {body_file!r}")
        return (self.repo / body_file).read_text()


class ReleaseNotesTests(NotesCase):

    def test_f6_rc_body_and_final_body(self) -> None:
        """F6 (O30, O31 as superseded): the release.yml body of v3.0.0-rc.2 is the written `## [3.0.0-rc.2]`
        section, cumulative after v2.1.1; the body of v3.0.0 is the written `## [3.0.0]` section, no rc
        heading."""
        self.commit(self.repo, {"CHANGELOG.md": RC2_CHANGELOG, "package.json": '{"version": "3.0.0-rc.2"}\n'},
                    "release 3.0.0-rc.2")
        self.tag(self.repo, "v3.0.0-rc.2")
        body = self.workflow_body("v3.0.0-rc.2")
        self.assertEqual(body.strip(), section_of(RC2_CHANGELOG, "## [3.0.0-rc.2]").strip())
        for marker in ("mkrc1", "mkrc2"):
            self.assertIn(marker, body)
        self.assertNotIn("mka1", body)
        self.commit(self.repo, {"CHANGELOG.md": FINAL_CHANGELOG, "package.json": '{"version": "3.0.0"}\n'},
                    "release 3.0.0")
        self.tag(self.repo, "v3.0.0")
        body = self.workflow_body("v3.0.0")
        self.assertEqual(body.strip(), section_of(FINAL_CHANGELOG, "## [3.0.0]").strip())
        self.assertNotIn("mka1", body)
        self.assertEqual([line for line in body.splitlines() if line.startswith("#") and "3.0.0-rc" in line], [])
        self.assertEqual(headings(body), [])

    def test_o32_o33_release_md_names_no_cliff_changelog_command(self) -> None:
        """O32, O33 as superseded: reference/release.md names no `git cliff` command; the rc and final sections
        are written through `/release` (which runs the `/release-notes` steps)."""
        text = RELEASE_MD.read_text()
        self.assertIsNone(re.search(r"git[ -]cliff", text), "reference/release.md still names a git cliff command")
        self.assertRegex(text, r"(?<![\w-])/release(?![\w-])")


# ---------------------------------------------------------------------------------------------------------------
# Release workflow (O34, O35)
# ---------------------------------------------------------------------------------------------------------------

class WorkflowTests(Scratch):

    def stubs(self) -> tuple[Path, Path, Path]:
        """A git work folder with a stub scripts/repo_rules.py, a PATH folder of stubs, the shared call log."""
        work = self.new_repo(f"work-{len(list(self.root.iterdir()))}")
        log = self.root / f"calls-{work.name}.log"
        stub = f'#!/bin/sh\necho "$(basename "$0") $*" >> {shlex.quote(str(log))}\necho \'{{"ok": true}}\'\n'
        folder = self.root / f"stubs-{work.name}"
        folder.mkdir()
        for name in ("python3", "python", "git"):
            put(folder / name, stub)
            (folder / name).chmod(0o755)
        put(work / "scripts" / "repo_rules.py", stub)
        (work / "scripts" / "repo_rules.py").chmod(0o755)
        return work, folder, log

    def test_o34_rc_release_is_a_prerelease_and_not_latest(self) -> None:
        """O34, threat 4: release step `prerelease` true and `makeLatest` false for v3.0.0-rc.1; false and true
        for v3.0.0."""
        flow = Workflow()
        release = flow.index(flow.is_release)[0]
        for tag, expected in (("v3.0.0-rc.1", ("true", "false")), ("v3.0.0", ("false", "true"))):
            with self.subTest(tag):
                work, _, _ = self.stubs()
                context, _ = flow.context(tag, work, self.env, release)
                with_ = flow.steps[release].get("with") or {}
                got = tuple(interpolate(with_.get(key), context).strip().lower() for key in ("prerelease", "makeLatest"))
                self.assertEqual(got, expected, with_)

    def test_o35_promoted_runs_for_a_final_tag_only(self) -> None:
        """O35 as superseded, threat 2: release.yml runs `scripts/repo_rules.py promoted --tag <tag>` for v3.0.0,
        before the `release-notes` step, after a checkout of all tags; not for v3.0.0-rc.1."""
        flow = Workflow()
        promoted = flow.index(flow.is_promoted)
        self.assertTrue(promoted, "no release.yml step runs scripts/repo_rules.py promoted")
        notes = flow.index(is_notes)
        self.assertTrue(notes and max(promoted) < min(notes), (promoted, notes))
        checkouts = [n for n, s in enumerate(flow.steps[:min(promoted)])
                     if "actions/checkout" in str(s.get("uses", ""))
                     and (str((s.get("with") or {}).get("fetch-depth")) == "0"
                          or str((s.get("with") or {}).get("fetch-tags")).lower() == "true")]
        fetches = [n for n, s in enumerate(flow.steps[:min(promoted)])
                   if re.search(r"git fetch\b.*--tags", str(s.get("run", "")))]
        self.assertTrue(checkouts or fetches, "no full tag fetch before the promoted step")
        for tag, runs in (("v3.0.0", True), ("v3.0.0-rc.1", False)):
            with self.subTest(tag):
                work, folder, log = self.stubs()
                calls = []
                for n in promoted:
                    context, env = flow.context(tag, work, self.env, n)
                    step = flow.steps[n]
                    if not condition(step, context):
                        continue
                    env["PATH"] = f"{folder}{os.pathsep}{env.get('PATH', '')}"
                    for key, value in (step.get("env") or {}).items():
                        env[key] = interpolate(value, context)
                    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                                    interpolate(step["run"], context)], cwd=work, env=env, capture_output=True,
                                   text=True, timeout=60)
                    calls = log.read_text().splitlines() if log.exists() else []
                hit = [c for c in calls if re.search(rf"repo_rules\.py promoted --tag {re.escape(tag)}(\s|$)", c)]
                self.assertEqual(bool(hit), runs, calls)


# ---------------------------------------------------------------------------------------------------------------
# Promoted check (O36 to O41)
# ---------------------------------------------------------------------------------------------------------------

KIT_SOURCE = {"skill/SKILL.md": "# skill one\n", "skills/pause/SKILL.md": "# pause one\n",
              "claude/agents/spec-tester.md": "agent one\n", "codex/agents/spec-tester.toml": "agent = 1\n",
              "bin/install.mjs": "// one\n", "lib/kit.mjs": "export const one = 1\n"}


class PromotedCase(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.new_repo("dev-loop-copy")
        self.commit(self.repo, {**KIT_SOURCE, "package.json": '{"version": "2.1.1"}\n', "README.md": "# kit\n",
                                "CHANGELOG.md": "# Changelog\n", ".agents/skills/dev-loop/SKILL.md": "# copy 1\n"},
                    "base")
        self.tag(self.repo, "v2.1.1")

    def promoted(self, tag: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(REPO_RULES), "promoted", "--tag", tag, "--repo", str(self.repo)],
                                cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"unparsed_stdout": result.stdout, "stderr": result.stderr}
        return result.returncode, payload if isinstance(payload, dict) else {"unparsed_stdout": result.stdout}

    def release_commit(self) -> None:
        """The final release PR: package.json, CHANGELOG.md, README.md and a kit copy, no kit source."""
        self.commit(self.repo, {"package.json": '{"version": "3.0.0"}\n', "CHANGELOG.md": "# Changelog\n\n## [3.0.0]\n",
                                "README.md": "# kit 3\n", ".agents/skills/dev-loop/SKILL.md": "# copy 3\n"},
                    "release 3.0.0")


class PromotedTests(PromotedCase):

    def test_f5_promote(self) -> None:
        """F5 (O36, O37): no kit source change after the last rc passes; a change to skill/SKILL.md fails."""
        self.commit(self.repo, {"lib/kit.mjs": "export const one = 2\n"}, "rc.1 work")
        self.tag(self.repo, "v3.0.0-rc.1")
        self.commit(self.repo, {"skill/SKILL.md": "# skill two\n"}, "rc.2 work")
        self.tag(self.repo, "v3.0.0-rc.2")
        self.release_commit()
        self.tag(self.repo, "v3.0.0")
        code, payload = self.promoted("v3.0.0")
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("ok"), payload.get("tag"), payload.get("candidate"), payload.get("violations")),
                         (True, "v3.0.0", "v3.0.0-rc.2", []), payload)
        self.commit(self.repo, {"skill/SKILL.md": "# skill three\n"}, "late kit change")
        self.git(self.repo, "tag", "-d", "v3.0.0")
        self.tag(self.repo, "v3.0.0")
        code, payload = self.promoted("v3.0.0")
        self.assertEqual(code, 1, payload)
        self.assertEqual((payload.get("ok"), payload.get("candidate"), payload.get("violations")),
                         (False, "v3.0.0-rc.2", ["skill/SKILL.md"]), payload)

    def test_o38_rc_numbers_compare_as_numbers(self) -> None:
        """O38: tags v3.0.0-rc.9 and v3.0.0-rc.10: the candidate is v3.0.0-rc.10."""
        self.tag(self.repo, "v3.0.0-rc.9")
        self.commit(self.repo, {"lib/kit.mjs": "export const ten = 10\n"}, "rc.10 work")
        self.tag(self.repo, "v3.0.0-rc.10")
        self.release_commit()
        self.tag(self.repo, "v3.0.0")
        code, payload = self.promoted("v3.0.0")
        self.assertEqual(payload.get("candidate"), "v3.0.0-rc.10", payload)
        self.assertEqual((code, payload.get("ok"), payload.get("violations")), (0, True, []), payload)

    def test_o39_no_release_candidate(self) -> None:
        """O39: no v3.0.0-rc.* tag: exit 1, ok false, candidate null, reason no-release-candidate."""
        self.tag(self.repo, "v3.0.1-rc.1")
        self.release_commit()
        self.tag(self.repo, "v3.0.0")
        code, payload = self.promoted("v3.0.0")
        self.assertEqual(code, 1, payload)
        self.assertIn("candidate", payload)
        self.assertEqual((payload.get("ok"), payload.get("candidate"), payload.get("reason")),
                         (False, None, "no-release-candidate"), payload)

    def test_o40_rc_tag_is_skipped(self) -> None:
        """O40: `promoted --tag v3.0.0-rc.3`: exit 0, ok true, skipped not-a-final-tag."""
        self.tag(self.repo, "v3.0.0-rc.3")
        code, payload = self.promoted("v3.0.0-rc.3")
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("ok"), payload.get("skipped")), (True, "not-a-final-tag"), payload)

    def test_o41_unknown_tag(self) -> None:
        """O41: `promoted --tag v9.9.9` with no such tag: exit 2, `error`."""
        code, payload = self.promoted("v9.9.9")
        self.assertEqual(code, 2, payload)
        self.assertTrue(payload.get("error"), payload)


# ---------------------------------------------------------------------------------------------------------------
# Text (O42 to O46)
# ---------------------------------------------------------------------------------------------------------------

class TextTests(unittest.TestCase):

    def test_o42_skill_session_rule(self) -> None:
        """O42, D8: SKILL.md names main-newer, pause and /continue for the start of Stages 3, 6 and 7, and keeps
        the Stage 3 start rule for worktree-newer (amended by one-session-effort)."""
        blocks = paragraphs(SKILL.read_text())
        newer = [b for b in blocks if "worktree-newer" in b and "Stage 3 start" in b]
        self.assertTrue(newer, "no Stage 3 start rule for worktree-newer")
        main = "\n".join(b for b in blocks if "main-newer" in b)
        for pattern in (r"\bpause\b", r"/continue", r"Stages? 3\b", r"\b6\b", r"\b7\b"):
            self.assertRegex(main, pattern)

    def test_o43_release_audit_skill_passes_exclude(self) -> None:
        """O43, D9: the release-audit skill names `exclude` from release-plan, out of the audit scope."""
        blocks = [b for b in paragraphs(AUDIT_SKILL.read_text()) if "exclude" in b]
        self.assertTrue([b for b in blocks if "scope" in b], blocks)

    def test_o44_agents_md_dogfood_and_stage_7_hint(self) -> None:
        """O44, D12, D13: the Dogfood line holds "latest tag, release or pre-release"; the hint `rc: now|later`."""
        text = AGENTS.read_text()
        dogfood = [line for line in text.splitlines() if line.startswith("Dogfood")]
        self.assertTrue(dogfood and "latest tag, release or pre-release" in dogfood[0], dogfood)
        self.assertRegex(text, r"rc: now\\?\|later")

    def test_o45_release_md_cycle_steps(self) -> None:
        """O45 as superseded, D10 of Written Release Notes: release.md runs the cycle through `/release`, with no
        rc upgrade PR step; the dogfood upgrade is `commands.release_prepare`; `release_check` runs
        `scripts/canary.sh` and `promoted --sha`; `release_verify` runs the `npx` upgrade."""
        text = RELEASE_MD.read_text()
        self.assertRegex(text, r"(?<![\w-])/release(?![\w-])")
        self.assertNotIn("npx github:tchlyah/dev-loop#vX.Y.Z-rc.N upgrade", text)
        commands = json.loads(DEV_LOOP_JSON.read_text()).get("commands") or {}
        self.assertEqual(commands.get("release_prepare"), "node bin/install.mjs upgrade", commands)
        self.assertEqual(commands.get("release_check"), "scripts/release-check.sh", commands)
        self.assertEqual(commands.get("release_verify"), "scripts/release-verify.sh", commands)
        check = RELEASE_CHECK_SH.read_text()
        self.assertIn("scripts/canary.sh", check)
        self.assertRegex(check, r"repo_rules\.py promoted\b[^\n]*--sha")
        self.assertRegex(RELEASE_VERIFY_SH.read_text(),
                         r"npx\b[^\n]*github:tchlyah/dev-loop#\S*RELEASE_TAG\S*[^\n]*\bupgrade\b")

    def test_o46_cli_references(self) -> None:
        """O46: status.md names direction, worktree-newer and main-newer; release-plan.md names exclude."""
        status = STATUS_MD.read_text()
        for word in ("direction", "worktree-newer", "main-newer"):
            self.assertIn(word, status)
        self.assertIn("exclude", PLAN_MD.read_text())


if __name__ == "__main__":
    unittest.main()
