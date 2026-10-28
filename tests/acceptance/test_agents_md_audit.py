#!/usr/bin/env python3
"""Held-out acceptance tests for the AGENTS.md audit (spec: AGENTS.md Audit).

One test per `## Acceptance flows` item (F1 to F5); each docstring names the `## Acceptance oracle` lines
(O<n>) it proves. Seams: the files of this repo (`AGENTS.md`, `reference/`) and the `devloop.py --help`
text; `scripts/repo_rules.py comments|subjects` as a process (stdout JSON, exit code) in temporary git repos
with a bare `origin`, `main` pushed and a feature branch; `scripts/gate.sh` copied into a temporary repo with
stub suites; `devloop.py merge-check` on a temporary project. Every name in the temp repos is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from feature_folders_support import write_ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS = REPO_ROOT / "AGENTS.md"
REFERENCE = REPO_ROOT / "reference"
CLI_DIR = REFERENCE / "cli"
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
REPO_RULES = REPO_ROOT / "scripts" / "repo_rules.py"
GATE = REPO_ROOT / "scripts" / "gate.sh"
CLIFF = REPO_ROOT / "cliff.toml"

BUDGET = 16000
BRANCH = "feat/palette"
SKILL_MARKER = "stub skill suite ran: palette-7f3"

APP_BASE = (
    '"""Palette helpers."""\n'
    "\n"
    "\n"
    "def colour():\n"
    "    return 'blue'\n"
    "\n"
    "\n"
    "def shade(value):\n"
    "    x = value + 1\n"
    "    # Keep the shade inside the palette range.\n"
    "    return x % 7\n"
)
SHADE_FUNC = (
    "def shade(value):\n"
    "    x = value + 1\n"
    "    # Keep the shade inside the palette range.\n"
    "    return x % 7\n"
)
TOOL_BASE = "#!/bin/sh\n# Print the palette steps.\necho one\necho two\necho three\n"
LOG_BASE = "export function log(value) {\n  const text = String(value);\n  return text;\n}\n"


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


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


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\s*$", text, re.M)
    if match is None:
        raise AssertionError(f"no `## {title}` section")
    rest = text[match.end():]
    end = re.search(r"^## ", rest, re.M)
    return rest[:end.start()] if end else rest


def subcommands() -> list[str]:
    result = subprocess.run([sys.executable, str(DEVLOOP), "--help"], capture_output=True, text=True, timeout=60)
    match = re.search(r"\{([a-z0-9,-]+)\}", result.stdout)
    if match is None:
        raise AssertionError(f"no subcommand choice list in devloop.py --help: {result.stdout[:400]!r}")
    return match.group(1).split(",")


def same_sha(a: object, b: str) -> bool:
    return isinstance(a, str) and len(a) >= 7 and (a.startswith(b) or b.startswith(a))


class Scratch:
    """A git repo; with `origin`: a bare origin, `main` pushed, and the feature branch checked out."""

    def __init__(self, root: Path, base_files: dict[str, str], origin: bool = True) -> None:
        self.root = root
        self.env = clean_env(root)
        self.repo = root / "acme-palette"
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

    def write(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            put(self.repo / path, text)

    def commit(self, files: dict[str, str], message: str, removed: tuple[str, ...] = ()) -> str:
        self.write(files)
        for path in removed:
            self.git("rm", "-q", "--", path)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def reset(self) -> None:
        self.git("reset", "-q", "--hard", self.base)
        self.git("clean", "-q", "-f", "-d", "-x")

    def rules(self, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(REPO_RULES), *args, "--repo", str(self.repo)], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"repo_rules.py {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        if not isinstance(payload, dict):
            raise AssertionError(f"repo_rules.py {' '.join(args)}: stdout is not a JSON object: {payload!r}")
        return result.returncode, payload


class ScratchCase(unittest.TestCase):
    def scratch(self, base_files: dict[str, str], origin: bool = True) -> Scratch:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Scratch(Path(tmp.name).resolve(), base_files, origin)

    def check(self, scratch: Scratch, command: str) -> tuple[int, dict]:
        code, out = scratch.rules(command)
        self.assertIsInstance(out.get("violations"), list, out)
        self.assertIs(out.get("ok"), not out["violations"], out)
        self.assertEqual(code, 0 if out["ok"] else 1, out)
        self.assertTrue(same_sha(out.get("base"), scratch.base), out)
        return code, out

    def assert_no_base(self, scratch: Scratch, command: str) -> None:
        code, out = scratch.rules(command)
        self.assertEqual(code, 0, out)
        self.assertIs(out.get("ok"), True, out)
        self.assertEqual(out.get("skipped"), "no-base", out)
        self.assertIsNone(out.get("base"), out)
        self.assertEqual(out.get("violations"), [], out)


class DocsContractTests(unittest.TestCase):
    def test_f1_docs_contract(self) -> None:
        """F1, O1 to O7: AGENTS.md budget, the CLI files and index, reference/kit.md, reference/release.md."""
        agents_bytes = AGENTS.read_bytes()
        agents = agents_bytes.decode("utf-8")
        with self.subTest(oracle=1):
            self.assertLessEqual(len(agents_bytes), BUDGET, f"AGENTS.md has {len(agents_bytes)} bytes")

        commands = subcommands()
        with self.subTest(oracle=2):
            self.assertTrue(CLI_DIR.is_dir(), "no reference/cli/ folder")
            self.assertEqual(sorted(p.name for p in CLI_DIR.iterdir()), sorted(f"{c}.md" for c in commands))

        with self.subTest(oracle=3):
            for command in commands:
                path = CLI_DIR / f"{command}.md"
                self.assertTrue(path.is_file(), path)
                self.assertEqual(path.read_text().splitlines()[0], f"# {command}", path)

        with self.subTest(oracle=4):
            cli = section(agents, "devloop CLI")
            lines = cli.splitlines()
            for command in commands:
                naming = [line for line in lines if line.startswith("- ") and f"reference/cli/{command}.md" in line]
                self.assertEqual(len(naming), 1, f"index lines for {command}: {naming}")
            self.assertTrue([line for line in lines if "reference/cli/<command>.md" in line
                             and "before you change, test or debug a command" in line],
                            "no read line in `## devloop CLI`")

        with self.subTest(oracle=5):
            kit = (REFERENCE / "kit.md").read_text()
            self.assertTrue([line for line in kit.splitlines()
                             if line.lstrip().startswith("|") and ".dev-loop/MANIFEST.json" in line],
                            "no kit table row for .dev-loop/MANIFEST.json")
            ruling = kit.find("R10")
            self.assertGreaterEqual(ruling, 0, "no Ruling R10")
            items = {}
            for line in kit[ruling:].splitlines():
                match = re.match(r"\s*([1-3])\. ", line)
                if match and match.group(1) not in items:
                    items[match.group(1)] = line
            self.assertEqual(sorted(items), ["1", "2", "3"], items)
            self.assertIn("security-audit", items["3"])
            self.assertRegex(kit, r"(?m)^## Upgrade\s*$")
            self.assertTrue("reference/kit.md" in agents, "AGENTS.md names no " + "reference/kit.md")
            self.assertTrue("kit-changed" in agents, "AGENTS.md names no " + "kit-changed")

        with self.subTest(oracle=6):
            release = (REFERENCE / "release.md").read_text()
            for n in range(1, 7):
                self.assertRegex(release, rf"(?m)^\s*{n}\. ", f"no release step {n}")
            self.assertIn("/release-audit", release)
            self.assertIn("git tag", release)
            self.assertLess(release.find("/release-audit"), release.find("git tag"), release)
            self.assertTrue("reference/release.md" in agents, "AGENTS.md names no " + "reference/release.md")

        with self.subTest(oracle=7):
            self.assertTrue([line for line in agents.splitlines()
                             if line.startswith("- `metrics") and "reference/cli/metrics.md" in line],
                            "no metrics index line in AGENTS.md")
            self.assertTrue([line for line in agents.splitlines()
                             if "5 features" in line and "Feature Metrics.md" in line],
                            "AGENTS.md holds no cost data rule")
            metrics = (CLI_DIR / "metrics.md").read_text()
            self.assertRegex(metrics, r"(?m)^- `metrics[ `]")
            self.assertIn("--write", metrics)


class CommentCheckTests(ScratchCase):
    def violations(self, scratch: Scratch) -> list[dict]:
        _, out = self.check(scratch, "comments")
        return out["violations"]

    def assert_clean(self, scratch: Scratch) -> None:
        code, out = self.check(scratch, "comments")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [], out)

    def assert_one(self, scratch: Scratch, path: str, needle: str) -> dict:
        code, out = self.check(scratch, "comments")
        self.assertEqual(code, 1, out)
        found = [v for v in out["violations"] if v.get("path") == path and needle in (v.get("text") or "")]
        self.assertEqual(len(found), 1, out)
        return found[0]

    def test_f2_comment_check(self) -> None:
        """F2, O8 to O14, O15a: `repo_rules.py comments` on a feature branch."""
        scratch = self.scratch({"src/app.py": APP_BASE, "scripts/tool.sh": TOOL_BASE, "src/log.mjs": LOG_BASE,
                                ".gitignore": "node_modules/\n", "README.md": "# Acme palette\n"})

        with self.subTest(oracle=8):
            scratch.commit({"scripts/tool.sh": "#!/bin/sh\n# Print the palette steps.\necho one\n"
                            "# Run the second step after the first one.\necho two\necho three\n"}, "Explain a step")
            code, out = self.check(scratch, "comments")
            self.assertEqual(code, 1, out)
            self.assertEqual(len(out["violations"]), 1, out)
            violation = out["violations"][0]
            self.assertEqual(violation.get("path"), "scripts/tool.sh", out)
            self.assertEqual(violation.get("line"), 4, out)
            self.assertIn("Run the second step after the first one.", violation.get("text") or "", out)
            scratch.reset()

        with self.subTest(oracle=9):
            scratch.commit({"src/app.py": APP_BASE.replace(
                "def colour():\n",
                "def colour():\n    # `--since` is a devloop.py drive and sessions option, never a Stryker one.\n")},
                "Note an option")
            self.assert_one(scratch, "src/app.py", "never a Stryker one")
            scratch.reset()

        allowed = {
            "shortcut": {"src/app.py": APP_BASE.replace(
                "    x = value + 1\n", "    x = value + 1\n    # shortcut: no lock, add one when two writers exist\n")},
            "url": {"src/app.py": APP_BASE.replace(
                "    x = value + 1\n", "    x = value + 1\n    # See https://example.invalid/palette for the range.\n")},
            "noqa": {"src/app.py": APP_BASE.replace("    x = value + 1\n", "    x = value + 1\n    # noqa: E501\n")},
            "eslint": {"src/log.mjs": LOG_BASE.replace(
                "  return text;\n", "  // eslint-disable-next-line no-console\n  console.log(text);\n  return text;\n")},
            "waiver": {"src/app.py": APP_BASE.replace(
                "    x = value + 1\n",
                "    x = value + 1\n    # The palette library rounds down here, allow-comment: dependency\n")},
            "leading-block": {"scripts/new.sh": "#!/bin/sh\n# Print the palette names.\n# One name per line.\n\n"
                                                "echo red\necho blue\n"},
            "continuation": {"scripts/tool.sh": TOOL_BASE.replace(
                "echo two\n", "# shortcut: one pass, cache it when the list grows\n# past a few hundred names.\n"
                              "echo two\n")},
        }
        for case, files in allowed.items():
            with self.subTest(oracle=10, case=case):
                scratch.commit(files, f"Add the {case} line")
                self.assert_clean(scratch)
                scratch.reset()

        with self.subTest(oracle=11):
            scratch.commit({"src/app.py": APP_BASE + '\n\ndef banner():\n    return """\n# not a comment\n"""\n'},
                           "Add a banner")
            self.assert_clean(scratch)
            scratch.reset()

        with self.subTest(oracle=12, case="unchanged-base-comment"):
            scratch.commit({"src/app.py": APP_BASE.replace("return x % 7", "return x % 9")}, "Widen the shade")
            self.assert_clean(scratch)
            scratch.reset()
        with self.subTest(oracle=12, case="uncommitted"):
            scratch.write({"scripts/tool.sh": TOOL_BASE.replace("echo two\n", "# Explain the hidden step here.\n"
                                                                              "echo two\n")})
            self.assert_one(scratch, "scripts/tool.sh", "Explain the hidden step here.")
            scratch.reset()
        with self.subTest(oracle=12, case="untracked"):
            scratch.write({"src/extra.py": "def extra():\n    # Explain the hidden step here.\n    return 1\n"})
            self.assert_one(scratch, "src/extra.py", "Explain the hidden step here.")
        with self.subTest(oracle=12, case="ignored"):
            put(scratch.repo / ".gitignore", "node_modules/\nsrc/extra.py\n")
            self.assert_clean(scratch)
            scratch.reset()

        with self.subTest(oracle=13):
            bad = "value = 1\n# Explain the hidden step here.\nvalue = 2\n"
            scratch.commit({"tests/acceptance/test_palette.py": bad, ".agents/tools/palette.sh": bad,
                            ".claude/hooks/palette.sh": bad, ".codex/hooks/palette.sh": bad,
                            ".dev-loop/hooks/palette.sh": bad, "notes/palette.md": bad,
                            "config/palette.toml": bad}, "Add files the check skips")
            self.assert_clean(scratch)
            scratch.reset()

        with self.subTest(oracle="15a", case="moved"):
            scratch.commit({"src/app.py": APP_BASE.replace(SHADE_FUNC, "").rstrip("\n") + "\n",
                            "src/shade.py": SHADE_FUNC}, "Move shade to its own file")
            self.assert_clean(scratch)
            scratch.reset()
        with self.subTest(oracle="15a", case="changed"):
            scratch.commit({"src/app.py": APP_BASE.replace("inside the palette range", "inside the colour range")},
                           "Reword the shade comment")
            self.assert_one(scratch, "src/app.py", "inside the colour range")
            scratch.reset()

        with self.subTest(oracle=14):
            no_base = self.scratch({"src/app.py": APP_BASE}, origin=False)
            no_base.commit({"src/app.py": APP_BASE.replace("def colour():\n",
                                                           "def colour():\n    # Explain the hidden step.\n")},
                           "Add a comment")
            self.assert_no_base(no_base, "comments")


class SubjectCheckTests(ScratchCase):
    def base_files(self) -> dict[str, str]:
        return {"cliff.toml": CLIFF.read_text(), "src/app.py": APP_BASE, "README.md": "# Acme palette\n"}

    def subjects(self, scratch: Scratch) -> tuple[int, dict]:
        code, out = self.check(scratch, "subjects")
        self.assertIsInstance(out.get("warnings"), list, out)
        return code, out

    def test_f3_subject_check(self) -> None:
        """F3, O14 to O18: `repo_rules.py subjects` on a feature branch, plus usage."""
        scratch = self.scratch(self.base_files())

        with self.subTest(oracle=15, case="older-warnings"):
            older = [scratch.commit({"src/a.txt": subject}, subject)
                     for subject in ("⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x")]
            scratch.commit({"src/b.txt": "b\n"}, "✨(skill) Add x")
            code, out = self.subjects(scratch)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["violations"], [], out)
            warnings = out["warnings"]
            self.assertEqual([w.get("subject") for w in warnings],
                             ["⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x"], out)
            for warning, sha in zip(warnings, older):
                self.assertTrue(same_sha(warning.get("commit"), sha), out)
            scratch.reset()

        for subject in ("⚡(test) Run x", "⏪(codex) Revert x", "✨(Skill) Add x", "✨(skill)Add x"):
            with self.subTest(oracle=15, head=subject):
                head = scratch.commit({"src/b.txt": "b\n"}, subject)
                code, out = self.subjects(scratch)
                self.assertEqual(code, 1, out)
                self.assertEqual(len(out["violations"]), 1, out)
                self.assertEqual(out["violations"][0].get("subject"), subject, out)
                self.assertTrue(same_sha(out["violations"][0].get("commit"), head), out)
                scratch.reset()

        with self.subTest(oracle=16):
            scratch.git("checkout", "-q", "-b", "side")
            scratch.commit({"src/side.txt": "side\n"}, "✨(skill) Add y")
            scratch.git("checkout", "-q", BRANCH)
            scratch.commit({"src/b.txt": "b\n"}, "✨(skill) Add x")
            scratch.git("merge", "-q", "--no-ff", "side", "-m", "Merge branch side into the feature")
            merge = scratch.git("rev-parse", "HEAD")
            code, out = self.subjects(scratch)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["violations"], [], out)
            self.assertEqual(out["warnings"], [], out)
            self.assertNotIn(merge, json.dumps(out))
            scratch.reset()

        with self.subTest(oracle=17):
            bare = self.base_files()
            del bare["cliff.toml"]
            no_cliff = self.scratch(bare)
            no_cliff.commit({"src/b.txt": "b\n"}, "✨(skill) Add x")
            code, out = no_cliff.rules("subjects")
            self.assertEqual(code, 1, out)
            self.assertTrue(out.get("error"), out)

        with self.subTest(oracle=18):
            result = subprocess.run([sys.executable, str(REPO_RULES), "palette", "--repo", str(scratch.repo)],
                                    cwd=scratch.repo, env=scratch.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

        with self.subTest(oracle=14):
            no_base = self.scratch(self.base_files(), origin=False)
            no_base.commit({"src/b.txt": "b\n"}, "⚡(test) Run x")
            self.assert_no_base(no_base, "subjects")
            self.assert_no_base(no_base, "comments")


class GateScopeTests(unittest.TestCase):
    def test_f4_gate_scope(self) -> None:
        """F4, O19: `GATE_SCOPE=changed` runs the skill suite for a change under reference/, README.md or
        scripts/repo_rules.py alone."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        repo = root / "acme-gate"
        env = clean_env(root)
        files = {
            ".gitignore": "node_modules/\n",
            "README.md": "# Acme gate\n",
            "reference/cli/status.md": "# status\n\nStatus text.\n",
            "scripts/repo_rules.py": "print('rules')\n",
            "tests/unit/python/test_dev_loop.py": f"print({SKILL_MARKER!r})\n",
            "tests/unit/node/stub.test.mjs": "import test from 'node:test';\ntest('stub', () => {});\n",
        }
        for path, text in files.items():
            put(repo / path, text)
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(GATE, repo / "scripts" / "gate.sh")
        (repo / "scripts" / "gate.sh").chmod(0o755)
        (repo / "node_modules" / "playwright").mkdir(parents=True)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

        git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            git("config", key, value)
        git("add", "-A")
        git("commit", "-q", "-m", "Base")

        for path, text in (("reference/cli/status.md", "# status\n\nStatus text, moved.\n"),
                           ("README.md", "# Acme gate\n\nSee reference/.\n"),
                           ("scripts/repo_rules.py", "print('rules, two')\n")):
            with self.subTest(oracle=19, path=path):
                put(repo / path, text)
                result = subprocess.run(["bash", str(repo / "scripts" / "gate.sh")], cwd=repo,
                                        env={**env, "GATE_SCOPE": "changed"}, capture_output=True, text=True,
                                        timeout=300)
                output = result.stdout + result.stderr
                self.assertIn(SKILL_MARKER, output, output[-2000:])
                self.assertEqual(result.returncode, 0, output[-2000:])
                git("checkout", "-q", "--", path)


class ReviewGateTests(unittest.TestCase):
    def test_f5_review_gate(self) -> None:
        """F5, O20: a commit after the reviewed head that changes only reference/cli/status.md makes
        `merge-check` report it `unreviewed`, naming that path."""
        status_doc = (CLI_DIR / "status.md").read_text()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        notes = root / "notes"
        put(notes / "Features" / "20261026 Palette" / "Spec.md",
            "---\ntype: spec\nfeature: palette\ntrack: M\nreview_status: approved\nacceptance:\nshipped:\n"
            "---\n\n# Palette\n")
        put(notes / "Features" / "20261026 Palette" / "Plan.md",
            "---\ntype: plan\nspec: Spec.md\n---\n\n# Plan\n\n### Task 1: Build it\n")
        scratch = Scratch(root, {
            "dev-loop.json": json.dumps({"version": "2.1.1", "harnesses": ["claude", "codex"]}, indent=2) + "\n",
            "GLOSSARY.md": "# Glossary\n\n**Palette**:\nA set of colours.\n",
            "src/app.py": APP_BASE,
            "reference/cli/status.md": status_doc,
        })
        scratch.env["DEV_LOOP_NOTES"] = str(notes)
        scratch.commit({"tests/acceptance/test_palette.py": "def test_flow():\n    assert True\n"},
                       "✅(acceptance) Add the palette tests")
        reviewed = scratch.commit({"src/app.py": APP_BASE.replace("'blue'", "'navy'")}, "✨(palette) Build it")
        write_ledger(scratch.repo, notes / "Features" / "20261026 Palette" / "Plan.md",
                     f"# Ledger\nTask 1: complete (tests green)\nFeature gate: green at {reviewed}\n")
        put(scratch.repo / ".git" / "info" / "exclude", ".superpowers/\n")
        late = scratch.commit({"reference/cli/status.md": status_doc + "\nOne more sentence.\n"},
                              "📝(status) Extend the contract")

        result = subprocess.run([sys.executable, str(DEVLOOP), "merge-check", "--feature", "palette"],
                                cwd=scratch.repo, env=scratch.env, capture_output=True, text=True, timeout=120)
        try:
            out = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"merge-check: stdout is not JSON (exit {result.returncode}): "
                                 f"{result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        self.assertEqual(result.returncode, 0, out)
        self.assertTrue(same_sha(out.get("reviewed"), reviewed), out)
        found = [item for item in out.get("reasons") or []
                 if isinstance(item, dict) and item.get("reason") == "unreviewed"
                 and same_sha(item.get("commit"), late)]
        self.assertTrue(found, out)
        self.assertIn("reference/cli/status.md", found[0].get("paths") or [], out)
        self.assertEqual(out.get("merge"), "human", out)


if __name__ == "__main__":
    unittest.main()
