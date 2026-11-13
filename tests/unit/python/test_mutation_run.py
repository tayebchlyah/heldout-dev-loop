#!/usr/bin/env python3
"""Tests for `devloop.py mutation --run`: adapter call, output check, statuses, report."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board_fixtures import slow  # noqa: E402
from mutation_fixtures import DEVLOOP, adapter_script, commit, make_repo, run_mutation  # noqa: E402

ENTRY = {"path": "app/", "command": "./adapt", "critical": ["core/**"]}
TEN = "".join(f"l{n}\n" for n in range(1, 11))


def output(mutants: list[dict], units: list[str], not_covered: list[dict] | None = None, **extra: object) -> str:
    return json.dumps({"units": units, "mutants": mutants, "not_covered": not_covered or [], **extra})


def mutant(unit: str, file: str, line: int, status: str, diff: str | None = None, **extra: object) -> dict:
    return {"unit": unit, "file": file, "line": line, "status": status, "diff": diff, **extra}


class MutationRun(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.count = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_case(self, entries: list[dict], base: dict[str, str], change: dict[str, str | None], *args: str,
                 config: dict | None = None, **options: float):
        self.count += 1
        folder = self.root / f"case{self.count}"
        folder.mkdir()
        data = {"version": "0.0.0", "mutation": entries, **(config or {})}
        repo = make_repo(folder, {"dev-loop.json": json.dumps(data), **base})
        before = commit(repo, change)
        return run_mutation(repo, "--run", "--head", "HEAD", "--base", before, *args, **options)

    def record(self, name: str = "record") -> Path:
        self.count += 1
        return self.root / f"{name}{self.count}"

    @slow
    def test_request_and_cwd(self) -> None:
        record, cwd = self.record(), self.record("cwd")
        script = adapter_script(record, output([], [], [{"file": "core/a.py", "start": 3, "end": 4, "reason": "r"}]))
        script = script.replace("#!/bin/sh\n", f"#!/bin/sh\npwd > {shlex.quote(str(cwd))}\n", 1)
        code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": TEN},
                                     {"app/core/a.py": TEN.replace("l3\n", "x\n").replace("l4\n", "y\n")})
        self.assertEqual((code, out["status"]), (0, "pass"))
        self.assertEqual(json.loads(record.read_text()), {
            "protocol": 1, "run": True, "base": out["base"], "head": out["head"],
            "files": {"core/a.py": [[3, 4]]}, "only": None})
        self.assertTrue(cwd.read_text().strip().endswith("/app"))

    def test_no_selected_file(self) -> None:
        record = self.record()
        code, out, _ = self.run_case([ENTRY], {"app/adapt": adapter_script(record, "{}"), "app/web/b.py": "a\n"},
                                     {"app/web/b.py": "b\n"})
        self.assertFalse(record.exists())
        self.assertEqual((code, out["status"]), (0, "pass"))
        self.assertEqual(out["entries"], [{"path": "app/", "status": "pass", "cause": None, "mutants": 0,
                                           "need_tests": 0}])

    def test_golden_report(self) -> None:
        mutants = [
            mutant("u3", "core/a.py", 7, "survived",
                   '-        raise ValueError("bad")\n+        raise ValueError("XXbadXX")'),
            mutant("u1", "core/a.py", 4, "no-tests"),
            mutant("u2", "core/a.py", 5, "inconclusive", detail="timeout"),
            mutant("u1", "core/a.py", 3, "survived", "-    return a > b\n+    return a >= b"),
            mutant("u2", "core/a.py", 6, "inconclusive"),
        ]
        not_covered = [{"file": "core/a.py", "start": 8, "end": 9, "reason": "no mutable code"}]
        script = adapter_script(self.record(), output(mutants, ["u1", "u2", "u3"], not_covered, x=1))
        changed = "".join(f"x{n}\n" for n in range(1, 11))
        code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": TEN, "app/web/b.py": "a\n",
                                               "api/y.go": "a\n"},
                                     {"app/core/a.py": changed, "app/web/b.py": "b\n"},
                                     config={"stacks": [{"path": "api/", "preset": "go"}]})
        self.assertEqual((code, out["status"]), (0, "survivors"))
        self.assertEqual(out["entries"], [{"path": "app/", "status": "survivors", "cause": None, "mutants": 5,
                                           "need_tests": 1}])
        report = out["report"].replace(out["base"], "<base>").replace(out["head"], "<head>")
        self.assertEqual(report + "\n", (HERE / "fixtures" / "mutation" / "report.txt").read_text())

    @slow
    def test_classification_through_the_report(self) -> None:
        rows = {
            "a.py": [("py", '-        raise ValueError("bad")\n+        raise ValueError("XXbadXX")')],
            "b.go": [("go-message", '-\treturn fmt.Errorf("bad")\n+\treturn fmt.Errorf("")'),
                     ("go-decision", '-\tif s == "x" {\n+\tif s == "" {')],
            "c.ts": [("ts-message", '-  throw new Error("bad")\n+  throw new Error("")'),
                     ("ts-decision", '-  if (k === "id") {\n+  if (k === "") {')],
            "d.rb": [("rb", '-  raise "bad"\n+  raise ""'), ("rb-null", None)],
        }
        mutants = [mutant(unit, file, 1, "survived", diff) for file, cases in rows.items() for unit, diff in cases]
        script = adapter_script(self.record(), output(mutants, [m["unit"] for m in mutants]))
        entry = {"path": ".", "command": "./adapt", "critical": ["*.*"]}
        code, out, _ = self.run_case([entry], {"adapt": script, **{f: "a\n" for f in rows}}, {f: "b\n" for f in rows})
        self.assertEqual((code, out["status"], out["entries"][0]["need_tests"]), (0, "survivors", 4))
        report = out["report"]
        start = report.index(". message-only survivors, to Deferred: 3\n")
        for unit, file in (("py", "a.py"), ("go-message", "b.go"), ("ts-message", "c.ts")):
            self.assertIn(f"\n  {unit} {file}:1\n", report[start:])
        for unit, file in (("go-decision", "b.go"), ("ts-decision", "c.ts"), ("rb", "d.rb"), ("rb-null", "d.rb")):
            self.assertIn(f"\n  {unit}\n    {file}:1 survived\n", report[:start])
        only_message = [mutant("py", "a.py", 1, "survived", rows["a.py"][0][1])]
        script = adapter_script(self.record(), output(only_message, ["py"]))
        code, out, _ = self.run_case([{**entry, "critical": ["a.py"]}], {"adapt": script, "a.py": "a\n"},
                                     {"a.py": "b\n"})
        self.assertEqual((code, out["status"]), (0, "pass"))

    @slow
    def test_exit_codes(self) -> None:
        cases = [
            ("exit 2", adapter_script(self.record(), "{}", 2, "x"), "setup", "x"),
            ("exit 127", adapter_script(self.record(), "{}", 127), "setup", "exit 127"),
            ("exit 126", adapter_script(self.record(), "{}", 126), "setup", "exit 126"),
            ("exit 3", adapter_script(self.record(), "{}", 3), "failed", "exit 3"),
            ("signal", "#!/bin/sh\ncat > /dev/null\nkill -9 $$\n", "failed", "exit -9"),
            ("blank stderr tail", adapter_script(self.record(), "{}", 3, "boom\n\n\n"), "failed", "boom"),
        ]
        for name, script, status, cause in cases:
            with self.subTest(name):
                code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": "a\n"},
                                             {"app/core/a.py": "b\n"})
                self.assertEqual((code, out["status"], out["entries"][0]["status"]), (1, status, status))
                self.assertEqual(out["entries"][0]["cause"], cause)

    @slow
    def test_output_faults(self) -> None:
        good = mutant("u1", "core/a.py", 1, "survived", "-a\n+b")
        gap = {"file": "core/b.py", "start": 1, "end": 1, "reason": "r"}

        def bad(field: str, value: object) -> str:
            return output([{**good, field: value}], ["u1"], [gap])

        no_unit = {k: v for k, v in good.items() if k != "unit"}
        cases = [
            ("not json", "not json", "JSON"),
            ("no unit", output([no_unit], ["u1"], [gap]), "unit"),
            ("status lived", bad("status", "lived"), "lived"),
            ("line 0", bad("line", 0), "line"),
            ("line true", bad("line", True), "line"),
            ("line 1.0", bad("line", 1.0), "line"),
            ("file outside", bad("file", "../x.py"), "../x.py"),
            ("unit not in units", bad("unit", "u9"), "u9"),
            ("start after end", output([good], ["u1"], [{**gap, "start": 5, "end": 4}]), "start"),
            ("file not named", output([good], ["u1"]), "core/b.py"),
            ("two objects", output([good], ["u1"], [gap]) + " {}", "JSON"),
            ("array", json.dumps([json.loads(output([good], ["u1"], [gap]))]), "JSON"),
            ("unit with newline", output([{**good, "unit": "u\n1"}], ["u\n1"], [gap]), "unit"),
            ("diff row", bad("diff", "-a\nx"), "diff"),
            ("empty unit in units", output([good], ["u1", ""], [gap]), "units"),
        ]
        for name, text, word in cases:
            with self.subTest(name):
                script = adapter_script(self.record(), text)
                code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": "a\n",
                                                       "app/core/b.py": "a\n"},
                                             {"app/core/a.py": "b\n", "app/core/b.py": "b\n"})
                self.assertEqual((code, out["entries"][0]["status"]), (1, "setup"))
                self.assertIn(word, out["entries"][0]["cause"])

    def test_large_stdin_unread(self) -> None:
        lines = "".join(f"l{n}\n" for n in range(1, 12001))
        changed = "".join(f"x{n}\n" if n % 2 else f"l{n}\n" for n in range(1, 12001))
        text = output([], [], [{"file": "core/a.py", "start": 1, "end": 1, "reason": "r"}])
        script = f"#!/bin/sh\nprintf '%s' {shlex.quote(text)}\n"
        code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": lines},
                                     {"app/core/a.py": changed}, timeout=30)
        self.assertEqual((code, out["status"]), (0, "pass"))
        self.assertIn("app/core/a.py:1-1 r", out["report"])

    def test_kit_stderr_closed(self) -> None:
        script = "#!/bin/sh\ncat > /dev/null\nyes | head -c 300000 >&2\nprintf x\n"
        repo = make_repo(self.root / "closed", {"dev-loop.json": json.dumps({"mutation": [ENTRY]}),
                                                "app/adapt": script, "app/core/a.py": "a\n"})
        before = commit(repo, {"app/core/a.py": "b\n"})
        done = subprocess.run(["sh", "-c", 'exec "$@" 2>&-', "sh", sys.executable, str(DEVLOOP), "mutation", "--repo",
                               str(repo), "--run", "--head", "HEAD", "--base", before],
                              capture_output=True, text=True, timeout=60)
        out = json.loads(done.stdout)
        self.assertEqual((done.returncode, out["status"]), (1, "setup"))
        self.assertIn("JSON", out["entries"][0]["cause"])

    @slow
    def test_two_entries_one_fails(self) -> None:
        a, b = self.record(), self.record()
        entries = [{"path": "a/", "command": "./adapt", "critical": ["**"]},
                   {"path": "b/", "command": "./adapt", "critical": ["**"]}]
        survivor = output([mutant("u1", "x.py", 1, "survived", "-a\n+b")], ["u1"])
        code, out, _ = self.run_case(entries, {"a/adapt": adapter_script(a, "{}", 3, "broke"),
                                               "b/adapt": adapter_script(b, survivor), "a/x.py": "a\n",
                                               "b/x.py": "a\n"}, {"a/x.py": "b\n", "b/x.py": "b\n"})
        self.assertTrue(a.exists() and b.exists())
        self.assertEqual((code, out["status"]), (1, "failed"))
        self.assertEqual([e["status"] for e in out["entries"]], ["failed", "survivors"])
        self.assertIn("entry a/: ./adapt failed\na/ mutants run: 0\n", out["report"])
        self.assertIn("\na/ failed: broke\n", out["report"])
        self.assertIn("entry b/: ./adapt survivors\nb/ mutants run: 1\n", out["report"])

    @slow
    def test_control_characters(self) -> None:
        mutants = [mutant("u1", "core/a.py", 3, "inconclusive", detail="time\nout")]
        not_covered = [{"file": "core/a.py", "start": 4, "end": 4, "reason": "no\tcode"}]
        script = adapter_script(self.record(), output(mutants, ["u1"], not_covered))
        code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": TEN},
                                     {"app/core/a.py": TEN.replace("l3\n", "x\n").replace("l4\n", "y\n")})
        self.assertEqual(code, 0)
        self.assertIn("\n  time out: 1\n", out["report"])
        self.assertIn("\n  app/core/a.py:4-4 no code\n", out["report"])
        script = adapter_script(self.record(), "{}", 3, "e" * 300)
        code, out, _ = self.run_case([ENTRY], {"app/adapt": script, "app/core/a.py": TEN},
                                     {"app/core/a.py": TEN.replace("l3\n", "x\n")})
        self.assertEqual((code, out["entries"][0]["cause"]), (1, "e" * 200))
        self.assertIn(f"\napp/ failed: {'e' * 200}\n", out["report"])


if __name__ == "__main__":
    unittest.main()
