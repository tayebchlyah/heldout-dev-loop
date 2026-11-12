"""`source-wall`: the `source_dirs` decision of a spec-tester tool call, run as a process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

from source_fixtures import key, set_branch, set_main, source_repo
from support import git_in
from test_devloop import DEVLOOP

SUITE = "tests/" + "acceptance"
SEAL = "python3 .agents/skills/dev-loop/scripts/devloop.py seal --feature gizmo --message "


def run(repo: Path, hook: object, env: dict[str, str] | None = None) -> tuple[int, dict]:
    env = dict(os.environ, HOME=str(repo.parent)) if env is None else env
    done = subprocess.run([sys.executable, str(DEVLOOP), "source-wall", "--repo", str(repo)], input=json.dumps(hook),
                          env=env, capture_output=True, text=True, timeout=60)
    return done.returncode, json.loads(done.stdout)


def call(repo: Path, tool: str, tool_input: dict, cwd: Path | str | None = None) -> dict:
    return {"agent_id": "a", "agent_type": "spec-tester", "tool_name": tool, "tool_input": tool_input,
            "cwd": str(cwd or repo)}


def read(repo: Path, path: str, cwd: Path | str | None = None) -> dict:
    return call(repo, "Read", {"file_path": path}, cwd)


def bash(repo: Path, command: str) -> dict:
    return call(repo, "Bash", {"command": command})


class SourceWallTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = source_repo(Path(tmp.name).resolve())

    def verdicts(self, hooks: list[tuple[dict, bool]]) -> None:
        for hook, deny in hooks:
            with self.subTest(hook=hook["tool_input"], cwd=hook["cwd"]):
                code, out = run(self.repo, hook)
                self.assertEqual((code, out["mode"], out["deny"]), (0, "configured", deny), out)

    def test_modes_and_refs(self) -> None:
        hook = read(self.repo, "internal/x.go")
        self.assertEqual(run(self.repo, hook), (0, {"mode": "default", "deny": False, "reason": None, "ref": "main"}))
        set_branch(self.repo, key(["internal/"]))
        self.assertEqual(run(self.repo, hook)[1]["mode"], "default")
        set_main(self.repo, key(["internal/"]))
        code, out = run(self.repo, hook)
        self.assertEqual((code, out["mode"], out["deny"], out["ref"]), (0, "configured", True, "main"))
        self.assertIn("internal/", out["reason"])
        for text in (key("internal/"), "[]"):
            with self.subTest(text=text):
                set_main(self.repo, text)
                code, out = run(self.repo, hook)
                self.assertEqual(code, 1)
                self.assertIn("source_dirs", out["error"])
        set_main(self.repo, key([]))
        self.assertEqual(run(self.repo, read(self.repo, "src/app.py")),
                         (0, {"mode": "configured", "deny": False, "reason": None, "ref": "main"}))
        git_in(self.repo, "branch", "-D", "main")
        self.assertEqual(run(self.repo, hook), (0, {"mode": "default", "deny": False, "reason": None, "ref": None}))

    def test_path_forms(self) -> None:
        set_main(self.repo, key(["internal/", "cmd/"]))
        r = self.repo
        self.verdicts([(read(r, "internal/x.go"), True), (read(r, "cmd/app/main.go"), True),
                       (read(r, f"{r}/internal/x.go"), True), (bash(r, "cd internal && cat x.go"), True),
                       (bash(r, "git show HEAD:internal/x.go"), True), (bash(r, "cat $HOME/repo/internal/x.go"), True),
                       (bash(r, "cat ~/repo/internal/x.go"), True), (bash(r, f"cat internal/x.go > {SUITE}/x"), True),
                       (bash(r, "grep -rn func internal"), True),
                       (call(r, "Grep", {"pattern": "func", "path": "internal"}), True),
                       (read(r, "src/app.py"), False), (read(r, "pkg/src/y.py"), False), (read(r, "docs/guide.md"), False)])

    def test_worktree_tops(self) -> None:
        tree = self.repo / ".claude" / "worktrees" / "w"
        git_in(self.repo, "worktree", "add", "-q", "-b", "w", str(tree))
        set_main(self.repo, key(["internal/"]))
        self.verdicts([(read(self.repo, f"{self.repo}/internal/x.go", tree), True)])
        set_main(self.repo, key([".claude/"]))
        self.verdicts([(read(self.repo, f"{tree}/docs/guide.md", tree), False),
                       (call(self.repo, "Glob", {"path": f"{tree}/docs", "pattern": "*.md"}, tree), False),
                       (read(self.repo, f"{self.repo}/.claude/settings.json", tree), True)])

    def test_quoted_and_unresolved_words(self) -> None:
        set_main(self.repo, key(["internal/"]))
        r = self.repo
        self.verdicts([(bash(r, 'bash -c "cat internal/x.go"'), True),
                       (bash(r, "python3 -c \"print(open('internal/x.go').read())\""), True),
                       (bash(r, 'echo "$(cat internal/x.go)"'), True), (bash(r, 'cat "$ROOT"/internal/x.go'), True),
                       (bash(r, "cat */internal/x.go"), True),
                       (bash(r, 'cat "$(git rev-parse --show-toplevel)/internal/x.go"'), True),
                       (bash(r, "bash -c 'cat in\"\"ternal/x.go'"), True),
                       (bash(r, "bash -c 'cd int\"\"ernal; cat x.go'"), True),
                       (bash(r, "sh -c \"cat in''ternal/x.go\""), True),
                       (bash(r, 'bash -c "cat in\\\\ternal/x.go"'), True),
                       (bash(r, "bash -c \"bash -c 'cat in\\\"\\\"ternal/x.go'\""), True),
                       (bash(r, 'echo "$ROOT"/docs/guide.md'), False),
                       (bash(r, "cat ~+/internal/x.go"), True), (bash(r, "cd ~+/internal && cat x.go"), True),
                       (bash(r, "cat ~-/internal/x.go"), True), (bash(r, "cat ~root/internal/x.go"), True),
                       (bash(r, "cat ~/x"), False), (bash(r, "cat ~+/docs/guide.md"), False),
                       (bash(r, "cat {internal,x}/x.go"), True), (bash(r, "cat {x,internal}/x.go"), True),
                       (bash(r, "cat internal{,}/x.go"), True), (bash(r, "cat internal${E}/x.go"), True),
                       (call(r, "Grep", {"pattern": "func", "glob": "{internal,x}/**"}), True)])

    def test_suite_seal_and_heredoc_exceptions(self) -> None:
        set_main(self.repo, key(["api/", "tests/", "scripts/"]))
        r = self.repo
        gizmo = f"{SUITE}/api/test_gizmo.py"
        allowed = [call(r, "Write", {"file_path": gizmo, "content": "x"}), bash(r, f"git add {gizmo}"),
                   bash(r, f"python3 -m pytest --tb=no {gizmo}"), bash(r, f"python3 -m pytest --tb=no {SUITE}"),
                   call(r, "Grep", {"pattern": "api", "path": SUITE}), bash(r, SEAL + '"cover api/ flags"'),
                   bash(r, f"cat > {SUITE}/test_h.py <<'EOF'\nPATH = \"api/spec.go\"\nEOF")]
        denied = [read(r, "api/spec.go"), read(r, "tests/conftest.py"), read(r, "scripts/run.sh"),
                  bash(r, SEAL + '"$(cat api/spec.go)"'), bash(r, SEAL + '"cover api/ flags"\ncat api/spec.go')]
        self.verdicts([(hook, False) for hook in allowed] + [(hook, True) for hook in denied])
        os.symlink("../..", r / SUITE / "up")
        self.verdicts([(read(r, f"{SUITE}/up/api/spec.go"), True)])
        set_main(r, key(["api/", ".agents/"]))
        self.verdicts([(bash(r, SEAL + '"\u2705(acceptance) #302: Add tests"'), False),
                       (bash(r, SEAL + '"cover api/ flags" > out'), True), (bash(r, SEAL + '"$(cat api/spec.go)"'), True),
                       (bash(r, SEAL + '"cover api/ flags"\ncat api/spec.go'), True),
                       (bash(r, f"cd {SUITE} && python3 -m pytest --tb=no api/test_gizmo.py"), False)])

    def test_symlinked_entry(self) -> None:
        os.symlink("pkg", self.repo / "lnk")
        set_main(self.repo, key(["lnk/"]))
        self.verdicts([(read(self.repo, "lnk/src/y.py"), True), (read(self.repo, f"/{self.repo}/lnk/src/y.py"), True)])

    def test_codex_shapes(self) -> None:
        set_main(self.repo, key(["internal/"]))
        r = self.repo
        patch = "*** Begin Patch\n*** {} File: {}\n+{}\n*** End Patch"
        self.verdicts([(call(r, "apply_patch", {"command": patch.format("Add", "internal/n.go", "x")}), True),
                       (call(r, "apply_patch", {"command": patch.format("Add", f"{SUITE}/t.py", "see internal/x.go")}),
                        False),
                       (call(r, "view_image", {"path": "internal/a.png"}), True),
                       (call(r, "skillsread", {"name": "t", "path": "internal/x.go"}), True)])

    def test_bare_word_at_cwd(self) -> None:
        set_main(self.repo, key(["api/"]))
        code, out = run(self.repo, bash(self.repo, "pytest -k api"))
        self.assertEqual((code, out["deny"]), (0, True))
        self.assertIn("api/", out["reason"])

    def test_parse_error_falls_back_to_whitespace(self) -> None:
        set_main(self.repo, key(["internal/"]))
        self.verdicts([(bash(self.repo, command), True) for command in
                       ("cat 'internal/x.go", "cat in\"\"ternal/x.go\necho '", "cat in\\ternal/x.go\necho '")])

    def test_suite_reachable_under_a_tests_entry(self) -> None:
        set_main(self.repo, key(["tests/"]))
        r = self.repo
        self.verdicts([(bash(r, f"cd {SUITE} && python3 -m pytest --tb=no ."), False),
                       (bash(r, f"cd {r} && python3 -m pytest --tb=no {SUITE}"), False),
                       (call(r, "Glob", {"pattern": f"{SUITE}/**/*.py"}), False),
                       (call(r, "Glob", {"path": SUITE, "pattern": "**/*.py"}), False),
                       (call(r, "Glob", {"path": f"{r}/{SUITE}", "pattern": "*.py"}), False),
                       (bash(r, f"cat {SUITE}/{{,..}}/conftest.py"), True), (bash(r, f"cat {SUITE}/.*/conftest.py"), True),
                       (bash(r, f"cat {SUITE}/$X/conftest.py"), True), (bash(r, f"cat {SUITE}*/conftest.py"), True),
                       (bash(r, f"cat {SUITE}/{{.,x}}{{.,y}}/conftest.py"), True)])
        os.symlink("..", r / SUITE / "up")
        self.verdicts([(bash(r, f"cat {SUITE}/*/conftest.py"), True)])

    def test_unresolved_absolute_text_drops_the_top(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = source_repo(Path(tmp.name).resolve() / "src")
        set_main(repo, key(["src/"]))
        self.repo = repo
        self.verdicts([(call(repo, "Glob", {"path": f"{repo}/docs", "pattern": "*.md"}), False),
                       (bash(repo, f"cat {repo}/docs/*.md"), False), (bash(repo, f"cat {repo}/src/*.py"), True)])

    def test_every_folder_of_the_command(self) -> None:
        set_main(self.repo, key(["internal/"]))
        self.verdicts([(bash(self.repo, command), True) for command in
                       ("cd docs;cd ..;cat internal/x.go", "echo cd / ; cat internal/x.go",
                        "cd /tmp ; cd - ; cat internal/x.go", "cd internal && cat x.go",
                        "true&&cd docs&&cat ../internal/x.go", "echo;cd docs;cat ../internal/x.go",
                        "cd;cat repo/internal/x.go", "cd&&cat repo/internal/x.go", "cd\ncat repo/internal/x.go",
                        "cd '..'/repo/docs\ncat ../internal/x.go\necho '",
                        "cd \"..\"/repo/docs\ncat ../internal/x.go\necho '", "cat in\\\nternal/x.go",
                        "eval 'cd docs'; cat ../internal/x.go", "eval cd\\ docs; cat ../internal/x.go",
                        "bash -c 'cd docs; cat ../internal/x.go'", "sh -c 'cd docs && cat ../internal/x.go'",
                        "tar -C docs -cf - ../internal", "cd /nonexistent; cat internal/x.go",
                        "cd /tmp | cat internal/x.go", "cd /tmp & cat internal/x.go",
                        "false && cd /tmp; cat internal/x.go", "cd /tmp extra; cat internal/x.go",
                        "cd -P /tmp; cat internal/x.go", "if false; then\ncd /tmp\nfi\ncat internal/x.go",
                        "(cd /tmp); cat internal/x.go", "cd /tmp 2>/dev/null; cat internal/x.go",
                        "python3 -c \"import os; os.chdir('docs'); open('../internal/x.go')\"")])

    @unittest.skipUnless(sys.platform == "darwin", "the /private alias of the temp folder is macOS only")
    def test_private_var_alias_of_the_top(self) -> None:
        set_main(self.repo, key(["internal/"]))
        top = str(self.repo)
        other = top.removeprefix("/private") if top.startswith("/private/") else "/private" + top
        self.verdicts([(read(self.repo, "internal/x.go", cwd), True) for cwd in (top, other)]
                      + [(read(self.repo, f"{place}/internal/x.go"), True) for place in (top, other)])

    def test_case_and_nfc_fold(self) -> None:
        set_main(self.repo, key(["internal/", "café/"]))
        self.verdicts([(read(self.repo, "INTERNAL/x.go"), True),
                       (read(self.repo, unicodedata.normalize("NFD", "café/x.py")), True)])

    def test_nul_byte_is_an_error(self) -> None:
        set_main(self.repo, key(["internal/"]))
        code, out = run(self.repo, read(self.repo, "docs/\0x"))
        self.assertEqual(code, 1)
        self.assertIn("source-wall", out["error"])

    def test_unreadable_main_file_is_an_error(self) -> None:
        origin = self.repo
        git_in(origin, "config", "uploadpack.allowFilter", "true")
        clone = origin.parent / "clone"
        git_in(origin.parent, "clone", "-q", "--filter=blob:none", "-b", "main", f"file://{origin}", str(clone))
        set_main(origin, key(["internal/"]))
        git_in(clone, "fetch", "-q", "origin")
        env = dict(os.environ, HOME=str(clone.parent), GIT_NO_LAZY_FETCH="1")
        set_main(self.repo, key(["cmd/"]))
        blob = git_in(self.repo, "rev-parse", "main:dev-loop.json").strip()
        (self.repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        for repo, run_env in ((clone, env), (self.repo, None)):
            with self.subTest(repo=repo.name):
                code, out = run(repo, read(repo, "internal/x.go"), run_env)
                self.assertEqual(code, 1, out)
                self.assertIn("source_dirs", out["error"])

    def test_not_a_json_object(self) -> None:
        done = subprocess.run([sys.executable, str(DEVLOOP), "source-wall", "--repo", str(self.repo)], input="[]",
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not a JSON object", json.loads(done.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
