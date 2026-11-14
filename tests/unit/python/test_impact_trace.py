#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import impact_map  # noqa: E402
from repo_rules import SKIPPED_PREFIXES  # noqa: E402

HELD_OUT = SKIPPED_PREFIXES[0] + "flow.py"
MODULES = ("tests/unit/python/test_a.py", "tests/unit/python/test_b.py", "tests/unit/node/x.test.mjs")


def parse(text: str, tracked: set[str]) -> set[str]:
    return impact_map.parse_trace(text, "/r", "/r", tracked)


def opened(path: str, pid: int = 100) -> str:
    return f'{pid} openat(AT_FDCWD, "{path}", O_RDONLY|O_CLOEXEC) = 3</r/x>'


class ParseTraceTests(unittest.TestCase):
    def test_relative_open_follows_chdir(self) -> None:
        self.assertEqual(parse('100 chdir("sub") = 0\n' + opened("b.py"), {"sub/b.py"}), {"sub/b.py"})
        text = '100 chdir("nope") = -1 ENOENT (No such file or directory)\n' + opened("a.py")
        self.assertEqual(parse(text, {"a.py", "nope/a.py"}), {"a.py"})

    def test_fchdir(self) -> None:
        self.assertEqual(parse("100 fchdir(4</r/sub>) = 0\n" + opened("b.py"), {"sub/b.py"}), {"sub/b.py"})

    def test_children_inherit_the_cwd(self) -> None:
        for call in ("clone(child_stack=NULL, flags=CLONE_CHILD_SETTID|SIGCHLD) = 101",
                     "clone3({flags=CLONE_VM, exit_signal=SIGCHLD}, 88) = 101", "fork() = 101", "vfork() = 101"):
            with self.subTest(call=call):
                text = "\n".join(['100 chdir("sub") = 0', f"100 {call}", '100 chdir("/r") = 0', opened("c.py", 101)])
                self.assertEqual(parse(text, {"sub/c.py", "c.py"}), {"sub/c.py"})

    def test_dirfd_annotation(self) -> None:
        self.assertEqual(parse('100 openat(5</r/sub>, "c.py", O_RDONLY) = 3', {"sub/c.py"}), {"sub/c.py"})
        self.assertEqual(parse('100 openat(5, "c.py", O_RDONLY) = 3', {"sub/c.py", "c.py"}), set())

    def test_unfinished_and_resumed_join(self) -> None:
        text = "\n".join(['100 openat(AT_FDCWD, "e.py", O_RDONLY <unfinished ...>', "101 +++ exited with 0 +++",
                          "100 <... openat resumed>) = 3</r/e.py>"])
        self.assertEqual(parse(text, {"e.py"}), {"e.py"})
        text = "\n".join(['100 chdir("sub") = 0', "100 clone(child_stack=NULL, flags=SIGCHLD <unfinished ...>",
                          "100 <... clone resumed>) = 101", '100 chdir("/r") = 0', opened("c.py", 101)])
        self.assertEqual(parse(text, {"sub/c.py", "c.py"}), {"sub/c.py"})

    def test_only_tracked_repo_paths(self) -> None:
        text = "\n".join([opened("untracked.py"), opened("/etc/passwd"), opened("/r/a.py"), opened("../r/a.py"),
                          opened("/rr/a.py"),
                          '100 newfstatat(AT_FDCWD, "a.py", 0x7ffd, 0) = -1 ENOENT (No such file or directory)'])
        self.assertEqual(parse(text, {"a.py"}), {"a.py"})
        self.assertEqual(parse(opened("/rr/a.py"), {"a.py"}), set())

    def test_escapes_and_annotated_cwd(self) -> None:
        self.assertEqual(parse(opened("caf\\303\\251.md"), {"café.md"}), {"café.md"})
        self.assertEqual(parse(opened('q\\"x.md'), {'q"x.md'}), {'q"x.md'})
        text = '102 openat(AT_FDCWD</r/sub>, "c.py", O_RDONLY) = 3'
        self.assertEqual(parse(text, {"sub/c.py", "c.py"}), {"sub/c.py"})

    def test_vfork_child_keeps_its_own_chdir(self) -> None:
        text = "\n".join([
            '100 chdir("pdir") = 0',
            "100 clone(child_stack=0x1, flags=CLONE_VM|CLONE_VFORK|SIGCHLD <unfinished ...>",
            '101 access("f", R_OK) = 0',
            '101 chdir("/r/sub") = 0',
            '101 execve("/usr/bin/cat", ["cat", "f"], 0x1 /* 10 vars */ <unfinished ...>',
            "100 <... clone resumed>) = 101",
            "101 <... execve resumed>) = 0",
            '101 access("f", R_OK) = 0',
        ])
        self.assertEqual(parse(text, {"pdir/f"}), {"pdir/f"})
        self.assertEqual(parse(text, {"sub/f"}), {"sub/f"})

    def test_threads_share_the_cwd(self) -> None:
        flags = "CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND|CLONE_THREAD|CLONE_SYSVSEM"
        text = "\n".join([f"100 clone(child_stack=0x1, flags={flags}) = 101", '100 chdir("sub") = 0',
                          '101 access("f", R_OK) = 0'])
        self.assertEqual(parse(text, {"sub/f", "f"}), {"sub/f"})
        text = "\n".join([f"100 clone(child_stack=0x1, flags={flags}) = 101", '101 chdir("sub") = 0',
                          '100 access("f", R_OK) = 0'])
        self.assertEqual(parse(text, {"sub/f", "f"}), {"sub/f"})

    def test_forked_child_does_not_see_a_later_parent_chdir(self) -> None:
        text = "\n".join(["100 clone(child_stack=NULL, flags=CLONE_CHILD_SETTID|SIGCHLD) = 101", '100 chdir("sub") = 0',
                          '101 access("f", R_OK) = 0'])
        self.assertEqual(parse(text, {"sub/f", "f"}), {"f"})


def scratch_repo(root: Path) -> str:
    for path in ("a.py", impact_map.MAP, HELD_OUT, *MODULES, "tests/unit/python/test_dev_loop.py"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("{}\n" if path == impact_map.MAP else "")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit",
                                                 "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def reads(root: Path, *paths: str) -> str:
    return "\n".join(f'1 openat(AT_FDCWD, "{root}/{p}", O_RDONLY) = 3' for p in paths)


class TraceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.head = scratch_repo(self.root)
        self.out = self.root / impact_map.MAP
        self.calls: list[tuple[str, bool]] = []

    def trace(self, results: dict[tuple[str, bool], tuple[bool, str]]) -> tuple[int, str, str]:
        def run(module: str, traced: bool) -> tuple[bool, str]:
            self.calls.append((module, traced))
            return results.get((module, traced), (True, reads(self.root, "a.py") if traced else ""))
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out, \
                mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            code = impact_map.trace_command(self.root, self.out, 2, run)
        return code, out.getvalue(), err.getvalue()

    def test_writes_the_map(self) -> None:
        extra = reads(self.root, "a.py", impact_map.MAP, HELD_OUT)
        code, out, _ = self.trace({("tests/unit/python/test_a.py", True): (True, extra)})
        self.assertEqual((code, out), (0, "written\n"))
        paths = {"a.py": sorted(MODULES)}
        self.assertEqual(self.out.read_text(),
                         json.dumps({"source": self.head, "paths": paths}, indent=1, sort_keys=True) + "\n")
        self.assertEqual(sorted(self.calls), sorted((m, True) for m in MODULES))

    def test_unchanged_map_is_kept(self) -> None:
        text = json.dumps({"source": "old", "paths": {"a.py": sorted(MODULES)}}, indent=4)
        self.out.write_text(text)
        code, out, _ = self.trace({})
        self.assertEqual((code, out, self.out.read_text()), (0, "unchanged\n", text))

    def test_red_module_writes_no_map(self) -> None:
        red = {("tests/unit/python/test_b.py", True): (False, ""), ("tests/unit/python/test_b.py", False): (False, "")}
        for existing in (None, "keep me"):
            with self.subTest(existing=existing):
                self.calls.clear()
                if existing is None:
                    self.out.unlink(missing_ok=True)
                else:
                    self.out.write_text(existing)
                code, _, err = self.trace(red)
                self.assertEqual((code, err), (1, "failed: tests/unit/python/test_b.py\n"))
                self.assertEqual(self.out.read_text() if self.out.exists() else None, existing)
                self.assertIn(("tests/unit/python/test_b.py", False), self.calls)

    def test_trace_only_failure_keeps_its_reads(self) -> None:
        code, out, _ = self.trace({("tests/unit/python/test_b.py", True): (False, reads(self.root, "a.py")),
                                   ("tests/unit/python/test_b.py", False): (True, "")})
        self.assertEqual((code, out), (0, "trace-only: tests/unit/python/test_b.py\nwritten\n"))
        self.assertIn("tests/unit/python/test_b.py", json.loads(self.out.read_text())["paths"]["a.py"])

    def test_no_strace_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            run = subprocess.run([sys.executable, str(ROOT / "scripts" / "impact_map.py"), "trace"],
                                 env={**os.environ, "PATH": empty}, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("strace", run.stderr)


class RunModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "tests" / "unit" / "python").mkdir(parents=True)
        (self.root / "tests" / "unit" / "node").mkdir(parents=True)

    def module(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text)
        return name

    def test_run_module_env_and_result(self) -> None:
        report = self.root / "env.json"
        probe = self.module("tests/unit/python/test_env.py", (
            "import json, os, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_env(self):\n"
            "        tmp = os.environ['TMPDIR']\n"
            "        json.dump({'cwd': os.getcwd(), 'listing': os.listdir(tmp), 'tmp': tmp,\n"
            "                   'pythonpath': os.environ.get('PYTHONPATH'),\n"
            "                   'kept': [k for k in ('CLAUDE_PROJECT_DIR', 'KIT_ROOT', 'KIT_MAIN') if k in os.environ]},\n"
            "                  open(os.environ['OUT'], 'w'))\n"))
        env = {"CLAUDE_PROJECT_DIR": "x", "KIT_ROOT": "x", "KIT_MAIN": "x", "OUT": str(report)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(impact_map.run_module(self.root, probe, False), (True, ""))
        seen = json.loads(report.read_text())
        self.assertEqual(seen["cwd"], str(self.root / "tests" / "unit" / "python"))
        self.assertEqual((seen["listing"], seen["kept"]), ([], []))
        self.assertNotEqual(seen["tmp"], os.environ.get("TMPDIR"))
        self.assertFalse(Path(seen["tmp"]).exists())
        self.assertEqual(seen["pythonpath"], f"{self.root}/skill/scripts:{self.root}/tests/unit/python")
        red = self.module("tests/unit/python/test_red.py",
                          "import unittest\nclass T(unittest.TestCase):\n    def test_red(self):\n        self.fail()\n")
        self.assertEqual(impact_map.run_module(self.root, red, False), (False, ""))
        ok = self.module("tests/unit/node/ok.test.mjs", "import test from 'node:test';\ntest('ok', () => {});\n")
        self.assertEqual(impact_map.run_module(self.root, ok, False), (True, ""))

    def test_run_module_timeout(self) -> None:
        slow = self.module("tests/unit/python/test_sleep.py", "import time, unittest\n"
                           "class T(unittest.TestCase):\n    def test_sleep(self):\n        time.sleep(30)\n")
        start = time.monotonic()
        with mock.patch.object(impact_map, "TIMEOUT", 1):
            self.assertEqual(impact_map.run_module(self.root, slow, False), (False, ""))
        self.assertLess(time.monotonic() - start, 10)

    def test_run_module_timeout_keeps_the_partial_trace(self) -> None:
        slow = self.module("tests/unit/python/test_sleep.py", "import time, unittest\n"
                           "class T(unittest.TestCase):\n    def test_sleep(self):\n        time.sleep(30)\n")
        stand_in = [sys.executable, "-c", "import subprocess, sys\nopen(sys.argv[2], 'w').write('partial')\n"
                    "subprocess.run(sys.argv[3:])"]
        start = time.monotonic()
        with mock.patch.object(impact_map, "TIMEOUT", 1), mock.patch.object(impact_map, "STRACE", stand_in):
            self.assertEqual(impact_map.run_module(self.root, slow, True), (False, "partial"))
        self.assertLess(time.monotonic() - start, 10)


if __name__ == "__main__":
    unittest.main()
