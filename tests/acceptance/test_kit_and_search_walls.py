#!/usr/bin/env python3
"""Held-out oracle tests for the kit and search walls (spec: Kit And Search Walls).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. The seams are the ones the spec declares: the hook scripts `deny-acceptance-to-subagents.sh`,
`deny-path.sh tests/acceptance` and `acceptance-wall.sh` fed PreToolUse JSON on stdin (exit code and stderr);
`devloop.py audit` and `seal-check` stdout JSON on scratch repos with a Claude transcript or a Codex rollout; the
`skills/release-audit/SKILL.md` text. The scratch repo holds `dev-loop.json`, `tests/acceptance/test_x.py`,
`src/app.py`, `tests/unit/test_app.py` and the kit CLI under `.agents/skills/dev-loop/scripts`. Every name in
the scratch repos is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"
RELEASE_AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
KIT_VERSION = json.loads((REPO_ROOT / "package.json").read_text())["version"]

HELD_OUT = "tests/acceptance"
SUITE_FILE = f"{HELD_OUT}/test_x.py"
SLUG = "gizmo"
BRANCH = f"feat/{SLUG}"

D8 = ("Blocked: this search reaches the held-out suite tests/acceptance. Search the source folders by name "
      "(grep -rn <name> src lib, Grep path src), or exclude the suite (grep --exclude-dir=acceptance, "
      "rg -g '!tests/acceptance/**', git grep <name> -- . ':!tests/acceptance', "
      "find . -path ./tests/acceptance -prune -o ..., Grep glob !tests/acceptance/**).")
KIT_MESSAGE = "Blocked: installed dev-loop kit file."
SUITE_HIT = "./tests/acceptance/test_x.py:3:r = make(1)"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def same_sha(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def record_shas(record: dict) -> list[str]:
    """The commit ids of a taint record: a sha string, or an object with the sha under `sha`, `commit` or `id`."""
    shas = []
    for item in record.get("commits") or []:
        if isinstance(item, str):
            shas.append(item)
        elif isinstance(item, dict):
            shas.append(next(str(item[k]) for k in ("sha", "commit", "id") if k in item))
    return shas


def claude_transcript(*calls: tuple[str, dict, str]) -> str:
    """A Claude subagent transcript: one `tool_use` per call, its `tool_result` paired by `tool_use_id`."""
    lines = [{"type": "user", "message": {"role": "user", "content": "Fix the make helper, task 2 of the plan."}}]
    for n, (tool, tool_input, output) in enumerate(calls):
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"toolu_{n}", "name": tool, "input": tool_input}]}})
        lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": output}]}})
    return "".join(json.dumps(line) + "\n" for line in lines)


def codex_rollout(*calls: tuple[str, str]) -> str:
    """A Codex rollout: one completed `CommandExecution` item per call."""
    lines = [{"timestamp": "2026-10-29T09:00:00Z", "type": "session_meta", "payload": {"id": "rollout-1"}}]
    for n, (command, output) in enumerate(calls):
        lines.append({"timestamp": f"2026-10-29T09:00:{n + 1:02d}Z", "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"id": f"item_{n}", "type": "CommandExecution", "command": command,
                                               "aggregated_output": output, "exit_code": 0}}})
    return "".join(json.dumps(line) + "\n" for line in lines)


class WallRepo(unittest.TestCase):
    """A dev-loop project with the kit installed, on branch feat/gizmo, its base on main."""

    BASE_FILES: dict[str, str] = {}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        put(self.notes / "Features" / "20261029 Gizmo" / "Spec.md",
            "---\nfeature: gizmo\ntrack: M\nreview_status: approved\n---\n\n# Gizmo\n")
        self.transcripts = self.root / "transcripts"
        self.transcripts.mkdir()
        self.repo = self.root / "acme-gizmos"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.repo, "config", key, value)
        files = {
            "dev-loop.json": json.dumps({"version": KIT_VERSION, "harnesses": ["claude", "codex"]}) + "\n",
            "README.md": "# Acme gizmos\n",
            "src/app.py": "def make(n):\n    return n + 1\n",
            "tests/unit/test_app.py": "from app import make\n\n\ndef test_make():\n    assert make(1) == 2\n",
            SUITE_FILE: "from app import make\n\nr = make(1)\n\n\ndef test_make():\n    assert r == 2\n",
            **self.BASE_FILES,
        }
        for path, text in files.items():
            put(self.repo / path, text)
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, "#!/bin/sh\necho all unit tests pass\nexit 0\n")
        gate.chmod(0o755)
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                    "CLAUDE_PROJECT_DIR": str(self.repo)}

    def cli(self, *args: str, stdin: str | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env, input=stdin,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    def hook(self, harness: str, name: str, payload: dict, *args: str,
             folder: Path | None = None) -> subprocess.CompletedProcess[str]:
        source = folder or (CLAUDE_HOOKS if harness == "claude" else CODEX_HOOKS)
        shell = "bash" if harness == "claude" else "sh"
        return subprocess.run([shell, str(source / name), *args], cwd=self.repo, input=json.dumps(payload),
                              env=self.env, capture_output=True, text=True, timeout=120)

    def pre_tool(self, tool: str, tool_input: dict, agent_type: str | None = None) -> dict:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        if agent_type is not None:
            payload.update({"agent_id": f"agent-{agent_type}", "agent_type": agent_type})
        return payload

    def claude_walls(self, tool: str, tool_input: dict,
                     agent_type: str | None = "implementer") -> list[subprocess.CompletedProcess[str]]:
        """The settings net, plus the implementer frontmatter `deny-path.sh tests/acceptance` for an implementer."""
        payload = self.pre_tool(tool, tool_input, agent_type)
        results = [self.hook("claude", "deny-acceptance-to-subagents.sh", payload)]
        if agent_type == "implementer":
            results.append(self.hook("claude", "deny-path.sh", payload, HELD_OUT))
        return results

    def codex_wall(self, command: str, agent_type: str | None = "implementer") -> subprocess.CompletedProcess[str]:
        return self.hook("codex", "acceptance-wall.sh", self.pre_tool("Bash", {"command": command}, agent_type))

    def codex_patch(self, path: str, agent_type: str | None) -> subprocess.CompletedProcess[str]:
        text = f"*** Begin Patch\n*** Update File: {path}\n@@\n-# Skill\n+# Skill, edited\n*** End Patch\n"
        return self.hook("codex", "acceptance-wall.sh",
                         self.pre_tool("apply_patch", {"command": text, "patch": text}, agent_type))

    def assert_search_denied(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual((result.returncode, result.stderr.strip()), (2, D8), result.stdout + result.stderr)

    def assert_allowed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual((result.returncode, result.stderr.strip()), (0, ""), result.stdout + result.stderr)

    def assert_kit_denied(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.strip().startswith(KIT_MESSAGE), result.stderr)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def git_dir(self) -> Path:
        return (self.repo / git(self.repo, "rev-parse", "--git-dir")).resolve()

    def start(self, agent_id: str, agent_type: str, harness: str = "claude") -> None:
        """SubagentStart: the snapshot of HEAD, by `gate-snapshot.sh` (Claude) or `seal.sh start` (Codex)."""
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStart",
                   "agent_id": agent_id, "agent_type": agent_type}
        if harness == "claude":
            self.hook("claude", "gate-snapshot.sh", payload)
        else:
            self.hook("codex", "seal.sh", payload, "start", folder=self.repo / ".dev-loop" / "hooks" / "codex")
        self.assertTrue((self.git_dir() / "dev-loop-subagents" / f"{agent_id}.head").is_file(), agent_id)

    def commit_fix(self, message: str = "Fix make") -> tuple[str, str]:
        """An implementer commit of src/app.py; returns its sha and the `git commit` output naming it."""
        put(self.repo / "src" / "app.py", f"def make(n):\n    return n + 1\n\n\nFIXED = {message!r}\n")
        git(self.repo, "add", "--", "src/app.py")
        git(self.repo, "commit", "-q", "-m", message)
        sha = self.head()
        short = git(self.repo, "rev-parse", "--short", sha)
        return sha, f"[{BRANCH} {short}] {message}\n 1 file changed, 1 insertion(+), 1 deletion(-)\n"

    def audit(self, agent_id: str, agent_type: str, text: str) -> tuple[int, dict]:
        transcript = self.transcripts / f"{agent_id}.jsonl"
        transcript.write_text(text)
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStop",
                   "agent_id": agent_id, "agent_type": agent_type, "agent_transcript_path": str(transcript)}
        return self.cli("audit", stdin=json.dumps(payload))

    def open_taint(self, check: dict) -> list[str]:
        return [r.get("agent_id") for r in check.get("taint") or []]


class ClaudeCallerSearchTests(WallRepo):
    def test_f1_claude_caller_search_scoped_after_the_block(self) -> None:
        """O1, O4: a repo-wide grep is denied with the D8 line; the scoped grep runs; the audit records no taint."""
        for result in self.claude_walls("Bash", {"command": "grep -rn make ."}):
            self.assert_search_denied(result)
        scoped = "grep -rn make src tests/unit"
        for result in self.claude_walls("Bash", {"command": scoped}):
            self.assert_allowed(result)
        found = subprocess.run(["grep", "-rn", "make", "src", "tests/unit"], cwd=self.repo, capture_output=True,
                               text=True, check=True).stdout
        self.assertIn("src/app.py:1:def make(n):", found)

        self.start("impl-1", "implementer")
        _, commit_output = self.commit_fix()
        code, out = self.audit("impl-1", "implementer", claude_transcript(
            ("Bash", {"command": "grep -rn make ."}, D8),
            ("Bash", {"command": scoped}, found),
            ("Bash", {"command": "git commit -am 'Fix make'"}, commit_output)))
        self.assertEqual((code, out.get("taint")), (0, None), out)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check["ok"]), (0, True), check)


class CodexCallerSearchTests(WallRepo):
    def test_f2_codex_caller_search_scoped_after_the_block(self) -> None:
        """O8: the same flow through `acceptance-wall.sh` with `rg -n make`, then `rg make src`."""
        self.assert_search_denied(self.codex_wall("rg -n make"))
        self.assert_allowed(self.codex_wall("rg make src"))

        self.start("codex-impl-1", "implementer", harness="codex")
        _, commit_output = self.commit_fix()
        code, out = self.audit("codex-impl-1", "implementer", codex_rollout(
            ("rg make src", "src/app.py:1:def make(n):\n"),
            ("git commit -am 'Fix make'", commit_output)))
        self.assertEqual((code, out.get("taint")), (0, None), out)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check["ok"]), (0, True), check)


class ExclusionRouteTests(WallRepo):
    def test_f3_exclusion_passes_every_claude_wall_an_ineffective_one_does_not(self) -> None:
        """O5, O6: `rg -g '!tests/acceptance/**' make` passes both Claude walls; `--exclude-dir=tests/acceptance`
        is denied with the D8 line."""
        for result in self.claude_walls("Bash", {"command": "rg -g '!tests/acceptance/**' make"}):
            self.assert_allowed(result)
        for result in self.claude_walls("Bash", {"command": "grep -rn --exclude-dir=tests/acceptance make ."}):
            self.assert_search_denied(result)


class BackstopTests(WallRepo):
    def test_f4_wrapped_search_is_caught_by_the_audit_and_closed_by_revert(self) -> None:
        """O22, O27: a search behind `xargs` passes the walls; the audit path rule taints the commit; a revert
        closes it."""
        wrapped = "echo . | xargs grep -rn make"
        for result in self.claude_walls("Bash", {"command": wrapped}):
            self.assert_allowed(result)

        self.start("impl-4", "implementer")
        sha, commit_output = self.commit_fix()
        code, out = self.audit("impl-4", "implementer", claude_transcript(
            ("Bash", {"command": wrapped}, f"./src/app.py:1:def make(n):\n{SUITE_HIT}\n"),
            ("Bash", {"command": "git commit -am 'Fix make'"}, commit_output)))
        self.assertEqual(code, 0, out)
        record = out.get("taint")
        self.assertIsNotNone(record, out)
        self.assertEqual(record["set"], "acceptance", record)
        self.assertEqual(record["paths"], [SUITE_FILE], record)
        self.assertEqual(record["lines"], [], record)
        self.assertTrue(any(same_sha(s, sha) for s in record_shas(record)), record)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check["ok"]), (1, False), check)
        self.assertIn("impl-4", self.open_taint(check))

        git(self.repo, "revert", "--no-edit", sha)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check["ok"]), (0, True), check)
        self.assertNotIn("impl-4", self.open_taint(check))


class KitSkillEditTests(WallRepo):
    BASE_FILES = {".dev-loop/MANIFEST.json": json.dumps({"version": KIT_VERSION, "files": {}}) + "\n"}

    def test_f5_kit_skill_edits_are_denied_and_a_shell_write_is_kit_changed(self) -> None:
        """O16, O18: an `Edit` and an `apply_patch` of `.agents/skills/release/SKILL.md`, from the main session and
        from an implementer, are denied with the kit message; a shell write in `garden` is `kit-changed`."""
        target = ".agents/skills/release/SKILL.md"
        edit = {"file_path": str(self.repo / target), "old_string": "# Skill", "new_string": "# Skill, edited"}
        for agent_type in (None, "implementer"):
            with self.subTest(agent_type=agent_type):
                self.assert_kit_denied(self.hook("claude", "deny-acceptance-to-subagents.sh",
                                                 self.pre_tool("Edit", edit, agent_type)))
                self.assert_kit_denied(self.codex_patch(target, agent_type))

        put(self.repo / ".agents" / "skills" / "garden" / "x.md", "# Notes the gardener never wrote\n")
        code, check = self.cli("seal-check", "--uncommitted")
        self.assertEqual((code, check["ok"]), (1, False), check)
        kit_changed = [v.get("path") for v in check.get("violations", []) if v.get("reason") == "kit-changed"]
        self.assertIn(".agents/skills/garden/x.md", kit_changed, check)


class UnchangedRolesTests(WallRepo):
    def test_f6_main_sessions_and_spec_tester_get_no_search_deny(self) -> None:
        """O10, O11 (O1 as the contrast): the walls deny an implementer's repo-wide grep, but not the same search
        from either main session or from `spec-tester`."""
        self.assert_search_denied(self.claude_walls("Bash", {"command": "grep -rn make ."})[0])

        main_grep = self.hook("claude", "deny-acceptance-to-subagents.sh", self.pre_tool("Grep", {"pattern": "make"}))
        self.assertEqual(main_grep.returncode, 0, main_grep.stdout + main_grep.stderr)
        main_rg = self.codex_wall("rg make", agent_type=None)
        self.assertEqual(main_rg.returncode, 0, main_rg.stdout + main_rg.stderr)

        for tool, tool_input in (("Bash", {"command": "grep -rn make ."}), ("Grep", {"pattern": "make"})):
            with self.subTest(harness="claude", tool=tool):
                result = self.hook("claude", "deny-acceptance-to-subagents.sh",
                                   self.pre_tool(tool, tool_input, "spec-tester"))
                self.assertNotIn(D8, result.stderr)
        codex_tester = self.codex_wall("grep -rn make .", agent_type="spec_tester")
        self.assertNotIn(D8, codex_tester.stderr)


class ListingsTests(WallRepo):
    def test_f7_listings_and_gate_output_do_not_taint(self) -> None:
        """O34 (O25 as the contrast): a suite path in `git diff --name-only` and gate output gives no taint; the
        same path at the start of a search output line does."""
        lint = f"{SUITE_FILE}:3:1: E501 line too long\nall unit tests pass\n"
        self.start("impl-7", "implementer")
        _, commit_output = self.commit_fix()
        code, out = self.audit("impl-7", "implementer", claude_transcript(
            ("Bash", {"command": "git diff --name-only main...HEAD"}, f"{SUITE_FILE}\n"),
            ("Bash", {"command": "scripts/gate.sh"}, lint),
            ("Bash", {"command": "git commit -am 'Fix make'"}, commit_output)))
        self.assertEqual((code, out.get("taint")), (0, None), out)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check["ok"]), (0, True), check)

        self.start("codex-list", "implementer", harness="codex")
        code, out = self.audit("codex-list", "implementer", codex_rollout(
            ("git diff --name-only main...HEAD", f"{SUITE_FILE}\n")))
        self.assertEqual((code, out.get("taint")), (0, None), out)

        self.start("codex-search", "implementer", harness="codex")
        code, out = self.audit("codex-search", "implementer", codex_rollout(
            ("rg -n make", f"{SUITE_FILE}:3:r = make(1)\n")))
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(out.get("taint"), out)
        self.assertEqual(out["taint"]["paths"], [SUITE_FILE], out)


class ReleaseAuditTests(WallRepo):
    def test_f8_release_audit_agents_search_named_folders(self) -> None:
        """O9, O35: an audit agent's repo-wide grep is denied with the D8 line on both harnesses; a grep of named
        folders runs; `release-audit` step 4 puts the suite out of scope and says how its agents search."""
        self.assert_search_denied(self.claude_walls("Bash", {"command": "grep -rn make ."}, "general-purpose")[0])
        self.assert_search_denied(self.codex_wall("grep -rn make .", agent_type="task-reviewer"))
        self.assert_allowed(self.claude_walls("Bash", {"command": "grep -rn make src"}, "general-purpose")[0])
        self.assert_allowed(self.codex_wall("grep -rn make src", agent_type="task-reviewer"))

        step = re.search(r"^4\.\s.*?(?=^5\.\s)", RELEASE_AUDIT_SKILL.read_text(), re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(step, "no step 4 in skills/release-audit/SKILL.md")
        text = step.group(0)
        self.assertIn(HELD_OUT, text)
        self.assertRegex(text, re.compile(r"out of[^.]{0,40}scope|scope[^.]{0,80}tests/acceptance", re.IGNORECASE))
        self.assertRegex(text, re.compile(r"named[^.]{0,40}folders|folders[^.]{0,20}by name", re.IGNORECASE))
        self.assertRegex(text, re.compile(r"exclud", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
