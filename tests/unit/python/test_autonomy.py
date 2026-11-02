#!/usr/bin/env python3
"""Tests for devloop.py decide, pause and the pause note in status (autonomy.py)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from test_devloop import DEVLOOP, Sandbox, frontmatter, git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
import autonomy  # noqa: E402

SPEC = "20261001 Demo.md"
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CLAUDE_PROJECT_DIR")
DECIDE = ("decide", "--kind", "decision", "--what", "Use JSON", "--why", "stdlib only", "--evidence", "src/a.py:3")


def cells(row: str) -> list[str]:
    """The cells of a table row, split on `|` that no backslash escapes."""
    return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip())[1:-1]]


class AutonomySandbox(Sandbox):
    """The Sandbox repo with `dev-loop.json`, a spec `feature: demo` and a worktree on `feat/demo`."""

    def setUp(self) -> None:
        super().setUp()
        for name in SESSION_VARS:
            self.env.pop(name, None)
        self.commit(self.repo, "dev-loop.json", '{"version": "0.0.0"}\n')
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.spec_path = self.notes / "specs" / SPEC
        put(self.spec_path, frontmatter(feature="demo", track="M", review_status="approved"))
        self.wt = self.worktree("feat/demo", "wt")

    def worktree(self, branch: str, name: str) -> Path:
        path = self.root / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
        return path.resolve()

    def cli(self, *args: str, cwd: Path | None = None, **env: str) -> tuple[int, dict]:
        return run_cli(*args, cwd=cwd or self.wt, env={**self.env, **env})

    def rows(self, path: Path | None = None) -> list[str]:
        text = (path or self.spec_path).read_text()
        return [line for line in text.splitlines() if line.startswith("| ") and not line.startswith(("| Time", "| ---"))]


class DecideTests(AutonomySandbox):
    def test_first_row_adds_section_and_header(self) -> None:
        code, out = self.cli(*DECIDE)
        self.assertEqual(code, 0, out)
        text = self.spec_path.read_text()
        self.assertIn(f"{autonomy.LOG_HEADING}\n\n{autonomy.LOG_HEADER}\n{autonomy.LOG_RULE}\n", text)
        row = self.rows()
        self.assertEqual(len(row), 1)
        self.assertEqual(cells(row[0])[3:], ["decision", "", "Use JSON", "stdlib only", "src/a.py:3", ""])
        self.assertRegex(cells(row[0])[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")
        self.assertEqual(out, {"file": str(self.spec_path), "row": row[0]})

    def test_second_row_below_first_unchanged(self) -> None:
        self.cli(*DECIDE)
        first = self.rows()
        code, _ = self.cli("decide", "--kind", "discovery", "--what", "w2", "--why", "y2", "--evidence", "e2")
        self.assertEqual(code, 0)
        self.assertEqual(self.rows()[0], first[0])
        self.assertEqual(cells(self.rows()[1])[3], "discovery")

    def test_cells(self) -> None:
        stage = run_cli("status", "--feature", "demo", cwd=self.repo, env=self.env)[1]["stage"]
        cases = [
            # (extra args, env, cell index, expected)
            (("--risk", "--result", "green"), {}, 4, "risk"),
            (("--risk", "--result", "green"), {}, 8, "green"),
            ((), {"CLAUDE_CODE_SESSION_ID": "0123456789abcdef"}, 2, "01234567"),
            ((), {"CODEX_THREAD_ID": "fedcba9876543210"}, 2, "fedcba98"),
            ((), {}, 2, ""),
            ((), {}, 1, str(stage)),
        ]
        for extra, env, index, expected in cases:
            with self.subTest(extra=extra, env=env, index=index):
                code, out = self.cli(*DECIDE, *extra, **env)
                self.assertEqual(code, 0, out)
                self.assertEqual(cells(out["row"])[index], expected)

    def test_pipe_and_line_break_escaped(self) -> None:
        code, out = self.cli("decide", "--kind", "decision", "--what", "a|b\nc", "--why", "y", "--evidence", "e")
        self.assertEqual(code, 0, out)
        self.assertEqual(cells(self.rows()[0])[5], "a\\|b c")
        self.assertEqual(autonomy.log_rows(self.spec_path.read_text())[0]["decision"], "a|b c")
        for text, expected in (("a\n\nb", "a  b"), ("a\r\nb", "a b"), ("a\rb\n", "a b")):
            with self.subTest(text=text):
                self.assertEqual(autonomy.cell(text), expected)

    def test_log_rows_formatted_table(self) -> None:
        row = "| t | 4 | s | decision |  | w | y | e |  |"
        header = "|  " + "  |  ".join(c.capitalize() for c in autonomy.LOG_COLUMNS) + "  |"
        rule = "|" + "|".join(":---:" if i == 0 else "---" for i, _ in enumerate(autonomy.LOG_COLUMNS)) + "|"
        for rows in ([], [row]):
            with self.subTest(rows=rows):
                text = "\n".join([autonomy.LOG_HEADING, "", header, rule, *rows]) + "\n"
                self.assertEqual([r["row"] for r in autonomy.log_rows(text)], rows)

    def test_spec_mode_and_link_kept(self) -> None:
        self.spec_path.chmod(0o644)
        self.assertEqual(self.cli(*DECIDE)[0], 0)
        self.assertEqual(self.spec_path.stat().st_mode & 0o777, 0o644)
        target = self.root / "elsewhere.md"
        self.spec_path.rename(target)
        self.spec_path.symlink_to(target)
        self.assertEqual(self.cli(*DECIDE)[0], 0)
        self.assertTrue(self.spec_path.is_symlink())
        self.assertEqual(len(self.rows(target)), 2)

    def test_track_s_row_goes_to_git_dir(self) -> None:
        quick = self.worktree("feat/quick", "wt-quick")
        code, out = self.cli(*DECIDE, cwd=quick)
        self.assertEqual(code, 0, out)
        log = Path(git(quick, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "decisions.md"
        self.assertEqual(out["file"], str(log))
        self.assertIn(f"{autonomy.LOG_HEADING}\n\n{autonomy.LOG_HEADER}\n{autonomy.LOG_RULE}\n", log.read_text())
        self.assertEqual(len(self.rows(log)), 1)

    def test_feature_flag_on_main(self) -> None:
        code, out = self.cli(*DECIDE, "--feature", "demo", cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.rows()), 1)

    def test_refusals_write_nothing(self) -> None:
        before = self.spec_path.read_text()
        cases = [
            (DECIDE, self.repo, 1),
            ((*DECIDE, "--feature", "nope"), self.repo, 1),
            (("decide", "--kind", "decision", "--what", "w", "--why", "  ", "--evidence", "e"), self.wt, 1),
            (("decide", "--kind", "maybe", "--what", "w", "--why", "y", "--evidence", "e"), self.wt, 2),
        ]
        for args, cwd, expected in cases:
            with self.subTest(args=args):
                code, out = self.cli(*args, cwd=cwd)
                self.assertEqual(code, expected, out)
                self.assertIn("error", out)
                self.assertEqual(self.spec_path.read_text(), before)

    def test_section_not_last(self) -> None:
        self.cli(*DECIDE)
        self.spec_path.write_text(self.spec_path.read_text() + "\n## Notes\n\nx\n")
        self.cli("decide", "--kind", "decision", "--what", "w2", "--why", "y2", "--evidence", "e2")
        lines = self.spec_path.read_text().splitlines()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(lines.index(rows[1]), lines.index(rows[0]) + 1)
        self.assertGreater(lines.index("## Notes"), lines.index(rows[1]))

    def test_unreadable_spec_exit_1(self) -> None:
        self.spec_path.chmod(0)
        try:
            code, out = self.cli(*DECIDE, "--feature", "demo")
        finally:
            self.spec_path.chmod(0o644)
        self.assertEqual(code, 1, out)
        self.assertIn("error", out)

    def test_parallel_writers_both_land(self) -> None:
        env = {**self.env}
        procs = [subprocess.Popen([sys.executable, str(DEVLOOP), *DECIDE[:4], f"w{i}", *DECIDE[5:]], cwd=self.wt,
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(2)]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0, 0])
        self.assertEqual(sorted(cells(r)[5] for r in self.rows()), ["w0", "w1"])


EVENT = {"session_id": "s-1", "transcript_path": "/t/s-1.jsonl", "hook_event_name": "StopFailure",
         "error": "rate_limit", "last_assistant_message": "API Error: Rate limit reached"}
INTENT = ("intent", "verified", "open", "next")


class PauseSandbox(AutonomySandbox):
    """Plus a plan for the spec and a ledger in the worktree."""

    def setUp(self) -> None:
        super().setUp()
        put(self.notes / "plans" / SPEC, f"---\nspec: {SPEC}\n---\n\n# Plan\n\n### Task 1: one\n")
        put(self.wt / ".superpowers" / "sdd" / Path(SPEC).stem / "plan-path", f"{self.notes / 'plans' / SPEC}\n")
        put(self.wt / ".superpowers" / "sdd" / Path(SPEC).stem / "progress.md", "# ledger\nTask 1: complete\n\n")
        self.note = Path(git(self.wt, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "pause.json"

    def event(self, payload: dict | str, cwd: Path | None = None) -> tuple[int, dict]:
        stdin = payload if isinstance(payload, str) else json.dumps({**payload, "cwd": str(cwd or self.wt)})
        result = subprocess.run([sys.executable, str(DEVLOOP), "pause", "--event"], cwd=self.root, env=self.env,
                                input=stdin, capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)

    def saved(self) -> dict:
        return json.loads(self.note.read_text())


class PauseTests(PauseSandbox):
    def test_manual_note_fields(self) -> None:
        self.commit(self.wt, "a/x.py", "x\n")
        put(self.wt / "a" / "x.py", "y\n")
        put(self.wt / "new.txt", "x\n")
        code, out = self.cli("pause", "--intent", "fix parser", "--next", "run gate", "--agent", "a1=Task 2")
        self.assertEqual(code, 0, out)
        note = self.saved()
        self.assertEqual(out, {"file": str(self.note), "note": note})
        stage = run_cli("status", cwd=self.wt, env=self.env)[1]["stage"]
        expected = {"reason": "manual", "intent": "fix parser", "next": "run gate", "verified": None, "open": None,
                    "agents": [{"id": "a1", "task": "Task 2"}], "runs": [], "feature": "demo", "stage": stage,
                    "ledger_last": "Task 1: complete", "message": None, "harness": None, "session": None,
                    "transcript": None, "last_decision": None}
        self.assertEqual({k: note[k] for k in expected}, expected)
        self.assertEqual(note["dirty"], ["a/x.py", "new.txt"])
        self.assertRegex(note["at"], r"^\d{4}-\d\d-\d\dT")

    def test_session_and_transcript(self) -> None:
        claude = self.root / "cfg" / "projects" / "x" / "s-9.jsonl"
        codex = self.root / "cx" / "sessions" / "2026" / "10" / "25" / "rollout-1-c-1.jsonl"
        put(claude)
        put(codex)
        cases = [
            ({"CLAUDE_CODE_SESSION_ID": "s-9", "CLAUDE_CONFIG_DIR": str(self.root / "cfg")}, ("claude", "s-9", str(claude))),
            ({"CODEX_THREAD_ID": "c-1", "CODEX_HOME": str(self.root / "cx")}, ("codex", "c-1", str(codex))),
            ({"CLAUDE_CODE_SESSION_ID": "s-0", "CLAUDE_CONFIG_DIR": str(self.root / "cfg")}, ("claude", "s-0", None)),
        ]
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertEqual(self.cli("pause", **env)[0], 0)
                note = self.saved()
                self.assertEqual((note["harness"], note["session"], note["transcript"]), expected)

    def test_last_decision(self) -> None:
        row = self.cli(*DECIDE)[1]["row"]
        self.cli("pause")
        self.assertEqual(self.saved()["last_decision"], row)

    def test_event_note(self) -> None:
        cases = [
            (EVENT, "API Error: Rate limit reached"),
            ({**{k: v for k, v in EVENT.items() if k != "last_assistant_message"}, "error_details": "x"}, "x"),
        ]
        for payload, message in cases:
            with self.subTest(message=message):
                code, out = self.event(payload)
                self.assertEqual(code, 0, out)
                note = self.saved()
                self.assertEqual((note["reason"], note["message"], note["session"], note["transcript"], note["harness"]),
                                 ("rate_limit", message, "s-1", "/t/s-1.jsonl", "claude"))
                self.assertEqual([note[k] for k in INTENT], [None] * 4)

    def test_unreadable_other_spec_still_writes_note(self) -> None:
        self.cli(*DECIDE)
        other = self.notes / "specs" / "20261002 Other.md"
        put(other, frontmatter(feature="other", track="M", review_status="approved"))
        other.chmod(0)
        try:
            for run in (lambda: self.cli("pause"), lambda: self.event(EVENT)):
                self.note.unlink(missing_ok=True)
                code, out = run()
                self.assertEqual(code, 0, out)
                note = self.saved()
                self.assertEqual((note["stage"], note["ledger_last"], note["last_decision"]), (None, None, None))
        finally:
            other.chmod(0o644)

    def test_event_skips(self) -> None:
        cases = [(EVENT, self.repo, "not-a-feature-worktree"), ("not json", None, "bad-input")]
        for payload, cwd, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.event(payload, cwd), (0, {"skipped": reason}))
                self.assertFalse(self.note.exists())

    def test_new_pause_replaces_note(self) -> None:
        self.cli("pause", "--intent", "one")
        self.cli("pause", "--intent", "two")
        self.assertEqual(self.saved()["intent"], "two")

    def test_clear(self) -> None:
        self.cli("pause")
        self.assertEqual(self.cli("pause", "--clear"), (0, {"cleared": True}))
        self.assertFalse(self.note.exists())
        self.assertEqual(self.cli("pause", "--clear"), (0, {"cleared": False}))

    def test_refusals(self) -> None:
        cases = [(("pause", "--clear", "--intent", "x"), self.wt, 2), (("pause", "--event", "--intent", "x"), self.wt, 2),
                 (("pause", "--agent", "noequals"), self.wt, 2), (("pause",), self.home, 1)]
        for args, cwd, expected in cases:
            with self.subTest(args=args):
                code, out = self.cli(*args, cwd=cwd)
                self.assertEqual(code, expected, out)
                self.assertIn("error", out)
        self.assertFalse(self.note.exists())


class PauseStatusTests(PauseSandbox):
    def status_in(self, cwd: Path, *args: str, **env: str) -> dict:
        code, out = run_cli("status", *args, cwd=cwd, env={**self.env, **env})
        self.assertEqual(code, 0, out)
        return out

    def test_event_note_by_session(self) -> None:
        cases = [({"CLAUDE_CODE_SESSION_ID": "s-2"}, True), ({}, True), ({"CODEX_THREAD_ID": "s-2"}, True),
                 ({"CLAUDE_CODE_SESSION_ID": "s-1"}, False)]
        for env, paused in cases:
            with self.subTest(env=env):
                self.event(EVENT)
                note = self.saved()
                out = self.status_in(self.wt, **env)
                self.assertEqual(out.get("paused"), note if paused else None)
                self.assertEqual(out["next"] == "resume from the pause note", paused)
                self.assertEqual(self.note.exists(), paused)

    def test_manual_note_stays_in_same_session(self) -> None:
        self.cli("pause", "--intent", "x", CLAUDE_CODE_SESSION_ID="s-1")
        self.assertIn("paused", self.status_in(self.wt, CLAUDE_CODE_SESSION_ID="s-1"))
        self.assertTrue(self.note.exists())

    def test_board_and_decide_do_not_delete(self) -> None:
        self.event(EVENT)
        self.cli("board", "--no-serve", CLAUDE_CODE_SESSION_ID="s-1")
        self.cli(*DECIDE, CLAUDE_CODE_SESSION_ID="s-1")
        self.assertTrue(self.note.exists())

    def test_main_marks_feature_paused(self) -> None:
        put(self.notes / "specs" / "20261002 Other.md", frontmatter(feature="other", track="M", review_status="draft"))
        self.event(EVENT)
        features = {f["feature"]: f for f in self.status_in(self.repo, CLAUDE_CODE_SESSION_ID="s-2")["features"]}
        self.assertIs(features["demo"].get("paused"), True)
        self.assertNotIn("paused", features["other"])
        self.assertIn("paused", self.status_in(self.repo, "--feature", "demo", CLAUDE_CODE_SESSION_ID="s-2"))

    def test_session_a_pause_on_main(self) -> None:
        self.cli("pause", "--intent", "x", cwd=self.repo)
        self.assertEqual(self.status_in(self.repo)["paused"]["intent"], "x")

    def test_unreadable_note(self) -> None:
        put(self.note, "{")
        out = self.status_in(self.wt)
        self.assertEqual((out["paused"], out["next"]), ({"error": "unreadable pause note"}, "resume from the pause note"))


HOOKS = ROOT / "claude" / "hooks"
DENY_REASON = ("Delivery autonomy grant: no questions. Decide, log it with devloop.py decide, and go on. "
               "A stop-list item: stop and report it as your final message.")


class AutonomyHookTests(PauseSandbox):
    def hook(self, script: str, stdin: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/bin/bash", str(HOOKS / script)], input=stdin, capture_output=True, text=True,
                              env=env or self.env, cwd=self.root, timeout=60)

    def ask(self, cwd: Path) -> str:
        return json.dumps({"tool_name": "AskUserQuestion", "cwd": str(cwd)})

    def test_deny_ask(self) -> None:
        scratch = self.root / "scratch"
        git(self.repo, "worktree", "add", "-q", "-b", "scratch", str(scratch), "origin/main")
        bare = self.root / "bare"
        bare.mkdir()
        git(bare, "init", "-q")
        self.commit(bare, "a.txt", "a\n")
        git(bare, "worktree", "add", "-q", "-b", "feat/x", str(self.root / "bare-x"))
        no_jq = {**self.env, "PATH": "/nonexistent"}
        cases = [(self.ask(self.wt), None, True), (self.ask(self.repo), None, False), (self.ask(scratch), None, False),
                 (self.ask(self.root / "bare-x"), None, False), ("", None, False), (self.ask(self.wt), no_jq, False)]
        for stdin, env, denied in cases:
            with self.subTest(stdin=stdin, env=bool(env)):
                result = self.hook("deny-ask.sh", stdin, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                if denied:
                    out = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual((out["hookEventName"], out["permissionDecision"], out["permissionDecisionReason"]),
                                     ("PreToolUse", "deny", DENY_REASON))
                else:
                    self.assertEqual(result.stdout, "")

    def test_pause_hook(self) -> None:
        import shutil
        shutil.copytree(ROOT / "skill" / "scripts", self.wt / ".agents" / "skills" / "dev-loop" / "scripts")
        outside = self.root / "outside"
        outside.mkdir()
        cases = [(self.wt, True), (outside, False)]
        for cwd, written in cases:
            with self.subTest(cwd=cwd):
                self.note.unlink(missing_ok=True)
                result = self.hook("pause.sh", json.dumps({**EVENT, "cwd": str(cwd)}))
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                self.assertEqual(self.note.exists(), written)
                if written:
                    self.assertEqual(self.saved()["reason"], "rate_limit")

    def test_check_setup_names_missing_hook_entries(self) -> None:
        import check_setup
        settings = json.loads((ROOT / "claude" / "settings.fragment.json").read_text())
        cases = [("PreToolUse", "AskUserQuestion", "missing Claude hook: PreToolUse AskUserQuestion deny-ask.sh"),
                 ("StopFailure", "rate_limit", "missing Claude hook: StopFailure rate_limit pause.sh"),
                 ("PreToolUse", "Edit|Write|NotebookEdit", "missing Claude hook: PreToolUse Edit|Write|NotebookEdit worktree-wall.sh"),
                 ("PreToolUse", "Bash|Monitor", "missing Claude hook: PreToolUse Bash|Monitor git-guard.sh")]
        for event, matcher, line in cases:
            with self.subTest(event=event):
                trimmed = json.loads(json.dumps(settings))
                trimmed["hooks"][event] = [g for g in trimmed["hooks"][event] if g.get("matcher") != matcher]
                path = self.root / "settings.json"
                path.write_text(json.dumps(trimmed))
                failures: list[str] = []
                check_setup.check_claude_settings(path, failures)
                self.assertIn(line, failures)
                path.write_text(json.dumps(settings))
                failures = []
                check_setup.check_claude_settings(path, failures)
                self.assertNotIn(line, failures)

    def test_check_setup_flags_a_guard_still_on_bash(self) -> None:
        import check_setup
        settings = json.loads((ROOT / "claude" / "settings.fragment.json").read_text())
        for group in settings["hooks"]["PreToolUse"]:
            if "git-guard.sh" in json.dumps(group):
                group["matcher"] = "Bash"
        path = self.root / "settings.json"
        path.write_text(json.dumps(settings))
        failures: list[str] = []
        check_setup.check_claude_settings(path, failures)
        self.assertIn("missing Claude hook: PreToolUse Bash|Monitor git-guard.sh", failures)


class KitSkillTests(unittest.TestCase):
    def test_pause_is_user_invoked_only(self) -> None:
        skills = ROOT / "skills"
        cases = [("pause", True), ("continue", False)]
        for name, user_only in cases:
            with self.subTest(name=name):
                text = (skills / name / "SKILL.md").read_text()
                head = text.split("---")[1]
                self.assertIn(f"name: {name}\n", head)
                self.assertEqual("disable-model-invocation: true" in head, user_only)
                yaml = skills / name / "agents" / "openai.yaml"
                self.assertEqual(yaml.is_file() and "allow_implicit_invocation: false" in yaml.read_text(), user_only)


IMPLEMENTERS = [ROOT / "claude" / "agents" / "implementer.md", ROOT / "codex" / "agents" / "implementer.toml"]


class AutonomyContractTests(unittest.TestCase):
    """The kit text of Delivery autonomy: run instead of ask, the decision log, the report, pause and continue."""

    def test_kit_text_names(self) -> None:
        refs = ROOT / "skill" / "references"
        cases = [
            (ROOT / "skill" / "SKILL.md", ["devloop.py decide", "/pause", "/continue", "`paused`", "Attention", "Decisions",
                                           "never dispatch a new agent on unfinished work", "EnterWorktree",
                                           "--kind discovery", "--risk"]),
            (refs / "spec.md", ["## Decision log", "| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |",
                                "decide --feature <slug>"]),
            (refs / "claude.md", ["EnterWorktree", "SendMessage", "TaskStop", "deny-ask", "StopFailure"]),
            (refs / "codex.md", ["send_input", "followup_task", "resume_agent", "interrupt_agent", "close_agent",
                                 "CODEX_THREAD_ID", "errored", "writable root", "$pause", "$continue"]),
            *[(path, ["Discoveries"]) for path in IMPLEMENTERS],
        ]
        for path, values in cases:
            for value in values:
                with self.subTest(path=path.name, value=value):
                    self.assertIn(value.lower(), path.read_text().lower())

    def test_old_sections_are_gone(self) -> None:
        cases = [(ROOT / "skill" / "SKILL.md", ["## Assumptions", "## Discoveries"]),
                 (ROOT / "skill" / "references" / "spec.md", ["## Assumptions"]),
                 *[(path, ["devloop.py decide"]) for path in IMPLEMENTERS]]
        for path, values in cases:
            for value in values:
                with self.subTest(path=path.name, value=value):
                    self.assertNotIn(value, path.read_text())

    def test_delivery_text_never_asks(self) -> None:
        text = (ROOT / "skill" / "SKILL.md").read_text().lower()
        grant = text[text.index("autonomy grant"):text.index("monitor rule")]
        session_b = grant + text[text.index("## 3 acceptance"):]
        for value in ["ask before a tag push", "ask the user"]:
            with self.subTest(value=value):
                self.assertNotIn(value, session_b)


if __name__ == "__main__":
    unittest.main()
