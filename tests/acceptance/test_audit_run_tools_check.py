#!/usr/bin/env python3
"""Held-out oracle tests for `audit-run check` and `audit-run promote` (spec: Audit Run Tools, oracle 14 to 20).
Fixtures: `audit_run_tools_support.py`.

Fixture choices the spec leaves open:
- "An error naming the unit or field": the JSON `errors` text holds the coverage id, the field name or the path.
- The base hunter result: unit `app` covered by a source check, unit `upload` a candidate `fp-one` with a local
  check on `agents/h01/artifacts/out/r.txt` (a 10-byte `scratch/out/r.txt`).
- Promote cases (oracle 20) name every file in one result, one local check per file; the files a link points at
  sit outside the run folder. Sizes: 300 KiB over the file cap, five files of 250,000 bytes for the 1 MiB total.
"""

from __future__ import annotations

import copy
import json
import os
import unittest

from audit_run_tools_support import (AuditCase, check_of, critic_result, entry, hunter_result,
                                     needs_validation, put, snapshot, unit_out, verifier_result)


class CheckCase(AuditCase):

    def check_invalid(self, run, agent: str, data: object, state: str = "invalid") -> dict:
        run.write_result(agent, data)
        code, payload = run.call("check", "--agent", agent)
        self.assertEqual(code, 1, payload)
        self.assertEqual((payload.get("ok"), payload.get("state")), (False, state), payload)
        self.assertLessEqual(len(payload.get("errors") or []), 50, payload)
        return payload


class CheckTests(CheckCase):

    def test_o14_valid_hunter_result(self) -> None:
        run, h01, result, _ = self.hunted()
        run.write_result(h01, result)
        self.assertEqual(run.ok("check", "--agent", h01).get("ok"), True)

    def test_o15_each_broken_rule_alone(self) -> None:
        run, h01, base, (app, upload, admin) = self.hunted()

        def broken(change):
            data = copy.deepcopy(base)
            change(data)
            return data

        def units(data):
            return {u["coverage_id"]: u for u in data["units"]}

        def set_paths(data, paths):
            u = units(data)[app]
            u["reviewed_paths"] = paths
            u["checks"][0]["reviewed_paths"] = paths

        def bad_fp(data):
            data["candidates"][0]["fingerprint"] = "-bad fp"
            units(data)[upload]["candidate_fingerprints"] = ["-bad fp"]

        cases = {
            "assigned id missing": (lambda d: d["units"].pop(1), (upload,)),
            "unassigned id": (lambda d: d["units"].append(unit_out(admin, "covered", [check_of(h01, ["src/admin.py"])])),
                              (admin,)),
            "one id twice": (lambda d: d["units"].append(copy.deepcopy(d["units"][0])), (app,)),
            "covered with unresolved": (lambda d: units(d)[app].update(unresolved=["a fact"]), (app, "unresolved")),
            "candidate with no fingerprint": (lambda d: units(d)[upload].update(candidate_fingerprints=[]),
                                              (upload, "candidate_fingerprints")),
            "check agent_id": (lambda d: units(d)[app]["checks"][0].update(agent_id="h09"), ("agent_id", "h09")),
            "source check with artifact": (lambda d: units(d)[app]["checks"][0].update(
                artifact="agents/h01/artifacts/out/r.txt"), ("artifact",)),
            "local artifact elsewhere": (lambda d: units(d)[upload]["checks"][0].update(
                artifact="agents/h01/other/r.txt"), ("artifact", "agents/h01/other/r.txt")),
            "local artifact with no scratch file": (lambda d: units(d)[upload]["checks"][0].update(
                artifact="agents/h01/artifacts/out/none.txt"), ("artifact", "none.txt")),
            "reviewed_paths not the union": (lambda d: units(d)[app].update(reviewed_paths=["src/app.py", "README.md"]),
                                             ("reviewed_paths",)),
            "absolute path": (lambda d: set_paths(d, ["/etc/hosts"]), ("reviewed_paths", "/etc/hosts")),
            "dot-dot path": (lambda d: set_paths(d, ["../outside.py"]), ("reviewed_paths", "../outside.py")),
            "candidate the validator fails": (lambda d: d["candidates"][0].update(description="STUB-FAIL here"),
                                              ("fp-one", "candidate")),
            "fingerprint pattern": (bad_fp, ("-bad fp", "fingerprint")),
        }
        for case, (change, tokens) in cases.items():
            with self.subTest(case=case):
                self.assert_errors_name(self.check_invalid(run, h01, broken(change)), *tokens)

    def test_o16_missing_and_unreadable_results(self) -> None:
        run, h01, result, _ = self.hunted()
        path =run.scratch(h01) / "result.json"
        code, payload = run.call("check", "--agent", h01)
        self.assertEqual((code, payload.get("ok"), payload.get("state")), (1, False, "missing"), payload)
        put(run.repo.inputs / "real.json", json.dumps(result))
        for case, data in (("not JSON", "{oops"), ("JSON array", "[]"),
                           ("over 5 MiB", json.dumps(result) + " " * (5 * 1024 * 1024 + 1))):
            with self.subTest(case=case):
                self.check_invalid(run, h01, data)
        with self.subTest(case="link"):
            path.unlink()
            os.symlink(run.repo.inputs / "real.json", path)
            code, payload = run.call("check", "--agent", h01)
            self.assertEqual((code, payload.get("ok"), payload.get("state")), (1, False, "invalid"), payload)

    def test_o17_critic_result_rules(self) -> None:
        run, h01, result, (app, upload, admin) = self.hunted()
        run.finish(h01, result, promote=True)
        c01 = run.brief("critic")
        gap = {"surface": "src/report.py#entry", "boundary": "src/report.py#guard", "subsystem": "src",
               "attack_class": "ATTACK-CLASSES.md#Access control", "starting_paths": ["src/report.py"],
               "selected_companion_blocks": [], "excluded_blocks": [], "reason": "No unit reads the report route."}
        for case, data in (("stop with a missing unit", critic_result(missing=(gap,), stop=True)),
                           ("unknown reassign id", critic_result(reassign=("no-such-unit",), stop=False,
                                                                 notes={"no-such-unit": "x"})),
                           ("reassign id with no note", critic_result(reassign=(app,), stop=False)),
                           ("resolved lead with no note", critic_result(leads=("fp-one",), stop=True))):
            with self.subTest(case=case):
                self.check_invalid(run, c01, data)

    def test_o18_verifier_and_recheck_shape(self) -> None:
        run, h01, result, _ = self.hunted()
        run.finish(h01, result, promote=True)
        v01 = run.brief("verifier", "--fingerprint", "fp-one")
        record = needs_validation("fp-one", "Join may escape")
        self.check_invalid(run, v01, dict(verifier_result(record, v01, "src/upload.py"), decision="confirmed"))
        run.finish(v01, verifier_result(record, v01, "src/upload.py"))
        r01 = run.brief("recheck", "--fingerprint", "fp-one")
        other = dict(record, fingerprint="fp-other")
        self.check_invalid(run, r01, {"decision": "replace", "reason": "Another root cause.", "record": other})


class PromoteTests(CheckCase):

    def test_o19_promote_a_local_artifact(self) -> None:
        run, h01, result, _ = self.hunted()
        run.write_result(h01, result)
        payload = run.ok("promote", "--agent", h01)
        artifact = run.path / "agents" / h01 / "artifacts" / "out" / "r.txt"
        self.assertEqual(artifact.read_bytes(), b"0123456789")
        self.assertTrue(any(str(p).endswith("agents/h01/artifacts/out/r.txt") for p in payload.get("promoted") or []),
                        payload)

    def test_o20_promote_refuses_unsafe_files(self) -> None:
        run = self.start()
        (app,) = run.add(entry("app"))
        h01 = run.hunter(app)
        scratch, outside = run.scratch(h01), self.root / "outside"
        put(outside / "secret.txt", "secret")
        put(outside / "hard.txt", "hard")
        put(outside / "dir" / "f.txt", "f")
        os.symlink(outside / "secret.txt", scratch / "link.txt")
        os.link(outside / "hard.txt", scratch / "hard.txt")
        os.mkfifo(scratch / "pipe")
        (scratch / "folder").mkdir()
        put(scratch / "big.bin", b"x" * (300 * 1024))
        for i in range(1, 6):
            put(scratch / f"p{i}.bin", b"y" * 250_000)
        os.symlink(outside / "dir", scratch / "via")
        put(scratch / "pre.txt", "NEW")
        put(run.path / "agents" / h01 / "artifacts" / "pre.txt", "OLD")
        bad = ["link.txt", "hard.txt", "pipe", "folder", "big.bin", "via/f.txt", "pre.txt"]
        totals = [f"p{i}.bin" for i in range(1, 6)]
        checks = [check_of(h01, ["src/app.py"], artifact=rel) for rel in bad + totals]
        run.write_result(h01, hunter_result([unit_out(app, "covered", checks)]))
        before = snapshot(self.root)
        payload = run.ok("promote", "--agent", h01)
        rejected = payload.get("rejected") or []
        promoted = [str(p) for p in payload.get("promoted") or []]
        for rel in bad:
            with self.subTest(file=rel):
                found = [r for r in rejected if str(r.get("path", "")).endswith(rel)]
                self.assertEqual(len(found), 1, payload)
                self.assertTrue(found[0].get("reason"), found)
                self.assertFalse(any(p.endswith(rel) for p in promoted), payload)
        with self.subTest(case="1 MiB agent total"):
            self.assertEqual(sum(any(p.endswith(rel) for p in promoted) for rel in totals), 4, payload)
            self.assertEqual(sum(any(str(r.get("path", "")).endswith(rel) for r in rejected) for rel in totals), 1,
                             payload)
        artifacts = run.path / "agents" / h01 / "artifacts"
        self.assertEqual((artifacts / "pre.txt").read_text(), "OLD")
        after = snapshot(self.root)
        prefix = str(artifacts.relative_to(self.root)) + os.sep
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        self.assertEqual([k for k in changed if not k.startswith(prefix)], [], "written outside the artifacts folder")
        self.assertEqual((outside / "secret.txt").read_text(), "secret")


if __name__ == "__main__":
    unittest.main()
