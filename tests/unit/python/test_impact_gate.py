#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

MAP = {"source": "s", "paths": {"a.py": ["tests/unit/python/test_a.py"], "b.js": ["tests/unit/node/b.test.mjs"],
                                "both.txt": ["tests/unit/node/a.test.mjs", "tests/unit/python/test_b.py"],
                                "café.py": ["tests/unit/python/test_a.py"]}}
BOTH = ["a.test.mjs", "b.test.mjs"]
PY_STUB = "import os\nopen(os.path.join(os.environ['MARKS'], 'py'), 'w').write(repr(os.environ.get('DEVLOOP_TEST_MODULES')))\n"


def node_stub(name: str) -> str:
    return f"import {{ writeFileSync }} from 'node:fs';\nwriteFileSync(process.env.MARKS + '/{name}', '');\n"


class ImpactGateTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.repo, self.marks = base / "repo", base / "marks"
        self.marks.mkdir()
        files = {"tests/unit/python/test_dev_loop.py": PY_STUB, "tests/unit/python/test_a.py": "",
                 "tests/unit/python/test_b.py": "", "tests/unit/node/a.test.mjs": node_stub("a.test.mjs"),
                 "tests/unit/node/b.test.mjs": node_stub("b.test.mjs"), "tests/impact-map.json": json.dumps(MAP),
                 ".gitignore": "node_modules/\n", "a.py": "a\n", "b.js": "b\n", "both.txt": "c\n", "x.txt": "x\n",
                 "café.py": "c\n", "reference/cli/status.md": "s\n"}
        for script in ("gate.sh", "temp_leak.sh", "suite_lock.sh", "impact_map.py"):
            files[f"scripts/{script}"] = (ROOT / "scripts" / script).read_text()
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        (self.repo / "node_modules" / "playwright").mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "core.quotePath", "true")
        self.commit()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "c")

    def gate(self, change: str | None, scope: bool = True, env: dict[str, str] | None = None) -> dict:
        if change:
            with open(self.repo / change, "a") as handle:
                handle.write("changed\n")
        for mark in self.marks.iterdir():
            mark.unlink()
        run_env = {k: v for k, v in os.environ.items() if k not in ("DEVLOOP_REPORT_DIR", "GATE_SCOPE")}
        run_env.update(MARKS=str(self.marks), **(env or {}), **({"GATE_SCOPE": "changed"} if scope else {}))
        run = subprocess.run(["bash", "scripts/gate.sh"], cwd=self.repo, env=run_env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        if change:
            self.git("checkout", "-q", "--", change)
        py = self.marks / "py"
        return {"py": py.read_text() if py.exists() else None,
                "node": sorted(m.name for m in self.marks.iterdir() if m.name != "py")}

    def test_changed_scope_wires_the_selection_slow(self) -> None:
        rows = (("a.py", None, "'test_a'", []), ("b.js", None, None, ["b.test.mjs"]),
                ("both.txt", None, "'test_b'", ["a.test.mjs"]), ("scripts/gate.sh", None, "None", BOTH),
                ("x.txt", None, None, BOTH), ("reference/cli/status.md", None, "None", BOTH),
                ("a.py", {"DEVLOOP_TEST_MODULES": "bogus"}, "'test_a'", []),
                ("x.txt", {"DEVLOOP_TEST_MODULES": "bogus"}, None, BOTH))
        for change, env, py, node in rows:
            with self.subTest(change=change, env=env):
                self.assertEqual(self.gate(change, env=env), {"py": py, "node": node})

    def test_full_gate_unchanged_slow(self) -> None:
        for change in ("a.py", None):
            with self.subTest(change=change):
                self.assertEqual(self.gate(change, scope=False), {"py": "None", "node": BOTH})

    def test_fallbacks_slow(self) -> None:
        for gone in ("tests/impact-map.json", "scripts/impact_map.py"):
            (self.repo / gone).unlink()
            self.commit()
            for change, py in (("reference/cli/status.md", "None"), ("x.txt", None)):
                with self.subTest(gone=gone, change=change):
                    self.assertEqual(self.gate(change), {"py": py, "node": BOTH})

    def test_a_broken_select_runs_both_full_suites(self) -> None:
        (self.repo / "scripts" / "impact_map.py").write_text("import sys; sys.exit(3)\n")
        self.commit()
        self.assertEqual(self.gate("x.txt"), {"py": "None", "node": BOTH})

    def test_a_rename_runs_the_modules_of_the_old_path(self) -> None:
        self.git("mv", "a.py", "c.py")
        self.assertIn("test_a", self.gate(None)["py"] or "")

    def test_a_non_ascii_tracked_path_reaches_the_map_slow(self) -> None:
        self.assertEqual(self.gate("café.py"), {"py": "'test_a'", "node": []})

    def test_a_non_ascii_untracked_path_runs_the_skill_suite_slow(self) -> None:
        (self.repo / "skill").mkdir()
        (self.repo / "skill" / "é.md").write_text("e\n")
        self.assertEqual(self.gate(None)["py"], "None")

    def test_a_non_ascii_path_in_the_fallback_runs_the_skill_suite_slow(self) -> None:
        (self.repo / "scripts" / "impact_map.py").unlink()
        self.commit()
        (self.repo / "skill").mkdir()
        (self.repo / "skill" / "é.md").write_text("e\n")
        self.assertEqual(self.gate(None)["py"], "None")

    def test_no_change_runs_nothing(self) -> None:
        self.assertEqual(self.gate(None), {"py": None, "node": []})


if __name__ == "__main__":
    unittest.main()
