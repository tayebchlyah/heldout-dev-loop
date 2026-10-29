#!/usr/bin/env python3
"""Tests for the `acceptance` case floor of feature_gate.py."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from test_feature_gate import RepoCase, git, guard, junit, run


class CaseFloorGuardTests(unittest.TestCase):
    def test_short_run_stops(self) -> None:
        short = {**run(0, cases=3), "short": True, "floor": 4}
        self.assertEqual(guard([run(2, cases=4), short]),
                         {"verdict": "stop", "reason": "short", "ids": [], "counts": [2, 0], "floor": 4})
        self.assertEqual(guard([run(2, cases=4), short, run(1)])["counts"], [2, 0, 1])


class CaseFloorTests(RepoCase):
    def test_case_floor(self) -> None:
        bad = {"at": "2026-10-01T00:00:00Z", "mode": "full", "exit": 1, "failing": 1, "ids": ["t::a"],
               "stale": "skipped", "counted": True, "log": "x.log", "cases": 9}
        rows = [
            ("three passing", "commit", (), junit([], passing=3), 1, 4, True, "stop"),
            ("three passing one skipped", "commit", (), junit([], passing=3, skipped=1), 1, 4, True, "stop"),
            ("four passing", "commit", (), junit([], passing=4), 0, 4, False, "green"),
            ("five passing", "commit", (), junit([], passing=5), 0, 4, False, "green"),
            ("three cases one failing", "commit", (), junit([("t", "a")], passing=2), 1, 4, False, "continue"),
            ("new seal", "seal", (), junit([], passing=3), 0, None, False, "green"),
            ("amended", "amend", (), junit([], passing=3), 1, 4, True, "stop"),
            ("seal rebased", "rebase", (), junit([], passing=3), 1, 4, True, "stop"),
            ("later commit amended", "amendlate", (), junit([], passing=3), 1, 4, True, "stop"),
            ("failed mode", "commit", ("--failed",), junit([], passing=1), 0, None, False, "continue"),
            ("bad record heads", "heads", (), junit([], passing=3), 0, None, False, "green"),
        ]
        for name, step, args, xml, code, floor, short, verdict in rows:
            with self.subTest(name):
                shutil.rmtree(self.tmp)
                self.tmp.mkdir()
                repo = self.make_repo({"acceptance": "./run.sh", "acceptance_failed": "./run.sh"})
                (repo / "fixture.xml").write_text(junit([("t", "a"), ("t", "b")], passing=2))
                (repo / "exit.txt").write_text("1")
                if step in ("rebase", "amendlate"):
                    for n, rel in enumerate(("tests/acceptance/test_x.py", "app.py")[:1 if step == "rebase" else 2]):
                        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                        (repo / rel).write_text("x = 2\n")
                        git(repo, "add", ".")
                        git(repo, "commit", "-qm", f"c{n}")
                    first = self.devloop("acceptance", cwd=repo)[1]["head"]
                    later = {**os.environ, "GIT_COMMITTER_DATE": "2030-01-01T00:00:00Z"}
                    cmd = (["rebase", "--force-rebase", "HEAD~1"] if step == "rebase"
                           else ["commit", "--amend", "--no-edit", "--reset-author"])
                    subprocess.run(["git", "-C", str(repo), *cmd], env=later, check=True, capture_output=True)
                    self.assertNotEqual(git(repo, "rev-parse", "HEAD"), first)
                elif step == "heads":
                    self.record(repo).parent.mkdir(parents=True)
                    self.record(repo).write_text(json.dumps({"runs": [{**bad, "head": "--output=x"},
                                                                      {**bad, "head": "0" * 40}]}))
                else:
                    first = self.devloop("acceptance", cwd=repo)[1]["head"]
                if step in ("commit", "seal"):
                    path = repo / ("tests/acceptance/test_x.py" if step == "seal" else "app.py")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("x = 2\n")
                    git(repo, "add", ".")
                    git(repo, "commit", "-qm", step)
                elif step == "amend":
                    git(repo, "commit", "-q", "--amend", "-m", "init again")
                (repo / "fixture.xml").write_text(xml)
                (repo / "exit.txt").write_text("0" if code == 0 or short else "1")
                got, out, _ = self.devloop("acceptance", *args, cwd=repo)
                self.assertEqual((got, out["floor"], out["short"], out["guard"]["verdict"]),
                                 (code, floor, short, verdict))
                last = json.loads(self.record(repo).read_text())["runs"][-1]
                self.assertEqual((last["floor"], last["short"]), (floor, short))
                if short:
                    self.assertEqual((out["guard"]["reason"], out["guard"]["floor"]), ("short", 4))
                    self.assertTrue(out["hint"].startswith(f"ran {out['cases']} of 4 cases (full run at {first[:12]})"))


if __name__ == "__main__":
    unittest.main()
