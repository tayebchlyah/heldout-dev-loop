#!/usr/bin/env python3
"""Held-out oracle tests for the seal drop gap (spec: Seal Drop Gap).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle`
lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py seal`, `seal --rebase-continue`, `seal-check` and `gate --summary` on scratch git repos
(rebases, resets, switches and cherry-picks are plain git commands); the record file
`<git-dir>/dev-loop/seals.json`; the `seal.sh` Stop hook of both harnesses fed hook JSON on stdin;
`reference/cli/seal.md` and `reference/cli/seal-check.md`. Every name in the temp repos is invented.
"""

from __future__ import annotations

import json
import os
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
BRANCH_REF = f"refs/heads/{BRANCH}"
DROPPED = "seal-dropped"
UNREADABLE = "seal-record-unreadable"
SKIPPED = "seal-record-skipped"

# The O3 file: S1 adds lines after line 3 (near the start), S2 adds a test after line 36 (the end).
# Main's O6 change is on line 16, 13 lines from S1 and 20 lines from S2.
DIAL = f"{HELD_OUT}/test_dial.py"
DIAL_TEXT = ("DIAL_TICKS = [\n" + "".join(f'    "tick-{n:02d}",\n' for n in range(1, 31)) + "]\n\n\n"
             "def test_ticks():\n    assert len(DIAL_TICKS) >= 30\n")
DIAL_S1 = DIAL_TEXT.replace('    "tick-02",\n', '    "tick-02",\n    "early-a",\n    "early-b",\n')
DIAL_S2_ADDED = '\n\ndef test_last_tick():\n    assert DIAL_TICKS[-1] == "tick-30"\n'
DIAL_MAIN = DIAL_TEXT.replace('"tick-15"', '"tick-15-wide"')

# O7: the seal adds lines after line 2; main changes line 2.
TALLY = f"{HELD_OUT}/test_tally.py"
TALLY_TEXT = "def test_tally():\n    assert TALLY == 3\n"
TALLY_SEALED = "def test_tally():\n    assert TALLY == 3\n    assert TALLY_MAX == 9\n"
TALLY_MAIN = "def test_tally():\n    assert TALLY == 4\n"
TALLY_RESOLVED = "def test_tally():\n    assert TALLY == 4\n    assert TALLY_MAX == 9\n"

# O1: two new test files staged at once (paths sorted in the record).
SPOKE = f"{HELD_OUT}/test_spoke.py"
AXLE = f"{HELD_OUT}/test_axle.py"
RIM = f"{HELD_OUT}/test_rim.py"

# Seal Move Gap O5: a non-seal commit changes line 2, a seal commit adds lines 8 lines away.
PANEL = f"{HELD_OUT}/test_panel.py"
PANEL_TEXT = ('def test_panel_width():\n    assert PANEL["width"] == 40\n\n\n'
              'def test_panel_height():\n    assert PANEL["height"] == 60\n\n\n'
              'def test_panel_depth():\n    assert PANEL["depth"] == 5\n')
PANEL_RELAXED = PANEL_TEXT.replace('PANEL["width"] == 40', 'PANEL["width"] >= 0')
PANEL_ADDED = '\n\ndef test_panel_colour():\n    assert PANEL["colour"] == "teal"\n'

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


class Project:
    """A dev-loop project on branch feat/widget, its base on main, a notes folder with the widget spec."""

    def __init__(self, test: unittest.TestCase, root: Path, name: str, files: dict[str, str]) -> None:
        self.test = test
        self.home = root / "home"
        self.home.mkdir(exist_ok=True)
        self.notes = root / "notes"
        put(self.notes / "Features" / "20261106 Widget" / "Spec.md",
            "---\nfeature: widget\ntrack: M\nreview_status: approved\n---\n\n# Widget\n")
        self.repo = root / name
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.repo, "config", key, value)
        put(self.repo / "dev-loop.json", json.dumps({"version": "2.1.1", "harnesses": ["claude", "codex"]}) + "\n")
        put(self.repo / "README.md", "# Acme widgets\n\nStatus: draft\n")
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, "#!/bin/sh\necho all unit tests pass\nexit 0\n")
        gate.chmod(0o755)
        for path, text in files.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = self.head()
        git(self.repo, "checkout", "-q", "-b", BRANCH)

    def env(self, cwd: Path) -> dict[str, str]:
        return {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                "CLAUDE_PROJECT_DIR": str(cwd)}

    def cli(self, *args: str, cwd: Path | None = None) -> tuple[int, dict]:
        cwd = cwd or self.repo
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd, env=self.env(cwd),
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    def stage(self, files: dict[str, str], cwd: Path | None = None) -> None:
        cwd = cwd or self.repo
        for path, text in files.items():
            put(cwd / path, text)
        git(cwd, "add", "--", *files)

    def seal(self, files: dict[str, str], message: str = "Add widget tests", cwd: Path | None = None) -> str:
        cwd = cwd or self.repo
        self.stage(files, cwd)
        code, out = self.cli("seal", "--feature", SLUG, "--message", message, cwd=cwd)
        self.test.assertEqual(code, 0, out)
        return self.head(cwd)

    def commit(self, files: dict[str, str], message: str) -> str:
        self.stage(files)
        git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def head(self, cwd: Path | None = None) -> str:
        return git(cwd or self.repo, "rev-parse", "HEAD")

    def git_dir(self, cwd: Path | None = None) -> Path:
        cwd = cwd or self.repo
        return (cwd / git(cwd, "rev-parse", "--git-dir")).resolve()

    def record_file(self, cwd: Path | None = None) -> Path:
        return self.git_dir(cwd) / "dev-loop" / "seals.json"

    def record(self, cwd: Path | None = None) -> list[dict]:
        path = self.record_file(cwd)
        self.test.assertTrue(path.is_file(), f"no seal record at {path}")
        data = json.loads(path.read_text())
        self.test.assertIsInstance(data, dict, data)
        self.test.assertIsInstance(data.get("seals"), list, data)
        return data["seals"]

    def entry(self, sha: str) -> dict:
        hits = [e for e in self.record() if same_sha(str(e.get("commit") or ""), sha)]
        self.test.assertEqual(len(hits), 1, f"one record entry for {sha} expected: {self.record()}")
        return hits[0]

    def trailer(self, name: str, rev: str = "HEAD", cwd: Path | None = None) -> str:
        lines = [line for line in git(cwd or self.repo, "log", "-1", "--format=%B", rev).splitlines()
                 if line.startswith(f"{name}:")]
        self.test.assertEqual(len(lines), 1, f"one {name} trailer expected: {lines}")
        return lines[0].split(":", 1)[1].strip()

    def rebase_in_progress(self) -> bool:
        git_dir = self.git_dir()
        return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()

    def seal_check(self) -> tuple[int, dict]:
        return self.cli("seal-check")

    def assert_clean(self) -> None:
        code, check = self.seal_check()
        self.test.assertEqual((code, check.get("ok"), check.get("violations")), (0, True, []), check)

    def found(self, payload: dict, reason: str, sha: str | None = None) -> list[dict]:
        return [v for v in payload.get("violations", []) if v.get("reason") == reason
                and (sha is None or same_sha(str(v.get("commit") or ""), sha))]

    def assert_no_drop(self) -> dict:
        _, check = self.seal_check()
        self.test.assertFalse(self.found(check, DROPPED), check)
        return check

    def two_seals(self) -> tuple[str, str]:
        """The O3 branch: S1 near the start of the dial file, S2 near its end."""
        s1 = self.seal({DIAL: DIAL_S1}, message="Add the early ticks")
        s2 = self.seal({DIAL: DIAL_S1 + DIAL_S2_ADDED}, message="Add the last tick test")
        return s1, s2

    def install_kit(self) -> None:
        """The kit layout a project holds: the skill under .agents, the hooks under .dev-loop/hooks."""
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)

    def stop_hook(self, harness: str) -> dict:
        shell = "bash" if harness == "claude" else "sh"
        script = self.repo / ".dev-loop" / "hooks" / harness / "seal.sh"
        result = subprocess.run([shell, str(script), "turn"], cwd=self.repo, env=self.env(self.repo),
                                input=json.dumps({**STOP, "cwd": str(self.repo)}),
                                capture_output=True, text=True, timeout=120)
        self.test.assertEqual(result.returncode, 0, result.stderr)
        try:
            out = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            raise AssertionError(f"seal.sh ({harness}): stdout is not JSON: {result.stdout[-400:]!r}")
        self.test.assertEqual(out.get("decision"), "block", out)
        return out


class DropGapTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def project(self, name: str, files: dict[str, str] | None = None) -> Project:
        return Project(self, self.root, name, {DIAL: DIAL_TEXT} if files is None else files)


class SealRecordTests(DropGapTest):
    def test_f1_seal_twice_records_both_seals_in_the_worktree_git_dir(self) -> None:
        """O1, O2."""
        p = self.project("acme-widgets")

        # O2: a seal in a linked worktree writes under that worktree's git dir only.
        tree = self.root / "acme-widgets-gadget"
        git(p.repo, "worktree", "add", "-q", "-b", "feat/gadget", str(tree), p.base)
        wt_sha = p.seal({RIM: "def test_rim():\n    assert RIM == 26\n"}, message="Add the rim test", cwd=tree)
        wt_record = p.record_file(tree)
        self.assertNotEqual(wt_record, p.record_file(), "a linked worktree has its own git dir")
        self.assertTrue(wt_record.is_file(), f"no seal record at {wt_record}")
        self.assertFalse((p.git_dir() / "dev-loop" / "seals.json").exists(),
                         "the main checkout's git dir got the worktree's record")
        wt_entries = p.record(tree)
        self.assertEqual(len(wt_entries), 1, wt_entries)
        self.assertEqual(wt_entries[0].get("branch"), "refs/heads/feat/gadget", wt_entries)
        self.assertTrue(same_sha(str(wt_entries[0].get("commit") or ""), wt_sha), wt_entries)

        # O1: one seal of two staged files, then a second seal.
        first = p.seal({SPOKE: "def test_spoke():\n    assert SPOKES == 32\n",
                        AXLE: "def test_axle():\n    assert AXLE == 'steel'\n"}, message="Add the spoke tests")
        entries = p.record()
        self.assertEqual(len(entries), 1, entries)
        entry = entries[0]
        self.assertEqual(entry.get("branch"), BRANCH_REF, entry)
        self.assertTrue(same_sha(str(entry.get("commit") or ""), first), entry)
        self.assertEqual(entry.get("patches"), [p.trailer("Dev-Loop-Seal-Patch")], entry)
        self.assertEqual(entry.get("seal"), p.trailer("Dev-Loop-Seal"), entry)
        self.assertEqual(entry.get("feature"), SLUG, entry)
        self.assertEqual(entry.get("paths"), sorted([SPOKE, AXLE]), entry)

        second = p.seal({RIM: "def test_rim():\n    assert RIM == 28\n"}, message="Add the rim test")
        entries = p.record()
        self.assertEqual(len(entries), 2, entries)
        self.assertTrue(same_sha(str(entries[0].get("commit") or ""), first), entries)
        self.assertTrue(same_sha(str(entries[1].get("commit") or ""), second), entries)
        self.assertEqual(entries[1].get("patches"), [p.trailer("Dev-Loop-Seal-Patch")], entries)
        self.assertEqual(entries[1].get("paths"), [RIM], entries)
        self.assertEqual(len(p.record(tree)), 1, "the main checkout's seal changed the worktree's record")


class DroppedSealTests(DropGapTest):
    def test_f2_dropped_seal_commits_fail_the_branch_and_cherry_pick_clears_it(self) -> None:
        """O3, O4, O5, O9, O14, O15 (seal-dropped), both harnesses."""
        p = self.project("acme-widgets")
        s1, s2 = p.two_seals()
        s1_patch = p.trailer("Dev-Loop-Seal-Patch", s1)
        s2_patch = p.trailer("Dev-Loop-Seal-Patch", s2)

        # O4: the last seal commit dropped by a reset.
        git(p.repo, "reset", "-q", "--hard", s1)
        code, check = p.seal_check()
        self.assertEqual(code, 1, check)
        self.assertTrue(p.found(check, DROPPED, s2), check)
        self.assertFalse(p.found(check, DROPPED, s1), check)

        # O5: every seal commit dropped.
        git(p.repo, "reset", "-q", "--hard", p.base)
        code, check = p.seal_check()
        self.assertEqual(code, 1, check)
        self.assertTrue(p.found(check, DROPPED, s1), check)
        self.assertTrue(p.found(check, DROPPED, s2), check)

        # O3: the first seal commit dropped, S2 replays with no conflict.
        git(p.repo, "reset", "-q", "--hard", s2)
        drop = git_try(p.repo, "rebase", "--onto", p.base, s1)
        self.assertEqual(drop.returncode, 0, drop.stdout + drop.stderr)
        self.assertEqual(p.trailer("Dev-Loop-Seal-Patch"), s2_patch)
        self.assertNotIn("early-a", (p.repo / DIAL).read_text())
        recorded = p.entry(s1)
        code, check = p.seal_check()
        self.assertEqual((code, check.get("ok")), (1, False), check)
        self.assertEqual(len(check.get("violations", [])), 1, check)
        violation = check["violations"][0]
        self.assertEqual(violation.get("reason"), DROPPED, check)
        self.assertTrue(same_sha(str(violation.get("commit") or ""), str(recorded.get("commit"))), check)
        self.assertTrue(same_sha(str(violation.get("commit") or ""), s1), check)
        self.assertIsNone(violation.get("path"), check)
        self.assertEqual(violation.get("paths"), recorded.get("paths"), check)
        self.assertEqual(violation.get("paths"), [DIAL], check)
        self.assertEqual(violation.get("patch"), s1_patch, check)

        # O14: a passing project gate on the O3 branch after the rebase.
        code, gate = p.cli("gate", "--summary")
        self.assertEqual(code, 1, gate)
        self.assertIs(gate["seal"]["ok"], False, gate)

        # O15: the Stop hook blocks and names the cherry-pick remedy.
        p.install_kit()
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                reason = p.stop_hook(harness).get("reason", "")
                self.assertIn(DROPPED, reason)
                self.assertIn("git cherry-pick", reason)

        # O9: the recorded S1 sha cherry-picked back clears it.
        pick = git_try(p.repo, "cherry-pick", str(recorded.get("commit")))
        self.assertEqual(pick.returncode, 0, pick.stdout + pick.stderr)
        p.assert_clean()


class NoFalseDropTests(DropGapTest):
    def test_f3_rebases_resets_merges_and_other_branches_give_no_false_drop(self) -> None:
        """O6, O7, O13, O17, O18, O19."""
        # O7: a conflict on line N resolved, then seal --rebase-continue updates the entry.
        p = self.project("acme-tally", {TALLY: TALLY_TEXT})
        sealed = p.seal({TALLY: TALLY_SEALED}, message="Add the tally max test")
        old_patch = p.trailer("Dev-Loop-Seal-Patch")
        git(p.repo, "checkout", "-q", "main")
        p.commit({TALLY: TALLY_MAIN}, "Tally four")
        git(p.repo, "checkout", "-q", BRANCH)
        rebase = git_try(p.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        p.stage({TALLY: TALLY_RESOLVED})
        code, out = p.cli("seal", "--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertFalse(p.rebase_in_progress())
        new_sha = p.head()
        new_patch = p.trailer("Dev-Loop-Seal-Patch")
        self.assertNotEqual(new_sha, sealed)
        entries = [e for e in p.record() if e.get("branch") == BRANCH_REF]
        self.assertEqual(len(entries), 1, p.record())
        patches = entries[0].get("patches") or []
        self.assertEqual(patches[-1:], [new_patch], entries)
        self.assertIn(old_patch, patches[:-1], entries)
        self.assertTrue(same_sha(str(entries[0].get("commit") or ""), new_sha), entries)
        p.assert_clean()

        # O17: the rebase undone.
        git(p.repo, "reset", "-q", "--hard", "ORIG_HEAD")
        self.assertEqual(p.trailer("Dev-Loop-Seal-Patch"), old_patch)
        p.assert_clean()

        # O6: a clean rebase onto a main that changed a line 10 or more lines from the sealed lines.
        p = self.project("acme-dial")
        p.two_seals()
        git(p.repo, "checkout", "-q", "main")
        p.commit({DIAL: DIAL_MAIN}, "Widen tick 15")
        git(p.repo, "checkout", "-q", BRANCH)
        rebase = git_try(p.repo, "rebase", "main")
        self.assertEqual(rebase.returncode, 0, rebase.stdout + rebase.stderr)
        self.assertIn("tick-15-wide", (p.repo / DIAL).read_text())
        p.assert_clean()

        # O13: main moved to the branch head.
        p = self.project("acme-merged")
        p.two_seals()
        git(p.repo, "branch", "-f", "main", "HEAD")
        p.assert_clean()

        # O18: another branch from the base in the same worktree.
        p = self.project("acme-switch")
        p.two_seals()
        git(p.repo, "switch", "-q", "-c", "other", p.base)
        p.assert_no_drop()

        # O19: a rebase stopped on a conflict in a non-seal commit placed before S2.
        p = self.project("acme-stopped")
        p.seal({DIAL: DIAL_S1}, message="Add the early ticks")
        p.commit({"README.md": "# Acme widgets\n\nStatus: beta\n"}, "Mark the widgets beta")
        p.seal({DIAL: DIAL_S1 + DIAL_S2_ADDED}, message="Add the last tick test")
        git(p.repo, "checkout", "-q", "main")
        p.commit({"README.md": "# Acme widgets\n\nStatus: stable\n"}, "Mark the widgets stable")
        git(p.repo, "checkout", "-q", BRANCH)
        rebase = git_try(p.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the README conflict")
        self.assertTrue(p.rebase_in_progress())
        self.assertNotIn("test_last_tick", (p.repo / DIAL).read_text())
        check = p.assert_no_drop()
        warnings = [w for w in check.get("warnings") or [] if isinstance(w, dict) and w.get("reason") == SKIPPED]
        self.assertEqual([w.get("why") for w in warnings], ["rebase"], check)


class RecordEdgeTests(DropGapTest):
    def test_f4_amend_missing_record_broken_record_and_failed_write(self) -> None:
        """O8, O10, O11, O12."""
        # O8: an amend that changes a sealed line, message kept: bad-seal only.
        p = self.project("acme-amend")
        p.seal({DIAL: DIAL_S1}, message="Add the early ticks")
        p.stage({DIAL: DIAL_S1.replace('"early-b"', '"early-z"')})
        git(p.repo, "commit", "-q", "--amend", "--no-edit")
        amended = p.head()
        code, check = p.seal_check()
        self.assertEqual(code, 1, check)
        self.assertTrue(p.found(check, "bad-seal", amended), check)
        self.assertFalse(p.found(check, DROPPED), check)

        # O10: the O3 branch after the rebase, with the record removed: no check.
        p = self.project("acme-removed")
        s1, _ = p.two_seals()
        drop = git_try(p.repo, "rebase", "--onto", p.base, s1)
        self.assertEqual(drop.returncode, 0, drop.stdout + drop.stderr)
        record = p.record_file()
        if record.exists():
            record.unlink()
        p.assert_no_drop()

        # O11: a record that is not JSON.
        p = self.project("acme-broken")
        p.seal({DIAL: DIAL_S1}, message="Add the early ticks")
        record = p.record_file()
        put(record, "not json")
        code, check = p.seal_check()
        self.assertEqual((code, check.get("ok")), (1, False), check)
        hits = p.found(check, UNREADABLE)
        self.assertEqual(len(hits), 1, check)
        self.assertEqual((p.repo / str(hits[0].get("path") or "")).resolve(), record.resolve(), check)

        # O12: the record path is a folder: seal commits, then fails.
        p = self.project("acme-folder")
        p.record_file().mkdir(parents=True)
        p.stage({DIAL: DIAL_S1})
        code, out = p.cli("seal", "--feature", SLUG, "--message", "Add the early ticks")
        self.assertEqual(code, 1, out)
        self.assertTrue(str(out.get("error") or "").startswith("seal record not written"), out)
        self.assertNotEqual(p.head(), p.base, "the seal commit should stay")
        self.assertTrue(same_sha(str(out.get("commit") or ""), p.head()), out)


class RemedyTextTests(DropGapTest):
    def test_f5_docs_and_changed_before_seal_remedy_name_the_sha_from_commits(self) -> None:
        """O15 (seal-record-unreadable, changed-before-seal), O16, both harnesses."""
        # O16: the command docs.
        check_doc = SEAL_CHECK_DOC.read_text()
        for text in (DROPPED, UNREADABLE, "<sha from commits>"):
            self.assertIn(text, check_doc)
        self.assertNotIn("rebase --onto <commit>^", check_doc)
        self.assertIn("seals.json", SEAL_DOC.read_text())

        # O15: the Stop hook on a record that is not JSON.
        p = self.project("acme-broken", {})
        p.seal({DIAL: DIAL_S1}, message="Add the early ticks")
        put(p.record_file(), "not json")
        p.install_kit()
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness, branch="unreadable"):
                self.assertIn(UNREADABLE, p.stop_hook(harness).get("reason", ""))

        # O15: the Stop hook on a changed-before-seal branch (Seal Move Gap O5).
        p = self.project("acme-panel", {PANEL: PANEL_TEXT})
        p.commit({PANEL: PANEL_RELAXED}, "Relax the panel width")
        p.seal({PANEL: PANEL_RELAXED + PANEL_ADDED}, message="Add the panel colour test")
        p.install_kit()
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness, branch="changed-before-seal"):
                reason = p.stop_hook(harness).get("reason", "")
                self.assertIn("changed-before-seal", reason)
                self.assertIn("<sha from commits>", reason)
                self.assertNotIn("<commit>^", reason)


if __name__ == "__main__":
    unittest.main()
