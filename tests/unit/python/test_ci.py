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
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

import ci  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main"]

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
        rows += [(run(c), "fail") for c in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE",
                                             "ACTION_REQUIRED")]
        rows += [(PENDING, "pending"), (run("SUCCESS", "QUEUED"), "pending"),
                 (context("SUCCESS"), "pass"), (context("FAILURE"), "fail"), (context("ERROR"), "fail"),
                 (context("PENDING"), "pending"), (context("EXPECTED"), "pending"),
                 ({"name": "x", "state": "SUCCESS"}, "pending")]
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
            ("pending check", view(statusCheckRollup=[run(), PENDING]), [], None),
            ("no check registered yet", view(statusCheckRollup=[]), [], None),
            ("mergeable unknown", view(mergeable="UNKNOWN"), [], None),
            ("merge state unknown", view(mergeStateStatus="UNKNOWN"), [], None),
            ("draft with a pending check", view(isDraft=True, statusCheckRollup=[PENDING]), [], None),
            ("unknown rollup kind", view(statusCheckRollup=[{"name": "x"}]), [], None),
        ]
        for name, pr, threads, want in rows:
            with self.subTest(name):
                got = ci.verdict(pr, threads)
                if want is None:
                    self.assertIsNone(got)
                    continue
                assert got is not None
                self.assertEqual((got["state"], got["reason"], got["failed"]), want)

    def test_open_threads(self) -> None:
        def node(resolved: bool = False, body: str = "Line one\nLine two", author: dict | None = None, line: int | None = 12,
                 association: str | None = "COLLABORATOR", **extra) -> dict:
            comment = {"author": author if author is not None else {"login": "bot"}, "body": body, "url": "u",
                       "authorAssociation": association}
            return {"isResolved": resolved, "path": "skill/x.py", "line": line, "comments": {"nodes": [comment]},
                    **extra}

        def answer(*nodes: dict) -> dict:
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(nodes)}}}}}

        ghost = node()
        ghost["comments"]["nodes"][0]["author"] = None
        long = "x" * 300
        bare = node()
        del bare["comments"]["nodes"][0]["authorAssociation"]
        rows = [
            ("resolved", answer(node(resolved=True)), []),
            ("open", answer(node()), [THREAD]),
            ("outdated still open", answer(node(isOutdated=True)), [THREAD]),
            ("first line, 200 characters", answer(node(body=long + "\nmore")), [{**THREAD, "text": "x" * 200}]),
            ("deleted author", answer(ghost), [{**THREAD, "author": None}]),
            ("file comment", answer(node(line=None)), [{**THREAD, "line": None}]),
            ("owner", answer(node(association="OWNER")), [THREAD]),
            ("member", answer(node(association="MEMBER")), [THREAD]),
            ("contributor: no text", answer(node(association="CONTRIBUTOR")), [{**THREAD, "text": None}]),
            ("outside user: no text", answer(node(association="NONE")), [{**THREAD, "text": None}]),
            ("no association: no text", answer(node(association=None)), [{**THREAD, "text": None}]),
            ("first timer: no text", answer(node(association="FIRST_TIMER")), [{**THREAD, "text": None}]),
            ("first time contributor: no text", answer(node(association="FIRST_TIME_CONTRIBUTOR")),
             [{**THREAD, "text": None}]),
            ("mannequin: no text", answer(node(association="MANNEQUIN")), [{**THREAD, "text": None}]),
            ("association key missing: no text", answer(bare), [{**THREAD, "text": None}]),
            ("outside user, long line: no text", answer(node(association="NONE", body=long + "\nmore")),
             [{**THREAD, "text": None}]),
            ("member then outside user", answer(node(association="MEMBER"), node(association="NONE")),
             [THREAD, {**THREAD, "text": None}]),
            ("no comment: no text", answer(node(comments={"nodes": []})),
             [{**THREAD, "author": None, "url": None, "text": None}]),
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
                                       "url": "https://github.com/o/r/pull/7#r1", "authorAssociation": "MEMBER"}]}}
FAIL_LINK = "https://github.com/o/r/actions/runs/99/job/5"
BUSY = {"err": "HTTP 502", "code": 1}
PR_RECORD = {"number": 7, "url": "https://github.com/o/r/pull/7", "branch": "feat/x"}


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
        outsider = {**THREAD_NODE, "comments": {"nodes": [{**THREAD_NODE["comments"]["nodes"][0],
                                                           "authorAssociation": "NONE"}]}}
        rows = [
            ("green", {"view": [self.pr()]}, (),
             {"state": "pass", "reason": None, "head": self.head, "threads": [], "stale_base": None,
              "failed_job": None, "pending": [], "url": "https://github.com/o/r/pull/7"}, {"pr view": 1, "compare": 0}),
            ("conflict at once", {"view": [self.pr(mergeable="CONFLICTING", statusCheckRollup=[PENDING])]}, (),
             {"state": "conflicts", "stale_base": None}, {"pr view": 1}),
            ("open thread", {"view": [self.pr()], "graphql": [graphql(THREAD_NODE)]}, (),
             {"state": "threads", "threads": [{"path": "skill/x.py", "line": 12, "author": "bot",
                                               "url": "https://github.com/o/r/pull/7#r1", "text": "Line one"}]}, {}),
            ("outsider thread beats a failed check",
             {"view": [self.pr(statusCheckRollup=[failed])], "graphql": [graphql(outsider)]}, (),
             {"state": "threads", "threads": [{"path": "skill/x.py", "line": 12, "author": "bot",
                                               "url": "https://github.com/o/r/pull/7#r1", "text": None}]}, {}),
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
            ("pr view is not json", {"view": [out("not json")]}, (), {"state": "error", "message": "pr view"}, {}),
            ("graphql is not json", {"view": [self.pr()], "graphql": [out("not json")]}, (),
             {"state": "error", "message": "graphql"}, {}),
            ("repository is null", {"view": [self.pr()], "graphql": [out({"data": {"repository": None}})]}, (),
             {"state": "error", "message": "graphql"}, {}),
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
        pairs = list(zip(graph, graph[1:]))
        self.assertIn(("-f", "owner=acme"), pairs)
        self.assertIn(("-f", "name=tool"), pairs)
        self.assertIn(("-F", "number=7"), pairs)
        self.assertTrue(any(c[1].startswith("repos/acme/tool/compare/") for c in self.calls() if c[:1] == ["api"]))

    def test_thread_query_names_author_association(self) -> None:
        self.wait({"view": [self.pr()]})
        graph = next(c for c in self.calls() if c[:2] == ["api", "graphql"])
        query = next(arg for arg in graph if arg.startswith("query="))
        self.assertRegex(query, r"\bauthorAssociation\b")

    def test_a_timeout_that_is_not_a_finite_number_above_zero_is_a_usage_error(self) -> None:
        for value in ("nan", "inf", "0", "-1"):
            with self.subTest(value=value):
                result = subprocess.run([sys.executable, str(DEVLOOP), "ci", "wait", f"--timeout={value}"],
                                        cwd=self.repo, env=self.env({"view": [self.pr()]}), capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual((result.returncode, self.calls()), (2, []))

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

    def record(self, cwd: Path | None = None) -> Path:
        git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=cwd or self.repo,
                                 capture_output=True, text=True, check=True).stdout.strip()
        return Path(git_dir) / "dev-loop" / "pr.json"

    def test_pr_record_on_every_final_state(self) -> None:
        rows = [("pass", {"view": [self.pr(headRefName="feat/x")]}, ()),
                ("fail", {"view": [self.pr(headRefName="feat/x", statusCheckRollup=[FAILED])], "run": [out(LOG)]}, ()),
                ("conflicts", {"view": [self.pr(headRefName="feat/x", mergeable="CONFLICTING")]}, ()),
                ("threads", {"view": [self.pr(headRefName="feat/x")], "graphql": [graphql(THREAD_NODE)]}, ()),
                ("blocked", {"view": [self.pr(headRefName="feat/x", mergeStateStatus="BEHIND")]}, ()),
                ("timeout", {"view": [self.pr(headRefName="feat/x", isDraft=True, statusCheckRollup=[PENDING])]},
                 ("--timeout", "0.001"))]
        for state, script, args in rows:
            with self.subTest(state):
                self.record().unlink(missing_ok=True)
                _, got = self.wait(script, *args, DEVLOOP_TEST_CI_POLL="0.05" if args else "0")
                self.assertEqual(got["state"], state)
                self.assertEqual(json.loads(self.record().read_text()), PR_RECORD)

    def test_pr_record_goes_to_the_worktree_git_dir(self) -> None:
        tree = self.root / "wt"
        subprocess.run([*GIT, "worktree", "add", "-q", "-b", "feat/x", str(tree)], cwd=self.repo, check=True)
        code, _ = self.wait({"view": [self.pr(headRefName="feat/x")]}, cwd=tree)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.record(tree).read_text()), PR_RECORD)
        self.assertFalse((self.repo / ".git" / "dev-loop" / "pr.json").exists())

    def test_no_pr_record_without_a_pr_number(self) -> None:
        rows = [("no pr", {"err": 'no pull requests found for branch "feat/x"', "code": 1}), ("pr view fails", BUSY),
                ("not json", out("not json")), ("number text", self.pr(number="7")), ("number 0", self.pr(number=0)),
                ("number true", self.pr(number=True))]
        for name, answer in rows:
            for before in (None, "keep\n"):
                with self.subTest(name, before=before):
                    self.record().unlink(missing_ok=True)
                    if before:
                        self.record().parent.mkdir(exist_ok=True)
                        self.record().write_text(before)
                    self.wait({"view": [answer]})
                    self.assertEqual(self.record().read_text() if self.record().exists() else None, before)

    def test_link_at_the_pr_record_is_not_written_through(self) -> None:
        script = {"view": [self.pr(headRefName="feat/x")]}
        clean = self.wait(script)
        self.record().unlink()
        victim, elsewhere = self.root / "victim", self.root / "elsewhere"
        victim.write_text("keep\n")
        elsewhere.mkdir()
        self.record().symlink_to(victim)
        for name, link in (("pr.json", self.record()), ("dev-loop", self.record().parent)):
            with self.subTest(name):
                if name == "dev-loop":
                    self.record().unlink()
                    self.record().parent.rmdir()
                    self.record().parent.symlink_to(elsewhere)
                self.assertEqual(self.wait(script)[0], clean[0])
                self.assertEqual(victim.read_text(), "keep\n")
                self.assertEqual(list(elsewhere.iterdir()), [])
                self.assertTrue(link.is_symlink())


class CiGithubTests(StubGh):
    LINE = "allow rebase merging in the GitHub settings of o/r (Stage 7 runs gh pr merge --rebase)"

    DELETE = ('turn on "Automatically delete head branches" in the GitHub settings of o/r (Stage 7 leaves the merged '
              'branch on GitHub): gh api -X PATCH repos/o/r -F delete_branch_on_merge=true, or waive it with '
              '"delete_branch_on_merge": false in dev-loop.json')
    REBASE_CALL = ["api", "repos/o/r", "--jq", ".allow_rebase_merge"]
    DELETE_CALL = ["api", "repos/o/r", "--jq", ".delete_branch_on_merge"]

    def check(self, origin: str | None, answers: list[dict]) -> list[str]:
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repo, capture_output=True)
        if origin:
            subprocess.run(["git", "remote", "add", "origin", origin], cwd=self.repo, check=True)
        with mock.patch.dict(os.environ, self.env({"repo": answers})):
            return ci.check_github(self.repo)

    def git(self, *args: str) -> None:
        subprocess.run([*GIT, *args], cwd=self.repo, check=True, capture_output=True)

    def test_check_github(self) -> None:
        both = [self.REBASE_CALL, self.DELETE_CALL]
        rows = [(url, [out("false"), out("false")], [self.LINE, self.DELETE], both) for url in
                ("git@github.com:o/r.git", "https://github.com/o/r", "https://github.com/o/r.git",
                 "ssh://git@github.com/o/r.git")]
        rows += [("git@github.com:o/r.git", [out("true"), out("true")], [], both),
                 ("git@github.com:o/r.git", [out("true"), out("false")], [self.DELETE], both),
                 ("git@github.com:o/r.git", [out("false"), out("true")], [self.LINE], both),
                 ("git@github.com:o/r.git", [BUSY, BUSY], [], both),
                 ("git@github.com:o/r.git", [out("true"), out("False")], [], both),
                 ("https://gitlab.com/o/r.git", [out("false")], [], []),
                 (None, [out("false")], [], [])]
        for origin, answers, want, calls in rows:
            with self.subTest(origin=origin, answers=answers):
                self.assertEqual(self.check(origin, answers), want)
                self.assertEqual(self.calls(), calls)

    def test_delete_branch_waiver(self) -> None:
        def commit_config(text: str | bytes) -> None:
            data = text if isinstance(text, bytes) else text.encode()
            (self.repo / "dev-loop.json").write_bytes(data)
            self.git("add", "dev-loop.json")
            self.git("commit", "-q", "-m", "config")

        def on_branch() -> None:
            commit_config("{}\n")
            self.git("checkout", "-q", "-b", "feat/x")
            commit_config('{"delete_branch_on_merge": false}\n')

        def no_main_ref() -> None:
            self.git("branch", "-m", "main", "trunk")
            (self.repo / "dev-loop.json").write_text('{"delete_branch_on_merge": false}\n')

        non_utf8 = b'{"delete_branch_on_merge": false, "x": "\xff"}'

        def no_main_ref_non_utf8() -> None:
            self.git("branch", "-m", "main", "trunk")
            (self.repo / "dev-loop.json").write_bytes(non_utf8)

        rows = (
            ("no config", lambda: None, [self.DELETE]),
            ("waived on main", lambda: commit_config('{"delete_branch_on_merge": false}\n'), []),
            ("true on main", lambda: commit_config('{"delete_branch_on_merge": true}\n'), [self.DELETE]),
            ("zero on main", lambda: commit_config('{"delete_branch_on_merge": 0}\n'), [self.DELETE]),
            ("string on main", lambda: commit_config('{"delete_branch_on_merge": "false"}\n'), [self.DELETE]),
            ("waived on a branch only", on_branch, [self.DELETE]),
            ("invalid JSON on main", lambda: commit_config("{not json\n"), [self.DELETE]),
            ("no main ref, working tree file", no_main_ref, []),
            ("non-UTF-8 on main", lambda: commit_config(non_utf8), [self.DELETE]),
            ("no main ref, non-UTF-8 working tree file", no_main_ref_non_utf8, [self.DELETE]),
        )
        for name, setup, want in rows:
            with self.subTest(state=name):
                self.tearDown()
                self.setUp()
                setup()
                self.assertEqual(self.check("git@github.com:o/r.git", [out("true"), out("false")]), want)
                calls = [self.REBASE_CALL] + ([self.DELETE_CALL] if want else [])
                self.assertEqual(self.calls(), calls)


class CiTextTests(unittest.TestCase):
    def test_stage_7_and_grant(self) -> None:
        skill = (ROOT / "skill" / "SKILL.md").read_text()
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
