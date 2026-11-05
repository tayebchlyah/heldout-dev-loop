#!/usr/bin/env python3
"""Held-out acceptance tests for the docstring check (spec: Docstring Check).

One test per `## Acceptance flows` item (F1, F2); each docstring names the `## Acceptance oracle` lines (O<n>)
it proves. Seam: `scripts/repo_rules.py comments` as a process (stdout JSON, exit code) in temporary git repos
with a bare `origin`, `main` pushed and a feature branch. Every name in the temp repos is invented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_RULES = REPO_ROOT / "scripts" / "repo_rules.py"
BRANCH = "feat/ledger"

STATE_BASE = (
    "def merge(rows):\n"
    '    """Join the rows into one table."""\n'
    "    return rows\n"
)
LOAD_FUNC = (
    "\n"
    "\n"
    "def load(path):\n"
    '    """Read the state file.\n'
    "\n"
    "    Return its text.\n"
    '    """\n'
    "    return open(path).read()\n"
)
STATE_WITH_LOAD = STATE_BASE + LOAD_FUNC
STORE = (
    "class Store:\n"
    "    def __init__(self):\n"
    '        """Open the store."""\n'
    "        self.items = {}\n"
    "\n"
    "    def get(self, key):\n"
    '        """Return one item."""\n'
    "        return self.items[key]\n"
    "\n"
    "    def _get(self, key):\n"
    '        """Look up the raw item."""\n'
    "        return self.items.get(key)\n"
)
CACHE = (
    "class Cache:\n"
    "    def get(self, key):\n"
    '        """Return one cached item."""\n'
    "        return key\n"
)
NESTED = (
    "def outer(path):\n"
    "    def parse(text):\n"
    '        """Split the text into rows."""\n'
    "        return text.split()\n"
    "\n"
    "    return parse(path)\n"
)
BROKEN = (
    "def load(path):\n"
    '    """Read the broken state."""\n'
    "    # Explain the hidden step here.\n"
    "    return path +\n"
)
SEAL = (
    "def merge_base():\n"
    '    """Find the fork point."""\n'
    "    return 1\n"
)

TOOL_BASE = "#!/bin/sh\n# Print the ledger steps.\nx=1\necho one\n"
OLD_SH = "#!/bin/sh\nrun_it # keep the old order\n"
PRIVATE_DOC = "def helper():\n    \"\"\"Explain the hidden helper.\"\"\"\n    return 1\n"


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture holds no {needle!r}")


def clean_env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_", "GATE_"))}
    home = root / "home"
    home.mkdir(exist_ok=True)
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true",
                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": "false", "GIT_CEILING_DIRECTORIES": str(root),
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    return env


def same_sha(a: object, b: str) -> bool:
    return isinstance(a, str) and len(a) >= 7 and (a.startswith(b) or b.startswith(a))


class Scratch:
    """A git repo; with `origin`: a bare origin, `main` pushed, and the feature branch checked out."""

    def __init__(self, root: Path, base_files: dict[str, str], origin: bool = True) -> None:
        self.env = clean_env(root)
        self.repo = root / "acme-ledger"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main" if origin else "trunk")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        self.base = self.commit(base_files, "Base")
        if origin:
            bare = root / "origin.git"
            self.git("init", "-q", "--bare", "-b", "main", str(bare))
            self.git("remote", "add", "origin", str(bare))
            self.git("push", "-q", "-u", "origin", "main")
            self.git("checkout", "-q", "-b", BRANCH)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def commit(self, files: dict[str, str], message: str, removed: tuple[str, ...] = ()) -> str:
        for path, text in files.items():
            put(self.repo / path, text)
        for path in removed:
            self.git("rm", "-q", "--", path)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def reset(self) -> None:
        self.git("reset", "-q", "--hard", self.base)
        self.git("clean", "-q", "-f", "-d", "-x")

    def comments(self) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(REPO_RULES), "comments", "--repo", str(self.repo)],
                                cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"repo_rules.py comments: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        if not isinstance(payload, dict):
            raise AssertionError(f"repo_rules.py comments: stdout is not a JSON object: {payload!r}")
        return result.returncode, payload


class CommentsCase(unittest.TestCase):
    def scratch(self, base_files: dict[str, str], origin: bool = True) -> Scratch:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Scratch(Path(tmp.name).resolve(), base_files, origin)

    def check(self, scratch: Scratch) -> tuple[int, dict]:
        code, out = scratch.comments()
        self.assertIsInstance(out.get("violations"), list, out)
        self.assertIs(out.get("ok"), not out["violations"], out)
        self.assertEqual(code, 0 if out["ok"] else 1, out)
        self.assertTrue(same_sha(out.get("base"), scratch.base), out)
        return code, out

    def assert_clean(self, scratch: Scratch) -> None:
        code, out = self.check(scratch)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [], out)

    def assert_only(self, scratch: Scratch, path: str, line: int, text: str) -> None:
        code, out = self.check(scratch)
        self.assertEqual(code, 1, out)
        self.assertEqual(len(out["violations"]), 1, out)
        violation = out["violations"][0]
        self.assertEqual(violation.get("path"), path, out)
        self.assertEqual(violation.get("line"), line, out)
        self.assertEqual(violation.get("text"), text, out)


class PrivateDocstringTests(CommentsCase):
    def test_f1_private_docstring(self) -> None:
        """F1, O1 to O13, O18: `comments` fails exactly the new docstrings of private names."""
        scratch = self.scratch({"pkg/state.py": STATE_BASE, ".gitignore": "node_modules/\n",
                                "README.md": "# Acme ledger\n"})
        load_line = line_of(STATE_WITH_LOAD, "Read the state file.")

        with self.subTest(oracle=1):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_WITH_LOAD}, "Add load")
            self.assert_only(scratch, "pkg/state.py", load_line, "Read the state file.")

        with self.subTest(oracle=2):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_WITH_LOAD}, "Add load")
            scratch.commit({"bin/run.py": "from state import load\n\nprint(load('ledger.txt'))\n"}, "Use load")
            self.assert_clean(scratch)

        for case, importer in (("import", "import state\n\nstate.load('ledger.txt')\n"),
                               ("import-as", "import pkg.state as st\n\nst.load('ledger.txt')\n")):
            with self.subTest(oracle=3, case=case):
                scratch.reset()
                scratch.commit({"pkg/state.py": STATE_WITH_LOAD, "bin/run.py": importer}, f"Use load, {case}")
                self.assert_clean(scratch)

        with self.subTest(oracle=4):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_WITH_LOAD,
                            "tests/unit/test_state.py": "from state import load\n\n\ndef test_load():\n"
                                                        "    assert load\n"}, "Test load")
            self.assert_only(scratch, "pkg/state.py", load_line, "Read the state file.")

        with self.subTest(oracle=5):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_WITH_LOAD,
                            "bin/run.py": "from other import load\n\nload('ledger.txt')\n"}, "Use another load")
            self.assert_only(scratch, "pkg/state.py", load_line, "Read the state file.")

        with self.subTest(oracle=6):
            scratch.reset()
            scratch.commit({"pkg/util.py": '"""Small helpers for the ledger."""\n\nVALUE = 2\n'}, "Add util")
            self.assert_clean(scratch)

        with self.subTest(oracle=7):
            scratch.reset()
            helpers = "def make():\n    \"\"\"Build a ledger fixture.\"\"\"\n    return 1\n"
            scratch.commit({"tests/unit/helpers.py": helpers,
                            "tests/unit/test_make.py": "from helpers import make\n\n\ndef test_make():\n"
                                                       "    assert make() == 1\n"}, "Add a test helper")
            self.assert_only(scratch, "tests/unit/helpers.py", 2, "Build a ledger fixture.")

        with self.subTest(oracle=8, case="public-class"):
            scratch.reset()
            scratch.commit({"pkg/store.py": STORE, "bin/run.py": "from store import Store\n\nStore().get('a')\n"},
                           "Add the store")
            self.assert_only(scratch, "pkg/store.py", line_of(STORE, "Look up the raw item."),
                             "Look up the raw item.")
        with self.subTest(oracle=8, case="private-class"):
            scratch.reset()
            scratch.commit({"pkg/cache.py": CACHE}, "Add the cache")
            self.assert_only(scratch, "pkg/cache.py", line_of(CACHE, "Return one cached item."),
                             "Return one cached item.")

        with self.subTest(oracle=9):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_BASE + "\n\n" + NESTED,
                            "bin/run.py": "from state import parse, outer\n\nouter('ledger.txt')\n"}, "Add outer")
            self.assert_only(scratch, "pkg/state.py", line_of(STATE_BASE + "\n\n" + NESTED, "Split the text"),
                             "Split the text into rows.")

        for case, doc in (("url", "See https://example.com for the row format."),
                          ("issue", "Work around the rounding of #215."),
                          ("waiver", "The parser rounds down here, allow-comment: dependency")):
            with self.subTest(oracle=10, case=case):
                scratch.reset()
                scratch.commit({"pkg/state.py": STATE_BASE + f'\n\ndef _fetch():\n    """{doc}"""\n    return 1\n'},
                               f"Add fetch, {case}")
                self.assert_clean(scratch)

        with self.subTest(oracle=11, case="moved"):
            scratch.reset()
            scratch.commit({"pkg/state.py": "VERSION = 1\n", "pkg/table.py": STATE_BASE}, "Move merge")
            self.assert_clean(scratch)
        with self.subTest(oracle=11, case="changed"):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_BASE.replace("one table", "one sheet")}, "Reword merge")
            self.assert_only(scratch, "pkg/state.py", 2, "Join the rows into one sheet.")

        with self.subTest(oracle=12):
            scratch.reset()
            scratch.commit({"pkg/state.py": STATE_BASE.replace("return rows", "return list(rows)")}, "Copy rows")
            self.assert_clean(scratch)

        with self.subTest(oracle=13):
            scratch.reset()
            scratch.commit({"pkg/broken.py": BROKEN}, "Add a broken file")
            code, out = self.check(scratch)
            self.assertEqual(code, 1, out)
            self.assertEqual([(v.get("path"), v.get("line")) for v in out["violations"]], [("pkg/broken.py", 3)], out)
            self.assertIn("Explain the hidden step here.", out["violations"][0].get("text") or "", out)

        with self.subTest(oracle=18, case="imported"):
            scratch.reset()
            scratch.commit({"seal/__init__.py": SEAL,
                            "bin/run.py": "from seal import merge_base\n\nmerge_base()\n"}, "Add merge_base")
            self.assert_clean(scratch)
        with self.subTest(oracle=18, case="no-importer"):
            scratch.reset()
            scratch.commit({"seal/__init__.py": SEAL}, "Add merge_base")
            self.assert_only(scratch, "seal/__init__.py", 2, "Find the fork point.")


class TrailingShellCommentTests(CommentsCase):
    def test_f2_trailing_shell_comment(self) -> None:
        """F2, O14 to O17, O19: `comments` fails exactly the new trailing `#` comments of `.sh` files."""
        scratch = self.scratch({"scripts/tool.sh": TOOL_BASE, "scripts/old.sh": OLD_SH,
                                ".gitignore": "node_modules/\n", "README.md": "# Acme ledger\n"})

        with self.subTest(oracle=14):
            scratch.reset()
            scratch.commit({"scripts/tool.sh": TOOL_BASE + 'if [ -n "$x" ]; then # no harness\n  echo yes\nfi\n'},
                           "Guard the step")
            self.assert_only(scratch, "scripts/tool.sh", 5, "# no harness")

        allowed = {
            "double-quote": 'echo "a # b"\n',
            "single-quote": "echo 'a # b'\n",
            "arg-count": "n=$#\n",
            "length": "n=${#list[@]}\n",
            "word": "x=a#b\n",
            "url": "cmd # see https://example.com\n",
            "shellcheck": "cmd # shellcheck disable=SC2086\n",
            "heredoc": "cat <<EOF\nx # y\nEOF\n",
        }
        for case, added in allowed.items():
            with self.subTest(oracle=15, case=case):
                scratch.reset()
                scratch.commit({"scripts/tool.sh": TOOL_BASE + added}, f"Add the {case} line")
                self.assert_clean(scratch)

        with self.subTest(oracle=16):
            scratch.reset()
            scratch.commit({"scripts/tool.sh": TOOL_BASE + "echo three # keep the old order\n"}, "Add a step")
            self.assert_clean(scratch)

        for prefix in ("tests/acceptance", ".agents", ".claude", ".codex", ".dev-loop"):
            with self.subTest(oracle=17, prefix=prefix):
                scratch.reset()
                scratch.commit({f"{prefix}/hooks/ledger.sh": "#!/bin/sh\ncmd # no harness here\n",
                                f"{prefix}/hooks/ledger_helper.py": PRIVATE_DOC}, "Add files the check skips")
                self.assert_clean(scratch)

        with self.subTest(oracle=19):
            no_base = self.scratch({"scripts/tool.sh": TOOL_BASE}, origin=False)
            no_base.commit({"scripts/tool.sh": TOOL_BASE + "cmd # no harness\n", "pkg/helper.py": PRIVATE_DOC},
                           "Add a step and a helper")
            code, out = no_base.comments()
            self.assertEqual(code, 0, out)
            self.assertIs(out.get("ok"), True, out)
            self.assertEqual(out.get("skipped"), "no-base", out)
            self.assertEqual(out.get("violations"), [], out)


if __name__ == "__main__":
    unittest.main()
