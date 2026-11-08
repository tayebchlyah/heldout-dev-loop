#!/usr/bin/env python3
"""Tests for devloop.py second-review: the locks and the decision order up to the CLI run."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

SCRIPTS = ROOT / "skill" / "scripts"
DEVLOOP = SCRIPTS / "devloop.py"
sys.path.insert(0, str(SCRIPTS))
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SecondReviewDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, project: str = '{"second_review": true}', branch: str = "main", local: str | None = '{"second_review": {"codex": {}}}',
             commit: bool = True) -> Path:
        """A bare origin, a main checkout with the local file, a feature worktree. Returns the worktree."""
        origin, main = self.tmp / "origin.git", self.tmp / "main"
        git(self.tmp, "init", "-q", "--bare", "-b", branch, str(origin))
        git(self.tmp, "clone", "-q", str(origin), str(main))
        git(main, "checkout", "-q", "-b", branch)
        (main / "dev-loop.json").write_text(project)
        git(main, "add", "."), git(main, "commit", "-qm", "base"), git(main, "push", "-q", "origin", branch)
        if local is not None:
            (main / "dev-loop.local.json").write_text(local)
        tree = self.tmp / "feature"
        git(main, "worktree", "add", "-q", "-b", "feat/x", str(tree))
        if commit:
            (tree / "a.txt").write_text("a\n")
            git(tree, "add", "."), git(tree, "commit", "-qm", "change")
        return tree

    def result(self, tree: Path, harness: str = "claude") -> tuple[dict, int]:
        import second_review
        def review(*args: object) -> tuple[dict, int]:
            return {**args[-1], "state": "done"}, 0  # type: ignore[dict-item]

        with mock.patch.object(second_review, "_review", side_effect=review) as patched:
            out, code = second_review.run(tree, harness, 15, {})
        out["reviewed"] = patched.called
        return out, code

    def check(self, tree: Path, state: str, reason: str | None, code: int, harness: str = "claude") -> dict:
        out, got = self.result(tree, harness)
        self.assertEqual((out["state"], out["reason"], got), (state, reason, code), out)
        return out

    def test_base_locks_on_reaches_review(self) -> None:
        out = self.check(self.make(), "done", None, 0)
        self.assertTrue(out["reviewed"])

    def test_project_lock_table(self) -> None:
        for project, state, reason, code in [
            ("{}", "skipped", "project-off", 0), ('{"second_review": false}', "skipped", "project-off", 0),
            ('{"second_review": "yes"}', "failed", "bad-config", 1), ('{"second_review": 1}', "failed", "bad-config", 1),
            ('{"second_review": null}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": true, "second_review": true}', "failed", "bad-config", 1)]:
            with self.subTest(project=project):
                self.setUp()
                self.check(self.make(project), state, reason, code)

    def test_main_ref_only_and_no_replace(self) -> None:
        tree = self.make('{"second_review": false}')
        (tree / "dev-loop.json").write_text('{"second_review": true}')
        git(tree, "commit", "-qam", "on")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "tag", "origin/main")
        git(tree, "branch", "origin/main")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "replace", git(tree, "rev-parse", "refs/remotes/origin/main:dev-loop.json"), git(tree, "rev-parse", "HEAD:dev-loop.json"))
        self.check(tree, "skipped", "project-off", 0)

    def test_local_lock_table(self) -> None:
        for local, state, reason, code in [
            ('{"second_review": false}', "skipped", "user-off", 0),
            ('{"second_review": {"codex": false}}', "skipped", "user-off", 0),
            (None, "skipped", "not-chosen", 0), ('{"second_review": {"claude": {}}}', "skipped", "not-chosen", 0),
            ("{}", "skipped", "not-chosen", 0),
            ('{"second_review": {"codex": {"model": "--oss"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"model": "gpt\\n"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"effort": "max"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"temp": 1}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"model": null}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": null}}', "failed", "bad-config", 1),
            ('{"second_review": {"gemini": {}}}', "failed", "bad-config", 1),
            ('{"second_review": true}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": {"codex": {}}, "second_review": false}', "failed", "bad-config", 1)]:
            with self.subTest(local=local):
                self.setUp()
                self.check(self.make(local=local), state, reason, code)

    def test_claude_accepts_bracket_and_slash_models(self) -> None:
        for model in ("opus[1m]", "vendor/model"):
            with self.subTest(model=model):
                self.setUp()
                local = json.dumps({"second_review": {"claude": {"model": model, "effort": "max"}}})
                out = self.check(self.make(local=local), "done", None, 0, harness="codex")
                self.assertEqual(out["reviewer"], "claude")

    def test_repo_state(self) -> None:
        self.check(self.make(commit=False), "skipped", "nothing-to-review", 0)
        self.setUp()
        tree = self.make()
        (Path(git(tree, "rev-parse", "--path-format=absolute", "--git-dir")) / "rebase-merge").mkdir()
        self.check(tree, "skipped", "rebase", 0)
        self.setUp()
        self.check(self.make(branch="master"), "skipped", "no-main-ref", 0)
        outside = self.tmp / "plain"
        outside.mkdir()
        self.check(outside, "failed", "git", 1)

    def test_payload_fields_fill_as_rules_pass(self) -> None:
        tree = self.make(local='{"second_review": {"codex": {"model": "m1", "effort": "low"}}}')
        out, _ = self.result(tree)
        self.assertEqual(out["head"], git(tree, "rev-parse", "HEAD"))
        self.assertEqual(out["base"], git(tree, "rev-parse", "refs/remotes/origin/main"))
        self.assertEqual((out["reviewer"], out["model"], out["effort"], out["endpoint"]), ("codex", "m1", "low", "default"))

    def test_timeout_must_be_finite_above_zero(self) -> None:
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                done = subprocess.run([sys.executable, str(DEVLOOP), "second-review", "--harness", "claude", "--timeout", value],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 2)


class SecondReviewExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_left_out_table(self) -> None:
        import second_review
        for path, out in [
            ("tests/acceptance/x", True), ("Tests/Acceptance/x", True), ("AGENTS.md", True), ("a/AGENTS.override.md", True),
            ("CLAUDE.md", True), ("sub/Claude.md", True), ("CLAUDE.local.md", True), (".mcp.json", True),
            (".claude/x", True), (".Codex/x", True), ("a/.agents/x", True), (".env", True), ("config/.env.local", True),
            ("HEAD", True), ("a/b/Head", True), (".second-review.diff", True), (".Second-Review.DIFF/x", True),
            ("sub/.second-review.diff", False), ("headers.h", False),
            ("src/app.py", False), ("environment.py", False), ("tests/unit/x", False)]:
            with self.subTest(path=path):
                self.assertEqual(second_review.left_out(path), out)

    def branch(self) -> tuple[Path, str, str]:
        """A repo whose branch commit holds every D7 edge case. Returns repo, main sha, branch sha."""
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "src").mkdir()
        (repo / "tests" / "acceptance").mkdir(parents=True)
        (repo / "src" / "app.py").write_text("v1\n")
        (repo / "AGENTS.md").write_text("a1\n")
        (repo / "tests" / "acceptance" / "t.py").write_text("held1\n")
        git(repo, "add", "."), git(repo, "commit", "-qm", "base")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-qb", "feat/x")
        (repo / "src" / "app.py").write_text("v2\n")
        (repo / "AGENTS.md").write_text("a2\n")
        (repo / "tests" / "acceptance" / "t.py").write_text("held2\n")
        (repo / ".gitattributes").write_text("src/** -diff export-ignore\n")
        (repo / ".env").write_text("SECRET=1\n")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text("{}\n")
        (repo / "big.bin").write_bytes(b"\x00" * 1_048_577)
        (repo / "edge.bin").write_bytes(b"\x01\xff" * 524_288)
        (repo / "src" / "café.txt").write_bytes("café\r\n".encode())
        os.symlink("/etc/hosts", repo / "link")
        git(repo, "-c", "core.autocrlf=false", "add", ".")
        def blob(data: bytes) -> str:
            return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=data, capture_output=True,
                                  check=True).stdout.decode().strip()

        upper, lower = blob(b"upper\n"), blob(b"lower\n")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{upper},DUP.txt")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{lower},dup.txt")
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
        git(repo, "commit", "-qm", "change")
        return repo, base, git(repo, "rev-parse", "HEAD")

    def test_export_tree(self) -> None:
        import second_review
        repo, base, sha = self.branch()
        dest = self.tmp / "out"
        dest.mkdir()
        files, left = second_review.export(repo, base, sha, dest)
        self.assertEqual(sorted(files), [".gitattributes", "DUP.txt", "edge.bin", "src/app.py", "src/café.txt"])
        self.assertEqual(left, 8)
        self.assertEqual(sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()),
                         [".gitattributes", ".second-review.diff", "DUP.txt", "edge.bin", "src/app.py", "src/café.txt"])
        self.assertEqual((dest / "edge.bin").read_bytes(), b"\x01\xff" * 524_288)
        self.assertEqual((dest / "src" / "café.txt").read_bytes(), "café\r\n".encode())
        self.assertEqual((dest / "DUP.txt").read_bytes(), b"upper\n")
        self.assertEqual(second_review.worktrees(repo), [repo.resolve()])

    def test_export_diff_ignores_user_config_and_attributes(self) -> None:
        import second_review
        repo, base, _ = self.branch()
        secret = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=b"CASED=1\n", capture_output=True,
                                check=True).stdout.decode().strip()
        for name in (".ENV", "config/.Env.Local"):
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{secret},{name}")
        git(repo, "commit", "-qm", "cased env")
        sha = git(repo, "rev-parse", "HEAD")
        marker, script, config = self.tmp / "ran", self.tmp / "ext.sh", self.tmp / "gitconfig"
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)
        config.write_text(f"[diff]\n\texternal = {script}\n[color]\n\tui = always\n")
        dest = self.tmp / "out"
        dest.mkdir()
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            second_review.export(repo, base, sha, dest)
        diff = (dest / ".second-review.diff").read_bytes().decode(errors="replace")
        self.assertIn("+v2", diff)
        self.assertIn("+a2", diff)
        self.assertNotIn("tests/acceptance", diff)
        self.assertNotIn("held", diff)
        self.assertNotIn("SECRET", diff)
        self.assertNotIn("CASED", diff)
        self.assertNotIn("\x1b[", diff)
        self.assertFalse(marker.exists())

    def test_export_never_forms_a_repo_and_skips_bad_paths(self) -> None:
        import second_review
        repo = self.tmp / "repo"
        git(self.tmp, "init", "-q", "-b", "main", str(repo))
        git(repo, "commit", "-q", "--allow-empty", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        marker = self.tmp / "ran"
        def blob(data: bytes) -> str:
            return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=data, capture_output=True,
                                  check=True).stdout.decode().strip()

        rows = [(b"HEAD", b"ref: refs/heads/main\n"), (b"config", f"[core]\n\tfsmonitor = touch {marker}\n".encode()),
                (b"objects/info/x", b"x\n"), (b"refs/heads/x", b"x\n"), (b"bad\xff.txt", b"x\n"),
                (b".second-review.diff/x", b"x\n"), (b"keep.txt", b"k\n")]
        index = b"".join(b"100644 " + blob(data).encode() + b"\t" + path + b"\0" for path, data in rows)
        subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=repo, input=index, check=True)
        git(repo, "commit", "-qm", "c")
        sha = git(repo, "rev-parse", "HEAD")
        dest = self.tmp / "out"
        dest.mkdir()
        files, left = second_review.export(repo, base, sha, dest)
        self.assertEqual(sorted(files), ["config", "keep.txt", "objects/info/x", "refs/heads/x"])
        self.assertEqual(left, 3)
        self.assertTrue((dest / ".second-review.diff").is_file())
        subprocess.run(["git", "status"], cwd=dest, capture_output=True, env={**os.environ, "GIT_CEILING_DIRECTORIES": str(self.tmp)})
        self.assertFalse(marker.exists())

    def test_export_inside_work_tree_raises(self) -> None:
        import second_review
        repo, base, sha = self.branch()
        dest = repo / "out"
        dest.mkdir()
        with self.assertRaises(RuntimeError):
            second_review.export(repo, base, sha, dest)
        self.assertEqual(list(dest.iterdir()), [])

    def test_sweep(self) -> None:
        import second_review
        now = time.time()
        for name, age in [("devloop-second-review-old", 25), ("devloop-second-review-new", 1), ("other-old", 25)]:
            (self.tmp / name).mkdir()
            (self.tmp / name / "f").write_text("x")
            os.utime(self.tmp / name, (now - age * 3600, now - age * 3600))
        second_review.sweep(self.tmp, now)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["devloop-second-review-new", "other-old"])

    def test_lookup(self) -> None:
        import second_review
        tree = self.tmp / "wt"

        def cli(folder: Path, mode: int = 0o755) -> Path:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "codex").write_text("#!/bin/sh\n")
            (folder / "codex").chmod(mode)
            return folder / "codex"

        inside = cli(tree / "bin")
        (self.tmp / "into").symlink_to(tree / "bin")
        (tree / "out").mkdir()
        (tree / "out" / "codex").symlink_to(cli(self.tmp / "elsewhere"))
        cli(self.tmp / "rel" / "bin")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        (self.tmp / "relay").mkdir()
        (self.tmp / "relay" / "codex").symlink_to(inside)
        cli(self.tmp / "plain", 0o644)
        good = cli(self.tmp / "good")
        cli(self.tmp / "later")
        path = os.pathsep.join(["", "rel/bin", str(tree / "bin"), str(tree / "out"), str(self.tmp / "into"),
                                str(self.tmp / "relay"),
                                str(self.tmp / "plain"), str(self.tmp / "good"), str(self.tmp / "later")])
        self.assertEqual(second_review.lookup("codex", path, [tree]), good.resolve())
        self.assertIsNone(second_review.lookup("codex", os.pathsep.join(["", str(tree / "bin")]), [tree]))

    def test_review_no_cli_and_failed_export(self) -> None:
        import second_review
        tree = SecondReviewDecisionTests.make(self)  # type: ignore[arg-type]
        out, code = second_review.run(tree, "claude", 15, {"PATH": "/usr/bin:/bin"})
        self.assertEqual((out["state"], out["reason"], out["record"], code), ("skipped", "no-cli", None, 0))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "codex").write_text("#!/bin/sh\n")
        (bin_dir / "codex").chmod(0o755)
        inside = tree / "scratch"
        inside.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(inside)):
            out, code = second_review.run(tree, "claude", 15, {"PATH": str(bin_dir)})
        self.assertEqual((out["state"], out["reason"], out["record"], code), ("failed", "export", None, 1))
        self.assertEqual(list(inside.iterdir()), [])


TABLE = "| file | line | finding | class | verdict |\n| --- | --- | --- | --- | --- |\n| a.txt | 1 | x | correctness | PLAUSIBLE |\n"
HELD = ("assert held_out_value == 'first distinctive acceptance line'",
        "assert held_out_value == 'second distinctive acceptance line'")
STUB = """#!{python}
import json, os, signal, subprocess, sys, time, glob
argv = sys.argv[1:]
stdin = sys.stdin.read()
states = [json.load(open(p))["state"] for p in glob.glob({records!r} + "/*/record.json")]
files = sorted(os.path.relpath(os.path.join(d, f)) for d, _, fs in os.walk(".") for f in fs)
json.dump({{"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "stdin": stdin, "files": files,
           "states": states}}, open({log!r}, "w"))
def out(text):
    open(argv[argv.index("-o") + 1], "w").write(text)
{body}
"""


class SecondReviewRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "stub.json"

    def tree(self, local: str = '{"second_review": {"codex": {}}}', held: bool = False) -> Path:
        tree = SecondReviewDecisionTests.make(self, local=local)  # type: ignore[arg-type]
        if held:
            (tree / "tests" / "acceptance").mkdir(parents=True)
            (tree / "tests" / "acceptance" / "test_x.py").write_text("\n".join(HELD) + "\n")
            git(tree, "add", "."), git(tree, "commit", "-qm", "held")
        return tree

    def stub(self, name: str, body: str) -> None:
        records = self.tmp / "main" / ".git" / "dev-loop" / "second-review"
        (self.bin / name).write_text(STUB.format(python=sys.executable, records=str(records), log=str(self.log), body=body))
        (self.bin / name).chmod(0o755)

    def review(self, tree: Path, harness: str = "claude", timeout: float = 15) -> tuple[dict, int, dict]:
        import second_review
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.tmp), "LANG": "en_US.UTF-8",
               "PWD": str(tree), "OLDPWD": str(tree), "CLAUDE_PROJECT_DIR": str(tree), "ANTHROPIC_BASE_URL": "http://x"}
        out, code = second_review.run(tree, harness, timeout, env)
        seen = json.loads(self.log.read_text()) if self.log.exists() else {}
        return out, code, seen

    def test_stop_survives_eperm_from_killpg(self) -> None:
        import second_review
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.addCleanup(child.kill)
        real = os.killpg

        def eperm(pgid: int, sig: int) -> None:
            if sig:
                real(pgid, sig)
            raise PermissionError(1, "Operation not permitted")

        with mock.patch.object(os, "killpg", side_effect=eperm):
            second_review._stop(child)
        self.assertIsNotNone(child.returncode)

    def test_codex_run(self) -> None:
        self.stub("codex", "out(TABLE)\nsys.stderr.write('tokens used\\n1,000\\nwork\\ntokens used\\n12,345\\n')".replace(
            "TABLE", repr(TABLE)))
        tree = self.tree()
        out, code, seen = self.review(tree)
        self.assertEqual((out["state"], out["reason"], code), ("done", None, 0), out)
        self.assertEqual((out["output"], out["head"], out["endpoint"], out["effort"], out["model"]),
                         (TABLE, git(tree, "rev-parse", "HEAD"), "default", "high", None))
        self.assertEqual((out["files"], out["tokens"]), (2, 12345))
        self.assertGreater(out["seconds"], 0)
        argv, cwd = seen["argv"], Path(seen["cwd"])
        self.assertEqual(argv[:6], ["exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "-C"])
        self.assertEqual(Path(argv[6]).resolve(), cwd)
        for value in ['default_permissions="devloop-review"',
                      'permissions.devloop-review.filesystem={":minimal"="read", ":project_roots"="read", '
                      '":tmpdir"="none", ":slash_tmp"="none"}', 'model_reasoning_effort="high"']:
            self.assertEqual(argv[argv.index(value) - 1], "-c")
        self.assertIn("-o", argv)
        self.assertEqual(argv[-1], "-")
        self.assertNotIn("--sandbox", argv)
        self.assertNotIn("-m", argv)
        self.assertIn(".second-review.diff", seen["stdin"])
        self.assertIn(out["head"], seen["stdin"])
        self.assertEqual(seen["files"], [".second-review.diff", "a.txt", "dev-loop.json"])
        env = seen["env"]
        self.assertEqual(set(env) - {"HOME", "LANG", "PATH", "__CF_USER_TEXT_ENCODING"}, set(), env)
        self.assertTrue(env["PATH"].startswith(f"{self.bin.resolve()}{os.pathsep}"))
        self.assertTrue(env["PATH"].endswith("/usr/bin:/bin:/usr/sbin:/sbin"))
        self.assertFalse(str(cwd).startswith(str(self.tmp.resolve())))
        self.assertFalse(cwd.exists())
        record = Path(out["record"])
        common = Path(git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        self.assertEqual(record.parent, common / "dev-loop" / "second-review")
        self.assertTrue(record.name.endswith("-codex"))
        self.assertEqual(seen["states"], ["running"])
        self.assertEqual(json.loads((record / "record.json").read_text()), out)
        self.assertEqual((record / "output.md").read_text(), TABLE)
        self.assertIn("tokens used", (record / "stderr.log").read_text())
        import hashlib
        diff = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", "refs/remotes/origin/main...HEAD"], cwd=tree, capture_output=True).stdout
        self.assertEqual((record / "sent.txt").read_text().splitlines(), ["a.txt", "dev-loop.json", hashlib.sha256(diff).hexdigest()])
        self.setUp()
        self.stub("codex", "out('t')")
        out, _, seen = self.review(self.tree(local='{"second_review": {"codex": {"model": "m1", "effort": "low"}}}'))
        self.assertEqual(seen["argv"][seen["argv"].index("-m") + 1], "m1")
        self.assertIn('model_reasoning_effort="low"', seen["argv"])

    def test_claude_run(self) -> None:
        reply = json.dumps({"result": TABLE, "is_error": False, "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.stub("claude", f"print({reply!r})")
        out, code, seen = self.review(self.tree(local='{"second_review": {"claude": {"model": "opus[1m]", "effort": "max"}}}'),
                                      harness="codex")
        self.assertEqual((out["state"], out["reviewer"], out["output"], out["tokens"], code), ("done", "claude", TABLE, 35, 0), out)
        self.assertEqual(seen["argv"], ["-p", "--restricted", "--strict-mcp-config", "--tools=Read,Grep,Glob", "--output-format",
                                        "json", "--effort", "max", "--model", "opus[1m]"])
        self.assertIn(".second-review.diff", seen["stdin"])

    def test_failures(self) -> None:
        for name, body, harness, reason in [
            ("codex", "sys.stderr.write('start\\nboom\\n'); sys.exit(3)", "claude", "exit 3"),
            ("codex", "out('')", "claude", "no-output"),
            ("codex", "pass", "claude", "no-output"),
            ("claude", "print(json.dumps({'result': 'x', 'is_error': True}))", "codex", "no-output"),
            ("claude", "print('not json')", "codex", "no-output")]:
            with self.subTest(body=body):
                self.setUp()
                self.stub(name, body)
                local = json.dumps({"second_review": {name: {}}})
                out, code, seen = self.review(self.tree(local=local), harness=harness)
                self.assertEqual((out["state"], out["reason"], out["output"], code), ("failed", reason, None, 1), out)
                self.assertFalse(Path(seen["cwd"]).exists())
                if reason == "exit 3":
                    self.assertEqual(out["message"], "boom")

    def test_exec_failure(self) -> None:
        for shebang, reason in [("#!/nonexistent/interpreter\n", "exit 127"), ("not a script\n", "exit 126")]:
            with self.subTest(shebang=shebang):
                self.setUp()
                (self.bin / "codex").write_text(shebang)
                (self.bin / "codex").chmod(0o755)
                out, code, _ = self.review(self.tree())
                self.assertEqual((out["state"], out["reason"], out["record"], code), ("failed", reason, None, 1), out)
                self.assertTrue(out["message"])
                self.assertEqual(list((self.tmp / "main" / ".git" / "dev-loop" / "second-review").iterdir()), [])

    def test_record_failure_is_export(self) -> None:
        import second_review
        self.stub("codex", "out('t')")
        with mock.patch.object(second_review, "record_dir", side_effect=PermissionError("denied")):
            out, code, seen = self.review(self.tree())
        self.assertEqual((out["state"], out["reason"], out["record"], code), ("failed", "export", None, 1), out)
        self.assertEqual(seen, {})

    CHILD_SECONDS = 120

    def test_every_run_kills_the_group(self) -> None:
        sleeper = f"time.sleep({self.CHILD_SECONDS})"
        for tail, timeout, state, reason, code in [(sleeper, 15, "failed", "timeout", 1), ("out('t')", 15, "done", None, 0)]:
            with self.subTest(state=state):
                self.setUp()
                self.group_dies(tail, timeout, state, reason, code)

    def fire_timeout_after_pids(self, pids: Path):
        real = subprocess.Popen.communicate

        def communicate(proc, input=None, timeout=None):
            if Path(str(proc.args[0])).name != "codex":
                return real(proc, input, timeout)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    return real(proc, input, 0.1)
                except subprocess.TimeoutExpired:
                    input = None
                    if len(pids.read_text().split() if pids.exists() else []) == 2:
                        raise
            self.fail("stub never wrote its pids")

        return mock.patch.object(subprocess.Popen, "communicate", communicate)

    def group_dies(self, tail: str, timeout: float, state: str, reason: str | None, code: int) -> None:
        pids = self.tmp / "pids"
        self.stub("codex", f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({self.CHILD_SECONDS})'])\n"
                           f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n{tail}")
        tree = self.tree()
        started = time.monotonic()
        # #289: the timeout fires once the stub child exists, so a slow start cannot beat it.
        with self.fire_timeout_after_pids(pids) if reason == "timeout" else contextlib.nullcontext():
            out, got, _ = self.review(tree, timeout=timeout)
        # #289: a run that waits for the child instead of killing the group never returns sooner.
        self.assertLess(time.monotonic() - started, self.CHILD_SECONDS)
        self.assertEqual((out["state"], out["reason"], got), (state, reason, code), out)
        for pid in map(int, pids.read_text().split()):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"pid {pid} still alive")

    def test_taint(self) -> None:
        from seal.check import check
        for body, state in [(f"out({HELD!r}[0] + '\\n' + {HELD!r}[1])", "tainted"),
                            (f"out('t'); sys.stderr.write('\\n'.join({HELD!r}))", "tainted"),
                            (f"sys.stderr.write('\\n'.join({HELD!r})); sys.exit(3)", "tainted"),
                            (f"out({HELD!r}[0])", "done"),
                            (f"out('tests/' + 'acceptance/test_x.py:3:x\\n' + {HELD!r}[0])", "done")]:
            with self.subTest(body=body):
                self.setUp()
                self.stub("codex", body)
                tree = self.tree(held=True)
                out, code, _ = self.review(tree)
                self.assertEqual((out["state"], code), (state, 0), out)
                record = Path(out["record"])
                taint = Path(git(tree, "rev-parse", "--path-format=absolute", "--git-dir")) / "dev-loop" / "taint.json"
                if state == "done":
                    self.assertFalse(taint.exists())
                    continue
                self.assertEqual((out["output"], out["message"], out["reason"]), (None, None, None))
                self.assertEqual(sorted(p.name for p in record.iterdir()), ["record.json", "sent.txt"])
                [entry] = json.loads(taint.read_text())["records"]
                self.assertEqual((entry["agent_type"], entry["agent_id"], entry["commits"]), ("second-review", record.name, []))
                self.assertEqual(sorted(entry["lines"]), sorted(HELD))
                self.assertIn(entry, check(tree, False)["warnings"])

    def test_record_dir_and_tokens(self) -> None:
        import datetime
        import second_review
        tree = self.tree()
        now = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
        names = [second_review.record_dir(tree, "codex", now).name for _ in range(3)]
        self.assertEqual(names, ["20260102T030405Z-codex", "20260102T030405Z-codex-2", "20260102T030405Z-codex-3"])
        for reviewer, stdout, stderr, tokens in [
            ("claude", '{"usage": {"input_tokens": 7, "output_tokens": 3}}', "", 22),
            ("claude", '{"usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 100, '
                       '"cache_creation_input_tokens": 8}}', "", 42),
            ("claude", "not json", "", None), ("claude", '{"result": "x"}', "", None),
            ("codex", "", "tokens used\n1,234\nmore\ntokens used: 56,789\n", 56789), ("codex", "", "nothing", None)]:
            with self.subTest(stdout=stdout, stderr=stderr):
                self.assertEqual(second_review.tokens(reviewer, stdout, stderr), tokens)

    def test_child_env_keeps_proxy(self) -> None:
        import second_review
        keep = {k: "v" for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "no_proxy", "SSL_CERT_FILE",
                                 "NODE_EXTRA_CA_CERTS", "CODEX_CA_CERTIFICATE")}
        env = second_review.child_env({**keep, "GH_TOKEN": "s", "AWS_SECRET_ACCESS_KEY": "s"}, Path("/b/c"), None)
        self.assertEqual(set(env), {*keep, "PATH"})


class SecondReviewSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, harness: str = "claude", **make: object) -> tuple[list[str], list[tuple[str, list[str]]], Path]:
        import check_setup
        self.setUp()  # a fresh tree per call
        tree = SecondReviewDecisionTests.make(self, **make)  # type: ignore[arg-type]
        failures, groups = check_setup.check_second_review(tree, harness)
        return failures, groups, tree

    def test_project_true_without_local_key_asks_for_the_other_family(self) -> None:
        for harness, reviewer in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(harness=harness):
                failures, groups, _ = self.check(harness, local=None)
                self.assertEqual(failures, [])
                self.assertEqual(len(groups), 1)
                heading, items = groups[0]
                self.assertEqual(items, [])
                self.assertIn(f"Choose the {reviewer} second reviewer", heading)
                self.assertIn(str(self.tmp / "main" / "dev-loop.local.json"), heading)
                self.assertIn('"second_review": false', heading)
                self.assertIn(f'"second_review": {{"{reviewer}": {{"model": "<model>"}}}}', heading)

    def test_local_without_the_reviewer_entry_asks(self) -> None:
        _, groups, _ = self.check(local='{"second_review": {"claude": {}}}')
        self.assertEqual(len(groups), 1)
        self.assertIn("codex", groups[0][0])

    def test_no_line_when_the_user_chose(self) -> None:
        for local in ('{"second_review": false}', '{"second_review": {"codex": false}}', '{"second_review": {"codex": {}}}',
                      '{"second_review": {"codex": {"model": "gpt-5", "effort": "low"}}}'):
            with self.subTest(local=local):
                self.assertEqual(self.check(local=local)[:2], ([], []))

    def test_no_line_when_the_project_does_not_allow_it(self) -> None:
        for project in ("{}", '{"second_review": false}'):
            for local in (None, '{"second_review": true}', "not json"):
                with self.subTest(project=project, local=local):
                    self.assertEqual(self.check(project=project, local=local)[:2], ([], []))

    def test_no_main_ref_gives_no_line(self) -> None:
        import check_setup
        repo = self.tmp / "solo"
        git(self.tmp, "init", "-q", "-b", "other", str(repo))
        (repo / "dev-loop.json").write_text('{"second_review": true}')
        self.assertEqual(check_setup.check_second_review(repo, "claude"), ([], []))

    def test_working_tree_true_is_ignored_while_main_has_no_key(self) -> None:
        import check_setup
        tree = SecondReviewDecisionTests.make(self, project="{}", local=None)  # type: ignore[arg-type]
        (tree / "dev-loop.json").write_text('{"second_review": true}')
        self.assertEqual(check_setup.check_second_review(tree, "claude"), ([], []))

    def test_bad_project_value_fails(self) -> None:
        failures, groups, _ = self.check(project='{"second_review": "on"}')
        self.assertEqual(len(failures), 1)
        self.assertIn("second_review", failures[0])
        self.assertEqual(groups, [])

    def test_bad_local_shapes_fail(self) -> None:
        for local in ("not json", "[]", '{"second_review": "yes"}', '{"second_review": {"gemini": {}}}',
                      '{"second_review": {"codex": {"model": "-x"}}}', '{"second_review": {"codex": {"effort": "max"}}}',
                      '{"second_review": {"codex": {}, "codex": {}}}', '{"second_review": null}',
                      '{"second_review": {"codex": {"model": null}}}'):
            with self.subTest(local=local):
                failures, groups, _ = self.check(local=local)
                self.assertEqual(len(failures), 1, failures)
                self.assertIn("second_review", failures[0])
                self.assertNotIn("D4", failures[0])
                self.assertEqual(groups, [])

    def test_command_and_setup_read_the_local_file_alike(self) -> None:
        import check_setup
        for case, state, reason in [("folder", "skipped", "not-chosen"), ("tracked", "failed", "bad-config")]:
            with self.subTest(case=case):
                self.setUp()
                tree = SecondReviewDecisionTests.make(self, local=None if case == "folder" else '{"second_review": {"codex": {}}}')  # type: ignore[arg-type]
                main = self.tmp / "main"
                if case == "folder":
                    (main / "dev-loop.local.json").mkdir()
                else:
                    git(main, "add", "-f", "dev-loop.local.json"), git(main, "commit", "-qm", "track")
                out, _ = SecondReviewDecisionTests.result(self, tree)  # type: ignore[arg-type]
                self.assertEqual((out["state"], out["reason"]), (state, reason), out)
                failures, groups = check_setup.check_second_review(tree, "claude")
                if case == "folder":
                    self.assertEqual((failures, len(groups)), ([], 1))
                else:
                    self.assertEqual((len(failures), groups), (1, []), failures)
                    self.assertIn("second_review", failures[0])
                    self.assertIn("tracked", failures[0])


def section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


class SecondReviewTextTests(unittest.TestCase):
    """The kit text that wires the command into Shaping and the Stage 6 review wave, AGENTS.md, the canary."""

    def test_skill_runs_the_second_review_and_asks_only_in_shaping(self) -> None:
        skill = (SCRIPTS.parent / "SKILL.md").read_text()
        stage6 = section(skill, "6 Feature gate")
        for value in ("devloop.py second-review", "logged --name second-review", "Monitor", "second-review:<reviewer>",
                      "second review:", "`skipped: <reason>`", "`failed: <reason>`", "`tainted`", "Only `fail` fails"):
            with self.subTest(value=value):
                self.assertIn(value, stage6)
        for value in ("second_review", "dev-loop.local.json", "Delivery never asks", "no default answer"):
            with self.subTest(value=value):
                self.assertIn(value, skill)

    def test_routes_name_their_harness_and_the_sandbox_gap(self) -> None:
        refs = SCRIPTS.parent / "references"
        for route, needles in (("claude.md", [("second-review", "--harness claude"), ("sandbox", "Codex")]),
                               ("codex.md", [("second-review", "--harness codex"), ("dev-workspace", "Claude reviewer")])):
            lines = section((refs / route).read_text(), "Feature gate").splitlines()
            for a, b in needles:
                with self.subTest(route=route, needles=(a, b)):
                    self.assertTrue(any(a in line and b in line for line in lines))

    def test_init_installs_the_prompt(self) -> None:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, True)
        git(repo, "init", "-q", "-b", "main")
        (repo / "notes").mkdir()
        subprocess.run(["node", str(ROOT / "bin" / "install.mjs"), "init", "--yes", "--harness=claude",
                        f"--notes={repo / 'notes'}"], cwd=repo, check=True, capture_output=True)
        path = ".agents/skills/dev-loop/references/second-review.md"
        self.assertTrue((repo / path).is_file())
        self.assertIn(path, json.loads((repo / ".dev-loop" / "MANIFEST.json").read_text())["files"])

    def test_agents_md_and_the_canary_name_the_second_review(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text()
        for value in ("`second-review --harness claude|codex [--timeout <minutes>]`", "skill/scripts/second_review.py",
                      "skill/references/second-review.md"):
            with self.subTest(value=value):
                self.assertIn(value, agents)
        self.assertIn("test_second_review.py", (ROOT / "tests" / "unit" / "python" / "README.md").read_text())
        contract = (ROOT / "reference" / "cli" / "second-review.md").read_text()
        for value in ("`second-review --harness claude|codex [--timeout <minutes>]`", "`not-chosen`", "`tainted`",
                      "`second_review`"):
            with self.subTest(value=value):
                self.assertIn(value, contract)
        help_text = subprocess.run(["bash", str(ROOT / "scripts" / "canary.sh"), "--help"],
                                   capture_output=True, text=True).stdout
        self.assertIn("second-review", help_text)


if __name__ == "__main__":
    unittest.main()
