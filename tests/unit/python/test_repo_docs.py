#!/usr/bin/env python3
"""Tests for the repo docs: the AGENTS.md budget, its CLI index, the skill suite file index, kit and release files."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from support import ROOT, budget_report  # noqa: E402


README_SECTIONS = ["What and why", "The loop", "Quick start", "How it works", "Usage", "Configuration",
                   "Inspirations and credits", "Security model and limits", "Contributing", "License"]
HOW_IT_WORKS = ["Tracks", "Shaping and Delivery", "The held-out suite and its seal", "The walls",
                "The feature gate", "The autonomy grant"]
README_LINKS = ["docs/usage.md", "docs/configuration.md", "docs/commands.md", "docs/upgrade.md",
                "CONTRIBUTING.md", "SECURITY.md", "LICENSE"]


def readme_section(readme: str, title: str) -> str:
    return readme.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


def help_commands() -> set[str]:
    text = subprocess.run([sys.executable, str(ROOT / "skill" / "scripts" / "devloop.py"), "--help"],
                          capture_output=True, text=True, check=True).stdout
    return set(re.search(r"\{([a-z,-]+)\}", text).group(1).split(","))


class RepoDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = (ROOT / "AGENTS.md").read_text()
        self.cli = self.agents.split("## devloop CLI", 1)[1].split("\n## ", 1)[0]

    def test_readme_sections_in_order(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertEqual(readme.splitlines()[0], "# heldout-dev-loop")
        self.assertEqual(re.findall(r"^## (.+)$", readme, re.M), README_SECTIONS)
        self.assertEqual(re.findall(r"^### (.+)$", readme_section(readme, "How it works"), re.M), HOW_IT_WORKS)
        self.assertIn("```mermaid", readme_section(readme, "The loop"))
        self.assertIn("actions/workflows/ci.yml/badge.svg", readme)
        self.assertIn("license-MIT", readme)

    def test_readme_links_each_guide_and_community_file(self) -> None:
        readme = (ROOT / "README.md").read_text()
        for target in README_LINKS:
            with self.subTest(target=target):
                self.assertIn(f"]({target})", readme)
                self.assertTrue((ROOT / target).is_file())

    def test_inspirations_row_per_source_and_adapted_rows_name_a_source_line(self) -> None:
        section = readme_section((ROOT / "README.md").read_text(), "Inspirations and credits")
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in section.splitlines()
                if line.startswith("| ") and not line.startswith(("| Part", "| ---"))]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(len(row), 5, row)
            self.assertIn(row[3], ("invoked", "adapted", "idea"), row)
        for src in json.loads((ROOT / "upstream.json").read_text())["sources"]:
            with self.subTest(source=src["name"]):
                mine = [row for row in rows if f"`{src['name']}`" in row[2]]
                self.assertTrue(mine)
                for row in mine:
                    if row[3] == "adapted":
                        path = re.fullmatch(r"`([^`]+)`", row[4]).group(1)
                        lines = (ROOT / path).read_text().splitlines()
                        self.assertTrue(any(src["version"] in line and ("Source" in line or "adapt" in line.lower())
                                            for line in lines), (src["name"], path))
                self.assertIn(f"https://github.com/{src['repo']}", section)
        held_out = [row for row in rows if row[0].startswith("Held-out suite")]
        self.assertEqual([row[3] for row in held_out], ["idea"])
        self.assertIn("machine learning", held_out[0][2])
        self.assertIn("scripts/upstream_watch.py", section)

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
                      "`/hooks` trusts it again",
                      "Walled agents web upgrade (#240)", "`/hooks` trusts the hooks again"):
            with self.subTest(value=value):
                self.assertIn(value, kit)
        self.assertNotIn('web_search = "disabled"', kit)
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
        self.assertNotIn("npx github:tayebchlyah/heldout-dev-loop#vX.Y.Z-rc.N upgrade", release)

    def test_codex_kit_copy_docs(self) -> None:
        kit = (ROOT / "reference" / "kit.md").read_text()
        codex = (ROOT / "skill" / "references" / "codex.md").read_text()
        paragraph = next(p for p in kit.split("\n\n") if p.startswith("Codex kit copy upgrade (#227)"))
        gaps = re.search(r"^- Gaps.*$", codex, re.M).group(0)
        for text, value in (
            (paragraph, "/hooks"),
            (paragraph, "1 MB"),
            (gaps, ".codex/hooks.json"),
            (gaps, "turn a hook off"),
            (gaps, "stale `trusted_hash`"),
            (gaps, "only at the top of an outermost write root"),
            (gaps, "`features.hooks = false`"),
            (gaps, "`merge-check` `harness-config`"),
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        for value in ("kit-main", "innermost repo"):
            with self.subTest(absent=value):
                self.assertNotIn(value, codex)

    def test_upgrade_steps_name_no_kit_source_path(self) -> None:
        kit = (ROOT / "reference" / "kit.md").read_text()
        steps = kit.split("3.0.0 upgrade (breaking)", 1)[1].split("\n\nWhat changes", 1)[0]
        self.assertEqual(re.findall(r"`(?:skills?|claude|codex|bin|lib)/[^`]*`", steps), [])


if __name__ == "__main__":
    unittest.main()
