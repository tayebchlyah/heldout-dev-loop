#!/usr/bin/env python3
"""Held-out oracle tests for Session B autonomy (spec: Session B Autonomy).

One test per `## Acceptance flows` item (F<n>), plus the kit text lines; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON
and exit code of `devloop.py decide`, `pause`, `status` and `board`; the files they write (the spec
`## Decision log`, `<git-dir>/dev-loop/pause.json`, the board status note); the hook scripts
`deny-ask.sh` and `pause.sh` fed hook JSON on stdin; `bin/install.mjs init`; `check_setup.py` output;
the kit text. Every name in the temp repos is invented.

The edge lines of the CLI (O3 to O9, O12, O15, O15a, O15b, O18, O20, O22 outside a repo, O25) belong
to the unit seams, not to this suite. The D19 live checks need a live harness and are not oracle lines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import CLAUDE_HOOKS, DEVLOOP, REPO_ROOT, SCRIPTS, Board, git, put

SKILL = REPO_ROOT / "skill"
CHECKER = SCRIPTS / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"

SLUG = "demo"
HEADER = "| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |"
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
RESUME = "resume from the pause note"
STOP_FAILURE = {"session_id": "s-1", "transcript_path": "/t/s-1.jsonl", "hook_event_name": "StopFailure",
                "error": "rate_limit", "last_assistant_message": "API Error: Rate limit reached"}
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_PROJECT_DIR")


def cells(line: str) -> list[str]:
    """The cells of one Markdown table row: split on `|` not escaped by `\\`."""
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:|-]+\|", line.strip()))


def denied(result: subprocess.CompletedProcess[str]) -> bool:
    """A hook denies with exit 2, or with a JSON deny or block decision on stdout."""
    if result.returncode == 2:
        return True
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        return False
    if not isinstance(out, dict):
        return False
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision")
    return decision in ("deny", "block")


class AutonomyCase(Board):
    """The board sandbox (a main checkout with `dev-loop.json`, a bare origin, a notes folder, a sandboxed
    HOME), the kit committed on `main` (the skill under `.agents/skills/dev-loop`, the Claude hooks under
    `.dev-loop/hooks/claude`), a spec `feature: demo` with no `## Decision log`, and a worktree on `feat/demo`."""

    def setUp(self) -> None:
        super().setUp()
        for key in SESSION_VARS:
            self.env.pop(key, None)
        self.env.update(CLAUDE_CONFIG_DIR=str(self.root / "claude-config"), CODEX_HOME=str(self.root / "codex-home"),
                        PYTHONDONTWRITEBYTECODE="1")
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test_*"))
        shutil.copytree(CLAUDE_HOOKS, self.repo / ".dev-loop" / "hooks" / "claude")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "kit")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.spec_path = self.notes / "specs" / "20261024 Demo.md"
        put(self.spec_path, "---\ntype: spec\nreview_status: approved\nfeature: demo\ntrack: M\nacceptance:\nshipped:\n"
                            "---\n\n# Demo\n\n## Goal\n\nA demo feature for the widget shop.\n\n## Decisions\n\n"
                            "| ID | Topic | Decision |\n| --- | --- | --- |\n| D1 | Format | Plain text. |\n")
        self.wt = self.worktree(SLUG)

    # runs

    def cli(self, *args: str, cwd: Path, session: str | None = None, stdin: str | None = None) -> tuple[int, dict]:
        env = dict(self.env)
        if session is not None:
            env["CLAUDE_CODE_SESSION_ID"] = session
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd, env=env, input=stdin,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    def run_ok(self, *args: str, cwd: Path, session: str | None = None, stdin: str | None = None) -> dict:
        code, payload = self.cli(*args, cwd=cwd, session=session, stdin=stdin)
        self.assertEqual(code, 0, payload)
        return payload

    def status(self, *args: str, cwd: Path, session: str | None = None) -> dict:
        return self.run_ok("status", *args, cwd=cwd, session=session)

    def hook(self, name: str, payload: dict, cwd: Path, script_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
        script = (script_dir or CLAUDE_HOOKS) / name
        self.assertTrue(script.is_file(), f"no hook script {script}")
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(cwd)}
        return subprocess.run(["bash", str(script)], cwd=cwd, input=json.dumps(payload), env=env,
                              capture_output=True, text=True, timeout=120)

    def pause_note(self, wt: Path) -> Path:
        return Path(git(wt, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "pause.json"

    def stop_failure(self) -> Path:
        """O11 through pause.sh, the kit copy in the worktree: the note holds the hook values."""
        result = self.hook("pause.sh", {**STOP_FAILURE, "cwd": str(self.wt)}, self.wt,
                           script_dir=self.wt / ".dev-loop" / "hooks" / "claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = self.pause_note(self.wt)
        self.assertTrue(path.is_file(), f"no pause note after pause.sh: {result.stdout} {result.stderr}")
        note = json.loads(path.read_text())
        self.assertEqual((note.get("reason"), note.get("message"), note.get("session"), note.get("transcript"),
                          note.get("harness")),
                         ("rate_limit", "API Error: Rate limit reached", "s-1", "/t/s-1.jsonl", "claude"), note)
        for key in ("intent", "verified", "open", "next"):
            self.assertIn(key, note)
            self.assertIsNone(note[key], key)
        return path

    # decision log

    def log_lines(self) -> list[str]:
        """The table lines of the spec's `## Decision log`, which must be its last section."""
        text = self.spec_path.read_text()
        headings = re.findall(r"(?m)^## .*$", text)
        self.assertTrue(headings and headings[-1].strip() == "## Decision log", f"last section is not the log:\n{text}")
        section = text.rsplit("## Decision log", 1)[1]
        lines = [line for line in section.splitlines() if line.strip()]
        self.assertTrue(lines and all(line.lstrip().startswith("|") for line in lines), section)
        self.assertEqual(lines[0].strip(), HEADER, section)
        return lines

    def log_rows(self) -> list[str]:
        return [line for line in self.log_lines()[1:] if not is_separator(line)]


class LogAsYouGoTests(AutonomyCase):
    def test_f1_log_as_you_go(self) -> None:
        """O1, O2, O19 (decision row only): the controller logs a decision and a discovery; the spec shows two
        rows; the board shows the decision only."""
        stage = str(self.status(cwd=self.wt)["stage"])
        out = self.run_ok("decide", "--kind", "decision", "--what", "Use JSON", "--why", "stdlib only",
                          "--evidence", "src/a.py:3", cwd=self.wt)
        self.assertEqual(Path(out["file"]).resolve(), self.spec_path.resolve(), out)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1, rows)
        first = rows[0]
        self.assertEqual(out["row"].strip(), first.strip(), out)
        row = cells(first)
        self.assertEqual(len(row), 9, first)
        self.assertRegex(row[0], TIME)
        self.assertEqual(row[1:], [stage, "", "decision", "", "Use JSON", "stdlib only", "src/a.py:3", ""], first)

        self.run_ok("decide", "--kind", "discovery", "--what", "Parser drops tabs", "--why", "the fixture has tabs",
                    "--evidence", "tests/unit/test_parse.py:12", "--result", "fixed in Task 2", cwd=self.wt)
        rows = self.log_rows()
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(rows[0], first)
        self.assertEqual(cells(rows[1])[3:], ["discovery", "", "Parser drops tabs", "the fixture has tabs",
                                              "tests/unit/test_parse.py:12", "fixed in Task 2"], rows[1])

        self.ok(cwd=self.wt)
        self.assertEqual(self.note(SLUG)["decisions"], ["Use JSON: stdlib only"])


class LimitThenAnotherSessionTests(AutonomyCase):
    def test_f2_limit_then_another_session(self) -> None:
        """O11, O13, O17, O22 (note written): pause.sh gets a rate_limit StopFailure; another session's status
        returns paused; pause --clear removes the note; status then has no paused."""
        path = self.stop_failure()
        note = json.loads(path.read_text())
        self.assertEqual(note.get("feature"), SLUG, note)

        out = self.status(cwd=self.wt, session="s-2")
        self.assertEqual(out.get("paused"), note, out)
        self.assertEqual(out.get("next"), RESUME, out)
        self.assertTrue(path.is_file(), "status in another session removed the note")

        self.assertEqual(self.run_ok("pause", "--clear", cwd=self.wt, session="s-2"), {"cleared": True})
        self.assertFalse(path.exists())
        self.assertEqual(self.run_ok("pause", "--clear", cwd=self.wt, session="s-2"), {"cleared": False})
        self.assertNotIn("paused", self.status(cwd=self.wt, session="s-2"))


class LimitThenSameSessionTests(AutonomyCase):
    def test_f3_limit_then_continue_in_the_same_session(self) -> None:
        """O11, O14: after the StopFailure note, status in the same session has no paused, and the note is gone."""
        path = self.stop_failure()
        out = self.status(cwd=self.wt, session="s-1")
        self.assertNotIn("paused", out)
        self.assertFalse(path.exists(), "status in the note's own session kept the note")


class PauseFromMainTests(AutonomyCase):
    def test_f4_pause_then_main_sees_it(self) -> None:
        """O10, O16: /pause in the worktree writes a manual note; on main, status marks the feature
        paused: true, and status --feature demo returns the note."""
        put(self.wt / "scratch.txt", "half done\n")
        out = self.run_ok("pause", "--intent", "fix parser", "--next", "run gate", "--agent", "a1=Task 2", cwd=self.wt)
        path = self.pause_note(self.wt)
        self.assertTrue(path.is_file(), out)
        self.assertEqual(Path(out["file"]).resolve(), path.resolve(), out)
        note = json.loads(path.read_text())
        self.assertEqual(out.get("note"), note, out)
        self.assertEqual((note.get("reason"), note.get("intent"), note.get("next"), note.get("agents"),
                          note.get("feature")),
                         ("manual", "fix parser", "run gate", [{"id": "a1", "task": "Task 2"}], SLUG), note)
        self.assertIn("scratch.txt", note.get("dirty") or [], note)

        main = self.status(cwd=self.repo, session="s-2")
        items = [item for item in main.get("features", []) if item.get("feature") == SLUG]
        self.assertEqual(len(items), 1, main)
        self.assertIs(items[0].get("paused"), True, main)

        feature = self.status("--feature", SLUG, cwd=self.repo, session="s-2")
        self.assertEqual(feature.get("paused"), note, feature)
        self.assertEqual(feature.get("next"), RESUME, feature)
        self.assertTrue(path.is_file())


class NoQuestionsTests(AutonomyCase):
    def test_f5_no_questions_in_session_b(self) -> None:
        """O21: deny-ask.sh denies AskUserQuestion in a feature worktree, naming devloop.py decide, and does not
        deny on main, in a linked worktree on a branch without `/`, or in a repo without dev-loop.json."""
        def ask(cwd: Path) -> subprocess.CompletedProcess[str]:
            payload = {"session_id": "s-1", "cwd": str(cwd), "hook_event_name": "PreToolUse",
                       "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "JSON or YAML?"}]}}
            return self.hook("deny-ask.sh", payload, cwd)

        result = ask(self.wt)
        self.assertTrue(denied(result), result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("devloop.py decide", json.dumps(json.loads(result.stdout)), result.stdout)

        plain = self.repo.parent / "worktrees" / "plain-branch"
        git(self.repo, "worktree", "add", "-q", "-b", "scratch", str(plain), "origin/main")
        other = self.root / "other-repo"
        other.mkdir()
        git(other, "init", "-q")
        put(other / "README.md", "# Other\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "base")
        other_wt = self.root / "other-wt"
        git(other, "worktree", "add", "-q", "-b", "feat/x", str(other_wt))
        for name, cwd in (("main", self.repo), ("branch without /", plain.resolve()),
                          ("no dev-loop.json", other_wt.resolve())):
            with self.subTest(cwd=name):
                result = ask(cwd)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(denied(result), result.stdout)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        (self.notes / "specs").mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")

    def init(self, name: str, harness: str) -> Path:
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, env=self.env, check=True, capture_output=True)
        result = subprocess.run(["node", str(INSTALLER), "init", "--yes", f"--harness={harness}", f"--notes={self.notes}"],
                                cwd=project, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return project

    def checker(self, project: Path) -> str:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.home),
                                 "--repo", str(project)], env=self.env, capture_output=True, text=True, timeout=180)
        return result.stdout + result.stderr

    @staticmethod
    def entries(settings: dict, event: str, matcher: str) -> list[str]:
        """The hook commands of the `event` entries whose matcher is `matcher`."""
        commands = []
        for entry in (settings.get("hooks") or {}).get(event) or []:
            if entry.get("matcher") == matcher:
                commands += [hook.get("command", "") for hook in entry.get("hooks") or []]
        return commands

    def test_f6_install(self) -> None:
        """O23, O24, O26: init writes the two kit skills, their Claude links and the two Claude hook entries;
        check_setup passes for them; a removed link is a failure line that names it."""
        project = self.init("both-project", "both")
        files = (".agents/skills/pause/SKILL.md", ".agents/skills/pause/agents/openai.yaml",
                 ".agents/skills/continue/SKILL.md")
        for rel in files:
            self.assertTrue((project / rel).is_file(), f"init did not write {rel}")
        for name in ("pause", "continue"):
            link = project / ".claude" / "skills" / name
            self.assertTrue(link.is_symlink(), f"no link .claude/skills/{name}")
            self.assertEqual(os.readlink(link).rstrip("/"), f"../../.agents/skills/{name}")
        settings = json.loads((project / ".claude" / "settings.json").read_text())
        for event, matcher, script in (("PreToolUse", "AskUserQuestion", "deny-ask.sh"),
                                       ("StopFailure", "rate_limit", "pause.sh")):
            commands = self.entries(settings, event, matcher)
            self.assertTrue([c for c in commands if f".dev-loop/hooks/claude/{script}" in c],
                            f"no {event} {matcher} entry running {script}: {settings.get('hooks')}")
            self.assertTrue((project / ".dev-loop" / "hooks" / "claude" / script).is_file(), script)
        manifest = (project / ".dev-loop" / "MANIFEST.json").read_text()
        for rel in files:
            self.assertIn(f'"{rel}"', manifest)

        # O24: the pause skill is never started by the model; continue may be.
        pause_md = (project / files[0]).read_text()
        self.assertRegex(pause_md, r"(?ms)\A---\n.*^disable-model-invocation:\s*true\s*$.*?^---\s*$")
        self.assertRegex((project / files[1]).read_text(), r"(?m)^\s*allow_implicit_invocation:\s*false\s*$")
        continue_md = (project / files[2]).read_text()
        self.assertNotIn("disable-model-invocation", continue_md)
        continue_yaml = project / ".agents" / "skills" / "continue" / "agents" / "openai.yaml"
        if continue_yaml.exists():
            self.assertNotRegex(continue_yaml.read_text(), r"allow_implicit_invocation:\s*false")

        # O26: check_setup names none of them after a clean init, and names `continue` once its link is gone.
        output = self.checker(project)
        self.assertEqual([line for line in output.splitlines()
                          if re.search(r"\b(pause|continue)\b|deny-ask|AskUserQuestion|StopFailure", line)], [], output)
        (project / ".claude" / "skills" / "continue").unlink()
        output = self.checker(project)
        self.assertTrue([line for line in output.splitlines() if re.search(r"\bcontinue\b", line)], output)

        # O23: a Codex-only install gets no Claude link and no Claude entry.
        codex = self.init("codex-project", "codex")
        for name in ("pause", "continue"):
            self.assertFalse(os.path.lexists(codex / ".claude" / "skills" / name), name)
        claude_settings = codex / ".claude" / "settings.json"
        if claude_settings.exists():
            settings = json.loads(claude_settings.read_text())
            self.assertEqual(self.entries(settings, "PreToolUse", "AskUserQuestion"), [])
            self.assertEqual(self.entries(settings, "StopFailure", "rate_limit"), [])


class KitTextTests(unittest.TestCase):
    def test_o27_to_o29_kit_text(self) -> None:
        """O27, O28, O29: the skill names the decision log and pause and continue; the spec template has the
        log; the Claude route enters the worktree; both implementers report Discoveries and never log."""
        skill = (SKILL / "SKILL.md").read_text()
        with self.subTest(oracle=27):
            stale = [line for line in skill.splitlines()
                     if re.search(r"## (Assumptions|Discoveries)", line)
                     and not re.search(r"(?i)replac|instead|old spec|earlier spec|no longer", line)]
            self.assertEqual(stale, [])
            for name in ("devloop.py decide", "/pause", "/continue", "paused"):
                self.assertIn(name, skill)
            self.assertRegex(skill, r"(?i)never[^.\n]{0,80}dispatch[^.\n]{0,40}new agent[^.\n]{0,60}unfinished work")
        with self.subTest(oracle=28):
            spec_ref = (SKILL / "references" / "spec.md").read_text()
            self.assertRegex(spec_ref, r"(?m)^\s*## Decision log\s*$")
            self.assertNotRegex(spec_ref, r"(?m)^\s*## Assumptions\s*$")
        with self.subTest(oracle=29):
            self.assertIn("EnterWorktree", (SKILL / "references" / "claude.md").read_text())
            for agent in (REPO_ROOT / "claude" / "agents" / "implementer.md",
                          REPO_ROOT / "codex" / "agents" / "implementer.toml"):
                text = agent.read_text()
                self.assertIn("Discoveries", text, agent.name)
                self.assertNotIn("devloop.py decide", text, agent.name)


if __name__ == "__main__":
    unittest.main()
