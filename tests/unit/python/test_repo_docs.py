#!/usr/bin/env python3
"""Tests for the repo docs: the AGENTS.md budget, its CLI index, the skill suite file index, kit and release files."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, budget_report  # noqa: E402


def help_commands() -> set[str]:
    text = subprocess.run([sys.executable, str(ROOT / "skill" / "scripts" / "devloop.py"), "--help"],
                          capture_output=True, text=True, check=True).stdout
    return set(re.search(r"\{([a-z,-]+)\}", text).group(1).split(","))


class RepoDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = (ROOT / "AGENTS.md").read_text()
        self.cli = self.agents.split("## devloop CLI", 1)[1].split("\n## ", 1)[0]

    def test_budget(self) -> None:
        report = budget_report(ROOT / "AGENTS.md", 16000)
        if report:
            self.fail(report)

    def test_budget_report_names_the_bytes_over_and_the_pinned_phrases(self) -> None:
        phrase = "Read `reference/cli/<command>.md` before you change, test or debug a command"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text(f"{phrase}, and more\n")
            self.assertIsNone(budget_report(path, 1000))
            report = budget_report(path, 10)
        lines = (report or "").splitlines()
        self.assertEqual(lines[0], "AGENTS.md: 87 bytes, 77 over 10. Trim it; the suite pins these phrases in it (pins under 12 characters are not listed):")
        self.assertIn(phrase, lines[1:])

    def test_test_file_index_lives_in_the_suite_readme(self) -> None:
        index = (HERE / "README.md").read_text()
        for test in sorted(HERE.glob("test_*.py")):
            with self.subTest(test=test.name):
                self.assertIn(f"`{test.name}`", index)
        self.assertIn("`tests/unit/python/README.md`", self.agents)
        self.assertNotIn("`test_dev_loop.py`: entry", self.agents)

    def test_test_files_take_the_repo_root_from_the_suite_helper(self) -> None:
        for folder, glob, helper, climb in (("node", "*.mjs", "helpers.mjs", r"import\.meta\.url"),
                                            ("python", "*.py", "support.py", r"\.parents\[")):
            for path in sorted((ROOT / "tests" / "unit" / folder).glob(glob)):
                if path.name != helper:
                    with self.subTest(path=f"{folder}/{path.name}"):
                        self.assertEqual([line for line in path.read_text().splitlines() if re.search(climb, line)], [])

    def test_cli_files_index_and_help_agree(self) -> None:
        commands = help_commands()
        files = {p.name for p in (ROOT / "reference" / "cli").iterdir()}
        self.assertEqual(files, {f"{c}.md" for c in commands})
        index = [m for line in self.cli.splitlines() if line.startswith("- `")
                 for m in re.findall(r"`reference/cli/([a-z-]+)\.md`", line)]
        self.assertEqual(sorted(index), sorted(commands))
        for command in commands:
            with self.subTest(command=command):
                first = (ROOT / "reference" / "cli" / f"{command}.md").read_text().split("\n", 1)[0]
                self.assertEqual(first, f"# {command}")

    def test_read_line(self) -> None:
        self.assertIn("Read `reference/cli/<command>.md` before you change, test or debug a command", self.cli)

    def test_kit_and_release_files(self) -> None:
        kit = (ROOT / "reference" / "kit.md").read_text()
        for value in ("`.dev-loop/MANIFEST.json`", "Ruling R10", "\n## Upgrade\n", "Tool walls upgrade (#97)",
                      "`/hooks` trusts it again"):
            with self.subTest(value=value):
                self.assertIn(value, kit)
        for value in (
            "release_notes", "version_files", "release_prepare", "release_check", "release_verify",
            "ci-skip", ".devloop-base", "--github-output", "needs: changes", "release-notes --tag",
        ):
            with self.subTest(kit_value=value):
                self.assertIn(value, kit)
        for value in ("`reference/kit.md`", "`kit-changed`", "`reference/release.md`"):
            with self.subTest(value=value):
                self.assertIn(value, self.agents)

    def test_release_candidate_rules(self) -> None:
        release = (ROOT / "reference" / "release.md").read_text()
        rules = re.search(r"^- `scripts/repo_rules.py`:.*$", self.agents, re.M).group(0)
        for text, value in (
            (self.agents, "installed kit pinned to the latest tag, release or pre-release (`dev-loop.json` `version`)"),
            (self.agents, "changes only by `upgrade` to the next tag"),
            (self.agents, "`rc: now|later: <reason>`"),
            (rules, "`promoted`"),
            (release, "## Release candidate"),
            (release, "`/release`"),
            (release, "## Final release"),
            (release, "`promoted --sha`"),
            (release, "`scripts/canary.sh`"),
            (release, "`rc: now|later: <reason>`"),
            (release, "`3.0.0`"),
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertLess(release.index("release-check"), release.index("git tag"))
        self.assertNotIn("git cliff", release)
        self.assertNotIn("npx github:tchlyah/dev-loop#vX.Y.Z-rc.N upgrade", release)


if __name__ == "__main__":
    unittest.main()
