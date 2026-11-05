"""The seal patch value (`git patch-id --verbatim`) and a failed `patch-id` in `seal`, `seal --rebase-continue`, `seal-check`."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from test_seal import SUITE
from test_seal_check import SealRepo

from seal import ACCEPTANCE_DIR, PATCH_TRAILER  # noqa: E402
from test_devloop import git, put, run_cli  # noqa: E402

HELLO = 'def test_a():\n    assert f("hello world")\n'
HELLO_AMENDED = 'def test_a():\n    assert f("helloworld")\n'
TEN = "".join(f"line {n}\n" for n in range(10))


class PatchIdTests(SealRepo):

    def setUp(self) -> None:
        super().setUp()
        tmp = Path(self._tmp.name)
        self.flag = tmp / "patch-id-fails"
        (tmp / "bin").mkdir()
        put(tmp / "bin" / "git", f'#!/bin/sh\nfor a in "$@"; do\n  if [ "$a" = patch-id ] && [ -e "{self.flag}" ]; then\n'
            f'    [ "$(cat "{self.flag}")" = silent ] && exit 0\n    echo boom >&2\n    exit 1\n  fi\ndone\n'
            f'exec "{shutil.which("git")}" "$@"\n')
        (tmp / "bin" / "git").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    def cli(self, *args: str) -> tuple[int, dict]:
        return run_cli(*args, cwd=self.repo, env=self.env)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def stage(self, text: str) -> None:
        put(self.repo / SUITE, text)
        git(self.repo, "add", "--", SUITE)

    def trailer(self) -> str:
        body = git(self.repo, "log", "-1", "--format=%B", "HEAD")
        return next(line.split(": ", 1)[1] for line in body.splitlines() if line.startswith(f"{PATCH_TRAILER}: "))

    def verbatim(self) -> str:
        diff = subprocess.run(["git", "-C", str(self.repo), "diff-tree", "-p", "-U1", "--no-renames", "--full-index",
                               "HEAD^", "HEAD", "--", f"{ACCEPTANCE_DIR}/"], capture_output=True, check=True).stdout
        out = subprocess.run(["git", "-C", str(self.repo), "patch-id", "--verbatim"], input=diff,
                             capture_output=True, check=True).stdout.decode()
        return hashlib.sha256(out.split(" ", 1)[0].encode()).hexdigest()

    def on_base(self, text: str) -> str:
        git(self.repo, "checkout", "-q", "main")
        sha = self.commit(SUITE, text)
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
        return sha

    def test_patch_trailer_is_the_verbatim_patch_id(self) -> None:
        self.seal(SUITE, HELLO)
        self.assertEqual(self.trailer(), self.verbatim())

    def test_patch_trailer_of_a_change_to_a_base_file_is_the_u1_patch_id(self) -> None:
        self.on_base(TEN)
        self.seal(SUITE, TEN.replace("line 5\n", "line 5\nadded\n"))
        self.assertEqual(self.trailer(), self.verbatim())

    def test_moved_line_is_bad_seal(self) -> None:
        base = "def test_a():\n    x = f()\n    assert x\n\n\ndef test_b():\n    y = g()\n    return y\n"
        self.on_base(base)
        self.seal(SUITE, base.replace("    x = f()\n", "    x = f()\n    assert x == 1\n"))
        put(self.repo / SUITE, base.replace("    return y\n", "    return y\n    assert x == 1\n"))
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        code, out = self.cli("seal-check")
        self.assertEqual(code, 1, out)
        self.assertIn({"reason": "bad-seal", "commit": self.head(), "path": None}, out["violations"])

    def test_clean_rebase_with_one_line_between_keeps_the_seal(self) -> None:
        start = self.head()
        for backend in ([], ["--apply"]):
            with self.subTest(backend=backend):
                git(self.repo, "checkout", "-q", "main")
                git(self.repo, "reset", "-q", "--hard", start)
                self.on_base(TEN)
                self.seal(SUITE, TEN.replace("line 5\n", "line 5\nadded\n"))
                git(self.repo, "checkout", "-q", "main")
                self.commit(SUITE, TEN.replace("line 4\n", "line 4 upstream\n"))
                git(self.repo, "checkout", "-q", "feat/x")
                git(self.repo, "rebase", "-q", *backend, "main")
                self.assertEqual(self.cli("seal-check"), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_conflict_resolved_with_rebase_continue_keeps_the_seal(self) -> None:
        self.on_base(TEN)
        self.seal(SUITE, TEN.replace("line 5\n", "line 5\nadded\n"))
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, TEN.replace("line 5\n", "line 5 upstream\n"))
        git(self.repo, "checkout", "-q", "feat/x")
        with self.assertRaises(Exception):
            git(self.repo, "rebase", "main")
        self.stage(TEN.replace("line 5\n", "line 5 upstream\nadded\n"))
        code, out = self.cli("seal", "--rebase-continue")
        self.assertEqual((code, out.get("rebase")), (0, "continued"), out)
        self.assertEqual(self.trailer(), self.verbatim())
        self.assertEqual(self.cli("seal-check")[0], 0)

    def test_failed_patch_id_makes_no_commit(self) -> None:
        for mode, error in [("fail", "git patch-id failed: boom"), ("silent", "git patch-id failed: no output")]:
            with self.subTest(mode):
                self.flag.write_text(mode)
                before = self.head()
                self.stage(HELLO)
                self.assertEqual(self.cli("seal", "--feature", "x", "--message", "m"),
                                 (1, {"error": error}))
                self.assertEqual(self.head(), before)

    def test_patch_id_failed_after_the_hooks_keeps_the_commit(self) -> None:
        hook = Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "hooks" / "post-commit"
        put(hook, f'#!/bin/sh\necho fail > "{self.flag}"\n')
        hook.chmod(0o755)
        before = self.head()
        self.stage(HELLO)
        code, out = self.cli("seal", "--feature", "x", "--message", "m")
        self.assertEqual((code, out), (1, {"error": "git patch-id failed: boom", "commit": self.head()}))
        self.assertNotEqual(self.head(), before)
        self.assertEqual(self.trailer(), self.verbatim())

    def test_rebase_continue_with_failed_patch_id_stops(self) -> None:
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "base\n")
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
        self.seal(SUITE, "feature\n")
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "upstream\n")
        git(self.repo, "checkout", "-q", "feat/x")
        with self.assertRaises(Exception):
            git(self.repo, "rebase", "main")
        state = Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "rebase-merge"
        message = (state / "message").read_text()
        self.stage("resolved\n")
        self.flag.write_text("fail")
        self.assertEqual(self.cli("seal", "--rebase-continue"), (1, {"error": "git patch-id failed: boom"}))
        self.assertTrue(state.is_dir())
        self.assertEqual((state / "message").read_text(), message)

    def test_whitespace_only_amend_is_bad_seal(self) -> None:
        indented = "def test_a():\n    assert f()\n\n\ndef test_b():\n    assert g()\n"
        cases = [("string literal", HELLO, HELLO_AMENDED),
                 ("indent into another function", indented, indented.replace("\ndef test_b():\n    assert",
                                                                             "\n    def test_b():\n        assert"))]
        for name, sealed, amended in cases:
            with self.subTest(name):
                self.seal(SUITE, sealed)
                put(self.repo / SUITE, amended)
                git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
                code, out = self.cli("seal-check")
                self.assertEqual(code, 1, out)
                self.assertIn({"reason": "bad-seal", "commit": self.head(), "path": None}, out["violations"])
                git(self.repo, "reset", "-q", "--hard", "main")

    def test_failed_patch_id_uses_the_blob_trailer(self) -> None:
        self.seal(SUITE, HELLO)
        self.flag.write_text("fail")
        self.assertEqual(self.cli("seal-check"), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))
        put(self.repo / SUITE, HELLO_AMENDED)
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        code, out = self.cli("seal-check")
        self.assertEqual(code, 1, out)
        self.assertIn({"reason": "bad-seal", "commit": self.head(), "path": None, "patch": "failed"}, out["violations"])
