#!/usr/bin/env python3
"""Tests for ci.py: the verdict of one ci wait pass, the poll loop, and the GitHub settings check."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import ci  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
