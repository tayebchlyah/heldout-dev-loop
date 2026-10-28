#!/usr/bin/env python3
"""Held-out acceptance tests for Retro Due (spec: Retro Due).

One test per `## Acceptance flows` item (F1 to F3), plus one test for the kit text line O25; each docstring
names the `## Acceptance oracle` lines (O<n>) it proves. Seams: `devloop.py status` and `devloop.py sessions`
(the kit source, `skill/scripts/devloop.py`) and `skill/scripts/check_setup.py`, run as processes in temporary
git repos with `DEV_LOOP_NOTES`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` set to temporary fixture folders.
Every name in the temp repos is invented. HOME is a temporary folder.

Fixture choices the spec leaves open:
- The Gardener Log has the Gardener Encode D8 shape: frontmatter, `# Gardener Log`, the table.
- Claude transcript times end in `.000Z`; the CLI gives them as `YYYY-MM-DDTHH:MM:SSZ`.
- A Codex rollout record has `timestamp`, `type`, `payload`; its `start` and `end` come from the first and
  last `timestamp`, as for Claude.
- Codex Pocock skills sit in `~/.agents/skills/<name>/` and `~/.codex/skills/<name>/` (both written).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import DEVLOOP, REPO_ROOT, Board, git, put  # noqa: E402
from feature_folders_support import NewLayout  # noqa: E402

SKILL = REPO_ROOT / "skill" / "SKILL.md"
REFS = REPO_ROOT / "skill" / "references"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"

SLUG = "demo"
LOG_NAME = "Gardener Log.md"
LOG_HEADER = "| Date | Item | Verdict | Check | Proof |"
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
UTC_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
POCOCK = ("grilling", "domain-modeling", "codebase-design", "code-review", "tdd", "diagnosing-bugs", "pr",
          "prototype", "wayfinder")


def day(offset: int) -> str:
    """Today (local date) plus `offset` days, as `YYYY-MM-DD`."""
    return (date.today() + timedelta(days=offset)).isoformat()


def log_text(*rows: tuple[str, str, str], header: str = LOG_HEADER) -> str:
    """A Gardener Log. Each row: (date, item, verdict), laid out in the order of `header`."""
    names = [cell.strip() for cell in header.strip().strip("|").split("|")]
    lines = ["---", "type: log", "---", "", "# Gardener Log", "", header, "| " + " | ".join("---" for _ in names) + " |"]
    for when, item, verdict in rows:
        values = {"Date": when, "Item": item, "Verdict": verdict, "Check": "", "Proof": ""}
        lines.append("| " + " | ".join(values[name] for name in names) + " |")
    return "\n".join(lines) + "\n"


def iso(when: datetime, millis: bool = True) -> str:
    text = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return text + (".000Z" if millis else "Z")


class RetroCase(NewLayout, Board):
    """The board sandbox (a main checkout `acme-widgets` on `main`, a bare origin, a notes folder, a sandboxed
    HOME), an open spec `feature: demo` with a worktree on `feat/demo`, a fixture `CLAUDE_CONFIG_DIR` and
    a fixture `CODEX_HOME`."""

    def setUp(self) -> None:
        super().setUp()
        for key in SESSION_VARS:
            self.env.pop(key, None)
        self.claude = self.root / "claude-config"
        (self.claude / "projects").mkdir(parents=True)
        self.codex = self.root / "codex-home"
        (self.codex / "sessions").mkdir(parents=True)
        self.env.update(CLAUDE_CONFIG_DIR=str(self.claude), CODEX_HOME=str(self.codex), PYTHONDONTWRITEBYTECODE="1")
        self.log = self.notes / LOG_NAME
        self.spec(SLUG, "Demo")
        self.wt = self.worktree(SLUG)

    def cli(self, *args: str, cwd: Path) -> tuple[int, dict | None, str]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd, env=self.env, capture_output=True,
                                text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        return result.returncode, payload, result.stdout[-600:] + result.stderr[-600:]

    def cli_ok(self, *args: str, cwd: Path) -> dict:
        code, payload, tail = self.cli(*args, cwd=cwd)
        self.assertIsInstance(payload, dict, f"devloop {' '.join(args)}: stdout is not one JSON object: {tail}")
        self.assertEqual(code, 0, payload)
        return payload


class WeeklyReviewDueTests(RetroCase):

    def status(self, *args: str, cwd: Path | None = None) -> dict:
        return self.cli_ok("status", *args, cwd=cwd or self.repo)

    def test_f1_weekly_review_due(self) -> None:
        """F1, O1 to O11: the due rule on `main`, the payloads that never carry the field, and the review that
        a `run` row (as `/garden` writes it) ends."""
        log_path = str(self.notes / LOG_NAME)

        plain = self.status()
        plain_next = plain.get("next")
        with self.subTest(oracle=7):
            self.assertEqual(plain.get("stage"), 1, plain)
            self.assertIn("features", plain)
            self.assertNotIn("review_due", plain)

        shipped = self.spec("widget-export", "Widget Export", shipped="2026-10-01")
        with self.subTest(oracle=8):
            self.assertEqual(self.status().get("review_due"), {"last": None, "days": None, "log": log_path})
        shipped.unlink()

        cases = [
            (1, log_text((day(-7), "3 items: 1 encode, 0 fix, 1 keep, 1 drop", "run")),
             {"last": day(-7), "days": 7, "log": log_path}),
            (2, log_text((day(-6), "2 items: 0 encode, 1 fix, 1 keep, 0 drop", "run")), None),
            (4, log_text((day(-7), "3 items: 1 encode, 0 fix, 1 keep, 1 drop", "run"),
                         header="| Verdict | Date | Item | Check | Proof |"),
             {"last": day(-7), "days": 7, "log": log_path}),
            (5, log_text(("2026-13-40", "1 items: 0 encode, 0 fix, 1 keep, 0 drop", "run"),
                         ("yesterday", "1 items: 0 encode, 0 fix, 1 keep, 0 drop", "run")),
             {"last": None, "days": None, "log": log_path}),
            (6, log_text(), {"last": None, "days": None, "log": log_path}),
            (9, log_text((day(1), "1 items: 1 encode, 0 fix, 0 keep, 0 drop", "run")), None),
        ]
        for oracle, text, expected in cases:
            put(self.log, text)
            with self.subTest(oracle=oracle):
                found = self.status()
                if expected is None:
                    self.assertNotIn("review_due", found)
                else:
                    self.assertEqual(found.get("review_due"), expected, found)

        put(self.log, log_text((day(-8), "4 items: 1 encode, 1 fix, 1 keep, 1 drop", "run"),
                               (day(0), "Comment rule", "encode")))
        with self.subTest(oracle=3):
            due = self.status().get("review_due")
            self.assertIsInstance(due, dict)
            self.assertEqual(due.get("last"), day(-8), due)

        with self.subTest(oracle=10):
            self.assertIn("review_due", self.status())
            self.assertEqual(self.status().get("next"), plain_next)

        with self.subTest(oracle=11):
            self.assertNotIn("review_due", self.status("--feature", SLUG))
            in_worktree = self.status(cwd=self.wt)
            self.assertEqual(in_worktree.get("feature"), SLUG, in_worktree)
            self.assertNotIn("review_due", in_worktree)

        # F1: a shipped spec and a `run` row 8 days old; a `run` row of today ends the review.
        self.spec("widget-export", "Widget Export", shipped="2026-10-01")
        put(self.log, log_text((day(-8), "3 items: 1 encode, 0 fix, 1 keep, 1 drop", "run")))
        with self.subTest(flow=1, step="due"):
            due = self.status().get("review_due")
            self.assertEqual(due, {"last": day(-8), "days": 8, "log": log_path})
        put(self.log, log_text((day(-8), "3 items: 1 encode, 0 fix, 1 keep, 1 drop", "run"),
                               (day(0), "2 items: 0 encode, 1 fix, 1 keep, 0 drop", "run")))
        with self.subTest(flow=1, step="garden ran"):
            self.assertNotIn("review_due", self.status())


class SessionListTests(RetroCase):

    def setUp(self) -> None:
        super().setUp()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    # Claude fixtures

    def claude_folder(self, worktree: Path) -> Path:
        return self.claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(worktree))

    def claude_transcript(self, worktree: Path, session: str, records: list[dict]) -> Path:
        path = self.claude_folder(worktree) / f"{session}.jsonl"
        lines = []
        for n, record in enumerate(records, 1):
            lines.append(json.dumps({"uuid": f"{session}-{n:03d}", "sessionId": session, "cwd": str(worktree),
                                     **record}))
        put(path, "".join(f"{line}\n" for line in lines))
        return path

    def claude_subagent(self, worktree: Path, session: str, agent_id: str, records: list[dict]) -> Path:
        base = self.claude_folder(worktree) / session / "subagents"
        put(base / f"agent-{agent_id}.meta.json",
            json.dumps({"agentType": "general-purpose", "description": "Check the widgets"}) + "\n")
        path = base / f"agent-{agent_id}.jsonl"
        put(path, "".join(json.dumps({"sessionId": session, "isSidechain": True, **r}) + "\n" for r in records))
        return path

    @staticmethod
    def user(when: datetime, content: object, **extra: object) -> dict:
        return {"type": "user", "timestamp": iso(when), "message": {"role": "user", "content": content}, **extra}

    @staticmethod
    def assistant(when: datetime, words: str) -> dict:
        return {"type": "assistant", "timestamp": iso(when),
                "message": {"id": f"msg-{when.timestamp():.0f}", "role": "assistant", "model": "claude-opus-4-7",
                            "content": [{"type": "text", "text": words}]}}

    # Codex fixtures

    def rollout(self, session: str, cwd: Path, source: object, start: datetime, prompts: list[str],
                padding: int = 0) -> Path:
        stamp = start.strftime("%Y-%m-%dT%H-%M-%S")
        path = self.codex / "sessions" / start.strftime("%Y/%m/%d") / f"rollout-{stamp}-{session}.jsonl"
        records = [{"timestamp": iso(start), "type": "session_meta",
                    "payload": {"id": session, "timestamp": iso(start), "cwd": str(cwd), "originator": "codex_cli_rs",
                                "cli_version": "0.50.0", "source": source}}]
        when = start
        for prompt in prompts:
            when += timedelta(minutes=5)
            records.append({"timestamp": iso(when), "type": "response_item",
                            "payload": {"type": "message", "role": "user",
                                        "content": [{"type": "input_text", "text": prompt}]}})
        when += timedelta(minutes=30)
        records.append({"timestamp": iso(when), "type": "response_item",
                        "payload": {"type": "message", "role": "assistant",
                                    "content": [{"type": "output_text", "text": "Done. " + "x" * padding}]}})
        put(path, "".join(json.dumps(r) + "\n" for r in records))
        return path

    def sessions(self, *args: str, cwd: Path | None = None) -> tuple[int, dict | None, str]:
        return self.cli("sessions", *args, cwd=cwd or self.repo)

    def item(self, listed: list[dict], harness: str, session: str) -> dict:
        found = [i for i in listed if i.get("harness") == harness and i.get("session") == session]
        self.assertEqual(len(found), 1, f"{harness} {session} listed {len(found)} times: {listed}")
        return found[0]

    def test_f2_session_list_for_retro(self) -> None:
        """F2, O12 to O23: Claude and Codex sessions of all the worktrees, the subagents folded into their parent,
        the filters, the fields, the order and the usage errors."""
        repo = Path(os.path.realpath(self.repo))
        wt = Path(os.path.realpath(self.wt))
        stage_times = {"3": iso(self.now - timedelta(days=4), millis=False),
                       "5": iso(self.now - timedelta(days=3) + timedelta(hours=1), millis=False)}
        put(self.repo / ".git" / "dev-loop" / "board-state.json",
            json.dumps({"features": {SLUG: {"stage_times": stage_times}}}) + "\n")

        # Claude, main worktree, 2 days old, one subagent (O12, O15). The largest session.
        main_start = self.now - timedelta(days=2, hours=1)
        main_end = self.now - timedelta(days=2)
        long_prompt = "Frame the widget export feature. " * 10
        long_prompt = long_prompt[:300]
        self.assertEqual(len(long_prompt), 300)
        claude_main = self.claude_transcript(repo, "c1a0de00-main-0001", [
            self.user(main_start, "Caveat: the messages below were generated by the user while running local "
                                  "commands.", isMeta=True),
            self.user(main_start + timedelta(minutes=1), "<command-name>/clear</command-name>"),
            self.user(main_start + timedelta(minutes=2), long_prompt),
            self.assistant(main_start + timedelta(minutes=3), "Reading the spec. " + "y" * 30000),
            self.assistant(main_end, "Framed."),
        ])
        claude_sub = self.claude_subagent(repo, "c1a0de00-main-0001", "a1b2c3", [
            self.user(main_start + timedelta(minutes=10), "Check the widgets"),
            self.assistant(main_start + timedelta(minutes=20), "Checked."),
        ])

        # Claude, feature worktree, in Stage 3 (O14). The smallest session.
        feature_start = self.now - timedelta(days=3)
        claude_feature = self.claude_transcript(wt, "c1a0de00-feat-0002", [
            self.user(feature_start, [{"type": "text", "text": "Continue demo"}]),
            self.assistant(feature_start + timedelta(minutes=40), "Continued."),
        ])

        # Claude, main worktree, 10 days old (O13).
        old_start = self.now - timedelta(days=10, hours=1)
        self.claude_transcript(repo, "c1a0de00-old-0003", [
            self.user(old_start, "An old request"),
            self.assistant(self.now - timedelta(days=10), "Old answer."),
        ])

        # Codex, main worktree, with a subagent and a guardian (O17, O18, O20). The middle session.
        codex_start = self.now - timedelta(days=1, hours=2)
        codex_main = self.rollout("019a0000-0000-7000-8000-00000000c0de", repo, "cli", codex_start,
                                  ["# AGENTS.md instructions for " + str(repo) + "\n\n<INSTRUCTIONS>Be brief."
                                   "</INSTRUCTIONS>",
                                   "<environment_context>\n  <cwd>" + str(repo) + "</cwd>\n</environment_context>",
                                   "  Fix the flaky widget test  "], padding=8000)
        codex_sub = self.rollout("019a0000-0000-7000-8000-0000000005ab", repo,
                                 {"subagent": {"thread_spawn": {"parent_thread_id": "019a0000-0000-7000-8000-00000000c0de",
                                                                "depth": 1}}},
                                 codex_start + timedelta(minutes=10), ["Look at the widget test"], padding=500)
        self.rollout("019a0000-0000-7000-8000-00000000917d", repo, {"subagent": {"other": "guardian"}},
                     codex_start + timedelta(minutes=12), ["Review this command"], padding=50000)

        expected_order = [("claude", "c1a0de00-main-0001"), ("codex", "019a0000-0000-7000-8000-00000000c0de"),
                          ("claude", "c1a0de00-feat-0002")]

        payload = self.cli_ok("sessions", "--since", "7", cwd=self.repo)
        listed = payload.get("sessions")
        self.assertIsInstance(listed, list, payload)
        with self.subTest(flow=2):
            self.assertEqual([(i.get("harness"), i.get("session")) for i in listed], expected_order, listed)
            since = payload.get("since")
            self.assertRegex(str(since), UTC_SECONDS)
            start = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            self.assertLess(abs((start - (self.now - timedelta(days=7))).total_seconds()), 300, since)

        with self.subTest(oracle=12):
            item = self.item(listed, "claude", "c1a0de00-main-0001")
            self.assertEqual(item.get("path"), str(claude_main), item)
            self.assertEqual(item.get("worktree"), str(repo), item)
            self.assertIsNone(item.get("feature"), item)
            self.assertEqual(item.get("start"), iso(main_start, millis=False), item)
            self.assertEqual(item.get("end"), iso(main_end, millis=False), item)
            self.assertEqual(item.get("bytes"), claude_main.stat().st_size + claude_sub.stat().st_size, item)
            self.assertEqual(item.get("subagents"), 1, item)

        with self.subTest(oracle=13):
            self.assertNotIn("c1a0de00-old-0003", [i.get("session") for i in listed])

        with self.subTest(oracle=14):
            item = self.item(listed, "claude", "c1a0de00-feat-0002")
            self.assertEqual(item.get("worktree"), str(wt), item)
            self.assertEqual((item.get("feature"), item.get("stage")), (SLUG, "3"), item)
            self.assertEqual(item.get("first_request"), "Continue demo", item)
            self.assertEqual(item.get("subagents"), 0, item)

        with self.subTest(oracle=15):
            item = self.item(listed, "claude", "c1a0de00-main-0001")
            self.assertEqual(item.get("first_request"), long_prompt[:200], item)

        with self.subTest(oracle=17):
            item = self.item(listed, "codex", "019a0000-0000-7000-8000-00000000c0de")
            self.assertNotIn("019a0000-0000-7000-8000-0000000005ab", [i.get("session") for i in listed])
            self.assertEqual(item.get("subagents"), 1, item)
            self.assertEqual(item.get("bytes"), codex_main.stat().st_size + codex_sub.stat().st_size, item)
            self.assertEqual(item.get("path"), str(codex_main), item)
            self.assertEqual(item.get("worktree"), str(repo), item)

        with self.subTest(oracle=18):
            self.assertNotIn("019a0000-0000-7000-8000-00000000917d", [i.get("session") for i in listed])

        with self.subTest(oracle=20):
            item = self.item(listed, "codex", "019a0000-0000-7000-8000-00000000c0de")
            self.assertEqual(item.get("first_request"), "Fix the flaky widget test", item)

        with self.subTest(oracle=21):
            sizes = [i.get("bytes") for i in listed]
            self.assertEqual(sizes, sorted(sizes, reverse=True), listed)
            self.assertGreater(sizes[0], sizes[1], listed)

        # Codex in a subfolder of the feature worktree (O16), and outside every worktree (O19).
        sub = wt / "src" / "widgets"
        sub.mkdir(parents=True)
        outside = self.root / "elsewhere"
        outside.mkdir()
        self.rollout("019a0000-0000-7000-8000-0000000fea70", sub, "exec", self.now - timedelta(hours=5),
                     ["Run the widget gate"])
        self.rollout("019a0000-0000-7000-8000-00000000075d", outside, "cli", self.now - timedelta(hours=4),
                     ["Something unrelated"])
        listed = self.cli_ok("sessions", "--since", "7", cwd=self.repo).get("sessions") or []

        with self.subTest(oracle=16):
            item = self.item(listed, "codex", "019a0000-0000-7000-8000-0000000fea70")
            self.assertEqual((item.get("worktree"), item.get("feature")), (str(wt), SLUG), item)

        with self.subTest(oracle=19):
            self.assertNotIn("019a0000-0000-7000-8000-00000000075d", [i.get("session") for i in listed])

        with self.subTest(decision="D11", check="item fields"):
            for item in listed:
                self.assertEqual(set(item), {"harness", "session", "path", "worktree", "feature", "start", "end",
                                             "bytes", "subagents", "stage", "first_request"}, item)
                self.assertRegex(item["start"], UTC_SECONDS)
                self.assertRegex(item["end"], UTC_SECONDS)

        with self.subTest(oracle=22):
            for args in (("--since", "0"), ("--since", "x"), ()):
                code, _, tail = self.sessions(*args)
                self.assertEqual(code, 2, f"sessions {' '.join(args)}: {tail}")

        with self.subTest(oracle=23):
            plain = self.root / "plain"
            plain.mkdir()
            code, payload, tail = self.cli("sessions", "--since", "7", cwd=plain)
            self.assertEqual(code, 1, tail)
            self.assertIsInstance(payload, dict, tail)
            self.assertTrue(payload.get("error"), payload)


class CodexSetupTests(unittest.TestCase):

    def test_f3_codex_setup_names_retro(self) -> None:
        """F3, O24: `check_setup.py --harness codex` with every Codex skill but `retro` names `retro`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home, notes, project = root / "home", root / "notes", root / "acme-widgets"
            for folder in (home, notes / "Features", project):
                folder.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if not k.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
            env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"})
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, env=env, check=True, capture_output=True)
            init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=codex", f"--notes={notes}"],
                                  cwd=project, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            for base in (home / ".agents" / "skills", home / ".codex" / "skills"):
                for name in POCOCK:
                    put(base / name / "SKILL.md", f"---\nname: {name}\ndescription: The {name} skill.\n---\n\n# {name}\n")
            result = subprocess.run([sys.executable, str(CHECKER), "--harness", "codex", "--home", str(home),
                                     "--repo", str(project)], env=env, capture_output=True, text=True, timeout=120)
            output = result.stdout + result.stderr
            lines = output.splitlines()
            heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
            self.assertTrue(heads, f"no 'User actions required' section:\n{output}")
            named = [line for line in lines[heads[0] + 1:] if re.search(r"(?<![\w-])retro(?![\w-])", line)]
            self.assertTrue(named, f"no user action line names retro:\n{output}")


class KitTextTests(unittest.TestCase):

    def test_o25_stage_1_and_route_text(self) -> None:
        """O25: SKILL.md Stage 1 names the due field, the session list, `## Gardener`, `/garden` and a waiver
        for this session only; the routes name their retro skill."""
        text = SKILL.read_text()
        match = re.search(r"^## 1 Frame.*$", text, re.M)
        self.assertIsNotNone(match, "no `## 1 Frame` section in SKILL.md")
        rest = text[match.end():]
        end = re.search(r"^## ", rest, re.M)
        stage1 = rest[:end.start()] if end else rest
        for phrase in ("review_due", "devloop.py sessions --since 7", "## Gardener", "/garden"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, stage1)
        with self.subTest(phrase="waiver"):
            self.assertRegex(stage1, r"(?i)waive")
            self.assertRegex(stage1, r"(?i)this session only")
        with self.subTest(route="claude"):
            self.assertIn("/retro", (REFS / "claude.md").read_text())
        with self.subTest(route="codex"):
            self.assertIn("$retro", (REFS / "codex.md").read_text())


if __name__ == "__main__":
    unittest.main()
