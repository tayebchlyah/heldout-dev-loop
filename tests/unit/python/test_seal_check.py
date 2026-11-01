"""`devloop.py seal-check`, the gate seal report, `weakened` and `kit-changed`."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from test_seal import SUITE, sha

from seal import ACCEPTANCE_DIR  # noqa: E402
from seal import audit as audit_module  # noqa: E402
from test_devloop import git, put, run_cli  # noqa: E402


class SealRepo(unittest.TestCase):
    """A repo whose `main` holds a README, checked out on `feat/x`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "README.md", "readme\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "feat/x")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def commit(self, path: str, text: str, message: str = "change") -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def seal(self, path: str, text: str) -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "--", path)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, out)
        return out["commit"]

    def check(self, *args: str) -> tuple[int, dict]:
        return run_cli("seal-check", *args, cwd=self.repo)

    def violations(self, *args: str) -> list[tuple]:
        return [(v["reason"], v["commit"], v["path"]) for v in self.check(*args)[1]["violations"]]


class SealCheckTests(SealRepo):
    def test_seal_commit_is_ok(self) -> None:
        self.seal(SUITE, "def test_flow():\n    assert True\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_unsealed_then_revert_is_ok(self) -> None:
        sha = self.commit(SUITE, "def test_flow():\n    pass\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertFalse(out["ok"])
        self.assertEqual(self.violations(), [("unsealed", None, SUITE)])
        git(self.repo, "revert", "--no-edit", sha)
        self.assertEqual(self.check()[0], 0)

    def test_amended_seal_is_bad_and_later_change_is_after_seal(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / SUITE, "b\n")
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        amended = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertIn(("bad-seal", amended, None), self.violations())
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.seal(SUITE, "a\n")
        self.commit(SUITE, "b\n")
        self.assertEqual(self.violations(), [("changed-after-seal", None, SUITE)])

    def test_seal_with_a_path_outside_is_bad(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / "README.md", "changed\n")
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        self.assertIn(("bad-seal", git(self.repo, "rev-parse", "HEAD").strip(), None), self.violations())

    def test_rebase_onto_main_with_another_sealed_file_is_ok(self) -> None:
        self.seal(SUITE, "a\n")
        git(self.repo, "checkout", "-q", "main")
        self.seal(f"{ACCEPTANCE_DIR}/other/test_other.py", "o\n")
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "rebase", "-q", "main")
        self.assertEqual(self.check()[0], 0)

    def test_clean_rebase_that_merges_upstream_into_the_sealed_file_is_ok(self) -> None:
        """D1: the replayed seal commit gets a new blob, its patch value still matches."""
        lines = [f"line {n}\n" for n in range(20)]
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "".join(lines))
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
        self.seal(SUITE, "".join(["changed by the feature\n"] + lines[1:]))
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "".join(lines[:-1] + ["changed upstream\n"]))
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "rebase", "-q", "main")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_seal_after_hooks_that_change_the_files_and_the_message_is_ok(self) -> None:
        """D3: pre-commit rewrites the staged blobs, commit-msg appends a paragraph after the trailers."""
        hooks = Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "hooks"
        put(hooks / "pre-commit", "#!/bin/sh\ngit diff --cached --name-only -z -- tests/acceptance/ |"
            " xargs -0 -I{} sh -c 'echo hooked >> \"{}\" && git add -- \"{}\"'\n")
        put(hooks / "commit-msg", "#!/bin/sh\nprintf '\\nAppended by a hook.\\n' >> \"$1\"\n")
        for hook in ("pre-commit", "commit-msg"):
            (hooks / hook).chmod(0o755)
        put(self.repo / SUITE, "a\n")
        git(self.repo, "add", "--", SUITE)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["commit"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(git(self.repo, "show", f"HEAD:{SUITE}"), "a\nhooked\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_uncommitted_only_with_the_flag(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / f"{ACCEPTANCE_DIR}/x/test_new.py", "n\n")
        put(self.repo / SUITE, "changed\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))
        self.assertEqual(sorted(self.violations("--uncommitted")), [
            ("uncommitted", None, SUITE), ("uncommitted", None, f"{ACCEPTANCE_DIR}/x/test_new.py")])

    def test_uncommitted_untracked_counts_test_files_only(self) -> None:
        """D4: tracked changes count, an untracked path counts only when its name matches a test glob."""
        data = f"{ACCEPTANCE_DIR}/x/data.json"
        self.seal(data, "{}\n")
        cases = [
            ("untracked bytecode", f"{ACCEPTANCE_DIR}/x/__pycache__/x.pyc", False),
            ("untracked fixture", f"{ACCEPTANCE_DIR}/x/fixture.json", False),
            ("untracked test_*.py", f"{ACCEPTANCE_DIR}/x/test_new.py", True),
            ("untracked *_test.go", f"{ACCEPTANCE_DIR}/x/flow_test.go", True),
            ("untracked *.spec.*", f"{ACCEPTANCE_DIR}/x/flow.spec.ts", True),
            ("tracked fixture changed", data, True),
        ]
        for name, path, counted in cases:
            with self.subTest(name):
                git(self.repo, "checkout", "-q", "--", data)
                git(self.repo, "clean", "-qfdx")
                put(self.repo / path, "changed\n")
                self.assertEqual(self.violations("--uncommitted"), [("uncommitted", None, path)] if counted else [])

    def test_offending_commits_listed(self) -> None:
        """D6: unsealed and changed-after-seal list the non-seal commits that touched the path, no valid seal."""
        sealed = self.seal(SUITE, "a\n")
        git(self.repo, "revert", "--no-commit", sealed)
        code, out = run_cli("seal", "--feature", "x", "--message", "revert", "--reverts", sealed, cwd=self.repo)
        self.assertEqual(code, 0, out)
        after = self.commit(SUITE, "after the revert seal\n")
        other = f"{ACCEPTANCE_DIR}/x/test_other.py"
        unsealed = self.commit(other, "unsealed\n")
        self.assertEqual(self.check()[1]["violations"], [
            {"reason": "unsealed", "commit": None, "path": other, "commits": [unsealed]},
            {"reason": "changed-after-seal", "commit": None, "path": SUITE, "commits": [after]}])

    def test_no_base_is_ok(self) -> None:
        git(self.repo, "branch", "-m", "main", "trunk")  # no main, no origin/main: no base
        self.commit(SUITE, "unsealed\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_open_taint_makes_ok_false(self) -> None:
        start = git(self.repo, "rev-parse", "HEAD").strip()
        old = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "outside the branch history").strip()
        sha = self.commit("src/total.py", "def total(xs):\n    return sum(xs)\n")
        record = {"agent_id": "a1", "agent_type": "implementer", "at": "t", "set": "acceptance", "lines": [],
                  "start": start, "end": sha, "attribution": "transcript"}
        taint = {"records": [{**record, "commits": [audit_module.commit_record(self.repo, sha)]},
                             {**record, "agent_id": "closed", "commits": []},
                             {**record, "agent_id": "old", "end": old, "commits": []}],
                 "skipped": [{"reason": "no-transcript", "end": sha}, {"reason": "error", "end": old}]}
        path = audit_module.taint_file(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(taint))
        code, out = self.check()
        self.assertEqual((code, out["ok"], out["violations"]), (1, False, []))
        self.assertEqual([r["agent_id"] for r in out["taint"]], ["a1"])
        self.assertEqual(out["warnings"], [taint["records"][1], taint["skipped"][0]])
        for text in ("{not json", "[]"):
            with self.subTest(taint=text):
                path.write_text(text)
                code, out = self.check()
                self.assertEqual((code, out["ok"]), (1, False), out)
                self.assertEqual(self.violations(), [("taint-unreadable", None, str(path))])

    def test_closed_record_at_base_is_a_warning(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD").strip()
        record = {"agent_id": "a1", "agent_type": "implementer", "at": "t", "set": "acceptance", "lines": [],
                  "start": head, "end": head, "commits": [], "attribution": "transcript"}
        path = audit_module.taint_file(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"records": [record], "skipped": []}))
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": [record]}))

    def test_quoted_trailer_in_an_earlier_paragraph_is_no_seal(self) -> None:
        put(self.repo / "docs.md", "docs\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "docs\n\nDev-Loop-Seal: abc\n\nCo-Authored-By: t <t@example.invalid>")
        self.assertEqual(self.check()[0], 0)

    def test_unicode_path(self) -> None:
        path = f"{ACCEPTANCE_DIR}/x/café flow.py"
        self.commit(path, "a\n")
        self.assertEqual(self.violations(), [("unsealed", None, path)])
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.seal(path, "a\n")
        self.assertEqual(self.check()[0], 0)
        put(self.repo / path, "b\n")
        self.assertEqual(self.violations("--uncommitted"), [("uncommitted", None, path)])


PASSING_GATE = "#!/bin/sh\necho all good\n"


class GateSealRepo(SealRepo):
    def gate(self) -> tuple[int, dict]:
        put(self.repo / "scripts" / "gate.sh", PASSING_GATE)
        (self.repo / "scripts" / "gate.sh").chmod(0o755)
        return run_cli("gate", "--summary", cwd=self.repo)


class GateSealTests(GateSealRepo):
    def test_unsealed_commit_fails_a_passing_gate(self) -> None:
        self.commit(SUITE, "unsealed\n")
        code, out = self.gate()
        self.assertEqual((code, out["exit"], out["seal"]["ok"]), (1, 0, False), out)
        self.assertEqual([v["reason"] for v in out["seal"]["violations"]], ["unsealed"])

    def test_sealed_passing_gate_is_ok(self) -> None:
        self.seal(SUITE, "a\n")
        code, out = self.gate()
        self.assertEqual((code, out["seal"], out["weakened"]),
                         (0, {"ok": True, "violations": [], "taint": [], "warnings": []}, []), out)


class WeakenedTests(GateSealRepo):
    def write(self, path: str, text: str | bytes) -> None:
        if isinstance(text, bytes):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_bytes(text)
        else:
            put(self.repo / path, text)

    def test_weakened_reasons(self) -> None:
        unit = "tests/unit/test_a.py"
        cases = [
            ("skip added", unit, "@pytest.mark.skip\ndef test_a():\n    assert f() == 1\n    assert g() == 2\n",
             [{"path": unit, "reason": "skip-added"}]),
            ("assert removed", unit, "def test_a():\n    assert f() == 1\n", [{"path": unit, "reason": "assertions-removed"}]),
            ("js only and expect removed", "web/a.test.js", "it.only('a', () => {\n})\n",
             [{"path": "web/a.test.js", "reason": "skip-added"}, {"path": "web/a.test.js", "reason": "assertions-removed"}]),
            ("unicode path", "tests/unit/test_café.py", "def test_a():\n    pass\n",
             [{"path": "tests/unit/test_café.py", "reason": "assertions-removed"}]),
            ("not a test file", "src/a.py", "pass\n", []),
            ("assertion added", unit, "def test_a():\n    assert f() == 1\n    assert g() == 2\n    assert h()\n", []),
            ("latin-1 file", "tests/unit/test_lat.py", b"# caf\xe9\nassert x\n@pytest.mark.skip\n",
             [{"path": "tests/unit/test_lat.py", "reason": "skip-added"}]),
            ("binary file", "web/logo.test.png", b"\x89PNG\x00\xfe\xff", []),
        ]
        originals = {unit: "def test_a():\n    assert f() == 1\n    assert g() == 2\n",
                     "web/a.test.js": "it('a', () => {\n  expect(f()).toBe(1)\n})\n",
                     "tests/unit/test_café.py": "def test_a():\n    assert f()\n", "src/a.py": "assert x\n",
                     "tests/unit/test_lat.py": b"# caf\xe9\nassert x\n", "web/logo.test.png": b"\x89PNG\x00\xff"}
        git(self.repo, "checkout", "-q", "main")
        for path, text in originals.items():
            self.write(path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tests")
        for name, path, text, expected in cases:
            with self.subTest(name):
                git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
                self.write(path, text)
                git(self.repo, "add", "-A")
                git(self.repo, "commit", "-q", "-m", name)
                code, out = self.gate()
                self.assertEqual((code, out["weakened"]), (0, expected), out)


class SkipMarkerTests(unittest.TestCase):
    def test_each_marker_and_its_near_miss(self) -> None:
        from seal.weakened import skip_marker
        rows = [
            ("@pytest.mark.skip", "@mypytest.mark.skipper"),
            ("@pytest.mark.skipif(x)", "pytest.mark.skipped"),
            ("pytest.skip('slow')", "pytest.skipped('slow')"),
            ("@pytest.mark.xfail", "@pytest.mark.xfailing"),
            ("pytest.xfail('bug')", "notpytest.xfail('bug')"),
            ("@unittest.skip('slow')", "@unittest.skipping"),
            ("@unittest.skipIf(x, 'y')", "@unittest.skipIfNot(x)"),
            ("it.skip('a', () => {})", "it.skipped('a')"),
            ("describe.skip('a', () => {})", "stream.skipWhile(f)"),
            ("this.skip();", "this.skipper();"),
            ("ctx.skip()", "ctx.skipped()"),
            ("bench.skip('a', fn)", "bench.skipping('a')"),
            ("test.concurrent.skip(\"a\", ()=>{})", "test.concurrent.skips(\"a\")"),
            ("test.only('a', () => {})", "q.only_one()"),
            ("it.concurrent.only(\"a\")", "it.onlyif(\"a\")"),
            ("test.describe.only('a', () => {})", "options.onlyOne('a')"),
            ("test.describe.serial.skip('a', fn)", "test.describe.serial.skipAll('a')"),
            ("test.serial.only('a', t => {})", "test.serial.onlyFor('a')"),
            ("xit('a', () => {})", "sys.exit(1)"),
            ("xdescribe('a', () => {})", "maxdescribe('a')"),
            ("test.todo('a')", "list.todos()"),
            ("t.Skip(\"slow\")", "assert.Skipped()"),
            ("tt.Skip(\"later\")", "tt.SkipNow"),
        ]
        for hit, miss in rows:
            with self.subTest(hit=hit):
                self.assertTrue(skip_marker(hit))
                self.assertFalse(skip_marker(miss), miss)


KIT_FILE = ".agents/skills/dev-loop/scripts/seal/check.py"


KIT_AGENT = ".claude/agents/implementer.md"


KIT_LINK = ".claude/skills/dev-loop"


NEW_KIT_FILE = ".agents/skills/dev-loop/scripts/seal/new.py"
NOTES_SKILL = ".agents/skills/release-notes/SKILL.md"


class KitChangedTests(SealRepo):
    """A kit path differs from `<base>` and does not match the current MANIFEST: `kit-changed`."""

    def setUp(self) -> None:
        super().setUp()
        git(self.repo, "checkout", "-q", "main")
        put(self.repo / "dev-loop.json", '{"version": "2.2.0"}\n')
        put(self.repo / KIT_FILE, "def check(): pass\n")
        put(self.repo / KIT_AGENT, "agent\n")
        put(self.repo / NOTES_SKILL, "notes\n")
        os.makedirs(self.repo / ".claude/skills")
        os.symlink("../../.agents/skills/dev-loop", self.repo / KIT_LINK)
        self.entries = {KIT_FILE: sha("def check(): pass\n"), KIT_AGENT: sha("agent\n"),
                        KIT_LINK: "link:../../.agents/skills/dev-loop", NOTES_SKILL: sha("notes\n")}
        self.manifest()
        self.main = self.commit(".claude/agents/mine.md", "mine\n", "kit")
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")

    def manifest(self, **changes: str | None) -> None:
        """Writes the MANIFEST in the working tree, `entries` with `changes` (None drops the key)."""
        entries = {**self.entries, **changes}
        files = {key: value for key, value in entries.items() if value is not None}
        put(self.repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": "2.2.0", "files": files}))

    def stage(self) -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")

    def retarget(self) -> None:
        (self.repo / KIT_LINK).unlink()
        os.symlink("../../.agents/skills/other", self.repo / KIT_LINK)

    def no_manifest(self) -> None:
        for branch in ("main", "feat/x"):
            git(self.repo, "checkout", "-q", branch)
            git(self.repo, "rm", "-q", "-r", ".dev-loop")
            git(self.repo, "commit", "-q", "-m", "no kit")
        git(self.repo, "rebase", "-q", "main")
        put(self.repo / KIT_FILE, "changed\n")

    def test_kit_changed(self) -> None:
        edit = lambda: put(self.repo / KIT_FILE, "def check(): return OK\n")  # noqa: E731
        new = lambda path: put(self.repo / path, "x\n")  # noqa: E731
        drop = lambda: (self.repo / KIT_AGENT).unlink()  # noqa: E731
        rows = [
            ("edit, MANIFEST not updated", [edit, self.stage], (), [KIT_FILE]),
            ("edit and version bump", [edit, lambda: put(self.repo / "dev-loop.json", '{"version": "2.3.0"}\n'),
                                       self.stage], (), [KIT_FILE]),
            ("upgrade: edit and MANIFEST updated", [edit, lambda: self.manifest(**{KIT_FILE: sha("def check(): return OK\n")}),
                                                     self.stage], (), []),
            ("init --harness=both: new kit file listed", [lambda: new(".codex/agents/implementer.toml"),
                                                          lambda: self.manifest(**{".codex/agents/implementer.toml": sha("x\n")}),
                                                          self.stage], (), []),
            ("new file under the skill, not listed", [lambda: new(NEW_KIT_FILE), self.stage], (), [NEW_KIT_FILE]),
            ("new hook, not listed", [lambda: new(".dev-loop/hooks/claude/x.sh"), self.stage], (),
             [".dev-loop/hooks/claude/x.sh"]),
            ("kit agent deleted, still listed", [drop, self.stage], (), [KIT_AGENT]),
            ("kit agent deleted and unlisted", [drop, lambda: self.manifest(**{KIT_AGENT: None}), self.stage], (), []),
            ("link retargeted", [self.retarget, self.stage], (), [KIT_LINK]),
            ("invalid dev-loop.json and an edit", [edit, lambda: put(self.repo / "dev-loop.json", "{"), self.stage], (),
             [KIT_FILE]),
            ("files outside the kit", [lambda: new(".claude/agents/mine.md"), lambda: new(".agents/skills/other/SKILL.md"),
                                       lambda: new("skill/scripts/seal/check.py"), self.stage], (), []),
            ("uncommitted edit, no flag", [edit], (), []),
            ("uncommitted edit, flag", [edit], ("--uncommitted",), [KIT_FILE]),
            ("untracked new file, no flag", [lambda: new(NEW_KIT_FILE)], (), []),
            ("untracked new file, flag", [lambda: new(NEW_KIT_FILE)], ("--uncommitted",), [NEW_KIT_FILE]),
            ("continue skill file, flag", [lambda: new(".agents/skills/continue/SKILL.md")], ("--uncommitted",),
             [".agents/skills/continue/SKILL.md"]),
            ("pause skill file, committed", [lambda: new(".agents/skills/pause/agents/openai.yaml"), self.stage], (),
             [".agents/skills/pause/agents/openai.yaml"]),
            ("garden skill file, untracked, flag", [lambda: new(".agents/skills/garden/x.md")], ("--uncommitted",),
             [".agents/skills/garden/x.md"]),
            ("release skill file, committed", [lambda: new(".agents/skills/release/extra.md"), self.stage], (),
             [".agents/skills/release/extra.md"]),
            ("release-notes skill edit, committed", [lambda: put(self.repo / NOTES_SKILL, "edited\n"), self.stage], (),
             [NOTES_SKILL]),
            ("untracked bytecode", [lambda: new(".agents/skills/dev-loop/scripts/__pycache__/x.pyc")],
             ("--uncommitted",), []),
            ("no MANIFEST at base nor HEAD", [self.no_manifest, self.stage], ("--uncommitted",), []),
        ]
        for name, steps, args, expected in rows:
            with self.subTest(name):
                try:
                    for step in steps:
                        step()
                    self.assertEqual(self.violations(*args), [("kit-changed", None, path) for path in expected])
                finally:
                    git(self.repo, "checkout", "-q", "-f", "-B", "feat/x", self.main)
                    git(self.repo, "branch", "-q", "-f", "main", self.main)
                    git(self.repo, "clean", "-q", "-f", "-d", "-x")
