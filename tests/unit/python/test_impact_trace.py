#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import impact_map  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
