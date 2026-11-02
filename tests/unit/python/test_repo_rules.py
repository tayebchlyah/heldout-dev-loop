#!/usr/bin/env python3
"""Tests for scripts/repo_rules.py (comments, subjects), the gate scope and temp leak check, the acceptance.sh temp leak guard, the audit sandbox image."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
from test_devloop import git, put  # noqa: E402
import repo_rules  # noqa: E402

RULES = ROOT / "scripts" / "repo_rules.py"
EMOJIS = repo_rules.gitmoji(ROOT)


def run_rules(cwd: Path, *args: str) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(RULES), *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    try:
        return result.returncode, json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")


class RulesRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(tmp, "init", "-q", "--bare", "origin.git")
        git(self.repo, "init", "-q")
        git(self.repo, "remote", "add", "origin", str(tmp / "origin.git"))
        put(self.repo / "cliff.toml", (ROOT / "cliff.toml").read_text())
        put(self.repo / "README.md", "readme\n")
        self.base_files()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "✨(repo) Start")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "-b", "feat/x")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def base_files(self) -> None:
        pass

    def commit(self, subject: str, path: str = "a.txt", text: str | None = None) -> None:
        put(self.repo / path, text if text is not None else subject + "\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", subject)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()


class SubjectRuleTests(unittest.TestCase):
    def test_subject_table(self) -> None:
        for subject, ok in [
            ("✨(skill) Add x", True), ("⚡️(test) Run x", True), ("🧑‍💻(dev) x", True), ("⏪️(codex) Revert x", True),
            ("⚡(test) Run the skill suite in parallel, one process per CPU", False),
            ("⏪(codex) Revert x", False), ("🐛 fix: x", False), ("✨(Skill) Add x", False),
            ("✨(skill)Add x", False), ("✨(skill)  ", False), ("✨ fix(x) y", False), ("✨(-x) y", False),
            ("fix(x): y", False),
        ]:
            with self.subTest(subject=subject):
                self.assertIs(repo_rules.subject_ok(subject, EMOJIS), ok)

    def test_gitmoji_reads_each_parser_group(self) -> None:
        self.assertIn("⚡️", EMOJIS)
        self.assertIn("🧑‍💻", EMOJIS)
        self.assertNotIn("⚡", EMOJIS)


class SubjectCommandTests(RulesRepo):
    def test_older_bad_subjects_are_warnings_oldest_first(self) -> None:
        base = self.head()
        for i, subject in enumerate(["⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x", "✨(skill) Add x"]):
            self.commit(subject, f"f{i}.txt")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["base"], base)
        self.assertEqual(out["violations"], [])
        self.assertEqual([w["subject"] for w in out["warnings"]], ["⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x"])

    def test_bad_head_subject_fails(self) -> None:
        for subject in ["⚡(test) Run x", "⏪(codex) Revert x", "✨(Skill) Add x", "✨(skill)Add x"]:
            with self.subTest(subject=subject):
                self.commit(subject, "b.txt")
                code, out = run_rules(self.repo, "subjects")
                self.assertEqual(code, 1, out)
                self.assertEqual(out["violations"], [{"commit": self.head(), "subject": subject}])
                git(self.repo, "reset", "-q", "--hard", "HEAD~1")

    def test_merge_head_is_not_checked(self) -> None:
        self.commit("✨(skill) Add a", "a.txt")
        git(self.repo, "checkout", "-q", "-b", "side", "main")
        self.commit("✨(skill) Add b", "b.txt")
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [])

    def test_no_cliff_toml_is_an_error(self) -> None:
        (self.repo / "cliff.toml").unlink()
        self.commit("✨(skill) Add x")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_bad_cliff_toml_is_an_error(self) -> None:
        self.commit("✨(skill) Add x", "cliff.toml", "[git\n")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_no_base(self) -> None:
        git(self.repo, "remote", "remove", "origin")
        git(self.repo, "branch", "-q", "-m", "main", "trunk")
        for command in ("subjects", "comments"):
            with self.subTest(command=command):
                code, out = run_rules(self.repo, command)
                self.assertEqual(code, 0, out)
                self.assertEqual(out, {"ok": True, "base": None, "skipped": "no-base", "violations": []})

    def test_failed_git_call_is_an_error(self) -> None:
        for command in ("comments", "subjects"):
            with self.subTest(command=command):
                payload, code = getattr(repo_rules, command)(self.repo, "0" * 40)
                self.assertEqual(code, 1)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["base"], "0" * 40)
                self.assertIn("failed", payload["error"])

    def test_usage_errors(self) -> None:
        for args in (["nope"], ["subjects", "--bad"], []):
            with self.subTest(args=args):
                self.assertEqual(subprocess.run([sys.executable, str(RULES), *args], cwd=self.repo,
                                                capture_output=True).returncode, 2)
        with tempfile.TemporaryDirectory() as outside:
            code, out = run_rules(Path(outside), "subjects")
            self.assertEqual(code, 2)
            self.assertIn("error", out)


class PromotedCommandTests(RulesRepo):
    def release(self, tag: str, files: dict[str, str], annotated: bool = False) -> None:
        for path, text in files.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"🔖(release) {tag}")
        if annotated:
            git(self.repo, "tag", "-a", "-m", tag, tag)
        else:
            git(self.repo, "tag", tag)

    def promoted(self, tag: str) -> tuple[int, dict]:
        return run_rules(self.repo, "promoted", "--tag", tag)

    def final_after_rc(self, extra: dict[str, str], annotated: bool = False) -> tuple[int, dict]:
        self.release("v3.0.0-rc.2", {"skill/SKILL.md": "a\n"}, annotated)
        self.release("v3.0.0", {"package.json": "{}\n", "CHANGELOG.md": "c\n", "README.md": "r2\n",
                                ".agents/skills/x.md": "x\n", **extra}, annotated)
        return self.promoted("v3.0.0")

    def test_non_kit_changes_pass(self) -> None:
        code, out = self.final_after_rc({})
        self.assertEqual((code, out), (0, {"ok": True, "tag": "v3.0.0", "candidate": "v3.0.0-rc.2", "violations": []}))

    def test_kit_source_change_fails(self) -> None:
        code, out = self.final_after_rc({"skill/SKILL.md": "b\n"})
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["violations"], ["skill/SKILL.md"])

    def test_violations_sorted_over_every_kit_prefix(self) -> None:
        paths = ["skill/a", "skills/a", "claude/a", "codex/a", "bin/a", "lib/a"]
        self.release("v3.0.0-rc.1", {p: "1\n" for p in paths})
        self.release("v3.0.0", {p: "2\n" for p in paths})
        code, out = self.promoted("v3.0.0")
        self.assertEqual(code, 1)
        self.assertEqual(out["violations"], sorted(paths))

    def test_annotated_tags_give_the_same_result(self) -> None:
        self.assertEqual(self.final_after_rc({}, annotated=True)[0], 0)

    def test_annotated_tags_with_a_violation(self) -> None:
        code, out = self.final_after_rc({"skill/SKILL.md": "b\n"}, annotated=True)
        self.assertEqual((code, out["violations"]), (1, ["skill/SKILL.md"]))

    def test_highest_candidate_by_number(self) -> None:
        self.release("v3.0.0-rc.9", {"skill/a": "1\n"})
        self.release("v3.0.0-rc.10", {"skill/a": "2\n"})
        self.release("v3.0.0", {"package.json": "{}\n"})
        code, out = self.promoted("v3.0.0")
        self.assertEqual((code, out["candidate"]), (0, "v3.0.0-rc.10"))

    def test_other_versions_and_bad_forms_are_not_candidates(self) -> None:
        self.release("v3.0.1-rc.1", {"skill/a": "1\n"})
        self.release("v3.0.0-rc.01", {"skill/a": "2\n"})
        self.release("v3.0.0-rc.0", {"skill/a": "3\n"})
        self.release("v3.0.0-beta.1", {"skill/a": "4\n"})
        self.release("v3.0.0", {"package.json": "{}\n"})
        code, out = self.promoted("v3.0.0")
        self.assertEqual((code, out["candidate"], out["reason"]), (1, None, "no-release-candidate"))
        self.assertFalse(out["ok"])

    def test_no_candidate(self) -> None:
        self.release("v3.0.0", {"package.json": "{}\n"})
        code, out = self.promoted("v3.0.0")
        self.assertEqual(code, 1)
        self.assertEqual((out["ok"], out["candidate"], out["reason"]), (False, None, "no-release-candidate"))

    def test_release_candidate_tag_is_skipped(self) -> None:
        self.release("v3.0.0-rc.3", {"skill/a": "1\n"})
        code, out = self.promoted("v3.0.0-rc.3")
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped"], "not-a-final-tag")

    def test_missing_release_candidate_tag_is_skipped(self) -> None:
        self.release("v3.0.0-rc.1", {"skill/a": "1\n"})
        code, out = self.promoted("v3.0.0-rc.3")
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped"], "not-a-final-tag")

    def test_unknown_tag_and_bad_names_are_usage_errors(self) -> None:
        self.release("v3.0.0-rc.1", {"skill/a": "1\n"})
        for tag in ("v9.9.9", "3.0.0", "v3.0", "v3.0.0-rc.0", "v03.0.0", "v3.0.0-beta.1", "main"):
            with self.subTest(tag=tag):
                code, out = self.promoted(tag)
                self.assertEqual(code, 2)
                self.assertIn("error", out)

    def test_tag_is_required(self) -> None:
        self.assertEqual(subprocess.run([sys.executable, str(RULES), "promoted"], cwd=self.repo,
                                        capture_output=True).returncode, 2)

    def sha_run(self, extra: dict[str, str], *args: str) -> tuple[int, dict]:
        self.release("v3.0.0-rc.2", {"skill/SKILL.md": "a\n"})
        for path, text in {"package.json": "{}\n", "CHANGELOG.md": "c\n",
                           ".agents/skills/x/SKILL.md": "x\n", **extra}.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "🔖(release) v3.0.0")
        return run_rules(self.repo, "promoted", "--tag", "v3.0.0", *args)

    def test_promoted_sha_before_the_tag(self) -> None:
        code, out = self.sha_run({}, "--sha", "HEAD")
        self.assertEqual((code, out["ok"], out["candidate"]), (0, True, "v3.0.0-rc.2"))

    def test_promoted_sha_kit_source_change(self) -> None:
        code, out = self.sha_run({"skill/SKILL.md": "b\n"}, "--sha", "HEAD")
        self.assertEqual((code, out["violations"]), (1, ["skill/SKILL.md"]))

    def test_promoted_sha_unresolved(self) -> None:
        code, out = self.sha_run({}, "--sha", "nope")
        self.assertEqual(code, 2)
        self.assertIn("error", out)

    def test_sha_needs_promoted(self) -> None:
        self.assertEqual(subprocess.run([sys.executable, str(RULES), "subjects", "--sha", "HEAD"], cwd=self.repo,
                                        capture_output=True).returncode, 2)

    def test_runs_without_a_main_base(self) -> None:
        git(self.repo, "branch", "-m", "other")
        git(self.repo, "branch", "-D", "main")
        code, out = self.final_after_rc({})
        self.assertEqual((code, out["ok"]), (0, True))


class RepoSubjectsTests(unittest.TestCase):
    def test_this_repo(self) -> None:
        code, out = run_rules(ROOT, "subjects")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])


SINCE = "# `--since` is a devloop.py drive and sessions option, never a Stryker one."
EXCEPTION = "# Exception: a bare --force-with-lease (with or without --force-if-includes) to the"
ALLOWED = ["# shortcut: no lock, add one when two writers exist", "# see https://example.invalid/x", "# noqa: E501",
           "// eslint-disable-next-line no-console", "# allow-comment: dependency, git 2.40 drops the flag",
           "# fixes #123", "# RFC 3339 time", "# SPDX-License-Identifier: MIT", "# Copyright 2026",
           "# shellcheck disable=SC2086", "// @ts-expect-error", "# type: ignore", "# pragma: no cover"]


class CommentRuleTests(unittest.TestCase):
    def test_kind_table(self) -> None:
        for text in ALLOWED:
            with self.subTest(text=text):
                self.assertTrue(repo_rules.kind_ok(text))
        for text in [SINCE, EXCEPTION, "# shortcut: no reason", "# plain words", "# 3. A plain push asks.",
                     "# record type: decision", "# License check first", "# no pragma here", "# fmt: see below"]:
            with self.subTest(text=text):
                self.assertFalse(repo_rules.kind_ok(text))

    def test_comment_lines_table(self) -> None:
        for path, text, expected in [
            ("a.py", 'def f():\n    """Doc.\n    # not a comment\n    """\n    # real\n    x = 1  # trailing\n',
             {5: "# real"}),
            ("a.py", "def f(:\n    # x\n", {2: "# x"}),
            ("a.sh", "#!/bin/sh\n  # x\necho '# no'\n", {1: "#!/bin/sh", 2: "# x"}),
            ("a.mjs", "// x\n# y\nconst a = 1 // z\n", {1: "// x"}),
            ("a.ts", "x = 1 // y\n  // z\n", {2: "// z"}),
        ]:
            with self.subTest(path=path, text=text):
                self.assertEqual(repo_rules.comment_lines(path, text), expected)

    def test_allowed_lines(self) -> None:
        text = "#!/bin/sh\n# head one\n\n# head two\nset -u\n# plain\n# more\n# shortcut: x, y\n# next\n# after\necho\n# z\n"
        self.assertEqual(repo_rules.allowed_lines("a.sh", text), {1, 2, 4, 8, 9, 10})
        self.assertEqual(repo_rules.allowed_lines("a.py", '"""Doc."""\n# x\n'), set())


class CommentCommandTests(RulesRepo):
    def base_files(self) -> None:
        put(self.repo / "a.sh", "#!/bin/bash\n# head\nset -u\n\n# 3. A plain push asks.\nhas x\n")
        put(self.repo / "m.py", '"""Mod."""\n\n\ndef f():\n    return 1\n')
        put(self.repo / "old.py", '"""Old."""\n\n\ndef g():\n    # moved words stay the same\n    return 2\n')

    def comments(self) -> tuple[int, dict]:
        return run_rules(self.repo, "comments")

    def assert_violations(self, expected: list[tuple[str, int, str]]) -> None:
        code, out = self.comments()
        self.assertEqual([(v["path"], v["line"], v["text"]) for v in out["violations"]], expected, out)
        self.assertEqual(code, 1 if expected else 0)
        self.assertEqual(out["ok"], not expected)

    def test_mid_file_shell_comment(self) -> None:
        self.commit("✨(x) Add", "a.sh", "#!/bin/bash\n# head\nset -u\n# plain words\n\n# 3. A plain push asks.\nhas x\n")
        self.assert_violations([("a.sh", 4, "# plain words")])

    def test_since_comment_in_a_function(self) -> None:
        self.commit("✨(x) Add", "m.py", f'"""Mod."""\n\n\ndef f():\n    {SINCE}\n    return 1\n')
        self.assert_violations([("m.py", 5, SINCE)])

    def test_continuation_of_a_base_line_still_fails(self) -> None:
        self.commit("✨(x) Add", "a.sh",
                    f"#!/bin/bash\n# head\nset -u\n\n# 3. A plain push asks.\n{EXCEPTION}\nhas x\n")
        self.assert_violations([("a.sh", 6, EXCEPTION)])

    def test_allowed_kinds_mid_file(self) -> None:
        body = "".join(f"x{i}=1\n{line}\n" for i, line in enumerate(ALLOWED) if line.startswith("#"))
        self.commit("✨(x) Add", "n.sh", "#!/bin/sh\n# lead\n\n# lead two\nset -u\n" + body + "# shortcut: a, b\n# next line\n")
        put(self.repo / "n.mjs", "const a = 1\n// eslint-disable-next-line no-console\n// @ts-expect-error\n")
        self.assert_violations([])

    def test_uncommitted_untracked_and_ignored(self) -> None:
        put(self.repo / "m.py", f'"""Mod."""\n\n\ndef f():\n    # uncommitted\n    return 1\n')
        put(self.repo / "new dir" / "u.sh", "set -u\n# untracked\n")
        put(self.repo / "ign" / "i.sh", "set -u\n# ignored\n")
        put(self.repo / ".gitignore", "ign/\n")
        self.assert_violations([("m.py", 5, "# uncommitted"), ("new dir/u.sh", 2, "# untracked")])

    def test_skipped_paths_and_types(self) -> None:
        for path in ["tests/acceptance/t.py", ".agents/a.sh", ".claude/c.sh", ".codex/c.sh", ".dev-loop/d.sh",
                     "node_modules/x/i.js", "x.md", "x.toml"]:
            put(self.repo / path, "x = 1\n# plain words\n")
        self.assert_violations([])

    def test_base_lines_unchanged(self) -> None:
        self.commit("✨(x) Add", "b.txt", "b\n")
        self.assert_violations([])

    def test_moved_comment_is_not_new(self) -> None:
        self.commit("♻️(x) Move", "new.py", '"""New."""\n\n\ndef g():\n    # moved words stay the same\n    return 2\n')
        (self.repo / "old.py").unlink()
        self.assert_violations([])
        put(self.repo / "new.py", '"""New."""\n\n\ndef g():\n    # moved words changed\n    return 2\n')
        self.assert_violations([("new.py", 5, "# moved words changed")])


class RepoCommentsTests(unittest.TestCase):
    def test_this_repo(self) -> None:
        code, out = run_rules(ROOT, "comments")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])


class RepoAuditImageTests(unittest.TestCase):
    def test_this_repo_names_its_audit_sandbox_image(self) -> None:
        config = json.loads((ROOT / "dev-loop.json").read_text())
        self.assertEqual(config.get("security_audit"), {"images": {"audit": "dev-loop-audit:1"}})
        bases = re.findall(r"^FROM (\S+)", (ROOT / "scripts" / "audit-image" / "Dockerfile").read_text(), re.M)
        self.assertTrue(bases)
        for base in bases:
            with self.subTest(base=base):
                self.assertRegex(base, r"^[a-z0-9.:-]+@sha256:[0-9a-f]{64}$")
        release = (ROOT / "reference" / "release.md").read_text()
        self.assertIn("docker build -t dev-loop-audit:1 scripts/audit-image", release)


class GateScopeTests(unittest.TestCase):
    def test_changed_scope_runs_the_skill_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            git(repo, "init", "-q")
            put(repo / "scripts" / "gate.sh", (ROOT / "scripts" / "gate.sh").read_text())
            put(repo / "scripts" / "temp_leak.sh", (ROOT / "scripts" / "temp_leak.sh").read_text())
            put(repo / "scripts" / "suite_lock.sh", (ROOT / "scripts" / "suite_lock.sh").read_text())
            put(repo / "tests" / "unit" / "python" / "test_dev_loop.py", "print('SKILL-RAN')\n")
            put(repo / "tests" / "unit" / "node" / "a.test.mjs", "")
            (repo / "node_modules" / "playwright").mkdir(parents=True)
            put(repo / ".gitignore", "node_modules/\n")
            for path in ("reference/cli/status.md", "README.md", "scripts/repo_rules.py", "cliff.toml", "x.txt"):
                put(repo / path, "a\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "base")
            for path, runs in (("reference/cli/status.md", True), ("README.md", True),
                               ("scripts/repo_rules.py", True), ("cliff.toml", True),
                               ("scripts/temp_leak.sh", True), ("scripts/suite_lock.sh", True), ("x.txt", False)):
                with self.subTest(path=path):
                    put(repo / path, (repo / path).read_text() + "true\n" if path.endswith(".sh") else "b\n")
                    out = subprocess.run(["bash", "scripts/gate.sh"], cwd=repo, capture_output=True, text=True,
                                         env={**{k: v for k, v in os.environ.items() if k != "DEVLOOP_REPORT_DIR"},
                                              "GATE_SCOPE": "changed"}, timeout=60).stdout
                    self.assertEqual("SKILL-RAN" in out, runs, out)
                    git(repo, "checkout", "-q", "--", path)


class GateTempLeakTests(unittest.TestCase):
    def test_only_the_python_tempfile_probe_is_not_a_leak(self) -> None:
        rows = (
            ([("ab3d_9x7", "blat")], []),
            ([("ab3d_9x7", "")], []),
            ([("ab3d_9x7", "blah")], ["ab3d_9x7"]),
            ([("ab3d_9x7", "blat\n")], ["ab3d_9x7"]),
            ([("ab3d_9x7q", "blat")], ["ab3d_9x7q"]),
            ([("AB3D_9X7", "blat")], ["AB3D_9X7"]),
            ([("ab3d-9x7", "blat")], ["ab3d-9x7"]),
            ([("ab3d_9x7", "dir")], ["ab3d_9x7"]),
            ([("ab3d_9x7", "link")], ["ab3d_9x7"]),
            ([("ab3d_9x7", "fifo")], ["ab3d_9x7"]),
            ([("ab3d_9x7", "blat"), ("leftover", "x")], ["leftover"]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            put(repo / "scripts" / "gate.sh", (ROOT / "scripts" / "gate.sh").read_text())
            put(repo / "scripts" / "temp_leak.sh", (ROOT / "scripts" / "temp_leak.sh").read_text())
            put(repo / "scripts" / "suite_lock.sh", (ROOT / "scripts" / "suite_lock.sh").read_text())
            put(repo / "tests" / "unit" / "node" / "a.test.mjs", "")
            put(repo / "blat.txt", "blat")
            (repo / "node_modules" / "playwright").mkdir(parents=True)
            for entries, left in rows:
                with self.subTest(entries=entries):
                    put(repo / "tests" / "unit" / "python" / "test_dev_loop.py",
                        "import os\n"
                        f"for name, content in {entries!r}:\n"
                        "    path = os.path.join(os.environ['TMPDIR'], name)\n"
                        "    if content == 'dir': os.mkdir(path)\n"
                        "    elif content == 'link': os.symlink(os.path.abspath('blat.txt'), path)\n"
                        "    elif content == 'fifo': os.mkfifo(path)\n"
                        "    else: open(path, 'w').write(content)\n")
                    run = subprocess.run(["bash", "scripts/gate.sh"], cwd=repo, capture_output=True, text=True,
                                         env={k: v for k, v in os.environ.items()
                                              if k not in ("DEVLOOP_REPORT_DIR", "GATE_SCOPE")}, timeout=60)
                    report = run.stdout.partition("temp leak, the tests left in TMPDIR:\n")[2].split()
                    self.assertEqual(report, left, run.stdout)
                    self.assertEqual(run.returncode, 1 if left else 0, run.stdout)


class GateFailureReportTests(unittest.TestCase):
    def run_gate(self, repo: Path, reports: Path | None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("DEVLOOP_REPORT_DIR", "GATE_SCOPE")}
        if reports:
            env["DEVLOOP_REPORT_DIR"] = str(reports)
        return subprocess.run(["bash", "scripts/gate.sh"], cwd=repo, capture_output=True, text=True,
                              env=env, timeout=60)

    def make_repo(self, root: Path, py: str, playwright: bool = True) -> Path:
        repo = root / "repo"
        for name in ("gate.sh", "temp_leak.sh", "suite_lock.sh"):
            put(repo / "scripts" / name, (ROOT / "scripts" / name).read_text())
        put(repo / "tests" / "unit" / "python" / "junit_report.py",
            (ROOT / "tests" / "unit" / "python" / "junit_report.py").read_text())
        put(repo / "tests" / "unit" / "python" / "test_dev_loop.py", py)
        put(repo / "tests" / "unit" / "node" / "a.test.mjs", "")
        if playwright:
            (repo / "node_modules" / "playwright").mkdir(parents=True)
        return repo

    def temp_leak_case(self, reports: Path) -> ET.Element:
        cases = list(ET.parse(reports / "temp-leak.xml").getroot().iter("testcase"))
        self.assertEqual([c.get("name") for c in cases], ["TMPDIR"])
        return cases[0]

    def test_a_temp_leak_is_a_failing_junit_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = self.make_repo(root, "import os\nopen(os.path.join(os.environ['TMPDIR'], 'left-over'), 'w')\n")
            reports = root / "reports"
            reports.mkdir()
            run = self.run_gate(repo, reports)
            self.assertEqual(run.returncode, 1, run.stdout)
            case = self.temp_leak_case(reports)
            self.assertEqual(case.get("classname"), "temp-leak")
            self.assertIsNotNone(case.find("failure"))
            self.assertIn("left-over", case.find("failure").text or "")

    def test_a_clean_run_has_a_passing_temp_leak_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = self.make_repo(root, "pass\n")
            reports = root / "reports"
            reports.mkdir()
            run = self.run_gate(repo, reports)
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertIsNone(self.temp_leak_case(reports).find("failure"))

    def test_missing_playwright_prints_a_failed_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp).resolve(), "pass\n", playwright=False)
            run = self.run_gate(repo, None)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout.strip(), "FAILED setup: run npm ci && npx playwright install chromium")


class TempLeakRuleTests(unittest.TestCase):
    def test_temp_leak_lists_leftovers_one_per_line(self) -> None:
        rule = str(ROOT / "scripts" / "temp_leak.sh")
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            sourced = subprocess.run(["bash", "-c", '. "$0"', rule], capture_output=True, text=True)
            self.assertEqual((sourced.stdout, sourced.returncode), ("", 0), sourced.stderr)
            run = subprocess.run(["bash", "-c", '. "$0"; temp_leak "$1"', rule, tmp], capture_output=True, text=True)
            self.assertEqual((run.stdout, run.returncode), ("", 0), run.stderr)
            (scratch / "node-compile-cache").mkdir()
            put(scratch / "ab3d_9x7", "blat")
            (scratch / "left one").mkdir()
            put(scratch / "other", "x")
            run = subprocess.run(["bash", "-c", '. "$0"; temp_leak "$1"', rule, tmp], capture_output=True, text=True)
            self.assertEqual(sorted(run.stdout.splitlines()), ["left one", "other"], run.stderr)
            self.assertEqual(run.returncode, 0)

    def test_temp_leak_lists_a_name_that_echo_would_swallow(self) -> None:
        rule = str(ROOT / "scripts" / "temp_leak.sh")
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("-n", "-e", "-ne"):
                (Path(tmp) / name).mkdir()
            run = subprocess.run(["bash", "-c", '. "$0"; temp_leak "$1"', rule, tmp], capture_output=True, text=True)
            self.assertEqual(sorted(run.stdout.splitlines()), ["-e", "-n", "-ne"], run.stderr)


class AcceptanceTempLeakTests(unittest.TestCase):
    LEAK = "temp leak, the tests left in TMPDIR:\n"

    def run_acceptance(self, root: Path, py: str, node: str, report: bool = True) -> subprocess.CompletedProcess:
        caller, suite = root / "caller tmp", root / "suite"
        caller.mkdir()
        put(suite / "test_fix.py", "import os, json, tempfile, unittest\n\n\nclass Fix(unittest.TestCase):\n"
            f"    def test_it(self):\n        {py}\n")
        put(suite / "fix.test.mjs", "import test from 'node:test';\n"
            "import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';\n"
            "import { tmpdir } from 'node:os';\nimport { join } from 'node:path';\n"
            f"test('it', () => {{ {node} }});\n")
        env = {k: v for k, v in os.environ.items() if k not in ("DEVLOOP_REPORT_DIR", "GATE_SCOPE")}
        env.update(TMPDIR=str(caller), SEEN=str(root / "seen.json"), DEVLOOP_SUITE_LOCK_HELD="1")
        if report:
            (root / "reports").mkdir()
            env["DEVLOOP_REPORT_DIR"] = str(root / "reports")
        run = subprocess.run(["bash", str(ROOT / "scripts" / "acceptance.sh"), str(suite)], cwd=root,
                             capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(os.listdir(caller), [], run.stdout + run.stderr)
        return run

    def leak_case(self, root: Path) -> ET.Element:
        suite = ET.parse(root / "reports" / "acceptance-temp-leak.xml").getroot().find("testsuite")
        assert suite is not None
        self.assertEqual(suite.get("name"), "temp-leak")
        cases = list(suite.iter("testcase"))
        self.assertEqual([(c.get("classname"), c.get("name")) for c in cases], [("temp-leak", "TMPDIR")])
        return cases[0]

    def leaked(self, run: subprocess.CompletedProcess) -> list[str]:
        return run.stdout.partition(self.LEAK)[2].splitlines()

    def test_tests_run_in_an_empty_scratch_that_goes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "json.dump({'dir': tempfile.gettempdir(), "
                                      "'entries': os.listdir(tempfile.gettempdir())}, open(os.environ['SEEN'], 'w'))", "")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            seen = json.loads((root / "seen.json").read_text())
            self.assertEqual(seen["entries"], [])
            self.assertNotEqual(os.path.realpath(seen["dir"]), os.path.realpath(root / "caller tmp"))
            self.assertFalse(os.path.exists(seen["dir"]))

    def test_python_leak_fails_with_one_failing_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "tempfile.mkdtemp(prefix='left-')", "")
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            left = self.leaked(run)
            self.assertEqual(len(left), 1, run.stdout)
            self.assertTrue(left[0].startswith("left-"), run.stdout)
            failure = self.leak_case(root).find("failure")
            assert failure is not None
            self.assertIn(left[0], failure.text or "")

    def test_node_leak_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "pass", "mkdirSync(join(tmpdir(), 'node-left'));")
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertEqual(self.leaked(run), ["node-left"])
            self.assertIsNotNone(self.leak_case(root).find("failure"))

    def test_clean_run_passes_with_three_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "os.rmdir(tempfile.mkdtemp())",
                                      "rmSync(mkdtempSync(join(tmpdir(), 'x-')), { recursive: true });")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertNotIn(self.LEAK, run.stdout)
            case = self.leak_case(root)
            self.assertIsNone(case.find("failure"))
            self.assertIsNone(case.find("skipped"))
            self.assertTrue((root / "reports" / "acceptance-python.xml").is_file())
            self.assertTrue((root / "reports" / "acceptance-node.xml").is_file())

    def test_allowed_entries_are_not_a_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "open(os.path.join(tempfile.gettempdir(), 'ab3d_9x7'), 'w').write('blat')",
                                      "mkdirSync(join(tmpdir(), 'node-compile-cache'));")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertNotIn("temp leak", run.stdout)
            self.assertIsNone(self.leak_case(root).find("failure"))

    def test_failing_test_and_leak_both_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "os.mkdir(os.path.join(tempfile.gettempdir(), 'a&b <c> d')); "
                                      "self.assertEqual(1, 2)", "")
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertEqual(self.leaked(run), ["a&b <c> d"])
            python = {c.get("name"): c for c in
                      ET.parse(root / "reports" / "acceptance-python.xml").getroot().iter("testcase")}
            self.assertIsNotNone(python["test_it"].find("failure"))
            failure = self.leak_case(root).find("failure")
            assert failure is not None
            self.assertIn("a&b <c> d", failure.text or "")

    def test_control_character_in_a_leftover_name_keeps_the_xml_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "os.mkdir(os.path.join(tempfile.gettempdir(), 'a\\x01b'))", "")
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            failure = self.leak_case(root).find("failure")
            assert failure is not None
            self.assertIn("a\\x01b", failure.text or "")

    def test_run_with_no_held_out_test_is_not_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            suite = root / "suite"
            put(suite / "test_none.py", "import unittest\n")
            put(suite / "none.test.mjs", "")
            (root / "reports").mkdir()
            env = {k: v for k, v in os.environ.items() if k not in ("DEVLOOP_REPORT_DIR", "GATE_SCOPE")}
            env.update(DEVLOOP_REPORT_DIR=str(root / "reports"), DEVLOOP_SUITE_LOCK_HELD="1")
            run = subprocess.run(["bash", str(ROOT / "scripts" / "acceptance.sh"), str(suite)], cwd=root,
                                 capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn("no held-out test ran", run.stdout)
            self.assertIsNone(self.leak_case(root).find("failure"))

    def test_leak_without_report_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.run_acceptance(root, "tempfile.mkdtemp(prefix='left-')", "", report=False)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn(self.LEAK, run.stdout)
            self.assertEqual(list(root.rglob("*.xml")), [])


if __name__ == "__main__":
    unittest.main()
