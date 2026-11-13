"""select_mutants.py, the mutation setup and the mutation contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_devloop
from support import CHECKER, MODULE_BASE, SELECTOR, SKILL_ROOT, all_instructions,git_in, make_git_repo, select_mutants, write


def commit_changes(repo: Path, files: dict[str, str]) -> str:
    base = git_in(repo, "rev-parse", "HEAD").strip()
    for path, text in files.items():
        write(repo / path, text)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "feature")
    return base


PY_AGENTS = "stacks: python\n"


MUTMUT_CONFIG = '[tool.mutmut]\nsource_paths = ["src"]\npytest_add_cli_args_test_selection = ["tests/unit"]\n'


MODULE_HEAD = (
    MODULE_BASE.replace("LIMIT = 1", "LIMIT = 2")
    .replace("x + 1", "x + 2")
    .replace("size = 2", "size = 3")
    .replace("self.size * 2", "self.size * 3")
    .replace("return 1\n\n\n@register", "return 4\n\n\n@register")
    .replace("    return 2\n", "    return 5\n")
)


class MutationContractTests(unittest.TestCase):
    def test_no_route_runs_mutation_in_the_gate(self) -> None:
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                self.assertNotIn("mutation.md", (SKILL_ROOT / "references" / route).read_text())
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        self.assertIn("select_mutants.py --base", mutation)

    def test_nightly_base_falls_back_to_the_root_commit(self) -> None:
        line = ("BASE=$(git rev-list -1 --before='24 hours ago' main); "
                "python3 .agents/skills/dev-loop/scripts/select_mutants.py "
                '--base "${BASE:-$(git rev-list --max-parents=0 main | tail -n 1)}" --head main --run')
        for heading in ["## Run", "## Nightly CI"]:
            with self.subTest(section=heading):
                self.assertIn(line, test_devloop.section(SKILL_ROOT / "references" / "mutation.md", heading))

    def test_fix_wave_commits_a_test_only_when_it_kills_a_listed_mutant(self) -> None:
        fix = test_devloop.section(SKILL_ROOT / "references" / "mutation.md", "## Fix")
        for value in [
            "home test file",
            "public entry",
            "select_mutants.py --base <report base> --head HEAD --run --only",
            "kills no listed mutant",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, fix)
        self.assertIn("kills one or more listed mutants", (SKILL_ROOT / "references" / "tests.md").read_text())

    def test_fix_wave_writes_one_test_per_function_in_parallel(self) -> None:
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        for value in ["one unit test per listed function", "in parallel", "Run mutmut once per night", "only_mutate"]:
            with self.subTest(value=value):
                self.assertIn(value, mutation)

    def test_no_route_uses_a_stryker_flag_that_does_not_exist(self) -> None:
        for line in all_instructions().splitlines():
            if "--since" in line:
                self.assertNotIn("stryker", line.lower())
                self.assertTrue("drive" in line or "devloop.py sessions" in line, line)

    def test_reference_names_the_selection_gap_and_the_setup_owner(self) -> None:
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        for value in ["What the selection does not cover", "Stage 0 owns this setup", "runtime_checkable", "mutants/"]:
            with self.subTest(value=value):
                self.assertIn(value, mutation)
        self.assertIn("references/mutation.md", (SKILL_ROOT / "references" / "bootstrap.md").read_text())

    def test_no_em_dash_in_the_mutation_payload(self) -> None:
        for path in [
            SKILL_ROOT / "references" / "mutation.md",
            SELECTOR,
            CHECKER,
        ]:
            with self.subTest(path=path.name):
                self.assertNotIn("\u2014", path.read_text())


class SelectMutantsTests(unittest.TestCase):
    def select(self, repo: Path, base: str, *extra: str) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(SELECTOR), "--repo", str(repo), "--head", "HEAD", "--base", base, "--json", *extra],
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        return result.returncode, payload, result.stderr

    def python_repo(self, root: Path) -> tuple[Path, str]:
        repo = make_git_repo(
            root,
            {
                "AGENTS.md": PY_AGENTS,
                "pyproject.toml": MUTMUT_CONFIG,
                "src/pkg/mod.py": MODULE_BASE,
                "tests/unit/test_mod.py": "def test_x():\n    assert True\n",
            },
        )
        base = commit_changes(
            repo,
            {"src/pkg/mod.py": MODULE_HEAD, "tests/unit/test_mod.py": "def test_x():\n    assert 1\n"},
        )
        return repo, base

    def test_selects_only_changed_functions_and_methods(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["mutmut_root"], ".")
            self.assertEqual(
                sel["patterns"],
                [
                    "pkg.mod.x_changed__mutmut_*",
                    "pkg.mod.x\u01c1Box\u01c1grow__mutmut_*",
                    "pkg.mod.x\u01c1Box\u01c1make__mutmut_*",
                ],
            )

    def test_reports_what_the_selection_does_not_cover(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            _, sel, _ = self.select(repo, base)
            gaps = " | ".join(sel["gaps"])
            self.assertIn("src/pkg/mod.py:1 outside any function", gaps)
            self.assertIn("class body outside any method", gaps)
            self.assertIn("decorated function, mutmut skips it", gaps)
            self.assertNotIn("tests/unit", gaps)

    def test_same_diff_gives_the_same_selection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            self.assertEqual(self.select(repo, base)[1], self.select(repo, base)[1])

    def test_mutmut_root_is_the_package_with_the_config(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": "[tool.uv.workspace]\nmembers = [\"backend\"]\n",
                    "backend/pyproject.toml": MUTMUT_CONFIG,
                    "backend/src/pkg/__init__.py": "def hook():\n    return 1\n",
                },
            )
            base = commit_changes(repo, {"backend/src/pkg/__init__.py": "def hook():\n    return 2\n"})
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["mutmut_root"], "backend")
            self.assertEqual(sel["patterns"], ["pkg.x_hook__mutmut_*"])

    def test_pure_deletion_inside_a_function_selects_it(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": MUTMUT_CONFIG,
                    "src/pkg/mod.py": "def trim(x):\n    y = x\n    y = y + 1\n    return y\n",
                },
            )
            base = commit_changes(repo, {"src/pkg/mod.py": "def trim(x):\n    y = x\n    return y\n"})
            _, sel, _ = self.select(repo, base)
            self.assertEqual(sel["patterns"], ["pkg.mod.x_trim__mutmut_*"])

    def test_missing_mutmut_config_is_a_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": "[project]\nname = 'x'\n", "src/a.py": "A = 1\n"}
            )
            base = commit_changes(repo, {"src/a.py": "A = 2\n"})
            code, _, err = self.select(repo, base)
            self.assertEqual(code, 2)
            self.assertIn("Stage 0", err)

    def test_ui_selects_changed_line_ranges_outside_tests(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": "stacks: ui\n",
                    "frontend/stryker.config.json": json.dumps({"mutate": ["src/**/*.ts", "!src/**/*.test.ts"]}),
                    "frontend/src/a.ts": "export const a = 1;\nexport const b = 2;\nexport const c = 3;\n",
                    "frontend/src/a.test.ts": "test('a', () => {});\n",
                    "frontend/src/Screen.tsx": "export const S = 1;\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "frontend/src/a.ts": "export const a = 1;\nexport const b = 20;\nexport const c = 30;\n",
                    "frontend/src/a.test.ts": "test('a', () => { expect(1); });\n",
                    "frontend/src/Screen.tsx": "export const S = 2;\n",
                },
            )
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["stryker_root"], "frontend")
            self.assertEqual(sel["stryker_ranges"], ["src/a.ts:2-3"])
            self.assertEqual(sel["skipped"], ["frontend/src/Screen.tsx outside the Stryker mutate list"])

    def test_only_critical_modules_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = MUTMUT_CONFIG + 'only_mutate = ["src/pkg/core/*"]\ndo_not_mutate = ["src/pkg/core/proto.py"]\n'
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": config,
                    "src/pkg/core/score.py": "def score(x):\n    return x > 1\n",
                    "src/pkg/core/proto.py": "def proto(x):\n    return x\n",
                    "src/pkg/api/routes.py": "def route(x):\n    return x\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "src/pkg/core/score.py": "def score(x):\n    return x >= 1\n",
                    "src/pkg/core/proto.py": "def proto(x):\n    return x + 0\n",
                    "src/pkg/api/routes.py": "def route(x):\n    return x + 0\n",
                },
            )
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["patterns"], ["pkg.core.score.x_score__mutmut_*"])
            self.assertEqual(
                sel["skipped"],
                ["src/pkg/api/routes.py outside only_mutate", "src/pkg/core/proto.py in do_not_mutate"],
            )

    def test_survivors_split_into_decisions_and_messages(self) -> None:
        cases = {
            "message": [
                '-    raise ValueError("bad window")\n+    raise ValueError("XXbad windowXX")\n',
                '-    logger.info("fetched %s", name)\n+    logger.info(None, name)\n',
                '-    return Finding(detail="gap")\n+    return Finding(detail="XXgapXX")\n',
            ],
            "decision": [
                '-    return {"status": "ok"}\n+    return {"XXstatusXX": "ok"}\n',
                '-    if kind == "gauge":\n+    if kind == "XXgaugeXX":\n',
                "-    if count >= limit:\n+    if count > limit:\n",
                "-    return total\n+    return None\n",
            ],
        }
        for expected, diffs in cases.items():
            for diff in diffs:
                with self.subTest(diff=diff):
                    self.assertEqual(select_mutants.classify(diff), expected)

    def test_base_option_sets_the_diff_base(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": MUTMUT_CONFIG, "src/pkg/mod.py": MODULE_BASE}
            )
            first = commit_changes(repo, {"src/pkg/mod.py": MODULE_BASE.replace("x + 1", "x + 2")})
            second = commit_changes(repo, {"src/pkg/mod.py": MODULE_BASE.replace("x + 1", "x + 2").replace("x - 1", "x - 2")})
            _, older, _ = self.select(repo, first)
            _, newer, _ = self.select(repo, second)
            self.assertEqual(older["patterns"], ["pkg.mod.x_changed__mutmut_*", "pkg.mod.x_untouched__mutmut_*"])
            self.assertEqual(newer["patterns"], ["pkg.mod.x_untouched__mutmut_*"])

    def test_no_mutable_change_selects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": MUTMUT_CONFIG, "README.md": "a\n"}
            )
            base = commit_changes(repo, {"README.md": "b\n"})
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual((sel["patterns"], sel["stryker_ranges"]), ([], []))

    def test_only_keeps_the_listed_functions_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": "stacks: python, ui\n",
                    "pyproject.toml": MUTMUT_CONFIG,
                    "src/pkg/mod.py": MODULE_BASE,
                    "frontend/stryker.config.json": json.dumps({"mutate": ["src/**/*.ts"]}),
                    "frontend/src/a.ts": "export const a = 1;\n",
                    "frontend/src/b.ts": "export const b = 1;\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "src/pkg/mod.py": MODULE_HEAD,
                    "frontend/src/a.ts": "export const a = 2;\n",
                    "frontend/src/b.ts": "export const b = 2;\n",
                },
            )
            code, sel, err = self.select(repo, base, "--only", "pkg.mod.x_changed", "src/b.ts")
            self.assertEqual(code, 0, err)
            self.assertEqual(
                (sel["patterns"], sel["stryker_ranges"]), (["pkg.mod.x_changed__mutmut_*"], ["src/b.ts:1-1"])
            )

    def test_only_outside_the_selection_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            code, _, err = self.select(repo, base, "--only", "pkg.mod.x_changed", "pkg.mod.x_gone")
            self.assertEqual(code, 2)
            self.assertIn("pkg.mod.x_gone", err)
            self.assertNotIn("x_changed", err)

    def test_report_names_the_base_and_head_for_the_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            head = git_in(repo, "rev-parse", "HEAD").strip()
            result = subprocess.run(
                [sys.executable, str(SELECTOR), "--repo", str(repo), "--head", "HEAD", "--base", base],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[:2], [f"base: {base}", f"head: {head}"])
