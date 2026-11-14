"""`merge-check`: the `test-time-waived` reason and the `test_time` key of `config-changed`."""

from __future__ import annotations

import json

from test_devloop import git
from test_merge_check import MergeCheckCase


class MergeTestTimeTests(MergeCheckCase):
    def test_waiver_trailer_waits_for_the_user(self) -> None:
        wt, _ = self.reviewed_feature()
        out = self.ok(wt)
        self.assertEqual((out["merge"], self.reasons(out, "test-time-waived")), ("auto", []))
        git(wt, "commit", "-q", "--allow-empty", "-m", "grow", "-m", "Test-Time: new subsystem")
        sha = self.head(wt)
        self.gate_green(wt, sha)
        out = self.ok(wt)
        self.assertEqual(out["merge"], "human")
        self.assertEqual(self.reasons(out, "test-time-waived"),
                         [{"reason": "test-time-waived", "commit": sha, "waiver": "new subsystem"}])

    def test_test_time_change_needs_a_human(self) -> None:
        base = {"version": "0.0.0"}
        five = {**base, "test_time": {"gate": {"unmarked": 5}}}
        rows = [
            ("branch adds the key", base, five, True),
            ("branch changes it", five, {**base, "test_time": {"gate": {"unmarked": 6}}}, True),
            ("branch drops it", five, base, True),
            ("equal value", five, five, False),
            ("branch sets false on an absent key", base, {**base, "test_time": False}, True),
        ]
        for name, main, branch, changed in rows:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.main_config(main)
                wt = self.feature()
                self.commit(wt, "dev-loop.json", json.dumps(branch) + "\n")
                self.gate_green(wt, self.head(wt))
                self.assertEqual(self.reasons(self.ok(wt), "config-changed"),
                                 [{"reason": "config-changed"}] if changed else [])
