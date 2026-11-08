#!/usr/bin/env python3
"""Tests for devloop.py second-review: the CLI run against stub reviewers."""

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
from test_second_review import SecondReviewDecisionTests, git  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))

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


if __name__ == "__main__":
    unittest.main()
