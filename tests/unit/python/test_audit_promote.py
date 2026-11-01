#!/usr/bin/env python3
"""Tests for `audit-run promote`: no-follow copy, limits."""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit_fixtures import AuditRunCase  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditPromoteTests(AuditRunCase):
    def promote(self, agent: str) -> dict:
        out, code = self.step("promote", "--agent", agent)
        self.assertEqual(code, 0, out)
        return out

    def test_promotes_the_named_file(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = [self.local_check(agent, "out/r.txt")]
        self.write_result(agent, result)
        out = self.promote(agent)
        dest = self.path(f"agents/{agent}/artifacts/out/r.txt")
        self.assertEqual((out, dest.read_bytes()), ({"promoted": [f"agents/{agent}/artifacts/out/r.txt"], "rejected": []},
                                                    b"0123456789"))
        self.assertEqual(self.state()["briefs"][agent]["promoted"], {f"agents/{agent}/artifacts/out/r.txt": 10})

    def test_rejections(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        scratch = self.path(f"agents/{agent}/scratch")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("SECRET")
        (outside / "x.txt").write_text("X")
        checks = [self.local_check(agent, name) for name in ("link.txt", "hard.txt", "fifo.txt", "dir.txt", "big.txt",
                                                            "linked/x.txt", "q1", "q2", "q3", "q4", "q5")]
        for name in ("link.txt", "fifo.txt", "dir.txt"):
            (scratch / name).unlink()
        (scratch / "link.txt").symlink_to(outside / "secret.txt")
        os.link(scratch / "hard.txt", scratch / "hard-2.txt")
        os.mkfifo(scratch / "fifo.txt")
        (scratch / "dir.txt").mkdir()
        (scratch / "big.txt").write_bytes(b"b" * (256 * 1024 + 1))
        shutil.rmtree(scratch / "linked")
        (scratch / "linked").symlink_to(outside, target_is_directory=True)
        for name in ("q1", "q2", "q3", "q4"):
            (scratch / name).write_bytes(b"q" * (256 * 1024))
        (scratch / "q5").write_bytes(b"5")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = checks
        self.write_result(agent, result)
        before_outside = {p.name: p.read_bytes() for p in outside.iterdir()}
        out = self.promote(agent)
        reasons = {r["path"].rsplit("/", 1)[-1]: r["reason"] for r in out["rejected"]}
        self.assertEqual(reasons, {"link.txt": "link", "hard.txt": "hard-linked", "fifo.txt": "not-regular",
                                   "dir.txt": "not-regular", "big.txt": "too-large", "x.txt": "link", "q5": "agent-limit"})
        self.assertEqual([p.rsplit("/", 1)[-1] for p in out["promoted"]], ["q1", "q2", "q3", "q4"])
        self.assertEqual({p.name: p.read_bytes() for p in outside.iterdir()}, before_outside)
        artifacts = self.path(f"agents/{agent}/artifacts")
        self.assertEqual(sorted(p.name for p in artifacts.rglob("*")), ["q1", "q2", "q3", "q4"])

    def test_missing_scratch_folder_is_missing_and_a_link_is_a_link(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = [self.local_check(agent, "out/r.txt")]
        self.write_result(agent, result)
        scratch = self.path(f"agents/{agent}/scratch")
        shutil.rmtree(scratch / "out")
        out = self.promote(agent)
        self.assertEqual(out["rejected"], [{"path": f"agents/{agent}/artifacts/out/r.txt", "reason": "missing"}])
        (scratch / "out").symlink_to(self.root, target_is_directory=True)
        out = self.promote(agent)
        self.assertEqual(out["rejected"], [{"path": f"agents/{agent}/artifacts/out/r.txt", "reason": "link"}])

    def test_existing_artifact_not_overwritten(self) -> None:
        agent, ids = self.hunter_brief("a.py")
        result = self.hunter_result(agent, ids)
        result["units"][0]["checks"] = [self.local_check(agent, "r.txt"), self.local_check(agent, "s.txt")]
        self.write_result(agent, result)
        self.path(f"agents/{agent}/artifacts/s.txt").write_bytes(b"PARENT")
        out = self.promote(agent)
        self.assertEqual(out["rejected"], [{"path": f"agents/{agent}/artifacts/s.txt", "reason": "exists"}])
        self.path(f"agents/{agent}/scratch/r.txt").write_bytes(b"CHANGED")
        out = self.promote(agent)
        self.assertEqual({r["reason"] for r in out["rejected"]}, {"exists"})
        self.assertEqual(self.path(f"agents/{agent}/artifacts/r.txt").read_bytes(), b"0123456789")
        self.assertEqual(self.path(f"agents/{agent}/artifacts/s.txt").read_bytes(), b"PARENT")

    def test_invalid_result_is_refused(self) -> None:
        agent, _ = self.hunter_brief("a.py")
        self.write_result(agent, b"nope")
        before = self.snapshot()
        out, code = self.step("promote", "--agent", agent)
        self.assertEqual((code, out["error"], out["state"]), (1, "invalid", "invalid"))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
