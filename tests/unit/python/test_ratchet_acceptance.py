"""`acceptance` `ratchet`: a grown held-out test is red, a new slow one is an Attention item."""

from __future__ import annotations

from ratchet_fixtures import ACCEPTANCE_BASE, RatchetRepo

COMMANDS = {"gate": "sh run.sh", "acceptance": "sh run.sh", "acceptance_failed": "sh run.sh"}


class AcceptanceRatchetTests(RatchetRepo):
    def on_branch(self) -> None:
        self.seed("acceptance", ACCEPTANCE_BASE, "main")
        self.branch()

    def test_base_run_writes_the_record(self) -> None:
        code, out = self.acceptance(ACCEPTANCE_BASE)
        path = self.store("acceptance") / f"{self.tree('HEAD')}.json"
        self.assertEqual((code, out["ratchet"]["state"], out["ratchet"]["record"]), (0, "no-baseline", str(path)))
        path.unlink()
        code, out = self.acceptance({**ACCEPTANCE_BASE, "x": 61.0})
        self.assertEqual((code, out["ratchet"]["state"], out["ratchet"]["ok"]), (0, "no-baseline", True))
        self.assertEqual(out["ratchet"]["attention"], [{"id": "x", "seconds": 61.0, "rule": "max", "limit": 60}])

    def test_grown_flow(self) -> None:
        self.on_branch()
        code, out = self.acceptance({**ACCEPTANCE_BASE, "m": 61.0})
        ratchet = out["ratchet"]
        self.assertEqual((code, ratchet["ok"], out["guard"]["verdict"], ratchet["record"]), (1, False, "green", None))
        self.assertEqual(ratchet["red"], [{"id": "m", "seconds": 61.0, "baseline": 40.0, "rule": "grown", "limit": 60.0}])
        self.assertEqual(ratchet["attention"], [{"id": "m", "seconds": 61.0, "rule": "max", "limit": 60}])
        code, out = self.acceptance({**ACCEPTANCE_BASE, "m": 55.0})
        self.assertEqual((code, out["ratchet"]["red"]), (0, []))

    def test_waived_grown_flow(self) -> None:
        self.on_branch()
        self.commit("x", trailer="flow does more work")
        code, out = self.acceptance({**ACCEPTANCE_BASE, "m": 61.0})
        ratchet = out["ratchet"]
        self.assertEqual((code, ratchet["ok"], ratchet["red"]), (0, True, []))
        self.assertEqual(ratchet["waived"],
                         [{"id": "m", "seconds": 61.0, "baseline": 40.0, "reason": "flow does more work"}])
        self.assertEqual((ratchet["growth"]["waived"], ratchet["growth"]["limit"]), (None, None))

    def test_new_flows_are_attention(self) -> None:
        self.on_branch()
        rows = [("n_new", 12.0, "unmarked", 10), ("n_slow_new", 31.0, "marked", 30), ("n_new", 61.0, "max", 60)]
        for name, seconds, rule, limit in rows:
            with self.subTest(name=name, seconds=seconds):
                code, out = self.acceptance({**ACCEPTANCE_BASE, name: seconds})
                self.assertEqual((code, out["ratchet"]["ok"]), (0, True))
                self.assertEqual(out["ratchet"]["attention"],
                                 [{"id": name, "seconds": seconds, "rule": rule, "limit": limit}])

    def test_skipped_at_baseline_is_not_new(self) -> None:
        self.seed("acceptance", ACCEPTANCE_BASE, "main", skipped=["d_one", "d_two"])
        self.branch()
        code, out = self.acceptance({**ACCEPTANCE_BASE, "d_one": 12.0, "d_two": 61.0})
        self.assertEqual((code, out["ratchet"]["ok"], out["ratchet"]["red"]), (0, True, []))
        self.assertEqual(out["ratchet"]["attention"], [{"id": "d_two", "seconds": 61.0, "rule": "max", "limit": 60}])

    def test_skipped_runs(self) -> None:
        self.main_config({"commands": COMMANDS})
        self.on_branch()
        before = set(self.store("acceptance").glob("*.json"))
        code, out = self.acceptance({**ACCEPTANCE_BASE, "m": 61.0}, "--failed")
        self.assertEqual((out["ratchet"]["state"], out["ratchet"]["reason"]), ("skipped", "failed-mode"))
        code, out = self.acceptance({**ACCEPTANCE_BASE, "m": 61.0}, failing=("a",), exit=1)
        self.assertEqual((code, out["ratchet"]["state"], out["ratchet"]["reason"]), (1, "skipped", "not-green"))
        self.assertEqual(set(self.store("acceptance").glob("*.json")), before)
