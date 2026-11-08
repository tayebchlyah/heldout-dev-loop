#!/usr/bin/env python3
"""Held-out acceptance tests for the CI thread text (spec: 20261108 CI Thread Text).

One test per `## Acceptance flows` item (F<n>). Each docstring names the `## Acceptance oracle` lines
(O<n>, with the spec file line) it proves. The seam is the one of the CI Follow-up oracle lines: the
stdout JSON and exit code of `devloop.py ci wait`, run in a git repo with one commit <H>, a stub `gh`
on `PATH` and `DEVLOOP_TEST_CI_POLL=0`. Setup, stub and default answers come from
`test_ci_follow_up.py`. The CI Follow-up F3 test (`test_ci_follow_up.py`) combines this feature with
that earlier one: its thread data carries `authorAssociation` `MEMBER`.

Fixture choices the spec leaves open:
- `authorAssociation` sits on the first comment node, next to `author`, `body` and `url`, as GitHub
  gives it: `comments.nodes[0].authorAssociation`.
- The `query` argument is the argv item of the `gh api graphql` call that starts with `query=`
  (CI Follow-up D6: `-f query=<query>`).
"""

from __future__ import annotations

import unittest

from test_ci_follow_up import CiCase, check, ok, threads_doc, view

MISSING = object()
THREAD_URL = "https://github.com/o/r/pull/7#r1"
TRUSTED_ITEM = {"path": "skill/x.py", "line": 12, "author": "bot", "url": THREAD_URL, "text": "Line one"}
OUTSIDER_ITEM = {**TRUSTED_ITEM, "text": None}


def the_thread(association: object = MISSING, body: str = "Line one\nLine two") -> dict:
    """The thread of the oracle preamble; `association` MISSING leaves the key out."""
    comment: dict = {"author": {"login": "bot"}, "body": body, "url": THREAD_URL}
    if association is not MISSING:
        comment["authorAssociation"] = association
    return {"isResolved": False, "isOutdated": False, "path": "skill/x.py", "line": 12,
            "comments": {"nodes": [comment]}}


class ThreadTextCase(CiCase):
    def wait_on(self, *threads: dict, **changes: list[dict]) -> tuple[int, dict]:
        self.defaults(api_graphql=[ok(threads_doc(*threads))], **changes)
        return self.ci_wait()


class CollaboratorThreadTests(ThreadTextCase):
    def test_f1_collaborator_thread_gives_its_first_line(self) -> None:
        """F1: a collaborator opens a thread on the PR; `ci wait` gives `threads` with its first line.
        O1 (spec line 50), O2 (spec line 51), O5 (spec line 54), O7 (spec line 56)."""
        with self.subTest(oracle=1):
            code, payload = self.wait_on(the_thread("MEMBER"))
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "threads", payload)
            self.assertEqual(payload.get("threads"), [TRUSTED_ITEM], payload)
        with self.subTest(oracle=7):
            graphql = self.stub.of("api", "graphql")
            self.assertTrue(graphql, self.stub.calls())
            queries = [arg for arg in graphql[0] if arg.startswith("query=")]
            self.assertEqual(len(queries), 1, graphql[0])
            self.assertIn("authorAssociation", queries[0])
        for association in ("OWNER", "COLLABORATOR"):
            with self.subTest(oracle=2, association=association):
                code, payload = self.wait_on(the_thread(association))
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload.get("state"), "threads", payload)
                self.assertEqual(payload.get("threads"), [TRUSTED_ITEM], payload)
        with self.subTest(oracle=5):
            first_line = "".join(chr(ord("a") + n % 26) for n in range(300))
            code, payload = self.wait_on(the_thread("COLLABORATOR", body=first_line + "\nLine two"))
            self.assertEqual(payload.get("state"), "threads", payload)
            self.assertEqual(payload.get("threads"), [{**TRUSTED_ITEM, "text": first_line[:200]}], payload)


class OutsiderThreadTests(ThreadTextCase):
    def test_f2_outsider_thread_gives_null_text(self) -> None:
        """F2: an outside GitHub user opens a thread on the PR; `ci wait` gives `threads` with `text` null.
        O3 (spec line 52), O4 (spec line 53), O6 (spec line 55), O8 (spec line 57)."""
        for association in ("CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN", "NONE"):
            with self.subTest(oracle=3, association=association):
                code, payload = self.wait_on(the_thread(association))
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload.get("state"), "threads", payload)
                self.assertEqual(payload.get("threads"), [OUTSIDER_ITEM], payload)
        for label, association in (("no key", MISSING), ("null", None)):
            with self.subTest(oracle=4, association=label):
                code, payload = self.wait_on(the_thread(association))
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload.get("state"), "threads", payload)
                self.assertEqual(payload.get("threads"), [OUTSIDER_ITEM], payload)
        with self.subTest(oracle=6):
            code, payload = self.wait_on(the_thread("MEMBER"), the_thread("NONE"))
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "threads", payload)
            self.assertEqual(payload.get("threads"), [TRUSTED_ITEM, OUTSIDER_ITEM], payload)
        with self.subTest(oracle=8):
            failed = view(self.H, statusCheckRollup=[
                check("test"), check("acceptance", conclusion="FAILURE",
                                     url="https://github.com/o/r/actions/runs/99/job/5")])
            code, payload = self.wait_on(the_thread("NONE"), pr_view=[ok(failed)])
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "threads", payload)
            self.assertEqual(payload.get("threads"), [OUTSIDER_ITEM], payload)


if __name__ == "__main__":
    unittest.main()
