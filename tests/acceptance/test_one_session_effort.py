#!/usr/bin/env python3
"""Held-out oracle tests for one session and effort per stage (spec: One Session and Effort per Stage), part 1:
`status`, `metrics`, the board label, acceptance flows 1 and 3.

Each docstring names the `## Acceptance oracle` lines (O<n>) or flows (F<n>) it proves. Seams: `devloop.py
status`, `metrics` and `board` run as processes in a temporary repo (stdout JSON, exit code, the notes files they
write), and `bin/install.mjs init` for flow 1. Fixtures: the board sandbox (a main checkout with `dev-loop.json`, a
bare origin, a notes folder, a sandboxed HOME), a fixture `CLAUDE_CONFIG_DIR` with fake transcripts holding
`api-request` records, and a hand-written `<git-common-dir>/dev-loop/board-state.json`. Every name is invented.

Fixture choices the spec leaves open:
- An `api-request` record has the shape Claude Code writes: `type`, `sessionId`, `id`, `timestamp`, `version`,
  `params.model`, `params.output_config.effort`; a record with no effort value has no `output_config`.
- A subagent writes its `api-request` records in its own transcript under `<session>/subagents/`.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import DEVLOOP, REPO_ROOT, Board, git, put
from feature_folders_support import NewLayout

INSTALLER = REPO_ROOT / "bin" / "install.mjs"
OPUS = "claude-opus-5-5"
DAY = "2026-10-20"
METRICS_FILE = "Feature Metrics.md"
OLD_HEADER = ("| feature | track | shipped | sessions | active h | user msgs | gate runs | gate s | unit tests "
              "| acceptance tests | denials | S1-2 | S3 | S4 | S5 | S6 | S7 | total | escaped |")
HEADER = ("| feature | track | shipped | sessions | active h | user msgs | gate runs | gate s | unit tests "
          "| acceptance tests | denials | S1-2 | S3 | S4 | S5 | S6 | S7 | total | effort | escaped |")
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR", "CLAUDE_EFFORT")


def at(clock: str) -> str:
    return f"{DAY}T{clock}.000Z"


def board_time(clock: str) -> str:
    return f"{DAY}T{clock}Z"


def cells(line: str) -> list[str]:
    """The cells of one Markdown table row: split on `|` not escaped by `\\`."""
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:|-]+\|", line.strip()))


def usage(inp: int = 100) -> dict:
    return {"input_tokens": inp, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}


class Transcript:
    """One Claude transcript file, built record by record."""

    def __init__(self, path: Path, session: str, cwd: Path) -> None:
        self.path, self.session, self.cwd = path, session, cwd
        self.lines: list[str] = []
        self.count = 0

    def _line(self, record: dict) -> None:
        self.count += 1
        self.lines.append(json.dumps(record))

    def assistant(self, clock: str, blocks: list[dict]) -> None:
        self._line({"type": "assistant", "uuid": f"{self.session}-{self.count:03d}", "sessionId": self.session,
                    "cwd": str(self.cwd), "gitBranch": "main", "timestamp": at(clock),
                    "message": {"id": f"msg_{self.session}_{self.count}", "type": "message", "role": "assistant",
                                "model": OPUS, "content": blocks, "usage": usage()}})

    def request(self, clock: str, effort: str | None) -> None:
        """One `api-request` record; `effort` None: no `output_config`."""
        params: dict = {"model": OPUS, "max_tokens": 128000}
        if effort is not None:
            params["output_config"] = {"effort": effort}
        self._line({"type": "api-request", "sessionId": self.session, "id": f"req-{self.session}-{self.count:03d}",
                    "timestamp": at(clock), "version": "2.1.310", "querySource": "sdk", "params": params})

    def save(self) -> None:
        put(self.path, "".join(f"{line}\n" for line in self.lines))


def bash(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def read(tool_id: str, path: Path) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": str(path)}}


def agent(tool_id: str, kind: str, description: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Agent",
            "input": {"subagent_type": kind, "description": description, "prompt": description}}


class EffortCase(NewLayout, Board):
    """The board sandbox, a fixture `CLAUDE_CONFIG_DIR`, no effort or session variable inherited."""

    def setUp(self) -> None:
        super().setUp()
        for key in SESSION_VARS:
            self.env.pop(key, None)
        self.claude = self.root / "claude-config"
        (self.claude / "projects").mkdir(parents=True)
        self.env.update(CLAUDE_CONFIG_DIR=str(self.claude), PYTHONDONTWRITEBYTECODE="1")
        self.metrics_file = self.notes / METRICS_FILE

    def cli(self, command: str, *args: str, cwd: Path, effort: str | None = None) -> tuple[int, dict]:
        env = dict(self.env)
        if effort is not None:
            env["CLAUDE_EFFORT"] = effort
        result = subprocess.run([sys.executable, str(DEVLOOP), command, *args], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"{command} {' '.join(args)}: stdout is not one JSON object (exit "
                                 f"{result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        self.assertIsInstance(payload, dict)
        self.assertEqual(result.returncode, 0, payload)
        return result.returncode, payload

    def status(self, *args: str, cwd: Path, effort: str | None = None) -> dict:
        return self.cli("status", *args, cwd=cwd, effort=effort)[1]

    def metrics(self, *args: str, cwd: Path) -> dict:
        return self.cli("metrics", *args, cwd=cwd)[1]

    def folder(self, worktree: Path) -> Path:
        return self.claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(worktree))

    def transcript(self, session: str) -> Transcript:
        return Transcript(self.folder(self.repo) / f"{session}.jsonl", session, cwd=self.repo)

    def subagent(self, session: str, agent_id: str, tool_use_id: str, kind: str, description: str) -> Transcript:
        base = self.folder(self.repo) / session / "subagents"
        put(base / f"agent-{agent_id}.meta.json",
            json.dumps({"agentType": kind, "description": description, "toolUseId": tool_use_id}) + "\n")
        return Transcript(base / f"agent-{agent_id}.jsonl", session, cwd=self.repo)

    def board_state(self, features: dict) -> None:
        put(self.repo / ".git" / "dev-loop" / "board-state.json", json.dumps({"features": features}) + "\n")

    @staticmethod
    def times(**clocks: str) -> dict:
        return {key.lstrip("s"): board_time(clock) for key, clock in clocks.items()}

    def claim(self, slug: str) -> str:
        return f"python3 .agents/skills/dev-loop/scripts/devloop.py board --claim {slug}"

    def table(self) -> list[str]:
        """The table lines of the notes file, header first."""
        lines = [line.strip() for line in self.metrics_file.read_text().splitlines()]
        start = next((n for n, line in enumerate(lines) if line.startswith("| feature |")), None)
        self.assertIsNotNone(start, self.metrics_file.read_text())
        out = []
        for line in lines[start:]:
            if not line.startswith("|"):
                break
            out.append(line)
        return out


class StatusEffortTests(EffortCase):
    def test_o1_main_with_no_feature(self) -> None:
        """O1: on `main` with no feature and `CLAUDE_EFFORT=medium`: `effort` is `{"level": "medium", "want": "high"}`."""
        out = self.status(cwd=self.repo, effort="medium")
        self.assertEqual(out.get("effort"), {"level": "medium", "want": "high"}, out)

    def test_o2_draft_spec_wants_high(self) -> None:
        """O2: a feature with a draft spec (stage 2) and `CLAUDE_EFFORT=high`: `{"level": "high", "want": "high"}`."""
        self.spec("draft-feature", "Draft Feature", review="draft")
        out = self.status("--feature", "draft-feature", cwd=self.repo, effort="high")
        self.assertEqual(out.get("stage"), 2, out)
        self.assertEqual(out.get("effort"), {"level": "high", "want": "high"}, out)

    def test_o3_delivery_stages_want_medium(self) -> None:
        """O3: a feature at stage 3, 5 or 7 in its worktree, `CLAUDE_EFFORT=high`: `{"level": "high", "want": "medium"}`."""
        self.spec("gamma", "Gamma Report")
        stage3 = self.worktree("gamma")
        stage5, _ = self.stage5("delta", "Delta Report")
        stage7 = self.stage7("omega", "Omega Report")
        for stage, wt in ((3, stage3), (5, stage5), (7, stage7)):
            with self.subTest(stage=stage):
                out = self.status(cwd=wt, effort="high")
                self.assertEqual(out.get("stage"), stage, out)
                self.assertEqual(out.get("effort"), {"level": "high", "want": "medium"}, out)

    def test_o4_track_s_stage_5_wants_medium(self) -> None:
        """O4: a Track S feature at stage 5: `want` is `medium`."""
        wt = self.worktree("fix-typo", prefix="fix")
        out = self.status(cwd=wt, effort="high")
        self.assertEqual(out.get("stage"), 5, out)
        self.assertEqual((out.get("effort") or {}).get("want"), "medium", out)

    def test_o5_level_null_when_unset_empty_or_unknown(self) -> None:
        """O5: `CLAUDE_EFFORT` unset, empty, or `turbo`: `level` is null."""
        for name, value in (("unset", None), ("empty", ""), ("turbo", "turbo")):
            with self.subTest(case=name):
                out = self.status(cwd=self.repo, effort=value)
                self.assertIn("effort", out, out)
                self.assertIsNone(out["effort"].get("level", "missing"), out)
                self.assertEqual(out["effort"].get("want"), "high", out)

    def test_o6_shipped_feature_wants_nothing(self) -> None:
        """O6: a shipped feature: `want` is null."""
        self.spec("old-export", "Old Export", shipped="2026-10-01")
        out = self.status("--feature", "old-export", cwd=self.repo, effort="medium")
        self.assertEqual(out.get("stage"), "shipped", out)
        self.assertIn("effort", out, out)
        self.assertIsNone(out["effort"].get("want", "missing"), out)
        self.assertEqual(out["effort"].get("level"), "medium", out)


class BoardLabelTests(EffortCase):
    def test_o14_approved_claim_waits_for_start_delivery(self) -> None:
        """O14: a claimed feature with an approved spec and no worktree: the note has `waiting` "start Delivery"."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        self.set_field(spec, "review_status", "approved")
        self.ok()
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (3, "start Delivery"))


class EffortRecordFlowTests(EffortCase):
    def test_f3_effort_record_and_table_migration(self) -> None:
        """F3, O7, O8, O9, O10, O13: a feature with board stage times and transcripts holding `api-request` records;
        `metrics --write` writes the row with the `effort` cell and migrates an old table."""
        slug = "widget-export"
        self.spec(slug, "Widget Export", track="M")
        wt = self.worktree(slug)
        self.board_state({slug: {"backfilled": False, "stage_times": self.times(
            s1="08:00:00", s2="08:30:00", s3="10:00:00", s4="11:00:00", s5="12:00:00", s6="14:00:00",
            s7="16:00:00")}})

        main = self.transcript("sess-main")
        main.assistant("09:00:00", [bash("toolu_claim", self.claim(slug))])
        for clock, effort in (("09:01:00", "high"), ("09:02:00", "medium"), ("09:03:00", "high"),
                              ("09:04:00", "high")):
            main.request(clock, effort)
        for clock, effort in (("10:01:00", "high"), ("10:02:00", "medium"), ("10:03:00", "medium"),
                              ("10:04:00", "high")):
            main.request(clock, effort)
        main.assistant("11:30:00", [read("toolu_r1", wt / "README.md")])
        main.request("12:05:00", "high")
        main.assistant("12:10:00", [agent("toolu_impl", "implementer", "Task 1: export")])
        impl = self.subagent("sess-main", "impl1", "toolu_impl", "implementer", "Task 1: export")
        impl.request("12:11:00", "medium")
        impl.assistant("12:11:30", [read("toolu_s1", wt / "src" / "export.py")])
        impl.request("12:12:00", "medium")
        impl.assistant("12:12:30", [read("toolu_s2", wt / "src" / "export.py")])
        impl.save()
        main.request("14:05:00", None)
        main.request("14:06:00", None)
        main.request("16:05:00", "medium")
        main.assistant("16:06:00", [read("toolu_r2", wt / "README.md")])
        main.save()

        older = ["| older-one | S |  | 1 | 0.5 | 2 | 1 | 12 | 1 | 0 | 0 | - | - | - | 4 | 2 | 1 | 7 |  |",
                 "| older-two | M | 2026-10-01 | 2 | 3.0 | 9 | 4 | 80 | 5 | 3 | 1 | 10 | 5 | 6 | 20 | 15 | 4 | 60 | yes |"]
        separator = "| " + " | ".join(["---"] * 19) + " |"
        put(self.metrics_file, "---\ntype: note\ntags: [dev-loop, feature-metrics]\n---\n\n# Feature Metrics\n\n"
                               "No process cut on cost until 5 features have a full row.\n\n"
                               + "\n".join([OLD_HEADER, separator, *older]) + "\n")

        out = self.metrics("--write", cwd=wt)
        effort = out.get("effort")
        self.assertIsInstance(effort, dict, out)
        with self.subTest(oracle=7):
            self.assertEqual(effort.get("1-2", {}).get("main"), {"high": 3, "medium": 1}, effort)
        with self.subTest(oracle=8):
            self.assertEqual(effort.get("3", {}).get("main"), {"high": 2, "medium": 2}, effort)
        with self.subTest(oracle=9):
            self.assertEqual(effort.get("5", {}).get("implementer"), {"medium": 2}, effort)
            self.assertEqual(effort.get("5", {}).get("main"), {"high": 1}, effort)
        with self.subTest(oracle=10):
            self.assertFalse(effort.get("4", {}).get("main"), effort)
            self.assertEqual(effort.get("6", {}).get("main"), {"?": 2}, effort)
        row = cells(out["row"])
        self.assertEqual(len(row), 20, out["row"])
        with self.subTest(oracle="7, 8, 9, 10"):
            self.assertEqual(row[-2], "1-2:h 3:h 4:- 5:h 6:? 7:m", out["row"])

        with self.subTest(oracle=13):
            table = self.table()
            self.assertEqual(table[0], HEADER, table)
            self.assertTrue(is_separator(table[1]), table)
            self.assertEqual(len(cells(table[1])), 20, table[1])
            self.assertNotIn(OLD_HEADER, self.metrics_file.read_text())
            rows = [line for line in table[2:] if not is_separator(line)]
            for old in older:
                name = cells(old)[0]
                found = [line for line in rows if cells(line)[0] == name]
                self.assertEqual(len(found), 1, rows)
                self.assertEqual(cells(found[0]), cells(old)[:-1] + ["?", cells(old)[-1]], found[0])
            self.assertEqual(cells([line for line in rows if cells(line)[0] == "older-two"][0])[-1], "yes")
            own = [line for line in rows if cells(line)[0] == slug]
            self.assertEqual(len(own), 1, rows)
            self.assertEqual(cells(own[0])[:-1], row[:-1], own[0])


class UnattributedTests(EffortCase):
    def test_o11_o12_unattributed_cell_and_new_file_header(self) -> None:
        """O11, O12: an `unattributed` feature: the `effort` cell is `?`; the header of a new file has `effort`
        between `total` and `escaped`."""
        slug = "audit-log"
        self.spec(slug, "Audit Log", track="M")
        wt = self.worktree(slug)
        self.board_state({slug: {"backfilled": True, "stage_times": self.times(
            s5="12:00:00", s6="14:00:00", s7="16:00:00")}})
        session = self.transcript("sess-1")
        session.request("12:09:00", "high")
        session.assistant("12:10:00", [read("toolu_r", wt / "src" / "audit.py")])
        session.request("14:09:00", "medium")
        session.assistant("14:10:00", [read("toolu_r2", wt / "src" / "audit.py")])
        session.save()
        self.assertFalse(self.metrics_file.exists())

        out = self.metrics("--write", cwd=wt)
        self.assertTrue(out.get("unattributed"), out)
        with self.subTest(oracle=11):
            self.assertEqual(cells(out["row"])[-2], "?", out["row"])
        with self.subTest(oracle=12):
            table = self.table()
            self.assertEqual(table[0], HEADER, table)
            own = [line for line in table[1:] if not is_separator(line) and cells(line)[0] == slug]
            self.assertEqual(len(own), 1, table)
            self.assertEqual(cells(own[0])[-2], "?", own[0])


class OneSessionFlowTests(unittest.TestCase):
    def test_f1_one_session_claude(self) -> None:
        """F1, O1, O3: `init` a project; `status` on `main` with `CLAUDE_EFFORT=high` gives `want` `high`; a spec is
        approved; `status --feature <slug>` in the new worktree with `CLAUDE_EFFORT=medium` gives
        `{"level": "medium", "want": "medium"}`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home, notes, scratch = root / "home", root / "notes", root / "tmp"
            for folder in (home, notes / "Features", scratch):
                folder.mkdir(parents=True)
            put(home / ".gitconfig", "[user]\n\tname = Robin Quill\n\temail = robin@example.invalid\n"
                                     "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
            env.update(HOME=str(home), TMPDIR=str(scratch), XDG_CONFIG_HOME=str(home / ".config"),
                       GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1", DEV_LOOP_NOTES=str(notes),
                       CLAUDE_CONFIG_DIR=str(root / "claude-config"))
            project = root / "kiosk-app"
            project.mkdir()

            def run(*args: str, cwd: Path = project, effort: str | None = None) -> subprocess.CompletedProcess[str]:
                run_env = dict(env)
                if effort is not None:
                    run_env["CLAUDE_EFFORT"] = effort
                return subprocess.run(args, cwd=cwd, env=run_env, capture_output=True, text=True, timeout=180)

            def ok(*args: str, cwd: Path = project, effort: str | None = None) -> str:
                done = run(*args, cwd=cwd, effort=effort)
                self.assertEqual(done.returncode, 0, f"{args}: {done.stdout[-600:]} {done.stderr[-600:]}")
                return done.stdout

            ok("git", "init", "-q", "-b", "main")
            put(project / "GLOSSARY.md", "# Kiosk\n\n**Kiosk**:\nA shop terminal.\n")
            ok("node", str(INSTALLER), "init", "--yes", "--harness=claude", f"--notes={notes}")
            ok("git", "add", "-A")
            ok("git", "commit", "-q", "-m", "dev-loop kit")
            devloop = project / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
            self.assertTrue(devloop.is_file(), "init did not install devloop.py")

            def status(*args: str, cwd: Path, effort: str) -> dict:
                return json.loads(ok(sys.executable, str(devloop), "status", *args, cwd=cwd, effort=effort))

            on_main = status(cwd=project, effort="high")
            self.assertEqual((on_main.get("effort") or {}).get("want"), "high", on_main)
            self.assertEqual(on_main["effort"].get("level"), "high", on_main)

            put(notes / "Features" / "20261027 Kiosk Login" / "Spec.md",
                "---\ntype: spec\nreview_status: approved\nfeature: kiosk-login\ntrack: M\nacceptance:\nshipped:\n"
                "---\n\n# Kiosk Login\n\n## Goal\n\nA login screen for the kiosk.\n")
            wt = root / "worktrees" / "kiosk-app-kiosk-login"
            ok("git", "worktree", "add", "-q", "-b", "feat/kiosk-login", str(wt))
            feature = status("--feature", "kiosk-login", cwd=wt.resolve(), effort="medium")
            self.assertEqual(feature.get("stage"), 3, feature)
            self.assertEqual(feature.get("effort"), {"level": "medium", "want": "medium"}, feature)


if __name__ == "__main__":
    unittest.main()
