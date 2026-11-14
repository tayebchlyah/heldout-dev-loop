#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import impact_map  # noqa: E402

TREE = ("tests/unit/python/test_a.py", "tests/unit/python/test_b.py", "tests/unit/node/x.test.mjs")
GOOD = json.dumps({"source": "s", "paths": {"a.py": ["tests/unit/python/test_a.py"]}})


def cli(stdin: str, map_text: str | None = GOOD) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts" / "impact_map.py", root / "scripts" / "impact_map.py")
        for path in TREE:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("")
        if map_text is not None:
            (root / impact_map.MAP).write_text(map_text)
        return subprocess.run([sys.executable, str(root / "scripts" / "impact_map.py"), "select"], input=stdin,
                              capture_output=True, text=True, timeout=60)


def every(_path: str) -> bool:
    return True


class SelectTests(unittest.TestCase):
    def test_map_path_selects_its_modules(self) -> None:
        run = cli("a.py\n")
        self.assertEqual((run.returncode, run.stdout), (0, "py test_a\nnode\n"), run.stderr)

    def test_test_modules_select_themselves(self) -> None:
        self.assertEqual(cli("tests/unit/python/test_b.py\n").stdout, "py test_b\nnode\n")
        self.assertEqual(cli("tests/unit/node/x.test.mjs\n").stdout, "py\nnode tests/unit/node/x.test.mjs\n")
        impact = {"tests/unit/python/test_c.py": ["tests/unit/node/x.test.mjs", "tests/unit/python/test_a.py"]}
        self.assertEqual(impact_map.select(["tests/unit/python/test_c.py"], impact, every),
                         (["test_a", "test_c"], ["tests/unit/node/x.test.mjs"]))

    def test_runner_paths_select_both_suites(self) -> None:
        for path in impact_map.RUNNERS:
            with self.subTest(path=path):
                self.assertEqual(impact_map.select([path], {}, every), ("all", "all"))

    def test_unmapped_paths_use_the_case_list(self) -> None:
        self.assertEqual(impact_map.select(["reference/cli/status.md"], {}, every), ("all", "all"))
        self.assertEqual(impact_map.select(["x.txt"], {}, every), ([], "all"))
        self.assertEqual(cli("x.txt\n").stdout, "py\nnode all\n")

    def test_bad_maps_fall_back_for_every_path(self) -> None:
        bad = ("{", "[]", '{"source": "s"}', '{"paths": []}', '{"paths": {"a.py": "tests/unit/python/test_a.py"}}',
               '{"paths": {"a.py": [1]}}')
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "none.json"
            self.assertIsNone(impact_map.load_map(missing))
            for text in bad:
                with self.subTest(text=text):
                    path = Path(tmp) / "map.json"
                    path.write_text(text)
                    self.assertIsNone(impact_map.load_map(path))
        for text in (None, *bad):
            with self.subTest(cli=text):
                self.assertEqual(cli("a.py\ntests/unit/python/test_b.py\n", text).stdout, "py all\nnode all\n")
        for runner in impact_map.RUNNERS:
            with self.subTest(runner=runner):
                self.assertEqual(impact_map.select([runner], None, every), ("all", "all"))
        self.assertEqual(impact_map.select(["tests/unit/python/test_b.py"], None, every), ("all", "all"))

    def test_no_changed_path_selects_nothing(self) -> None:
        self.assertEqual(cli("").stdout, "py\nnode\n")
        self.assertEqual(cli("\n\n").stdout, "py\nnode\n")

    def test_missing_modules_are_left_out(self) -> None:
        impact = {"a.py": ["tests/unit/python/test_a.py", "tests/unit/python/test_gone.py"]}
        self.assertEqual(impact_map.select(["a.py"], impact, lambda p: p != "tests/unit/python/test_gone.py"),
                         (["test_a"], []))

    def test_deleted_paths_keep_their_map_modules(self) -> None:
        impact = {"gone.py": ["tests/unit/python/test_a.py"],
                  "tests/unit/python/test_gone.py": ["tests/unit/python/test_repo_docs.py"]}
        self.assertEqual(impact_map.select(["gone.py"], impact, lambda p: p != "gone.py"), (["test_a"], []))
        self.assertEqual(impact_map.select(["tests/unit/python/test_gone.py"], impact,
                                           lambda p: p != "tests/unit/python/test_gone.py"), (["test_repo_docs"], []))

    def test_case_list_matches_gate_sh(self) -> None:
        line = re.search(r"^\s+(.+)\) py=1 ;;$", (ROOT / "scripts" / "gate.sh").read_text(), re.M)
        self.assertIsNotNone(line)
        self.assertEqual(line[1].split("|"), list(impact_map.CASE_LIST))

    @unittest.skipUnless((ROOT / impact_map.MAP).is_file(), "no map yet: D12")
    def test_committed_map_shape(self) -> None:
        text = (ROOT / impact_map.MAP).read_text()
        data = json.loads(text)
        self.assertIsNotNone(impact_map.load_map(ROOT / impact_map.MAP))
        self.assertRegex(data["source"], r"^[0-9a-f]{40}$")
        tracked = set(subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                                     check=True).stdout.splitlines())
        for path, modules in data["paths"].items():
            with self.subTest(path=path):
                self.assertIn(path, tracked)
                self.assertEqual(modules, sorted(modules))
                for module in modules:
                    self.assertTrue(impact_map.PY_MODULE.fullmatch(module) or impact_map.NODE_MODULE.fullmatch(module),
                                    module)
        self.assertEqual(text, json.dumps(data, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
