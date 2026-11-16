#!/usr/bin/env python3
"""Held-out acceptance tests for the comment link reason (spec: Comment Link Reason).

One test per `## Acceptance flows` item (F1 to F3); each docstring names the `## Acceptance oracle` lines (O<n>)
it proves. Seam: `scripts/repo_rules.py comments` as a process (stdout JSON, exit code) in temporary git repos
with a bare `origin`, `main` pushed and a feature branch; the `## Comments` sections of `CODING_STANDARDS.md`
and `skill/references/coding-standards.md`. F1 and F2 combine the new link rule with the earlier comment check
(AGENTS.md Audit: leading block, block kinds, old text) and docstring check (Docstring Check: private
docstrings). Every name in the temp repos is invented.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_RULES = REPO_ROOT / "scripts" / "repo_rules.py"
STANDARDS = REPO_ROOT / "CODING_STANDARDS.md"
TEMPLATE = REPO_ROOT / "skill" / "references" / "coding-standards.md"
BRANCH = "feat/canary"
KIND_LINE = "a link to an issue or an RFC, with at most a short reason on the same line, never more lines"

APP_BASE = (
    '"""Canary helpers."""\n'
    "\n"
    "\n"
    "def colour():\n"
    "    return 'blue'\n"
    "\n"
    "\n"
    "def shade(value):\n"
    "    x = value + 1\n"
    "    return x % 7\n"
)
APP_ANCHOR = "    x = value + 1\n"
TOOL_BASE = "#!/bin/sh\n# Print the canary steps.\nx=1\necho one\necho two\n"
TOOL_ANCHOR = "echo one\n"
LOG_BASE = "export function log(value) {\n  const text = String(value);\n  return text;\n}\n"
LOG_ANCHOR = "  const text = String(value);\n"
OLD_PAIR = "    # See #136 for the canary image.\n    # It pulls the image once per day.\n"
OLD_BASE = "def pull():\n    image = 'canary'\n" + OLD_PAIR + "    return image\n"
STATE_BASE = (
    "def merge(rows):\n"
    '    """Join the rows into one table."""\n'
    "    return rows\n"
)


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


def comments_section(path: Path) -> str:
    text = path.read_text()
    match = re.search(r"^## Comments\s*$", text, re.M)
    if match is None:
        raise AssertionError(f"{path.name}: no `## Comments` section")
    rest = text[match.end():]
    end = re.search(r"^#{1,2} ", rest, re.M)
    return rest[:end.start()] if end else rest


class Scratch:
    """A git repo with a bare origin, `main` pushed, and the feature branch checked out."""

    def __init__(self, root: Path, base_files: dict[str, str]) -> None:
        self.env = clean_env(root)
        self.repo = root / "acme-canary"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        self.base = self.commit(base_files, "Base")
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

    def commit(self, files: dict[str, str], message: str) -> str:
        for path, text in files.items():
            put(self.repo / path, text)
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
    def scratch(self, base_files: dict[str, str]) -> Scratch:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Scratch(Path(tmp.name).resolve(), base_files)

    def check(self, scratch: Scratch) -> tuple[int, dict]:
        code, out = scratch.comments()
        self.assertIsInstance(out.get("violations"), list, out)
        self.assertIs(out.get("ok"), not out["violations"], out)
        self.assertEqual(code, 0 if out["ok"] else 1, out)
        self.assertTrue(same_sha(out.get("base"), scratch.base), out)
        return code, out

    def assert_clean(self, scratch: Scratch, files: dict[str, str], message: str) -> None:
        scratch.reset()
        scratch.commit(files, message)
        code, out = self.check(scratch)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [], out)

    def assert_one(self, scratch: Scratch, files: dict[str, str], message: str, path: str, needle: str) -> dict:
        scratch.reset()
        scratch.commit(files, message)
        code, out = self.check(scratch)
        self.assertEqual(code, 1, out)
        self.assertEqual(len(out["violations"]), 1, out)
        violation = out["violations"][0]
        self.assertEqual(violation.get("path"), path, out)
        self.assertEqual(violation.get("line"), line_of(files[path], needle), out)
        self.assertIn(needle, violation.get("text") or "", out)
        return violation


class CommentBlockTests(CommentsCase):
    def test_f1_comment_block(self) -> None:
        """F1, O1 to O9, O13, O14: `comments` fails exactly the comment lines that only a link carries."""
        scratch = self.scratch({"src/app.py": APP_BASE, "scripts/tool.sh": TOOL_BASE, "src/log.mjs": LOG_BASE,
                                "src/pull.py": OLD_BASE, ".gitignore": "node_modules/\n",
                                "README.md": "# Acme canary\n"})

        def app(*lines: str) -> dict[str, str]:
            return {"src/app.py": APP_BASE.replace(APP_ANCHOR, APP_ANCHOR + "".join(f"    {x}\n" for x in lines))}

        def log(*lines: str) -> dict[str, str]:
            return {"src/log.mjs": LOG_BASE.replace(LOG_ANCHOR, LOG_ANCHOR + "".join(f"  {x}\n" for x in lines))}

        with self.subTest(oracle=1):
            self.assert_clean(scratch, app("# #136: the pinned image of the canary"), "Name the image")

        with self.subTest(oracle=2):
            self.assert_one(scratch, app("# #136: the pinned image of the canary", "# it runs with no network"),
                            "Name the image, with prose", "src/app.py", "it runs with no network")

        for case, first in (("rfc", "# RFC 3339 time, as the log writes it"),
                            ("url", "# See https://example.invalid/palette for the range.")):
            with self.subTest(oracle=3, case=case):
                self.assert_one(scratch, app(first, "# it runs with no network"), f"Add the {case} line",
                                "src/app.py", "it runs with no network")

        with self.subTest(oracle=4):
            self.assert_one(scratch, log("// #263: root keys compare by lineKey", "// so quotes do not matter"),
                            "Note the key order", "src/log.mjs", "so quotes do not matter")

        with self.subTest(oracle=5):
            self.assert_clean(scratch, app("# allow-comment: dependency, the tool drops the flag",
                                           "# https://example.invalid/tool/issues/12",
                                           "# until version 2 ships"), "Work around the tool")

        with self.subTest(oracle=6):
            self.assert_clean(scratch, app("# shortcut: no lock, add one when two writers exist",
                                           "# the canary has one writer today"), "Skip the lock")

        with self.subTest(oracle=7):
            self.assert_clean(scratch, app("# see #12", "# see #13"), "Name the issues")

        with self.subTest(oracle=8):
            self.assert_clean(scratch, {"scripts/new.sh": "#!/bin/sh\n# See #136 for the canary image.\n"
                                                          "# It pulls the image once per day.\n"
                                                          "# One pull per run.\n\necho pull\n"}, "Add the pull script")

        with self.subTest(oracle=9):
            self.assert_clean(scratch, {"src/fetch.py": "def fetch():\n    image = 'mirror'\n" + OLD_PAIR
                                                        + "    return image\n"}, "Copy the pull note")

        with self.subTest(oracle=13):
            self.assert_clean(scratch, {"src/app.py": APP_BASE.replace(APP_ANCHOR, APP_ANCHOR
                                                                       + "    x = 1  # #215: rounds down\n"),
                                        "scripts/tool.sh": TOOL_BASE.replace(TOOL_ANCHOR, TOOL_ANCHOR
                                                                             + "cmd # see #215 for the format\n")},
                              "Add trailing links")

        with self.subTest(oracle=14, case="shellcheck-url"):
            self.assert_one(scratch, app("# see https://shellcheck.net/wiki/SC2086", "# it runs with no network"),
                            "Link the lint page", "src/app.py", "it runs with no network")
        with self.subTest(oracle=14, case="eslint-url"):
            self.assert_one(scratch, log("// see https://eslint-community.github.io/eslint-utils/",
                                         "// so quotes do not matter"),
                            "Link the lint utils", "src/log.mjs", "so quotes do not matter")


class PrivateDocstringTests(CommentsCase):
    def test_f2_private_docstring(self) -> None:
        """F2, O10 to O12: `comments` fails exactly the multi-line private docstring a link carries."""
        scratch = self.scratch({"pkg/state.py": STATE_BASE, ".gitignore": "node_modules/\n",
                                "README.md": "# Acme canary\n"})

        def fetch(doc: str) -> dict[str, str]:
            return {"pkg/state.py": STATE_BASE + f'\n\ndef _fetch():\n    """{doc}"""\n    return 1\n'}

        for case, doc in (("issue", "Work around the rounding of #215."),
                          ("url", "See https://example.com for the row format.")):
            with self.subTest(oracle=10, case=case):
                self.assert_clean(scratch, fetch(doc), f"Add fetch, {case}")

        with self.subTest(oracle=11):
            files = fetch("Work around the rounding of #215.\n    It keeps the totals even.\n    ")
            violation = self.assert_one(scratch, files, "Add fetch, two lines", "pkg/state.py",
                                        "Work around the rounding of #215.")
            self.assertEqual(violation.get("text"), "Work around the rounding of #215.", violation)

        with self.subTest(oracle=12):
            self.assert_clean(scratch, fetch("The parser rounds down here, allow-comment: dependency\n"
                                             "    It keeps the totals even.\n    "), "Add fetch, waiver")


class StandardTextTests(unittest.TestCase):
    def test_f3_standard_text(self) -> None:
        """F3, O15: both `## Comments` sections hold the new link kind line and are equal."""
        standards = comments_section(STANDARDS)
        template = comments_section(TEMPLATE)
        self.assertIn(KIND_LINE, standards, "CODING_STANDARDS.md")
        self.assertIn(KIND_LINE, template, "skill/references/coding-standards.md")
        self.assertEqual(standards.strip(), template.strip())


if __name__ == "__main__":
    unittest.main()
