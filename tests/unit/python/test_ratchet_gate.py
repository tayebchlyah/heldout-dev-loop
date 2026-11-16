"""`gate --summary` `ratchet`: the read side and the gate rules, over seeded records."""

from __future__ import annotations

from ratchet_fixtures import BASE, RatchetRepo
from test_devloop import git


def item(id: str, seconds: float, rule: str, limit: float, baseline: float | None = None) -> dict:
    return {"id": id, "seconds": seconds, "baseline": baseline, "rule": rule, "limit": limit}


class GateRatchetTests(RatchetRepo):
    def on_branch(self, times: dict = BASE) -> None:
        self.seed("gate", times, "main")
        self.branch()

    def test_new_test_limits(self) -> None:
        self.on_branch()
        code, out = self.gate({**BASE, "n_new": 3.0})
        ratchet = out["ratchet"]
        self.assertEqual((code, out["exit"], ratchet["ok"], ratchet["state"]), (1, 0, False, "checked"))
        self.assertEqual(ratchet["red"], [item("n_new", 3.0, "unmarked", 2)])
        self.assertEqual((ratchet["growth"]["share"], ratchet["growth"]["ok"]), (0.02, True))
        code, out = self.gate({**BASE, "n_slow_new": 3.0})
        self.assertEqual((code, out["ratchet"]["red"], out["ratchet"]["ok"]), (0, [], True))
        code, out = self.gate({**BASE, "n_slow_new": 11.0})
        self.assertEqual((code, out["ratchet"]["red"]), (1, [item("n_slow_new", 11.0, "marked", 10)]))
        code, out = self.gate({**BASE, "n_new": 31.0})
        self.assertEqual(out["ratchet"]["red"], [item("n_new", 31.0, "max", 30)])

    def test_load_factor(self) -> None:
        self.on_branch()
        code, out = self.gate({**{k: v * 2 for k, v in BASE.items()}, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["factor"], out["ratchet"]["ok"]), (0, 2.0, True))
        nine = {**{c: 1.0 if c in "abcdefghi" else 0.05 for c in "abcdefghijkl"}}
        self.seed("gate", nine, "main")
        code, out = self.gate({**{k: v * 2 for k, v in nine.items()}, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["factor"]), (1, 1.0))

    def test_grown_test(self) -> None:
        self.on_branch()
        code, out = self.gate({**BASE, "m": 61.0})
        self.assertEqual((code, out["ratchet"]["red"]), (1, [item("m", 61.0, "max", 30, 40.0)]))
        code, out = self.gate({**BASE, "m": 55.0})
        self.assertEqual(out["ratchet"]["red"], [])

    def test_growth_and_waiver(self) -> None:
        self.on_branch()
        new = {**BASE, "n1_slow": 2.0, "n2_slow": 2.0, "n3_slow": 2.0}
        code, out = self.gate(new)
        growth = out["ratchet"]["growth"]
        self.assertEqual((code, out["ratchet"]["red"], out["ratchet"]["ok"]), (1, [], False))
        self.assertEqual((growth["share"], growth["limit"], growth["ok"], growth["waived"]), (0.039, 0.03, False, None))
        self.commit("x", trailer="new subsystem")
        code, out = self.gate(new)
        self.assertEqual((code, out["ratchet"]["growth"]["waived"], out["ratchet"]["ok"]), (0, "new subsystem", True))
        self.seed("gate", {k: 0.0 for k in BASE}, "main")
        code, out = self.gate({c: 1.0 for c in "abcdefghijkl"})
        self.assertEqual((code, out["ratchet"]["growth"]["share"], out["ratchet"]["ok"]), (0, 0.0, True))

    def test_config_at_main_ref(self) -> None:
        self.on_branch()
        branch = '{"version": "3.0.0", "commands": {"gate": "sh run.sh"}, "test_time": {"gate": {"unmarked": 5}}}'
        (self.repo / "dev-loop.json").write_text(branch)
        git(self.repo, "commit", "-q", "-am", "branch config")
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["red"]), (1, [item("n_new", 3.0, "unmarked", 2)]))
        main = '{"version": "3.0.0", "commands": {"gate": "sh run.sh"}, "test_time": %s}'
        rows = [('{"gate": {"max": -1}}', "test_time.gate.max"), ('{"gate": {"max": true}}', "test_time.gate.max"),
                ('{"gate": {"max": "2"}}', "test_time.gate.max"), ('{"gate": {"max": 0}}', "test_time.gate.max"),
                ('{"gate": {"max": NaN}}', "test_time.gate.max"),
                ('{"acceptance": {"max": Infinity}}', "test_time.acceptance.max"),
                ('{"gate": {"unmarkd": 2}}', "test_time.gate.unmarkd"), ("[]", "test_time")]
        for value, key in rows:
            with self.subTest(value=value):
                self.main_config(raw=main % value)
                code, out = self.gate(BASE)
                ratchet = out["ratchet"]
                self.assertEqual((code, ratchet["state"], ratchet["ok"]), (1, "error", False))
                self.assertIn(key, ratchet["reason"])
        self.main_config(raw=main % "false")
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["state"], out["ratchet"]["reason"]), (0, "skipped", "off"))
        self.main_config(raw="{")
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual(out["ratchet"]["red"], [item("n_new", 3.0, "unmarked", 2)])

    def test_states(self) -> None:
        code, out = self.gate(None)
        self.assertEqual((out["ratchet"]["state"], out["ratchet"]["reason"]), ("skipped", "no-junit"))
        code, out = self.gate({"x": 31.0, "m": 40.0, "a": 1.0})
        ratchet = out["ratchet"]
        self.assertEqual((code, ratchet["state"], ratchet["ok"], ratchet["red"]), (0, "no-baseline", True, []))
        self.assertEqual((ratchet["factor"], ratchet["baseline"], ratchet["reason"]), (None, None, None))
        self.assertEqual(ratchet["attention"], [{"id": "m", "seconds": 40.0, "rule": "max", "limit": 30},
                                                {"id": "x", "seconds": 31.0, "rule": "max", "limit": 30}])
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        git(self.repo, "branch", "-m", "main", "trunk")
        for exit in (0, 1):
            code, out = self.gate({"x": 31.0}, exit=exit)
            self.assertEqual((code, out["exit"]), (exit, exit))
            self.assertEqual((out["ratchet"]["state"], out["ratchet"]["reason"]), ("skipped", "no-main-ref"))

    def test_lookup_walks_ancestors(self) -> None:
        self.seed("gate", BASE)
        m_tree = self.tree("main")
        (self.repo / "two.txt").write_text("M2\n")
        git(self.repo, "add", "two.txt")
        git(self.repo, "commit", "-q", "-m", "M2")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.branch()
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual((out["ratchet"]["state"], out["ratchet"]["baseline"]["tree"]), ("checked", m_tree))
        (self.store("gate") / f"{self.tree('main')}.json").write_text("{")
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual(out["ratchet"]["baseline"]["tree"], m_tree)

    def test_junit_reading(self) -> None:
        self.on_branch()
        skipped = "<testsuite><testcase name='s_new' time='50'><skipped/></testcase>" \
                  "<testcase name='b_new' time='x'/><testcase name='n_new' time='1'/></testsuite>"
        retry = "<testsuite><testcase name='n_new' time='3'/></testsuite>"
        code, out = self.gate(BASE, raw=(skipped, retry))
        self.assertEqual(out["ratchet"]["red"], [item("n_new", 3.0, "unmarked", 2)])

    def test_skipped_at_baseline_is_not_new(self) -> None:
        self.seed("gate", BASE, "main", skipped=["d_one", "d_two"])
        self.branch()
        code, out = self.gate({**BASE, "d_one": 23.7, "d_two": 31.0, "n_new": 3.0})
        ratchet = out["ratchet"]
        self.assertEqual((code, ratchet["state"], ratchet["ok"]), (1, "checked", False))
        self.assertEqual(ratchet["red"], [item("n_new", 3.0, "unmarked", 2)])
        self.assertEqual(ratchet["attention"], [{"id": "d_two", "seconds": 31.0, "rule": "max", "limit": 30}])
        self.assertEqual((ratchet["growth"]["share"], ratchet["growth"]["ok"]), (0.02, True))

    def test_skipped_in_run_leaves_the_baseline_sum(self) -> None:
        self.seed("gate", {**BASE, "d_one": 40.0}, "main")
        self.branch()
        run = {**BASE, "n1_slow": 2.0, "n2_slow": 2.0, "n3_slow": 2.0}
        code, out = self.gate(run, raw=("<testsuite><testcase name='d_one' time='0'><skipped/></testcase></testsuite>",))
        growth = out["ratchet"]["growth"]
        self.assertEqual((code, growth["share"], growth["ok"]), (1, 0.039, False))

    def test_bad_skipped_passes_the_record_over(self) -> None:
        self.branch()
        for bad in ("d_one", [1]):
            with self.subTest(skipped=bad):
                self.seed("gate", BASE, "main", skipped=bad)
                code, out = self.gate({**BASE, "d_one": 23.7})
                self.assertEqual(out["ratchet"]["state"], "no-baseline")
