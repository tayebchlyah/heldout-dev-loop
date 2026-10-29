#!/usr/bin/env python3
"""Held-out acceptance tests for the merge-check binding (spec: Merge Check Binding), flows 4 and 5.

One test per `## Acceptance flows` item (F<n>), one subtest per `## Acceptance oracle` line (O<n>) it proves.
Seams and fixture choices: `merge_check_binding_support.py`; `release-check` (O30) on the `GitRepo` fixture of
`release_security_audit_support.py` (record line and run folder in the notes from `DEV_LOOP_NOTES`).

Fixture choices the spec leaves open:
- O28: the old record is the record of an earlier run with its `runs` replaced by runs of the same shape with no
  `cases` key: a full run with 4 failing, then a full green run.
- O29: the fake `docker` reports one `build` service, `api`; commit dates are set, the container date sits between
  the code commit and the `.github` commit.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import (APP_FEATURE, SLUG, TEST_BASE, GateRepo, Project, config_text, junit,
                                         reasons)
from release_security_audit_support import GitRepo, note_text, reasons_named, record_line

API = "a1" * 32


class Case(unittest.TestCase):

    def tmp(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name).resolve()

    def project(self, config: str | None = None) -> Project:
        return Project(self.tmp(), config)

    def check(self, project: Project, wt: Path) -> dict:
        code, out = project.merge_check(wt)
        self.assertEqual(code, 0, out)
        self.assertIn(out.get("merge"), ("auto", "human"), out)
        return out

    def reviewed(self, project: Project, branch: str = f"feat/{SLUG}", files: dict[str, str] | None = None
                 ) -> tuple[Path, str]:
        """A Track S feature worktree whose one commit is reviewed."""
        wt = project.worktree(branch)
        sha = project.commit(wt, files or {"src/app.py": APP_FEATURE}, "Build the feature")
        project.review(wt, sha, "S")
        return wt, sha


class ExecutablePathTests(Case):

    def test_f4_executable_paths_after_the_review_are_unreviewed(self) -> None:
        """F4, O9, O10, O10a, O29, O30: CI files, config files outside test folders and `docs` source are code."""
        project = self.project()
        wt, reviewed = self.reviewed(project)
        for path in (".github/workflows/deploy.test.yml", ".github/actions/test/action.yml", ".github/README.md",
                     "docker-compose.test.yml", "app/docs/views.py", "docs/conf.toml"):
            with self.subTest(oracle=9, path=path):
                late = project.commit(wt, {path: "name: acme\nrun: make deploy\n"}, f"Change {path}")
                out = self.check(project, wt)
                found = [item for item in reasons(out, "unreviewed")
                         if isinstance(item.get("commit"), str) and len(item["commit"]) >= 7
                         and late.startswith(item["commit"])]
                self.assertTrue(found, f"no unreviewed reason for {path}: {out}")
                self.assertIn(path, found[0].get("paths") or [], out)
                self.assertEqual(out["merge"], "human", out)
            project.git(wt, "reset", "-q", "--hard", reviewed)

        for oracle, files in (
                ("10", {"docs/guide.md": "# Guide\n", "docs/img/flow.png": b"\x89PNG\r\n\x1a\nflow",
                        "tests/unit/test_x.py": TEST_BASE + "\ndef test_y():\n    assert 2 == 2\n",
                        "src/x.test.ts": "test('x', () => {});\n", "docs/test_views.py": "def test_v():\n    pass\n",
                        "README.md": "# Acme shop\n\nNow with a guide.\n"}),
                ("10a", {"tests/fixtures/data.json": '{"id": 1}\n', "tests/fixtures/settings.yaml": "debug: true\n"})):
            with self.subTest(oracle=oracle):
                late = project.commit(wt, files, "Docs and tests")
                out = self.check(project, wt)
                self.assertEqual(reasons(out, "unreviewed"), [], out)
                commits = [str(item.get("commit")) for item in out.get("unreviewed_minor") or []
                           if isinstance(item, dict)]
                self.assertTrue([c for c in commits if len(c) >= 7 and late.startswith(c)], out)
                self.assertEqual(out["merge"], "auto", out)
            project.git(wt, "reset", "-q", "--hard", reviewed)

        with self.subTest(oracle=29):
            gate = GateRepo(self.tmp(), {"compose.yaml": "services:\n  api:\n    build: .\n",
                                         "Dockerfile": "FROM scratch\n"}, when="2026-01-01T00:00:00Z")
            gate.commit(gate.wt, {"app.py": "def widget():\n    return 2\n"}, "Change the widget",
                        when="2026-01-10T00:00:00Z")
            later = gate.commit(gate.wt, {".github/workflows/x.test.yml": "on: push\n"}, "Change the CI",
                                when="2026-01-12T00:00:00Z")
            (gate.fix / "docker-state.json").write_text(json.dumps({
                "services": {"api": {"build": {"context": ".", "dockerfile": "Dockerfile"}}},
                "containers": {"api": [API]}, "created": {API: "2026-01-11T00:00:00.123456789Z"}}))
            gate.fixture(junit(passing=1), 0)
            _, run, _ = gate.acceptance()
            stale = run.get("stale") or {}
            self.assertEqual(stale.get("state"), "stale", run)
            self.assertEqual(stale.get("reference"), later, run)

        with self.subTest(oracle=30):
            for files, stale_paths in (
                    ({".github/workflows/ci.yml": "on: push\n", "docker-compose.yml": "services: {}\n"},
                     [".github/workflows/ci.yml", "docker-compose.yml"]),
                    ({"tests/unit/test_x.py": TEST_BASE + "\ndef test_y():\n    assert 2 == 2\n",
                      "tests/fixtures/data.json": '{"id": 1}\n', "docs/guide.md": "# Guide\n"}, None)):
                r = GitRepo(self.tmp())
                r.tag("v1.0.0", r.A)
                r.run_folder(1, r.A)
                r.write_note(note_text([record_line(1, r.A)], []))
                r.tag("v1.1.0", r.side_commit(r.A, files, "C"))
                code, payload = r.devloop("release-check", "--tag", "v1.1.0")
                if stale_paths is None:
                    self.assertEqual(code, 0, payload)
                    self.assertIs(payload.get("ok"), True, payload)
                else:
                    self.assertEqual(code, 1, payload)
                    found = reasons_named(payload, "stale-audit")
                    self.assertTrue(found, payload)
                    for path in stale_paths:
                        self.assertIn(path, found[0].get("paths") or [], payload)


class AcceptanceVerdictTests(Case):

    def assert_run(self, gate: GateRepo, xml: str | None, exit_code: int, *args: str) -> tuple[int, dict]:
        gate.fixture(xml, exit_code)
        before = gate.ran()
        code, run, _ = gate.acceptance(*args)
        self.assertNotIn("error", run)
        self.assertEqual(gate.ran(), before + 1, f"the acceptance command did not run once: {run}")
        return code, run

    def assert_refused(self, gate: GateRepo, *args: str, names: tuple[str, ...] = ()) -> None:
        gate.fixture(junit(passing=1), 0)
        before, ran = gate.snapshot(), gate.ran()
        code, out, _ = gate.acceptance(*args)
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)
        for name in names:
            self.assertIn(name, str(out["error"]), out)
        self.assertEqual(gate.ran(), ran, "the acceptance command ran")
        self.assertEqual(gate.snapshot(), before, "a log, a reports folder or a record change was made")

    def guard(self, run: dict) -> dict:
        self.assertIsInstance(run.get("guard"), dict, run)
        return run["guard"]

    def records(self, run: dict) -> list[dict]:
        data = json.loads(Path(run["record"]).read_text())
        self.assertIsInstance(data.get("runs"), list, data)
        return data["runs"]

    def test_f5_acceptance_verdict_needs_the_main_command_and_executed_cases(self) -> None:
        """F5, O11, O12, O20 to O28: a `commands` change waits for the user; `acceptance` runs only the command
        `main` holds and reads green only with executed cases."""
        sh = {"acceptance": "sh run.sh"}
        project = self.project(config_text(commands=sh))
        for name, config in (("true", config_text(commands={"acceptance": "true"})), ("removed", config_text()),
                             ("gate", config_text(commands={**sh, "gate": "true"}))):
            with self.subTest(oracle=11, branch=name):
                wt, _ = self.reviewed(project, f"feat/cfg-{name}", {"dev-loop.json": config, "src/app.py": APP_FEATURE})
                out = self.check(project, wt)
                self.assertTrue(reasons(out, "config-changed"), out)
                self.assertEqual(out["merge"], "human", out)
        with self.subTest(oracle=11, branch="same"):
            wt, _ = self.reviewed(project, "feat/cfg-same")
            self.assertEqual(reasons(self.check(project, wt), "config-changed"), [])
        with self.subTest(oracle=12):
            project = self.project()
            wt, _ = self.reviewed(project, files={"dev-loop.json": config_text(commands={"acceptance": "true"}),
                                                  "src/app.py": APP_FEATURE})
            self.assertTrue(reasons(self.check(project, wt), "config-changed"))

        gate = GateRepo(self.tmp())
        with self.subTest(oracle=26):
            code, run = self.assert_run(gate, junit(passing=3), 0)
            self.assertEqual((run.get("cases"), run.get("failing")), (3, 0), run)
            self.assertEqual(self.guard(run).get("verdict"), "green", run)
            self.assertEqual(code, 0, run)
            self.assertEqual(self.records(run)[-1].get("cases"), 3, run)
        with self.subTest(oracle=27):
            code, run = self.assert_run(gate, junit(passing=1), 0, "--failed")
            self.assertEqual(self.guard(run).get("verdict"), "continue", run)
            self.assertEqual(code, 0, run)
        with self.subTest(oracle=24):
            count = len(self.records(run))
            code, run = self.assert_run(gate, junit(), 0)
            self.assertEqual(run.get("cases"), 0, run)
            self.assertEqual(self.guard(run).get("verdict"), "unknown", run)
            self.assertTrue(run.get("hint"), run)
            self.assertEqual(code, 1, run)
            runs = self.records(run)
            self.assertEqual((len(runs), runs[-1].get("cases")), (count + 1, 0), runs)
        with self.subTest(oracle=25):
            code, run = self.assert_run(gate, junit(skipped=2), 0)
            self.assertEqual(run.get("cases"), 0, run)
            self.assertEqual(self.guard(run).get("verdict"), "unknown", run)
            self.assertEqual(code, 1, run)
        with self.subTest(oracle=28):
            record = Path(run["record"])
            data = json.loads(record.read_text())
            template = {key: value for key, value in data["runs"][-1].items() if key != "cases"}
            data["runs"] = [{**template, "mode": "full", "exit": 1, "failing": 4, "counted": True,
                             "ids": [f"acme.Widgets::t{n}" for n in range(4)]},
                            {**template, "mode": "full", "exit": 0, "failing": 0, "counted": True, "ids": []}]
            record.write_text(json.dumps(data, indent=2) + "\n")
            code, run = self.assert_run(gate, junit(failing=("t_a", "t_b"), passing=1), 1)
            self.assertEqual(self.guard(run).get("verdict"), "continue", run)
            self.assertEqual(self.guard(run).get("counts"), [2], run)

        with self.subTest(oracle=20):
            gate.commit(gate.wt, {"dev-loop.json": gate.config({**gate.commands, "acceptance": "true"})}, "Swap")
            self.assert_refused(gate, names=("commands.acceptance", "main"))
            gate.git(gate.wt, "reset", "-q", "--hard", "HEAD~1")
        failed_only = gate.config({"acceptance_failed": gate.commands["acceptance_failed"]})
        with self.subTest(oracle=21, mode="full"):
            gate.commit(gate.wt, {"dev-loop.json": failed_only}, "Drop the command")
            self.assert_refused(gate)
            gate.git(gate.wt, "reset", "-q", "--hard", "HEAD~1")
        with self.subTest(oracle=21, mode="failed"):
            gate.commit(gate.wt, {"dev-loop.json": gate.config({**gate.commands, "acceptance_failed": "true"})},
                        "Swap the failed command")
            self.assert_refused(gate, "--failed")
            gate.git(gate.wt, "reset", "-q", "--hard", "HEAD~1")
        with self.subTest(oracle=22):
            gate.commit(gate.repo, {"dev-loop.json": failed_only}, "No acceptance on main")
            self.assert_refused(gate)
            gate.git(gate.repo, "reset", "-q", "--hard", "HEAD~1")
        with self.subTest(oracle=23):
            gate.git(gate.repo, "branch", "-m", "main", "trunk")
            self.assert_refused(gate)


if __name__ == "__main__":
    unittest.main()
