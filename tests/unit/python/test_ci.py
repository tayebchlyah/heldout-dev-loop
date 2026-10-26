#!/usr/bin/env python3
"""Tests for ci.py: the verdict of one ci wait pass, the poll loop, and the GitHub settings check."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import ci  # noqa: E402

DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main"]

# A stub gh: records each argv, answers by kind (pr view, api graphql, compare, run view, api repos) from
# script.json; the n-th call of a kind gets the n-th answer, the last one repeats; no answer: exit 1.
STUB_GH = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
d = os.environ["STUB_DIR"]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    kind = "view"
elif args[:2] == ["api", "graphql"]:
    kind = "graphql"
elif args[:1] == ["api"] and "/compare/" in args[1]:
    kind = "compare"
elif args[:2] == ["run", "view"]:
    kind = "run"
elif args[:1] == ["api"]:
    kind = "repo"
else:
    sys.exit("unexpected gh call: %s" % args)
answers = json.load(open(os.path.join(d, "script.json"))).get(kind, [])
counter = os.path.join(d, kind + ".n")
n = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(n + 1))
if not answers:
    sys.exit(1)
a = answers[min(n, len(answers) - 1)]
time.sleep(a.get("sleep", 0))
sys.stdout.write(a.get("out", ""))
sys.stderr.write(a.get("err", ""))
sys.exit(a.get("code", 0))
"""


class StubGh(unittest.TestCase):
    """A temp folder with a git repo (one commit, `self.head`) and the stub gh first on PATH."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "one"]):
            subprocess.run([*GIT, *args], cwd=self.repo, check=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True,
                                   check=True).stdout.strip()
        self.stub = self.root / "stub"
        (self.stub / "bin").mkdir(parents=True)
        gh = self.stub / "bin" / "gh"
        gh.write_text(STUB_GH.format(python=sys.executable))
        gh.chmod(0o755)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def env(self, script: dict, **extra: str) -> dict[str, str]:
        for old in self.stub.glob("*.n"):
            old.unlink()
        (self.stub / "calls.jsonl").write_text("")
        (self.stub / "script.json").write_text(json.dumps(script))
        return {**os.environ, "PATH": f"{self.stub / 'bin'}{os.pathsep}{os.environ['PATH']}",
                "STUB_DIR": str(self.stub), "DEVLOOP_TEST_CI_POLL": "0", **extra}

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in (self.stub / "calls.jsonl").read_text().splitlines()]


def run(conclusion: str | None = "SUCCESS", status: str = "COMPLETED", name: str = "test", link: str = "") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion, "detailsUrl": link}


def context(state: str, name: str = "ci/x", link: str = "https://ci.example/1") -> dict:
    return {"__typename": "StatusContext", "context": name, "state": state, "targetUrl": link}


def view(**fields) -> dict:
    base = {"isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
            "latestReviews": [], "statusCheckRollup": [run(name="test"), run(name="acceptance")]}
    return {**base, **fields}


PENDING = run(None, "IN_PROGRESS", "test")
FAILED = run("FAILURE", name="acceptance", link="https://github.com/o/r/actions/runs/99/job/5")
THREAD = {"path": "skill/x.py", "line": 12, "author": "bot", "url": "u", "text": "Line one"}
CHANGES = [{"state": "CHANGES_REQUESTED"}]


class CiVerdictTests(unittest.TestCase):
    def test_check_state(self) -> None:
        rows = [(run(c), "pass") for c in ("SUCCESS", "NEUTRAL", "SKIPPED")]
        rows += [(run(c), "fail") for c in sorted(ci.FAILED_CHECK_STATES)]
        rows += [(PENDING, "pending"), (run("SUCCESS", "QUEUED"), "pending"),
                 (context("SUCCESS"), "pass"), (context("FAILURE"), "fail"), (context("ERROR"), "fail"),
                 (context("PENDING"), "pending"), (context("EXPECTED"), "pending"),
                 ({"name": "x", "state": "SUCCESS"}, "pending")]
        self.assertEqual(len(ci.FAILED_CHECK_STATES), 6)
        for item, want in rows:
            with self.subTest(item=item):
                self.assertEqual(ci.check_state(item), want)

    def test_verdict(self) -> None:
        rows = [
            ("conflict beats a pending check", view(mergeable="CONFLICTING", statusCheckRollup=[PENDING]), [],
             ("conflicts", None, None)),
            ("dirty", view(mergeable="UNKNOWN", mergeStateStatus="DIRTY"), [], ("conflicts", None, None)),
            ("conflict beats a thread", view(mergeable="CONFLICTING"), [THREAD], ("conflicts", None, None)),
            ("thread beats a failed check", view(statusCheckRollup=[FAILED]), [THREAD], ("threads", None, None)),
            ("change request with a pending check", view(latestReviews=CHANGES, statusCheckRollup=[PENDING]), [],
             ("blocked", "changes-requested", None)),
            ("thread beats a change request", view(latestReviews=CHANGES), [THREAD], ("threads", None, None)),
            ("change request beats a failed check", view(latestReviews=CHANGES, statusCheckRollup=[FAILED]), [],
             ("blocked", "changes-requested", None)),
            ("fail while another check runs", view(statusCheckRollup=[FAILED, PENDING]), [], ("fail", None, FAILED)),
            ("hidden refusal", view(mergeStateStatus="BLOCKED", statusCheckRollup=[run(), FAILED]), [],
             ("fail", None, FAILED)),
            ("status context fails", view(statusCheckRollup=[context("ERROR")]), [],
             ("fail", None, context("ERROR"))),
            ("draft, green", view(isDraft=True), [], ("blocked", "draft", None)),
            ("behind, green", view(mergeStateStatus="BEHIND"), [], ("blocked", "behind", None)),
            ("blocked, review required", view(mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED"), [],
             ("blocked", "review-required", None)),
            ("blocked, changes requested", view(mergeStateStatus="BLOCKED", reviewDecision="CHANGES_REQUESTED"), [],
             ("blocked", "changes-requested", None)),
            ("blocked, other rule", view(mergeStateStatus="BLOCKED"), [], ("blocked", "rule", None)),
            ("green", view(), [], ("pass", None, None)),
            ("approved and commented reviews", view(latestReviews=[{"state": "APPROVED"}, {"state": "COMMENTED"}]), [],
             ("pass", None, None)),
            ("neutral and skipped", view(statusCheckRollup=[run("NEUTRAL"), run("SKIPPED")]), [], ("pass", None, None)),
        ]
        for name, pr, threads, (state, reason, failed) in rows:
            with self.subTest(name):
                got = ci.verdict(pr, threads)
                assert got is not None
                self.assertEqual((got["state"], got["reason"], got["failed"]), (state, reason, failed))

    def test_verdict_polls_again(self) -> None:
        rows = [
            ("pending check", view(statusCheckRollup=[run(), PENDING])),
            ("no check registered yet", view(statusCheckRollup=[])),
            ("mergeable unknown", view(mergeable="UNKNOWN")),
            ("merge state unknown", view(mergeStateStatus="UNKNOWN")),
            ("draft with a pending check", view(isDraft=True, statusCheckRollup=[PENDING])),
            ("unknown rollup kind", view(statusCheckRollup=[{"name": "x"}])),
        ]
        for name, pr in rows:
            with self.subTest(name):
                self.assertIsNone(ci.verdict(pr, []))

    def test_open_threads(self) -> None:
        def node(resolved: bool = False, body: str = "Line one\nLine two", author: dict | None = None, line: int | None = 12,
                 **extra) -> dict:
            comment = {"author": author if author is not None else {"login": "bot"}, "body": body, "url": "u"}
            return {"isResolved": resolved, "path": "skill/x.py", "line": line, "comments": {"nodes": [comment]},
                    **extra}

        def answer(*nodes: dict) -> dict:
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(nodes)}}}}}

        ghost = node()
        ghost["comments"]["nodes"][0]["author"] = None
        long = "x" * 300
        rows = [
            ("resolved", answer(node(resolved=True)), []),
            ("open", answer(node()), [THREAD]),
            ("outdated still open", answer(node(isOutdated=True)), [THREAD]),
            ("first line, 200 characters", answer(node(body=long + "\nmore")), [{**THREAD, "text": "x" * 200}]),
            ("deleted author", answer(ghost), [{**THREAD, "author": None}]),
            ("file comment", answer(node(line=None)), [{**THREAD, "line": None}]),
        ]
        for name, data, want in rows:
            with self.subTest(name):
                self.assertEqual(ci.open_threads(data), want)


def out(value) -> dict:
    return {"out": value if isinstance(value, str) else json.dumps(value)}


def graphql(*nodes: dict) -> dict:
    return out({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(nodes)}}}}})


LOG = "Setup\tinstalling\nRun\tERROR: test_widget failed\n" + "".join(f"Run\tdetail {i}\n" for i in range(40))
THREAD_NODE = {"isResolved": False, "path": "skill/x.py", "line": 12,
               "comments": {"nodes": [{"author": {"login": "bot"}, "body": "Line one\nLine two",
                                       "url": "https://github.com/o/r/pull/7#r1"}]}}
FAIL_LINK = "https://github.com/o/r/actions/runs/99/job/5"
BUSY = {"err": "HTTP 502", "code": 1}


class CiWaitTests(StubGh):
    def pr(self, **fields) -> dict:
        return out(view(**{"number": 7, "url": "https://github.com/o/r/pull/7", "headRefOid": self.head,
                           "baseRefName": "main", **fields}))

    def wait(self, script: dict, *args: str, cwd: Path | None = None, **extra: str) -> tuple[int, dict]:
        script = {"graphql": [graphql()], "compare": [out("0")], **script}
        result = subprocess.run([sys.executable, str(DEVLOOP), "ci", "wait", "--timeout", "1", *args],
                                cwd=cwd or self.repo, env=self.env(script, **extra), capture_output=True, text=True,
                                timeout=60)
        return result.returncode, json.loads(result.stdout)

    def kinds(self) -> list[str]:
        return ["compare" if c[:1] == ["api"] and "/compare/" in c[1] else " ".join(c[:2]) for c in self.calls()]

    def test_verdicts(self) -> None:
        failed = run("FAILURE", name="acceptance", link=FAIL_LINK)
        rows = [
            ("green", {"view": [self.pr()]}, (),
             {"state": "pass", "reason": None, "head": self.head, "threads": [], "stale_base": None,
              "failed_job": None, "pending": [], "url": "https://github.com/o/r/pull/7"}, {"pr view": 1, "compare": 0}),
            ("conflict at once", {"view": [self.pr(mergeable="CONFLICTING", statusCheckRollup=[PENDING])]}, (),
             {"state": "conflicts", "stale_base": None}, {"pr view": 1}),
            ("open thread", {"view": [self.pr()], "graphql": [graphql(THREAD_NODE)]}, (),
             {"state": "threads", "threads": [{"path": "skill/x.py", "line": 12, "author": "bot",
                                               "url": "https://github.com/o/r/pull/7#r1", "text": "Line one"}]}, {}),
            ("failed check at once", {"view": [self.pr(statusCheckRollup=[failed, PENDING])], "run": [out(LOG)],
                                      "compare": [out("0")]}, (),
             {"state": "fail", "failed_job": "acceptance", "url": FAIL_LINK, "stale_base": False},
             {"pr view": 1, "run view": 1, "compare": 1}),
            ("status context", {"view": [self.pr(statusCheckRollup=[context("ERROR")])]}, (),
             {"state": "fail", "failed_job": "ci/x", "url": "https://ci.example/1", "first_error": None},
             {"run view": 0}),
            ("stale base", {"view": [self.pr(statusCheckRollup=[failed])], "compare": [out("2")]}, (),
             {"state": "fail", "stale_base": True}, {}),
            ("compare fails", {"view": [self.pr(statusCheckRollup=[failed])], "compare": [BUSY]}, (),
             {"state": "fail", "stale_base": None}, {}),
            ("review required", {"view": [self.pr(mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED")]}, (),
             {"state": "blocked", "reason": "review-required", "stale_base": False}, {"compare": 1}),
            ("behind", {"view": [self.pr(mergeStateStatus="BEHIND")], "compare": [out("3")]}, (),
             {"state": "blocked", "reason": "behind", "stale_base": True}, {}),
            ("change request", {"view": [self.pr(latestReviews=[{"state": "CHANGES_REQUESTED"}],
                                                 statusCheckRollup=[PENDING])]}, (),
             {"state": "blocked", "reason": "changes-requested"}, {"pr view": 1}),
            ("pending, then green", {"view": [self.pr(statusCheckRollup=[PENDING]), self.pr()]}, (),
             {"state": "pass"}, {"pr view": 2}),
            ("no check yet, then green", {"view": [self.pr(statusCheckRollup=[]), self.pr()]}, (),
             {"state": "pass"}, {"pr view": 2}),
            ("unknown, then green", {"view": [self.pr(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN"), self.pr()]},
             (), {"state": "pass"}, {"pr view": 2}),
            ("old head, then green", {"view": [self.pr(headRefOid="b" * 40), self.pr()]}, (),
             {"state": "pass", "head": self.head}, {"pr view": 2, "api graphql": 1}),
            ("no pr", {"view": [{"err": 'no pull requests found for branch "feat/x"', "code": 1}]}, (),
             {"state": "no-pr"}, {}),
            ("pr view fails", {"view": [BUSY]}, (), {"state": "error", "message": "HTTP 502"}, {}),
            ("pr view fails on a later pass", {"view": [self.pr(statusCheckRollup=[PENDING]), BUSY]}, (),
             {"state": "error"}, {"pr view": 2}),
            ("graphql fails", {"view": [self.pr()], "graphql": [BUSY]}, (), {"state": "error"}, {}),
        ]
        for name, script, args, want, counts in rows:
            with self.subTest(name):
                code, got = self.wait(script, *args)
                self.assertEqual(code, 0 if want["state"] == "pass" else 1)
                for key, value in want.items():
                    if key == "message":
                        self.assertIn(value, got[key])
                    else:
                        self.assertEqual(got[key], value, key)
                kinds = self.kinds()
                for kind, n in counts.items():
                    self.assertEqual(kinds.count(kind), n, kind)
                for call in self.calls():
                    self.assertFalse({"--watch", "rerun"} & set(call), call)

    def test_first_error_and_compare_call(self) -> None:
        script = {"view": [self.pr(statusCheckRollup=[run("FAILURE", name="acceptance", link=FAIL_LINK)])],
                  "run": [out(LOG)]}
        _, got = self.wait(script)
        lines = got["first_error"].splitlines()
        self.assertEqual((lines[0], len(lines)), ("Run\tERROR: test_widget failed", 30))
        self.assertIn(["run", "view", "99", "--log-failed"], self.calls())
        compare = [c for c in self.calls() if c[:1] == ["api"] and "/compare/" in c[1]]
        self.assertEqual(compare[0][1], f"repos/o/r/compare/main...{self.head}")

    def test_timeout_names_the_pending_checks(self) -> None:
        rows = [("draft, pending", self.pr(isDraft=True, statusCheckRollup=[PENDING]), ["test"]),
                ("old head", self.pr(headRefOid="b" * 40), [])]
        for name, answer, pending in rows:
            with self.subTest(name):
                code, got = self.wait({"view": [answer]}, "--timeout", "0.001", DEVLOOP_TEST_CI_POLL="0.05")
                self.assertEqual((code, got["state"], got["pending"]), (1, "timeout", pending))

    def test_pr_number_goes_to_pr_view(self) -> None:
        for args, third in ((("--pr", "7"), "7"), ((), "--json")):
            with self.subTest(args=args):
                self.wait({"view": [self.pr()]}, *args)
                self.assertTrue(all(c[2] == third for c in self.calls() if c[:2] == ["pr", "view"]))

    def test_owner_and_repo_come_from_the_pr_url(self) -> None:
        answer = out(view(number=7, url="https://ghe.example/acme/tool/pull/7", headRefOid=self.head,
                          baseRefName="main", statusCheckRollup=[run("FAILURE")]))
        self.wait({"view": [answer]})
        graph = next(c for c in self.calls() if c[:2] == ["api", "graphql"])
        self.assertIn("owner=acme", graph)
        self.assertIn("name=tool", graph)
        self.assertTrue(any(c[1].startswith("repos/acme/tool/compare/") for c in self.calls() if c[:1] == ["api"]))

    def test_outside_a_git_repo_is_an_error_before_any_gh_call(self) -> None:
        outside = self.root / "plain"
        outside.mkdir()
        code, got = self.wait({"view": [self.pr()]}, cwd=outside)
        self.assertEqual((code, got["state"], self.calls()), (1, "error", []))

    def test_a_hanging_gh_call_is_an_error(self) -> None:
        env = self.env({"view": [{**self.pr(), "sleep": 2}]})
        with mock.patch.dict(os.environ, env), mock.patch.object(ci, "GH_TIMEOUT", 0.2), \
                contextlib.chdir(self.repo):
            code, got = ci.wait(None, 1)
        self.assertEqual((code, got["state"]), (1, "error"))


class CiGithubTests(StubGh):
    LINE = "allow rebase merging in the GitHub settings of o/r (Stage 7 runs gh pr merge --rebase)"

    def check(self, origin: str | None, answer: dict) -> list[str]:
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repo, capture_output=True)
        if origin:
            subprocess.run(["git", "remote", "add", "origin", origin], cwd=self.repo, check=True)
        with mock.patch.dict(os.environ, self.env({"repo": [answer]})):
            return ci.check_github(self.repo)

    def test_check_github(self) -> None:
        rows = [(url, out("false"), [self.LINE], True) for url in
                ("git@github.com:o/r.git", "https://github.com/o/r", "https://github.com/o/r.git",
                 "ssh://git@github.com/o/r.git")]
        rows += [("git@github.com:o/r.git", out("true"), [], True),
                 ("git@github.com:o/r.git", BUSY, [], True),
                 ("https://gitlab.com/o/r.git", out("false"), [], False),
                 (None, out("false"), [], False)]
        for origin, answer, want, called in rows:
            with self.subTest(origin=origin, answer=answer):
                self.assertEqual(self.check(origin, answer), want)
                calls = [["api", "repos/o/r", "--jq", ".allow_rebase_merge"]] if called else []
                self.assertEqual(self.calls(), calls)


class CiTextTests(unittest.TestCase):
    def test_stage_7_and_grant(self) -> None:
        skill = (HERE.parents[2] / "skill" / "SKILL.md").read_text()
        start = skill.index("\n## 7 ")
        stage = skill[start:skill.find("\n## ", start + 1)]
        grant = next(line for line in skill.splitlines() if line.startswith("- Stop and report only for:"))
        for needle in ("`conflicts`", "`threads`", "`blocked`", "`stale_base`", "`behind`", "`gh run rerun`",
                       "--force-with-lease --force-if-includes", "resolve"):
            with self.subTest(needle=needle):
                self.assertIn(needle, stage)
        for needle in ("`threads`", "`blocked`", "lease push"):
            with self.subTest(needle=needle):
                self.assertIn(needle, grant)


if __name__ == "__main__":
    unittest.main()
