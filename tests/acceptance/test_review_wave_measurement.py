#!/usr/bin/env python3
"""Held-out oracle tests for the review wave measurement (spec: Review Wave Measurement).

One test per `## Acceptance flows` item (F<n>), one subtest per `## Acceptance oracle` line (O<n>) it
proves, plus one test for the kit-file line O20. The tests use only the declared seams:
`devloop.py merge-check` and `check_setup.py` as processes (stdout and exit code) on temporary git
repos with a bare `origin`, and the `codex/agents/*.toml` files of this repo. Every name in the temp
repos is invented. HOME is always a temporary folder.

Fixture choices the spec leaves open (taken from the documented loop):
- The notes folder comes from `DEV_LOOP_NOTES`; the spec sits in `<notes>/specs/`, the plan in
  `<notes>/plans/` with `spec: <spec file name>`, the ledger in `.superpowers/sdd/<plan stem>/progress.md`
  of the feature worktree (untracked).
- A Track S feature has no spec and lives on branch `fix/<slug>`.
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

try:
    import tomllib
except ImportError:  # Python before 3.11
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
CODEX_AGENTS = REPO_ROOT / "codex" / "agents"

PIN_COMMIT = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"
SLUG = "token-refresh"
TITLE = "Token Refresh"
S_SLUG = "banner-typo"
BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}

APP_BASE = "def colour():\n    return 'red'\n"
APP_FEATURE = "def colour():\n    return 'blue'\n"
TOKEN_BASE = "def refresh(token):\n    return token\n"


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def same_sha(a: object, b: str) -> bool:
    return isinstance(a, str) and len(a) >= 7 and bool(b) and (a.startswith(b) or b.startswith(a))


def config_text(**extra: object) -> str:
    return json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n"


class Project:
    """A bare origin, a main checkout on `main`, a notes folder, and feature worktrees."""

    def __init__(self, root: Path, main_config: str) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.notes = root / "notes"
        (self.notes / "specs").mkdir(parents=True)
        (self.notes / "plans").mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
        self.env.update({"HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes), "GIT_CONFIG_NOSYSTEM": "1",
                         "GIT_EDITOR": "true", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                         "GIT_CONFIG_VALUE_0": "false"})
        self.origin = root / "origin.git"
        self.git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = root / "acme-tokens"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git(self.repo, "config", key, value)
        self.git(self.repo, "remote", "add", "origin", str(self.origin))
        self.base = self.commit(self.repo, {
            "dev-loop.json": main_config,
            "README.md": "# Acme tokens\n",
            "GLOSSARY.md": "# Glossary\n\n**Token**:\nA short-lived access pass.\n",
            "src/app.py": APP_BASE,
            "src/auth/token.py": TOKEN_BASE,
            "skill/SKILL.md": "# Acme skill\n\nStep one.\n",
            "tests/unit/test_x.py": "def test_x():\n    assert 1 == 1\n",
        }, "Base")
        self.git(self.repo, "push", "-q", "-u", "origin", "main")

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def commit(self, cwd: Path, files: dict[str, str], message: str) -> str:
        for path, text in files.items():
            put(cwd / path, text)
        self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def advance_main(self, files: dict[str, str], message: str) -> str:
        sha = self.commit(self.repo, files, message)
        self.git(self.repo, "push", "-q", "origin", "main")
        return sha

    def feature(self, slug: str, track: str | None, prefix: str = "feat") -> Path:
        if track is not None:
            put(self.notes / "specs" / f"20261024 {TITLE}.md",
                f"---\ntype: spec\nfeature: {slug}\ntrack: {track}\nreview_status: approved\nacceptance:\nshipped:\n"
                f"---\n\n# {TITLE}\n")
        self.git(self.repo, "fetch", "-q", "origin")
        wt = self.root / "worktrees" / f"acme-tokens-{slug}"
        self.git(self.repo, "worktree", "add", "-q", "-b", f"{prefix}/{slug}", str(wt), "refs/remotes/origin/main")
        return wt.resolve()

    def gate_green(self, wt: Path, sha: str | None) -> None:
        plan = self.notes / "plans" / f"20261024 {TITLE} Plan.md"
        put(plan, f"---\nspec: 20261024 {TITLE}.md\n---\n\n# Plan\n\n### Task 1: Build it\n")
        lines = ["# Ledger", "Task 1: complete (tests green)"]
        if sha is not None:
            lines.append(f"Feature gate: green at {sha}")
        put(wt / ".superpowers" / "sdd" / plan.stem / "progress.md", "\n".join(lines) + "\n")

    def review_done(self, wt: Path, sha: str) -> Path:
        git_dir = Path(self.git(wt, "rev-parse", "--absolute-git-dir"))
        marker = git_dir / "dev-loop" / "review-done"
        put(marker, sha + "\n")
        return marker

    def rebase(self, wt: Path) -> None:
        self.git(wt, "fetch", "-q", "origin")
        self.git(wt, "rebase", "-q", "refs/remotes/origin/main")

    def merge_check(self, cwd: Path, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), "merge-check", *args], cwd=cwd, env=self.env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"merge-check: stdout is not one JSON object (exit {result.returncode}): "
                                 f"{result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        if not isinstance(payload, dict):
            raise AssertionError(f"merge-check: stdout is not a JSON object: {payload!r}")
        return result.returncode, payload


def reasons(payload: dict, name: str) -> list[dict]:
    return [item for item in payload.get("reasons") or [] if isinstance(item, dict) and item.get("reason") == name]


class MergeDecisionFlowTests(unittest.TestCase):
    """F1: Stage 7 merge decision, oracle lines 1 to 15."""

    def project(self, main_config: str | None = None) -> Project:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Project(Path(tmp.name).resolve(), main_config if main_config is not None else config_text())

    def reviewed_feature(self, main_config: str | None = None, track: str | None = "M",
                         work: dict[str, str] | None = None, gate: bool = True) -> tuple[Project, Path, str]:
        """A feature worktree with its held-out tests, its build commit, and a reviewed head at HEAD."""
        project = self.project(main_config)
        slug = SLUG if track is not None else S_SLUG
        wt = project.feature(slug, track, prefix="feat" if track is not None else "fix")
        project.commit(wt, {f"tests/acceptance/test_{slug.replace('-', '_')}.py":
                            "def test_flow():\n    assert True\n"}, "Add the acceptance tests")
        reviewed = project.commit(wt, work or {"src/app.py": APP_FEATURE}, "Build the feature")
        if track is None:
            project.review_done(wt, reviewed)
        else:
            project.gate_green(wt, reviewed if gate else None)
        return project, wt, reviewed

    def check(self, project: Project, wt: Path) -> dict:
        code, out = project.merge_check(wt)
        self.assertEqual(code, 0, out)
        self.assertIn(out.get("merge"), ("auto", "human"), out)
        self.assertIsInstance(out.get("reasons"), list, out)
        return out

    def assert_auto(self, out: dict, reviewed: str) -> None:
        self.assertEqual(out["merge"], "auto", out)
        self.assertEqual(out["reasons"], [], out)
        self.assertTrue(same_sha(out.get("reviewed"), reviewed), out)

    def assert_reason(self, out: dict, name: str) -> list[dict]:
        found = reasons(out, name)
        self.assertTrue(found, f"no {name} reason: {out}")
        self.assertEqual(out["merge"], "human", out)
        return found

    def assert_unreviewed(self, out: dict, commit: str, path: str | None = None) -> None:
        found = [item for item in self.assert_reason(out, "unreviewed") if same_sha(item.get("commit"), commit)]
        self.assertTrue(found, f"no unreviewed reason for {commit}: {out}")
        if path is not None:
            self.assertIn(path, found[0].get("paths") or [], out)

    def test_f1_stage_7_merge_decision(self) -> None:
        """O1 to O15: merge-check on Track S, M and L worktrees."""
        with self.subTest(oracle=1):
            project, wt, reviewed = self.reviewed_feature()
            project.advance_main({"src/billing.py": "RATE = 3\n"}, "Add billing on main")
            project.rebase(wt)
            self.assertNotEqual(project.git(wt, "rev-parse", "HEAD"), reviewed)
            self.assert_auto(self.check(project, wt), reviewed)

        with self.subTest(oracle=2):
            project, wt, _ = self.reviewed_feature(config_text(merge="human"))
            self.assert_reason(self.check(project, wt), "merge-human")

        with self.subTest(oracle=3):
            project, wt, _ = self.reviewed_feature(track="L")
            self.assert_reason(self.check(project, wt), "track-l")

        with self.subTest(oracle=4):
            project, wt, _ = self.reviewed_feature(config_text(merge_human_paths=["src/auth/*"]),
                                                   work={"src/auth/token.py": "def refresh(token):\n    return token[::-1]\n"})
            found = self.assert_reason(self.check(project, wt), "human-path")
            self.assertIn(("src/auth/token.py", "src/auth/*"),
                          [(item.get("path"), item.get("pattern")) for item in found])

        with self.subTest(oracle=5):
            project, wt, _ = self.reviewed_feature(config_text(merge_human_paths=["src/auth/*"]), work={
                "dev-loop.json": config_text(merge_human_paths=[]),
                "src/auth/token.py": "def refresh(token):\n    return token.upper()\n"})
            out = self.check(project, wt)
            self.assertIn(("src/auth/token.py", "src/auth/*"),
                          [(item.get("path"), item.get("pattern")) for item in self.assert_reason(out, "human-path")])
            self.assert_reason(out, "config-changed")

        with self.subTest(oracle=6):
            project, wt, _ = self.reviewed_feature(config_text(merge="human"), work={
                "dev-loop.json": config_text(merge="auto"), "src/app.py": APP_FEATURE})
            out = self.check(project, wt)
            self.assert_reason(out, "merge-human")
            self.assert_reason(out, "config-changed")

        with self.subTest(oracle=7):
            project, wt, _ = self.reviewed_feature()
            late = project.commit(wt, {"src/app.py": "def colour():\n    return 'navy'\n"}, "Tweak after review")
            self.assert_unreviewed(self.check(project, wt), late, "src/app.py")

        with self.subTest(oracle=8):
            project, wt, _ = self.reviewed_feature()
            late = project.commit(wt, {"tests/unit/test_x.py": "def test_x():\n    assert 2 == 2\n",
                                       "README.md": "# Acme tokens\n\nNow with refresh.\n"}, "Docs and tests")
            out = self.check(project, wt)
            self.assertEqual(reasons(out, "unreviewed"), [], out)
            self.assertTrue([item for item in out.get("unreviewed_minor") or []
                             if isinstance(item, dict) and same_sha(item.get("commit"), late)], out)
            self.assertEqual(out["merge"], "auto", out)

        with self.subTest(oracle=9):
            project, wt, reviewed = self.reviewed_feature()
            project.advance_main({"src/app.py": "def colour():\n    return 'green'\n"}, "Recolour on main")
            project.git(wt, "fetch", "-q", "origin")
            conflict = subprocess.run(["git", "rebase", "refs/remotes/origin/main"], cwd=wt, env=project.env,
                                      capture_output=True, text=True)
            self.assertNotEqual(conflict.returncode, 0, "the rebase was expected to stop on a conflict")
            put(wt / "src" / "app.py", "def colour():\n    return 'teal'\n")
            project.git(wt, "add", "src/app.py")
            project.git(wt, "rebase", "--continue")
            resolved = project.git(wt, "rev-parse", "HEAD")
            self.assertNotEqual(resolved, reviewed)
            self.assert_unreviewed(self.check(project, wt), resolved)

        with self.subTest(oracle=10):
            project, wt, _ = self.reviewed_feature()
            late = project.commit(wt, {"skill/SKILL.md": "# Acme skill\n\nStep one, then step two.\n"},
                                  "Reword the skill")
            self.assert_unreviewed(self.check(project, wt), late)

        with self.subTest(oracle=11):
            project, wt, reviewed = self.reviewed_feature(track=None)
            project.advance_main({"src/billing.py": "RATE = 3\n"}, "Add billing on main")
            project.rebase(wt)
            self.assert_auto(self.check(project, wt), reviewed)
            Path(project.git(wt, "rev-parse", "--absolute-git-dir"), "dev-loop", "review-done").unlink()
            self.assert_reason(self.check(project, wt), "no-review")

        with self.subTest(oracle=12):
            project, wt, _ = self.reviewed_feature(gate=False)
            self.assert_reason(self.check(project, wt), "no-review")

        with self.subTest(oracle=13):
            project, wt, _ = self.reviewed_feature('{"version": "2.1.1", "harnesses": [\n', work={
                "dev-loop.json": config_text(), "src/app.py": APP_FEATURE})
            self.assert_reason(self.check(project, wt), "config-unreadable")

        with self.subTest(oracle=14):
            project, _, _ = self.reviewed_feature()
            code, out = project.merge_check(project.repo)
            self.assertEqual(code, 1, out)
            self.assertTrue(out.get("error"), out)

        with self.subTest(oracle=15):
            project = self.project()
            unmerged = project.base
            project.advance_main({"dev-loop.json": config_text(merge="human")}, "Merges wait for a person")
            wt = project.feature(SLUG, "M")
            reviewed = project.commit(wt, {"src/app.py": APP_FEATURE}, "Build the feature")
            project.gate_green(wt, reviewed)
            project.git(wt, "tag", "origin/main", unmerged)
            project.git(wt, "branch", "origin/main", unmerged)
            self.assert_reason(self.check(project, wt), "merge-human")


class MachineSetupFlowTests(unittest.TestCase):
    """F2: setup of a new machine, oracle lines 16 to 19."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        notes = self.root / "notes"
        notes.mkdir()
        self.project = self.root / "fresh-project"
        self.project.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
        self.env.update({"HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"})
        subprocess.run(["git", "init", "-q"], cwd=self.project, env=self.env, check=True, capture_output=True)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                              cwd=self.project, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.config = json.loads((self.project / "dev-loop.json").read_text())

    def checker(self, harness: str, **env: str) -> tuple[int, str]:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(self.project)], env={**self.env, **env}, capture_output=True,
                                text=True, timeout=120)
        return result.returncode, result.stdout + result.stderr

    def user_action_lines(self, output: str) -> list[str]:
        lines = output.splitlines()
        heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
        self.assertTrue(heads, f"no 'User actions required' section:\n{output}")
        return lines[heads[0] + 1:]

    def install_skill(self) -> str:
        """A test security-audit folder at both harness paths (Claude through a link). Returns its tree id."""
        folder = self.home / ".agents" / "skills" / "security-audit"
        put(folder / "SKILL.md", "---\nname: security-audit\ndescription: Audit a codebase.\n---\n\n# Audit\n")
        put(folder / "VALIDATION-AND-REPORTING.md", "# Phase 3: Independently validate every candidate\n")
        link = self.home / ".claude" / "skills" / "security-audit"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(folder, target_is_directory=True)
        scratch = self.root / "tree-scratch"
        subprocess.run(["git", "init", "-q", str(scratch)], env=self.env, check=True, capture_output=True)
        tree_env = {**self.env, "GIT_DIR": str(scratch / ".git"), "GIT_WORK_TREE": str(folder)}
        subprocess.run(["git", "add", "-A", "."], cwd=folder, env=tree_env, check=True, capture_output=True)
        return subprocess.run(["git", "write-tree"], cwd=folder, env=tree_env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_f2_new_machine_setup(self) -> None:
        """O16 to O19: check_setup.py reports a bad merge_human_paths and a missing or wrong security-audit skill."""
        for harness in ("claude", "codex"):
            with self.subTest(oracle=17, harness=harness):
                _, output = self.checker(harness)
                lines = [line for line in self.user_action_lines(output) if PIN_COMMIT in line]
                self.assertTrue(lines, output)
                self.assertIn("security-audit", lines[0])

        tree = self.install_skill()
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40,64}", tree), tree)
        for harness in ("claude", "codex"):
            with self.subTest(oracle=18, harness=harness):
                _, output = self.checker(harness)
                lines = [line for line in self.user_action_lines(output) if PIN_COMMIT in line]
                self.assertTrue(lines, output)
                self.assertRegex(lines[0], rf"security-audit\S* is tree {tree}")

        for harness in ("claude", "codex"):
            with self.subTest(oracle=19, harness=harness):
                _, output = self.checker(harness, DEVLOOP_TEST_SECURITY_AUDIT_TREE=tree)
                self.assertEqual([line for line in output.splitlines() if "security-audit" in line], [], output)

        for value in ("src/*", [""]):
            (self.project / "dev-loop.json").write_text(json.dumps({**self.config, "merge_human_paths": value},
                                                                   indent=2) + "\n")
            for harness in ("claude", "codex"):
                with self.subTest(oracle=16, value=value, harness=harness):
                    code, output = self.checker(harness, DEVLOOP_TEST_SECURITY_AUDIT_TREE=tree)
                    self.assertNotEqual(code, 0, output)
                    self.assertTrue([line for line in output.splitlines() if "merge_human_paths" in line], output)


class KitFileTests(unittest.TestCase):
    def test_o20_codex_reviewers_reason_at_high_effort(self) -> None:
        """O20: final-reviewer.toml and security-reviewer.toml set model_reasoning_effort = "high"."""
        for name in ("final-reviewer.toml", "security-reviewer.toml"):
            with self.subTest(agent=name):
                path = CODEX_AGENTS / name
                if tomllib is not None:
                    self.assertEqual(tomllib.loads(path.read_text()).get("model_reasoning_effort"), "high")
                else:
                    self.assertRegex(path.read_text(), r'(?m)^model_reasoning_effort\s*=\s*"high"\s*$')


if __name__ == "__main__":
    unittest.main()
