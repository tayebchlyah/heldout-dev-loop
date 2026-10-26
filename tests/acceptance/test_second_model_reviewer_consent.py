#!/usr/bin/env python3
"""Held-out oracle tests for the consent and the setup ask of the second-model reviewer (spec: Second-Model Reviewer).

Seams, fixture choices and shared helpers: `second_model_reviewer_support.py`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from second_model_reviewer_support import APP_FEATURE, base_env, Case, CHECKER, config_text, INSTALLER


class ConsentTests(Case):
    """F2: the project lock on origin/main and the local user lock decide (O4 to O10, O22 to O24)."""

    def test_o04_branch_cannot_turn_project_lock_on(self) -> None:
        for name, main_config in (("absent", config_text()), ("false", config_text(second_review=False))):
            with self.subTest(main=name):
                project = self.project(main_config=main_config)
                project.feature(files={"src/app.py": APP_FEATURE, "dev-loop.json": config_text(second_review=True)})
                project.stub()
                code, payload, _ = project.run("claude")
                self.assert_result(code, payload, "skipped", "project-off", exit=0)
                self.assertIsNone(project.stub_log(), "the stub ran")

    def test_o05_bad_project_config(self) -> None:
        duplicate = '{"version": "2.1.1", "harnesses": ["claude"], "second_review": false, "second_review": true}\n'
        for name, text in (("yes", config_text(second_review="yes")), ("one", config_text(second_review=1)),
                           ("null", config_text(second_review=None)), ("not-json", "{second_review: true\n"),
                           ("duplicate", duplicate)):
            with self.subTest(main=name):
                project = self.ready(main_config=text)
                code, payload, _ = project.run("claude")
                self.assert_result(code, payload, "failed", "bad-config", exit=1)
                self.assertIsNone(project.stub_log(), "the stub ran")

    def test_o06_forged_ref_or_replace_cannot_turn_lock_on(self) -> None:
        with self.subTest(forged="tag-and-branch"):
            project = self.project(main_config=config_text())
            wt = project.feature(files={"src/app.py": APP_FEATURE, "dev-loop.json": config_text(second_review=True)})
            project.git(wt, "tag", "origin/main", "HEAD")
            project.git(wt, "branch", "origin/main", "HEAD")
            project.stub()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "project-off", exit=0)
            self.assertIsNone(project.stub_log(), "the stub ran")
        with self.subTest(forged="replace"):
            project = self.project(main_config=config_text())
            wt = project.feature()
            old = project.git(wt, "rev-parse", "refs/remotes/origin/main:dev-loop.json")
            new = project.blob(wt, config_text(second_review=True).encode())
            project.git(wt, "replace", old, new)
            self.assertIn('"second_review": true', project.git(wt, "show", "refs/remotes/origin/main:dev-loop.json"))
            project.stub()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "project-off", exit=0)
            self.assertIsNone(project.stub_log(), "the stub ran")

    def test_o07_user_lock(self) -> None:
        for local, reason in (({"second_review": False}, "user-off"),
                              ({"second_review": {"codex": False}}, "user-off"),
                              (None, "not-chosen"),
                              ({"second_review": {"claude": {}}}, "not-chosen")):
            with self.subTest(local=local):
                project = self.ready(local=local)
                code, payload, _ = project.run("claude")
                self.assert_result(code, payload, "skipped", reason, exit=0)
                self.assertIsNone(project.stub_log(), "the stub ran")

    def test_o08_bad_local_shape(self) -> None:
        for local in ({"second_review": {"codex": {"model": "--oss"}}},
                      {"second_review": {"codex": {"model": "gpt\n"}}},
                      {"second_review": {"codex": {"effort": "max"}}},
                      {"second_review": {"codex": {"temp": 1}}},
                      {"second_review": {"codex": None}},
                      {"second_review": {"gemini": {}}},
                      {"second_review": True},
                      '{"second_review": {"codex": {}}\n'):
            with self.subTest(local=local):
                project = self.ready(local=local)
                code, payload, _ = project.run("claude")
                self.assert_result(code, payload, "failed", "bad-config", exit=1)
                self.assertIsNone(project.stub_log(), "the stub ran")

    def test_o09_nothing_to_review_rebase_no_main_ref(self) -> None:
        with self.subTest(case="nothing-to-review"):
            project = self.project()
            project.feature(files={})
            project.stub()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "nothing-to-review", exit=0)
        with self.subTest(case="rebase"):
            project = self.project()
            wt = project.feature()
            project.commit(project.repo, {"src/app.py": "def colour(theme):\n    return 'green'\n"}, "Green")
            project.git(project.repo, "push", "-q", "origin", "main")
            project.git(wt, "fetch", "-q", "origin")
            rebase = subprocess.run(["git", "rebase", "refs/remotes/origin/main"], cwd=wt, env=project.env,
                                    capture_output=True, text=True)
            self.assertNotEqual(rebase.returncode, 0, "the rebase did not stop on a conflict")
            project.stub()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "rebase", exit=0)
        with self.subTest(case="no-main-ref"):
            project = self.project(branch="master", origin=False)
            project.feature()
            project.stub()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "no-main-ref", exit=0)

    def test_o10_no_cli(self) -> None:
        with self.subTest(case="no codex"):
            project = self.project()
            project.feature()
            code, payload, _ = project.run("claude")
            self.assert_result(code, payload, "skipped", "no-cli", exit=0)
            self.assertIsNone(payload.get("record", "missing"), payload)
        with self.subTest(case="codex only inside the worktree"):
            project = self.project()
            wt = project.feature()
            planted = wt / "bin"
            project.stub(folder=planted)
            code, payload, _ = project.run("claude", path=[planted])
            self.assert_result(code, payload, "skipped", "no-cli", exit=0)
            self.assertIsNone(payload.get("record", "missing"), payload)
            self.assertIsNone(project.stub_log(), "the planted codex ran")


class SetupAskTests(unittest.TestCase):
    """F2: the `check_setup.py` ask and shape checks (O22 to O24)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        notes = self.root / "notes"
        (notes / "specs").mkdir(parents=True)
        tmpdir = self.root / "tmp"
        tmpdir.mkdir()
        self.env = base_env(self.home, tmpdir)
        self.project = self.root / "acme-gadgets"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                              cwd=self.project, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.config = json.loads((self.project / "dev-loop.json").read_text())
        self.local = json.loads((self.project / "dev-loop.local.json").read_text())
        origin = self.root / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.publish(None)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.project, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def write_config(self, value: object) -> None:
        config = dict(self.config)
        if value is not None:
            config["second_review"] = value
        (self.project / "dev-loop.json").write_text(json.dumps(config, indent=2) + "\n")

    def publish(self, value: object) -> None:
        """Commit dev-loop.json with that project value (None: no key) and push it to origin/main."""
        self.write_config(value)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", f"Project lock {value!r}")
        self.git("push", "-q", "origin", "HEAD:main")
        self.git("fetch", "-q", "origin")

    def set_local(self, value: object, present: bool = True) -> None:
        local = {k: v for k, v in self.local.items() if k != "second_review"}
        if present:
            local["second_review"] = value
        (self.project / "dev-loop.local.json").write_text(json.dumps(local, indent=2) + "\n")

    def checker(self, harness: str) -> str:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(self.project)], env=self.env, capture_output=True, text=True,
                                timeout=120)
        return result.stdout + result.stderr

    @staticmethod
    def about(line: str) -> bool:
        return bool(re.search(r"second[_ -]review", line, re.I)) or "dev-loop.local.json" in line

    def ask_lines(self, output: str, reviewer: str) -> list[str]:
        lines = output.splitlines()
        heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
        if not heads:
            return []
        return [line for line in lines[heads[0] + 1:]
                if "dev-loop.local.json" in line and "false" in line and reviewer in line]

    def assert_ask(self, harness: str, reviewer: str) -> None:
        output = self.checker(harness)
        self.assertTrue(self.ask_lines(output, reviewer), f"no ask line for {reviewer}:\n{output}")

    def assert_quiet(self, harness: str) -> None:
        output = self.checker(harness)
        self.assertEqual([line for line in output.splitlines() if self.about(line)], [], output)

    def test_o22_ask_when_project_on_and_user_silent(self) -> None:
        self.publish(True)
        self.set_local(None, present=False)
        self.assert_ask("claude", "codex")
        self.assert_ask("codex", "claude")
        self.set_local({"claude": {}})
        self.assert_ask("claude", "codex")

    def test_o23_no_line_when_answered_or_project_off(self) -> None:
        self.publish(True)
        self.set_local(None, present=False)
        self.assert_ask("claude", "codex")
        for local in (False, {"codex": False}, {"codex": {}}, {"codex": {"model": "gpt-5.5", "effort": "low"}}):
            with self.subTest(project=True, local=local):
                self.set_local(local)
                self.assert_quiet("claude")
        for project in (None, False):
            self.publish(project)
            for local in (None, False, {"codex": {}}, {"gemini": {}}, "yes"):
                with self.subTest(project=project, local=local):
                    self.set_local(local, present=local is not None)
                    self.assert_quiet("claude")
        with self.subTest(case="working tree true, origin/main without key"):
            self.publish(None)
            self.set_local(None, present=False)
            self.write_config(True)
            self.assert_quiet("claude")

    def test_o24_bad_shapes_fail(self) -> None:
        def failure_lines(output: str) -> list[str]:
            lines = output.splitlines()
            heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
            before = lines[:heads[0]] if heads else lines
            return [line for line in before if "second_review" in line]

        with self.subTest(case='project "on"'):
            self.publish("on")
            self.set_local({"codex": {}})
            output = self.checker("claude")
            self.assertTrue(failure_lines(output), output)
        self.publish(True)
        for local in ("yes", {"gemini": {}}, {"codex": {"model": "--oss"}}, {"codex": {"effort": "max"}},
                      {"codex": {"temp": 1}}, {"codex": None}):
            with self.subTest(local=local):
                self.set_local(local)
                output = self.checker("claude")
                self.assertTrue(failure_lines(output), output)


if __name__ == "__main__":
    unittest.main()
