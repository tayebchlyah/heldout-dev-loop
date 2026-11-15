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
from fnmatch import fnmatchcase
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

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


RC_PACKAGE = '{"version": "3.0.0-rc.2", "files": ["skill"]}\n'
FINAL_PACKAGE = '{"version": "3.0.0", "files": ["skill"]}\n'


class PromotedCommandTests(RulesRepo):
    def release(self, tag: str, files: dict[str, str | bytes], annotated: bool = False) -> None:
        for path, text in files.items():
            put(self.repo / path)
            (self.repo / path).write_bytes(text if isinstance(text, bytes) else text.encode())
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"🔖(release) {tag}")
        if annotated:
            git(self.repo, "tag", "-a", "-m", tag, tag)
        else:
            git(self.repo, "tag", tag)

    def promoted(self, tag: str) -> tuple[int, dict]:
        return run_rules(self.repo, "promoted", "--tag", tag)

    def final_after_rc(self, extra: dict[str, str], annotated: bool = False) -> tuple[int, dict]:
        self.release("v3.0.0-rc.2", {"skill/SKILL.md": "a\n", "package.json": RC_PACKAGE}, annotated)
        self.release("v3.0.0", {"package.json": FINAL_PACKAGE, "CHANGELOG.md": "c\n", "README.md": "r2\n",
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
        self.release("v3.0.0-rc.9", {"skill/a": "1\n", "package.json": '{"version": "3.0.0-rc.9"}\n'})
        self.release("v3.0.0-rc.10", {"skill/a": "2\n", "package.json": '{"version": "3.0.0-rc.10"}\n'})
        self.release("v3.0.0", {"package.json": '{"version": "3.0.0"}\n'})
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
        self.release("v3.0.0-rc.2", {"skill/SKILL.md": "a\n", "package.json": RC_PACKAGE})
        for path, text in {"package.json": FINAL_PACKAGE, "CHANGELOG.md": "c\n",
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

    def fail_closed(self, run):
        try:
            code, out = run()
            self.assertEqual((code, out["violations"]), (1, ["package.json"]))
        finally:
            self.tearDown()
            self.setUp()

    def two(self, rc, final):
        self.release("v3.0.0-rc.1", rc)
        self.release("v3.0.0", {"package.json": final})
        return self.promoted("v3.0.0")

    def test_package_json_beyond_version_fails(self) -> None:
        other = {"package.json": '{"version": "3", "files": []}\n'}
        for route, run in [("tag", lambda: self.final_after_rc(other)),
                           ("sha", lambda: self.sha_run(other, "--sha", "HEAD"))]:
            with self.subTest(route):
                self.fail_closed(run)

    def test_package_json_unreadable_or_loose_sides_fail_closed(self) -> None:
        s = '{"version": "1", "%s"}\n'
        for rc, final in [
            (RC_PACKAGE, "{not json\n"), ("", FINAL_PACKAGE), (RC_PACKAGE, "[1]\n"),
            (s % 'a": "x"', s.encode() % b'a": "\xff"'), (s % 'a": true', s % 'a": 1'),
            (s % 'a": 1, "b": 2', s % 'b": 2, "a": 1'),
        ]:
            with self.subTest(rc=rc, final=final):
                self.fail_closed(lambda: self.two({"package.json": rc} if rc else {"skill/a": "1\n"}, final))

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


class RepoCommentsTests(unittest.TestCase):
    def test_this_repo(self) -> None:
        code, out = run_rules(ROOT, "comments")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])


HYGIENE = {"tests/unit/node/payload.test.mjs", "tests/unit/python/test_repo_rules.py",
           "tests/unit/python/test_stacks_setup.py"}


class InertPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.globs = json.loads((ROOT / "dev-loop.json").read_text()).get("inert_paths", [])
        self.tracked = [p for p in git(ROOT, "ls-files", "-z").split("\0") if p]
        self.impact = json.loads((ROOT / "tests" / "impact-map.json").read_text())["paths"]

    def test_each_glob_matches_a_tracked_file(self) -> None:
        self.assertTrue(self.globs)
        for glob in self.globs:
            with self.subTest(glob=glob):
                self.assertTrue([p for p in self.tracked if fnmatchcase(p, glob)], glob)

    def test_each_inert_path_is_mapped_with_hygiene_readers_only(self) -> None:
        for path in (p for p in self.tracked if any(fnmatchcase(p, g) for g in self.globs)):
            with self.subTest(path=path):
                self.assertIn(path, self.impact)
                self.assertLessEqual(set(self.impact[path]), HYGIENE, path)

    def test_the_hygiene_job_runs_the_hygiene_set(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        parts = re.split(r"^  ([\w-]+):\n", ci.split("\njobs:\n", 1)[1], flags=re.M)
        job = dict(zip(parts[1::2], parts[2::2]))["hygiene"]
        node = re.findall(r"run: node --test (.+)\n", job)
        modules = re.findall(r"DEVLOOP_TEST_MODULES: (.+)\n", job)
        self.assertEqual(len(modules), 1)
        python = {f"tests/unit/python/{m.strip()}.py" for m in modules[0].split(",")}
        self.assertEqual({p for line in node for p in line.split()} | python, HYGIENE)


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
