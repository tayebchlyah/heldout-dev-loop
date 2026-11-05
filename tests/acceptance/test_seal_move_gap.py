#!/usr/bin/env python3
"""Held-out oracle tests for the seal move gap (spec: Seal Move Gap).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle`
lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py seal`, `seal --rebase-continue`, `seal-check` and `gate --summary` on a scratch git repo
(amends, cherry-picks and rebases are plain git commands); the `seal.sh` Stop hook of both harnesses
fed hook JSON on stdin; `reference/cli/seal.md` and `reference/cli/seal-check.md`.
Every name in the temp repos is invented.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"
SEAL_DOC = REPO_ROOT / "reference" / "cli" / "seal.md"
SEAL_CHECK_DOC = REPO_ROOT / "reference" / "cli" / "seal-check.md"

HELD_OUT = "tests/acceptance"
SLUG = "widget"
BRANCH = f"feat/{SLUG}"
DIFF_FLAGS = ("-p", "-U1", "--no-renames", "--full-index")
CHANGED_BEFORE = "changed-before-seal"

# F1: one assert line added inside test_needle, then moved after the `return` of make_gauge.
GAUGE = f"{HELD_OUT}/test_gauge.py"
GAUGE_HEAD = 'def make_gauge():\n    gauge = {"needle": 7, "dial": "brass"}\n    return gauge\n'
GAUGE_TAIL = '\n\ndef test_needle():\n    gauge = make_gauge()\n    assert gauge["needle"] == 7\n'
GAUGE_END = "    assert gauge is not None\n"
GAUGE_ADDED = '    assert gauge["dial"] == "brass"\n'
GAUGE_TEXT = GAUGE_HEAD + GAUGE_TAIL + GAUGE_END
GAUGE_SEALED = GAUGE_HEAD + GAUGE_TAIL + GAUGE_ADDED + GAUGE_END
GAUGE_MOVED = GAUGE_HEAD + GAUGE_ADDED + GAUGE_TAIL + GAUGE_END

# F2, O3: lines added after line 3; main changes line 2 (line 3 unchanged between).
ALPHA = f"{HELD_OUT}/test_alpha.py"
ALPHA_TEXT = ('def test_alpha():\n    assert ALPHA == 1\n    assert ALPHA_NAME == "alpha"\n\n\n'
              'def test_beta():\n    assert BETA == 2\n')
ALPHA_SEALED = ALPHA_TEXT.replace('"alpha"\n', '"alpha"\n    assert ALPHA_SIZE == 10\n    assert ALPHA_KIND == "letter"\n')
ALPHA_MAIN = ALPHA_TEXT.replace("ALPHA == 1\n", "ALPHA == 11\n")

# F2, O4: lines added after line 2; main changes line 2.
TALLY = f"{HELD_OUT}/test_tally.py"
TALLY_TEXT = "def test_tally():\n    assert TALLY == 3\n"
TALLY_SEALED = "def test_tally():\n    assert TALLY == 3\n    assert TALLY_MAX == 9\n"
TALLY_MAIN = "def test_tally():\n    assert TALLY == 4\n"
TALLY_RESOLVED = "def test_tally():\n    assert TALLY == 4\n    assert TALLY_MAX == 9\n"

# F3, F5: a non-seal change on line 2, sealed lines added after line 10 (8 lines away).
PANEL = f"{HELD_OUT}/test_panel.py"
PANEL_TEXT = ('def test_panel_width():\n    assert PANEL["width"] == 40\n\n\n'
              'def test_panel_height():\n    assert PANEL["height"] == 60\n\n\n'
              'def test_panel_depth():\n    assert PANEL["depth"] == 5\n')
PANEL_RELAXED = PANEL_TEXT.replace('PANEL["width"] == 40', 'PANEL["width"] >= 0')
PANEL_ADDED = '\n\ndef test_panel_colour():\n    assert PANEL["colour"] == "teal"\n'
PANEL_MORE = '\n\ndef test_panel_finish():\n    assert PANEL["finish"] == "matte"\n'

# F3, O7: a new file added by a non-seal commit.
HINGE = f"{HELD_OUT}/test_hinge.py"
HINGE_TEXT = "def test_hinge():\n    assert HINGE_ANGLE == 90\n"
HINGE_ADDED = "\n\ndef test_hinge_pin():\n    assert HINGE_PIN == 'steel'\n"

STOP = {"session_id": "session-1", "hook_event_name": "Stop", "stop_hook_active": False}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_try(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def same_sha(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def item_shas(violation: dict) -> list[str]:
    """The commit ids of a violation `commits` list: a sha string, or an object with it under `sha`, `commit` or `id`."""
    shas = []
    for item in violation.get("commits") or []:
        if isinstance(item, str):
            shas.append(item)
        elif isinstance(item, dict):
            shas.append(next(str(item[k]) for k in ("sha", "commit", "id") if k in item))
    return shas


def says_revert_does_not_clear(reason: str) -> bool:
    """One sentence of the reason names `git revert` and a negation."""
    for sentence in re.split(r"[.;\n]\s", reason):
        if "git revert" in sentence and re.search(r"\b(?:not|never|cannot)\b|n't\b", sentence):
            return True
    return False


class MoveGapRepo(unittest.TestCase):
    """A dev-loop project on branch feat/widget, its base on main, a notes folder with the widget spec."""

    BASE_FILES: dict[str, str] = {}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        put(self.notes / "Features" / "20261105 Widget" / "Spec.md",
            "---\nfeature: widget\ntrack: M\nreview_status: approved\n---\n\n# Widget\n")
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.repo, "config", key, value)
        put(self.repo / "dev-loop.json", json.dumps({"version": "2.1.1", "harnesses": ["claude", "codex"]}) + "\n")
        put(self.repo / "README.md", "# Acme widgets\n")
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, "#!/bin/sh\necho all unit tests pass\nexit 0\n")
        gate.chmod(0o755)
        for path, text in self.BASE_FILES.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = self.head()
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                    "CLAUDE_PROJECT_DIR": str(self.repo)}

    def cli(self, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    def stage(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            put(self.repo / path, text)
        git(self.repo, "add", "--", *files)

    def seal(self, files: dict[str, str], message: str = "Add widget tests") -> str:
        self.stage(files)
        code, out = self.cli("seal", "--feature", SLUG, "--message", message)
        self.assertEqual(code, 0, out)
        return self.head()

    def commit(self, files: dict[str, str], message: str) -> str:
        self.stage(files)
        git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def restart(self) -> None:
        """Back to the base commit, branch feat/widget kept."""
        git(self.repo, "reset", "-q", "--hard", self.base)

    def rebase_in_progress(self) -> bool:
        git_dir = (self.repo / git(self.repo, "rev-parse", "--git-dir")).resolve()
        return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()

    def u1_value(self, *diff_args: str) -> str:
        """sha256 of the first field of `git patch-id --verbatim` of the -U1 diff under tests/acceptance/."""
        diff = subprocess.run(["git", "diff", *diff_args, *DIFF_FLAGS, "--", f"{HELD_OUT}/"], cwd=self.repo,
                              check=True, capture_output=True, text=True).stdout
        self.assertTrue(diff, "empty diff under tests/acceptance")
        out = subprocess.run(["git", "patch-id", "--verbatim"], cwd=self.repo, input=diff, check=True,
                             capture_output=True, text=True).stdout
        return hashlib.sha256(out.split()[0].encode("utf-8")).hexdigest()

    def patch_trailer(self, rev: str = "HEAD") -> str:
        lines = [line for line in git(self.repo, "log", "-1", "--format=%B", rev).splitlines()
                 if line.startswith("Dev-Loop-Seal-Patch:")]
        self.assertEqual(len(lines), 1, f"one Dev-Loop-Seal-Patch trailer expected: {lines}")
        return lines[0].split(":", 1)[1].strip()

    def seal_check(self) -> tuple[int, dict]:
        return self.cli("seal-check")

    def assert_clean(self) -> None:
        code, check = self.seal_check()
        self.assertEqual((code, check.get("ok"), check.get("violations")), (0, True, []), check)

    def found(self, payload: dict, reason: str, sha: str | None = None, path: str | None = None) -> list[dict]:
        return [v for v in payload.get("violations", []) if v.get("reason") == reason
                and (sha is None or same_sha(v.get("commit") or "", sha))
                and (path is None or v.get("path") == path)]

    def assert_changed_before(self, sealed: str, path: str, sneak: str | None = None) -> dict:
        code, check = self.seal_check()
        self.assertEqual((code, check.get("ok")), (1, False), check)
        hits = self.found(check, CHANGED_BEFORE, sealed, path)
        self.assertTrue(hits, check)
        if sneak is not None:
            self.assertTrue(any(same_sha(s, sneak) for s in item_shas(hits[0])), check)
        return check

    def install_kit(self) -> None:
        """The kit layout a project holds: the skill under .agents, the hooks under .dev-loop/hooks."""
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)

    def stop_hook(self, harness: str) -> dict:
        shell = "bash" if harness == "claude" else "sh"
        script = self.repo / ".dev-loop" / "hooks" / harness / "seal.sh"
        result = subprocess.run([shell, str(script), "turn"], cwd=self.repo, env=self.env,
                                input=json.dumps({**STOP, "cwd": str(self.repo)}),
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            out = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            raise AssertionError(f"seal.sh ({harness}): stdout is not JSON: {result.stdout[-400:]!r}")
        self.assertEqual(out.get("decision"), "block", out)
        return out


class MovedLineTests(MoveGapRepo):
    BASE_FILES = {GAUGE: GAUGE_TEXT}

    def test_f1_amend_that_moves_a_sealed_line_fails_the_seal(self) -> None:
        """O1, O2 (O1 replaces Seal Verbatim Value O1: the -U1 diff)."""
        # O1: the patch trailer is the verbatim value of the staged -U1 diff.
        self.stage({GAUGE: GAUGE_SEALED})
        expected = self.u1_value("--cached")
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add the dial test")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.patch_trailer(), expected)
        self.assert_clean()

        # O2: the same line, same text and indent, moved after the `return` of make_gauge, message kept.
        self.stage({GAUGE: GAUGE_MOVED})
        git(self.repo, "commit", "-q", "--amend", "--no-edit")
        moved = self.head()
        code, check = self.seal_check()
        self.assertEqual((code, check.get("ok")), (1, False), check)
        self.assertTrue(self.found(check, "bad-seal", moved), check)


class RebaseKeepsSealTests(MoveGapRepo):
    BASE_FILES = {ALPHA: ALPHA_TEXT, TALLY: TALLY_TEXT}

    def test_f2_clean_rebase_both_backends_and_resolved_conflict_keep_the_seal(self) -> None:
        """O3, O4."""
        # O3: main changes the line 2 above the first sealed line, clean rebase with each backend.
        self.seal({ALPHA: ALPHA_SEALED}, message="Add alpha size tests")
        git(self.repo, "branch", "feat/widget-copy")
        git(self.repo, "checkout", "-q", "main")
        self.commit({ALPHA: ALPHA_MAIN}, "Raise alpha")
        for branch, backend in ((BRANCH, ()), ("feat/widget-copy", ("--apply",))):
            with self.subTest(backend=backend or "merge"):
                git(self.repo, "checkout", "-q", branch)
                rebase = git_try(self.repo, "rebase", *backend, "main")
                self.assertEqual(rebase.returncode, 0, rebase.stdout + rebase.stderr)
                self.assertIn("ALPHA == 11", (self.repo / ALPHA).read_text())
                self.assert_clean()

        # O4: main changes line N, the seal added lines after line N: a conflict, resolved, seal --rebase-continue.
        git(self.repo, "checkout", "-q", BRANCH)
        self.seal({TALLY: TALLY_SEALED}, message="Add the tally max test")
        git(self.repo, "checkout", "-q", "main")
        self.commit({TALLY: TALLY_MAIN}, "Tally four")
        git(self.repo, "checkout", "-q", BRANCH)
        rebase = git_try(self.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        self.stage({TALLY: TALLY_RESOLVED})
        code, out = self.cli("seal", "--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.rebase_in_progress())
        self.assertEqual(self.patch_trailer(), self.u1_value("HEAD^", "HEAD"))
        self.assert_clean()


class ChangedBeforeSealTests(MoveGapRepo):
    BASE_FILES = {PANEL: PANEL_TEXT}

    def test_f3_non_seal_commit_before_a_seal_commit_fails_the_branch(self) -> None:
        """O5, O6, O7, O8, O9, O10."""
        # O5: a non-seal commit changes line 2, then a seal commit adds lines 8 lines away.
        sneak = self.commit({PANEL: PANEL_RELAXED}, "Relax the panel width")
        sealed = self.seal({PANEL: PANEL_RELAXED + PANEL_ADDED}, message="Add the panel colour test")
        self.assert_changed_before(sealed, PANEL, sneak)

        # O10: a passing project gate on the O5 branch.
        code, gate = self.cli("gate", "--summary")
        self.assertEqual(code, 1, gate)
        self.assertIs(gate["seal"]["ok"], False, gate)

        # O6: the history rebuilt from the base, the seal commit cherry-picked after a non-seal commit.
        self.restart()
        original = self.seal({PANEL: PANEL_TEXT + PANEL_ADDED}, message="Add the panel colour test")
        self.restart()
        sneak = self.commit({PANEL: PANEL_RELAXED}, "Relax the panel width")
        pick = git_try(self.repo, "cherry-pick", original)
        self.assertEqual(pick.returncode, 0, pick.stdout + pick.stderr)
        picked = self.head()
        check = self.assert_changed_before(picked, PANEL)
        self.assertFalse(self.found(check, "bad-seal"), check)

        # O7: a non-seal commit adds a new test file, a seal commit changes it.
        self.restart()
        self.commit({HINGE: HINGE_TEXT}, "Add the hinge test")
        sealed = self.seal({HINGE: HINGE_TEXT + HINGE_ADDED}, message="Add the hinge pin test")
        self.assert_changed_before(sealed, HINGE)

        # O8: a non-seal commit and its revert, then a seal commit of the same file.
        self.restart()
        sneak = self.commit({PANEL: PANEL_RELAXED}, "Relax the panel width")
        git(self.repo, "revert", "--no-edit", sneak)
        self.seal({PANEL: PANEL_TEXT + PANEL_ADDED}, message="Add the panel colour test")
        self.assert_clean()

        # O9: a second seal commit adds lines to the file the first one sealed.
        self.restart()
        self.seal({PANEL: PANEL_TEXT + PANEL_ADDED}, message="Add the panel colour test")
        self.seal({PANEL: PANEL_TEXT + PANEL_ADDED + PANEL_MORE}, message="Add the panel finish test")
        self.assert_clean()


class SealDocsTests(MoveGapRepo):
    def test_f4_command_docs_name_the_new_diff_and_violation(self) -> None:
        """O11."""
        seal_doc = SEAL_DOC.read_text()
        self.assertIn("-U1", seal_doc)
        self.assertNotIn("-U0", seal_doc)
        self.assertIn(CHANGED_BEFORE, SEAL_CHECK_DOC.read_text())


class StopHookRemedyTests(MoveGapRepo):
    BASE_FILES = {PANEL: PANEL_TEXT}

    def test_f5_stop_hook_names_the_drop_remedy_drop_clears_revert_does_not(self) -> None:
        """O12, O13, O14, both harnesses."""
        self.install_kit()
        sneak = self.commit({PANEL: PANEL_RELAXED}, "Relax the panel width")
        sealed = self.seal({PANEL: PANEL_RELAXED + PANEL_ADDED}, message="Add the panel colour test")

        # O12: the block reason on the O5 branch.
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                reason = self.stop_hook(harness).get("reason", "")
                self.assertIn(CHANGED_BEFORE, reason)
                self.assertIn("git rebase --onto", reason)
                self.assertTrue(says_revert_does_not_clear(reason), reason)

        # O14: a revert of the non-seal commit leaves changed-before-seal.
        git(self.repo, "revert", "--no-edit", sneak)
        self.assert_changed_before(sealed, PANEL)

        # O13: the drop of the non-seal commit clears it.
        git(self.repo, "reset", "-q", "--hard", sealed)
        drop = git_try(self.repo, "rebase", "--onto", f"{sneak}^", sneak)
        self.assertEqual(drop.returncode, 0, drop.stdout + drop.stderr)
        self.assertNotIn('PANEL["width"] >= 0', (self.repo / PANEL).read_text())
        self.assert_clean()

        # O12: a branch with only an unsealed commit gets no drop remedy.
        self.restart()
        self.commit({HINGE: HINGE_TEXT}, "Add the hinge test")
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness, branch="unsealed"):
                self.assertNotIn("git rebase --onto", self.stop_hook(harness).get("reason", ""))


if __name__ == "__main__":
    unittest.main()
