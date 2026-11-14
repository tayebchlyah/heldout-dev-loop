"""`gate --summary` `ratchet.record`: the local record a green full gate on a clean tree writes."""

from __future__ import annotations

import json
import os

from ratchet_fixtures import BASE, RatchetRepo
from test_devloop import git


class RecordTests(RatchetRepo):
    def files(self) -> set[str]:
        folder = self.store("gate")
        return {p.name for p in folder.glob("*.json")} if folder.is_dir() else set()

    def test_base_run_writes_the_record(self) -> None:
        code, out = self.gate(BASE)
        path = self.store("gate") / f"{self.tree('HEAD')}.json"
        self.assertEqual((code, out["ratchet"]["state"], out["ratchet"]["record"]), (0, "no-baseline", str(path)))
        record = json.loads(path.read_text())
        self.assertEqual((record["times"], record["tree"]), (BASE, self.tree("HEAD")))
        self.assertEqual(record["head"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertRegex(record["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.branch()
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["baseline"]["tree"], out["ratchet"]["record"]),
                         (1, self.tree("main"), None))

    def test_record_writes(self) -> None:
        rows = {
            "scope": lambda: self.gate(BASE, env={"GATE_SCOPE": "changed"}),
            "dirty": lambda: ((self.repo / "new.txt").write_text("x"), self.gate(BASE))[1],
            "red gate": lambda: self.gate(BASE, exit=1),
        }
        for name, run in rows.items():
            with self.subTest(name):
                before = self.files()
                code, out = run()
                self.assertEqual((out["ratchet"]["record"], self.files()), (None, before))
                (self.repo / "new.txt").unlink(missing_ok=True)
        self.main_config({"test_time": False})
        before = self.files()
        code, out = self.gate(BASE)
        self.assertEqual((code, out["ratchet"]["record"], self.files()), (0, None, before))
        self.main_config({})
        self.seed("gate", BASE, "main")
        self.branch()
        before = self.files()
        code, out = self.gate({**BASE, "n_new": 3.0})
        self.assertEqual((code, out["ratchet"]["record"], self.files()), (1, None, before))

    def test_keeps_fifty_records(self) -> None:
        folder = self.store("gate")
        folder.mkdir(parents=True)
        for n in range(1, 51):
            path = folder / f"{n:040x}.json"
            path.write_text("{}")
            os.utime(path, (n, n))
        code, out = self.gate(BASE)
        names = self.files()
        self.assertEqual((code, len(names)), (0, 50))
        self.assertNotIn(f"{1:040x}.json", names)
        self.assertIn(f"{self.tree('HEAD')}.json", names)

    def test_rerun_replaces_the_record(self) -> None:
        self.gate(BASE)
        code, out = self.gate({**BASE, "a": 1.2})
        self.assertEqual((code, self.files()), (0, {f"{self.tree('HEAD')}.json"}))
        self.assertEqual(json.loads((self.store("gate") / f"{self.tree('HEAD')}.json").read_text())["times"]["a"], 1.2)

    def test_record_holds_normalized_times(self) -> None:
        self.seed("gate", BASE, "main")
        self.branch()
        code, out = self.gate({i: t * 2 for i, t in BASE.items()})
        self.assertEqual((code, out["ratchet"]["factor"]), (0, 2.0))
        record = json.loads((self.store("gate") / f"{self.tree('HEAD')}.json").read_text())
        self.assertEqual(record["times"], BASE)
