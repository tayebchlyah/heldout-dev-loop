#!/usr/bin/env python3
"""Held-out oracle tests for the verbatim seal patch value (spec: Seal Verbatim Value).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle`
lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py seal`, `seal --rebase-continue` and `seal-check` on a scratch git repo, a `git` wrapper
first on `PATH` that fails `patch-id`, and `reference/cli/seal.md`. Every name in the temp repos is invented.
"""

from __future__ import annotations

import hashlib
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
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
SEAL_DOC = REPO_ROOT / "reference" / "cli" / "seal.md"

HELD_OUT = "tests/acceptance"
SLUG = "widget"
BRANCH = f"feat/{SLUG}"
DIFF_FLAGS = ("-p", "-U0", "--no-renames", "--full-index")

GREETING = f"{HELD_OUT}/test_greeting.py"
GREETING_TEXT = ('def make_greeting():\n    return "hello world"\n\n\n'
                 'def test_greeting():\n    assert make_greeting() == "hello world"\n')
JOINED_TEXT = GREETING_TEXT.replace('== "hello world"', '== "helloworld"')
NESTED_TEXT = ('def make_greeting():\n    return "hello world"\n\n\n'
               '    def test_greeting():\n        assert make_greeting() == "hello world"\n')

LEDGER = f"{HELD_OUT}/test_ledger.py"
LEDGER_TEXT = ("LEDGER_ROWS = [\n" + "".join(f'    "row-{n:02d}",\n' for n in range(1, 21)) + "]\n\n\n"
               "def test_rows():\n    assert len(LEDGER_ROWS) == 20\n")
LEDGER_ADDED = ('\n\ndef test_first_row():\n    assert LEDGER_ROWS[0] == "row-01"\n\n\n'
                'def test_last_row():\n    assert LEDGER_ROWS[-1] == "row-20"\n')

SHARED = f"{HELD_OUT}/test_shared.py"
SHARED_TEXT = "def test_shared():\n    assert VALUE == 1\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_try(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def same_sha(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


class VerbatimRepo(unittest.TestCase):
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
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                    "CLAUDE_PROJECT_DIR": str(self.repo)}
        # A git first on PATH that exits 1 when any argument is `patch-id`, the real git otherwise.
        real_git = shutil.which("git")
        wrapper = self.root / "broken-git" / "git"
        put(wrapper, "#!/bin/sh\nfor arg in \"$@\"; do\n  if [ \"$arg\" = patch-id ]; then\n"
                     "    echo 'fatal: patch-id is broken on this host' >&2\n    exit 1\n  fi\ndone\n"
                     f"exec '{real_git}' \"$@\"\n")
        wrapper.chmod(0o755)
        self.broken_env = {**self.env, "PATH": f"{wrapper.parent}{os.pathsep}{os.environ.get('PATH', '')}"}

    def cli(self, *args: str, env: dict | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=env or self.env,
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

    def seal(self, files: dict[str, str], message: str = "Add widget tests") -> dict:
        self.stage(files)
        code, out = self.cli("seal", "--feature", SLUG, "--message", message)
        self.assertEqual(code, 0, out)
        return out

    def commit(self, files: dict[str, str], message: str) -> str:
        self.stage(files)
        git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def amend(self, files: dict[str, str]) -> str:
        self.stage(files)
        git(self.repo, "commit", "-q", "--amend", "--no-edit")
        return self.head()

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def rebase_in_progress(self) -> bool:
        git_dir = (self.repo / git(self.repo, "rev-parse", "--git-dir")).resolve()
        return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()

    def verbatim_value(self, *diff_args: str) -> str:
        """sha256 of the first field of `git patch-id --verbatim` of the -U0 diff under tests/acceptance/."""
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

    def seal_check(self, env: dict | None = None) -> tuple[int, dict]:
        return self.cli("seal-check", env=env)

    def bad_seals(self, payload: dict, sha: str) -> list[dict]:
        return [v for v in payload.get("violations", [])
                if v.get("reason") == "bad-seal" and same_sha(v.get("commit") or "", sha)]


class WhitespaceAmendTests(VerbatimRepo):
    def test_f1_whitespace_only_amend_fails_the_seal(self) -> None:
        """O1, O2, O3, O9."""
        # O1: the patch trailer is the verbatim value of the staged diff.
        self.stage({GREETING: GREETING_TEXT})
        expected = self.verbatim_value("--cached")
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add greeting tests")
        self.assertEqual(code, 0, out)
        sealed = self.head()
        self.assertTrue(same_sha(out["commit"], sealed), out)
        self.assertEqual(self.patch_trailer(), expected)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)

        # O2: "hello world" becomes "helloworld", message kept.
        joined = self.amend({GREETING: JOINED_TEXT})
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (1, False), check)
        self.assertTrue(self.bad_seals(check, joined), check)

        # O3: the sealed test indented into the body of another function, message kept.
        git(self.repo, "reset", "-q", "--hard", sealed)
        nested = self.amend({GREETING: NESTED_TEXT})
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (1, False), check)
        self.assertTrue(self.bad_seals(check, nested), check)

        # O9
        doc = SEAL_DOC.read_text()
        self.assertIn("patch-id --verbatim", doc)
        self.assertNotIn("--stable", doc)


class RebaseKeepsSealTests(VerbatimRepo):
    BASE_FILES = {LEDGER: LEDGER_TEXT, SHARED: SHARED_TEXT}

    def test_f2_clean_and_resolved_rebase_keep_the_seal(self) -> None:
        """O4, O5."""
        # O4: two sealed test functions, main changes a line 20 lines above them, clean rebase.
        self.seal({LEDGER: LEDGER_TEXT + LEDGER_ADDED}, message="Add ledger row tests")
        git(self.repo, "checkout", "-q", "main")
        self.commit({LEDGER: LEDGER_TEXT.replace('"row-02"', '"row-02b"')}, "Rename the second ledger row")
        git(self.repo, "checkout", "-q", BRANCH)
        rebase = git_try(self.repo, "rebase", "main")
        self.assertEqual(rebase.returncode, 0, rebase.stdout + rebase.stderr)
        self.assertIn('"row-02b"', (self.repo / LEDGER).read_text())
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"], check["violations"]), (0, True, []), check)

        # O5: a conflict in a sealed file, resolved and staged, then seal --rebase-continue.
        self.seal({SHARED: "def test_shared():\n    assert VALUE == 2\n"}, message="Raise the shared value")
        git(self.repo, "checkout", "-q", "main")
        self.commit({SHARED: "def test_shared():\n    assert VALUE == 3\n"}, "Shared value three")
        git(self.repo, "checkout", "-q", BRANCH)
        rebase = git_try(self.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        self.stage({SHARED: "def test_shared():\n    assert VALUE == 3\n    assert WIDGET_VALUE == 2\n"})
        code, out = self.cli("seal", "--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebase"], "continued", out)
        self.assertFalse(self.rebase_in_progress())
        self.assertEqual(self.patch_trailer(), self.verbatim_value("HEAD^", "HEAD"))
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"], check["violations"]), (0, True, []), check)


class BrokenPatchIdTests(VerbatimRepo):
    BASE_FILES = {SHARED: SHARED_TEXT}

    def test_f3_failed_patch_id_stops_seal_and_leaves_the_blob_check(self) -> None:
        """O6, O7, O8."""
        # O6: seal stops, no commit.
        self.stage({GREETING: GREETING_TEXT})
        before = self.head()
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add greeting tests", env=self.broken_env)
        self.assertEqual(code, 1, out)
        self.assertTrue(str(out.get("error", "")).startswith("git patch-id failed"), out)
        self.assertEqual(self.head(), before)

        # O8: a seal commit whose blob trailer matches its diff passes on the blob trailer alone.
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add greeting tests")
        self.assertEqual(code, 0, out)
        sealed = self.head()
        code, check = self.seal_check(env=self.broken_env)
        self.assertEqual((code, check["ok"], check["violations"]), (0, True, []), check)

        # O8: after the O2 amend, bad-seal with patch failed.
        joined = self.amend({GREETING: JOINED_TEXT})
        code, check = self.seal_check(env=self.broken_env)
        self.assertEqual((code, check["ok"]), (1, False), check)
        bad = self.bad_seals(check, joined)
        self.assertTrue(bad, check)
        self.assertEqual(bad[0].get("patch"), "failed", check)

        # O7: rebase stopped on a seal commit, seal --rebase-continue stops and the rebase stays open.
        git(self.repo, "reset", "-q", "--hard", sealed)
        self.seal({SHARED: "def test_shared():\n    assert VALUE == 2\n"}, message="Raise the shared value")
        git(self.repo, "checkout", "-q", "main")
        self.commit({SHARED: "def test_shared():\n    assert VALUE == 3\n"}, "Shared value three")
        git(self.repo, "checkout", "-q", BRANCH)
        rebase = git_try(self.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        self.addCleanup(git_try, self.repo, "rebase", "--abort")
        self.stage({SHARED: "def test_shared():\n    assert VALUE == 3\n    assert WIDGET_VALUE == 2\n"})
        code, out = self.cli("seal", "--rebase-continue", env=self.broken_env)
        self.assertEqual(code, 1, out)
        self.assertTrue(str(out.get("error", "")).startswith("git patch-id failed"), out)
        self.assertTrue(self.rebase_in_progress(), "the rebase should still be in progress")


if __name__ == "__main__":
    unittest.main()
