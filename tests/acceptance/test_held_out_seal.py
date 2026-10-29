#!/usr/bin/env python3
"""Held-out oracle tests for the held-out seal (spec: Held-Out Seal).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle`
lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py seal`, `seal --rebase-continue`, `seal-check`, `audit` and `gate --summary`; the hook
scripts fed hook JSON on stdin; `check_setup.py` output; the agent and settings fragment files.
Every name in the temp repos is invented.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
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
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"
CLAUDE_AGENTS = REPO_ROOT / "claude" / "agents"
CODEX_AGENTS = REPO_ROOT / "codex" / "agents"
CLAUDE_FRAGMENT = REPO_ROOT / "claude" / "settings.fragment.json"

HELD_OUT = "tests/acceptance"
SLUG = "widget"
BRANCH = f"feat/{SLUG}"
SEAL_CMD = f"python3 .agents/skills/dev-loop/scripts/devloop.py seal --feature {SLUG} --message 'Add widget tests'"

# Distinctive lines (30 characters or more, in one file only).
ACC1 = "assert frobnicate_widget(17) == 'blue-zebra-0042'"
ACC2 = "assert widget_registry.count_open_doors(depth=3) == 913"
ACC3 = "assert gadget_tray.polish(level='mirror') == 'gleaming-otter'"
ACC4 = "assert gadget_tray.weigh_in_grams(include_lid=True) == 4471"
ACC5 = "assert sprocket_ledger.balance_for('quartz-heron') == 77019"
ACC6 = "assert sprocket_ledger.rounding_mode(strict=True) == 'banker-kite'"
SRC1 = "return {'colour': 'blue-zebra', 'serial': f'{n:04d}', 'doors': 913}"
SRC2 = "WIDGET_REGISTRY_FALLBACK_PATH = '/var/lib/acme/widget-registry.db'"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_try(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


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


def allowed(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == 0 and not denied(result)


def same_sha(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def record_shas(record: dict) -> list[str]:
    """The commit ids of a taint record. The spec does not fix the shape of a `commits` item:
    a sha string, or an object with the sha under `sha`, `commit` or `id`."""
    shas = []
    for item in record.get("commits") or []:
        if isinstance(item, str):
            shas.append(item)
        elif isinstance(item, dict):
            shas.append(next(str(item[k]) for k in ("sha", "commit", "id") if k in item))
    return shas


def claude_transcript(*outputs: str) -> str:
    lines = [{"type": "user", "message": {"role": "user", "content": "Build task 1 of the plan."}}]
    for n, text in enumerate(outputs):
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"toolu_{n}", "name": "Bash", "input": {"command": "run"}}]}})
        lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": text}]}})
    return "".join(json.dumps(line) + "\n" for line in lines)


def search_transcript(command: str, output: str) -> str:
    """A Claude transcript with one `Bash` search call and its `tool_result` (O17 as amended by Kit And Search Walls)."""
    lines = [{"type": "user", "message": {"role": "user", "content": "Build task 1 of the plan."}},
             {"type": "assistant", "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "toolu_search", "name": "Bash", "input": {"command": command}}]}},
             {"type": "user", "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "toolu_search", "content": output}]}}]
    return "".join(json.dumps(line) + "\n" for line in lines)


def codex_rollout(*outputs: str) -> str:
    lines = [{"timestamp": "2026-10-24T09:00:00Z", "type": "session_meta", "payload": {"id": "rollout-1"}}]
    for n, text in enumerate(outputs):
        lines.append({"timestamp": f"2026-10-24T09:00:{n + 1:02d}Z", "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"id": f"item_{n}", "type": "CommandExecution", "command": "run",
                                               "aggregated_output": text, "exit_code": 0}}})
    return "".join(json.dumps(line) + "\n" for line in lines)


def legacy_rollout(*outputs: str) -> str:
    lines = [{"timestamp": "2026-10-24T09:00:00Z", "type": "session_meta", "payload": {"id": "rollout-2"}}]
    for n, text in enumerate(outputs):
        lines.append({"timestamp": f"2026-10-24T09:00:{n + 1:02d}Z", "type": "response_item",
                      "payload": {"type": "function_call_output", "call_id": f"call_{n}", "output": text}})
    return "".join(json.dumps(line) + "\n" for line in lines)


def patch(header: str, path: str, body: str) -> str:
    return f"*** Begin Patch\n*** {header}: {path}\n{body}*** End Patch\n"


class SealRepo(unittest.TestCase):
    """A dev-loop project on branch feat/widget, its base on main, a notes folder with the widget spec."""

    BASE_FILES: dict[str, str] = {}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        put(self.notes / "Features" / "20261024 Widget" / "Spec.md",
            "---\nfeature: widget\ntrack: M\nreview_status: approved\n---\n\n# Widget\n")
        self.transcripts = self.root / "transcripts"
        self.transcripts.mkdir()
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.repo, "config", key, value)
        put(self.repo / "dev-loop.json", json.dumps({"version": "2.1.1", "harnesses": ["claude", "codex"]}) + "\n")
        put(self.repo / "GLOSSARY.md", "# Glossary\n\n**Widget**:\nA thing with doors.\n")
        put(self.repo / "README.md", "# Acme widgets\n")
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, "#!/bin/sh\necho all unit tests pass\nexit 0\n")
        gate.chmod(0o755)
        for path, text in self.BASE_FILES.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                    "CLAUDE_PROJECT_DIR": str(self.repo)}

    # CLI

    def cli(self, *args: str, stdin: str | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env, input=stdin,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    def seal(self, files: dict[str, str], slug: str = SLUG, message: str = "Add widget tests") -> dict:
        for path, text in files.items():
            put(self.repo / path, text)
        git(self.repo, "add", "--", HELD_OUT)
        code, out = self.cli("seal", "--feature", slug, "--message", message)
        self.assertEqual(code, 0, out)
        return out

    def seal_check(self, *args: str) -> tuple[int, dict]:
        return self.cli("seal-check", *args)

    def commit(self, files: dict[str, str], message: str) -> str:
        for path, text in files.items():
            put(self.repo / path, text)
        git(self.repo, "add", "-A", "--", *files)
        git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def short(self, sha: str) -> str:
        return git(self.repo, "rev-parse", "--short", sha)

    def git_dir(self) -> Path:
        return (self.repo / git(self.repo, "rev-parse", "--git-dir")).resolve()

    def expected_seal(self) -> str:
        """sha256 of one `<path> <blob>` line per staged path under tests/acceptance, sorted by path."""
        staged = git(self.repo, "diff", "--cached", "--name-only", "--no-renames", "--", HELD_OUT).splitlines()
        lines = []
        for path in sorted(staged):
            blob = git_try(self.repo, "rev-parse", "--verify", "-q", f":{path}").stdout.strip() or "deleted"
            lines.append(f"{path} {blob}\n")
        return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()

    def reasons(self, payload: dict, reason: str) -> list[dict]:
        return [v for v in payload.get("violations", []) if v.get("reason") == reason]

    # Hooks

    def hook(self, harness: str, name: str, payload: dict | None, *args: str, raw: str | None = None,
             folder: Path | None = None) -> subprocess.CompletedProcess[str]:
        source = folder or (CLAUDE_HOOKS if harness == "claude" else CODEX_HOOKS)
        shell = "bash" if harness == "claude" else "sh"
        text = raw if raw is not None else json.dumps(payload)
        return subprocess.run([shell, str(source / name), *args], cwd=self.repo, input=text, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def pre_tool(self, tool: str, tool_input: dict, agent_type: str | None = None) -> dict:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        if agent_type is not None:
            payload.update({"agent_id": f"agent-{agent_type}", "agent_type": agent_type})
        return payload

    def apply_patch(self, text: str, agent_type: str | None = None) -> dict:
        # The patch text under `command` (Codex apply_patch hook input), also under `patch`.
        return self.pre_tool("apply_patch", {"command": text, "patch": text}, agent_type)

    def install_kit(self) -> None:
        """The kit layout a project holds: the skill under .agents, the hooks under .dev-loop/hooks."""
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)

    def kit_hooks(self, harness: str) -> Path:
        return self.repo / ".dev-loop" / "hooks" / harness

    # Audit

    def transcript(self, name: str, text: str) -> Path:
        path = self.transcripts / name
        path.write_text(text)
        return path

    def audit(self, agent_id: str, agent_type: str, transcript: Path) -> tuple[int, dict]:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStop",
                   "agent_id": agent_id, "agent_type": agent_type, "agent_transcript_path": str(transcript)}
        return self.cli("audit", stdin=json.dumps(payload))


class ControllerWallTests(SealRepo):
    def test_f1_controller_wall_both_harnesses(self) -> None:
        """O1, O3, O6."""
        target = str(self.repo / HELD_OUT / "test_x.py")
        # O1: Claude main session (no agent_id, no agent_type).
        for tool, tool_input in (("Edit", {"file_path": target, "old_string": "a", "new_string": "b"}),
                                 ("Write", {"file_path": target, "content": "x = 1\n"}),
                                 ("NotebookEdit", {"notebook_path": str(self.repo / HELD_OUT / "x.ipynb"),
                                                   "new_source": "x = 1"})):
            with self.subTest(tool=tool):
                result = self.hook("claude", "deny-acceptance-to-subagents.sh", self.pre_tool(tool, tool_input))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        read = self.hook("claude", "deny-acceptance-to-subagents.sh", self.pre_tool("Read", {"file_path": target}))
        self.assertTrue(allowed(read), read.stdout + read.stderr)

        # O3: Codex main session, apply_patch file headers only.
        update = self.hook("codex", "acceptance-wall.sh",
                           self.apply_patch(patch("Update File", f"{HELD_OUT}/test_x.py", "@@\n-x = 1\n+x = 2\n")))
        self.assertTrue(denied(update), update.stdout + update.stderr)
        body = patch("Update File", "src/y.py", "@@\n-NOTE = ''\n+NOTE = 'see tests/acceptance for the oracle'\n")
        body_only = self.hook("codex", "acceptance-wall.sh", self.apply_patch(body))
        self.assertTrue(allowed(body_only), body_only.stdout + body_only.stderr)

        # O6: every Claude guard hook entry has onFailure "block".
        fragment = json.loads(CLAUDE_FRAGMENT.read_text())
        guards = {"deny-acceptance-to-subagents.sh": 0, "git-guard.sh": 0}
        for group in fragment["hooks"]["PreToolUse"]:
            for entry in group["hooks"]:
                for name in guards:
                    if name in entry.get("command", ""):
                        guards[name] += 1
                        self.assertEqual(entry.get("onFailure"), "block", entry)
        self.assertTrue(all(guards.values()), guards)
        for agent in (CLAUDE_AGENTS / "implementer.md", CLAUDE_AGENTS / "spec-tester.md"):
            with self.subTest(agent=agent.name):
                frontmatter = agent.read_text().split("---", 2)[1]
                entries = [item for item in re.split(r"\n\s*-\s", frontmatter) if "deny-path.sh" in item]
                self.assertTrue(entries, f"no deny-path.sh hook in {agent.name}")
                for item in entries:
                    self.assertRegex(item, r"onFailure:\s*[\"']?block[\"']?", item)


class ReadWallTests(SealRepo):
    def test_f2_read_walls_both_harnesses(self) -> None:
        """O2, O4, O5."""
        target = str(self.repo / HELD_OUT / "test_x.py")
        # O2: Claude spec-tester may write the suite, any other subagent may not name it.
        write = self.hook("claude", "deny-acceptance-to-subagents.sh",
                          self.pre_tool("Write", {"file_path": target, "content": "x = 1\n"}, "spec-tester"))
        self.assertTrue(allowed(write), write.stdout + write.stderr)
        for tool, tool_input in (("Read", {"file_path": target}),
                                 ("Grep", {"pattern": "assert", "path": HELD_OUT}),
                                 ("Glob", {"pattern": f"{HELD_OUT}/**/*.py"}),
                                 ("Bash", {"command": f"cat {HELD_OUT}/test_x.py"})):
            with self.subTest(tool=tool):
                result = self.hook("claude", "deny-acceptance-to-subagents.sh",
                                   self.pre_tool(tool, tool_input, "implementer"))
                self.assertTrue(denied(result), result.stdout + result.stderr)
        self.install_kit()
        put(self.repo / HELD_OUT / "test_x.py", "def test_x():\n    assert True\n")
        for command, deny in ((f"rg -g '!{HELD_OUT}/**' assert", False), ("grep -rn assert .", True)):
            with self.subTest(oracle="O2 amended by Kit And Search Walls", command=command):
                result = self.hook("claude", "deny-acceptance-to-subagents.sh",
                                   self.pre_tool("Bash", {"command": command}, "implementer"))
                self.assertEqual(denied(result), deny, result.stdout + result.stderr)
                self.assertEqual(allowed(result), not deny, result.stdout + result.stderr)

        # O4: Codex subagents by agent_type.
        cat_suite = self.hook("codex", "acceptance-wall.sh",
                              self.pre_tool("Bash", {"command": f"cat {HELD_OUT}/test_x.py"}, "implementer"))
        self.assertTrue(denied(cat_suite), cat_suite.stdout + cat_suite.stderr)
        cat_source = self.hook("codex", "acceptance-wall.sh",
                               self.pre_tool("Bash", {"command": "cat src/y.py"}, "spec_tester"))
        self.assertTrue(denied(cat_source), cat_source.stdout + cat_source.stderr)
        add_test = self.hook("codex", "acceptance-wall.sh",
                             self.apply_patch(patch("Add File", f"{HELD_OUT}/test_x.py", "+x = 1\n"), "spec_tester"))
        self.assertTrue(allowed(add_test), add_test.stdout + add_test.stderr)

        # O5: each guard script fails closed on input that is not JSON.
        for harness, name, args in (("claude", "deny-acceptance-to-subagents.sh", ()),
                                    ("claude", "git-guard.sh", ()),
                                    ("claude", "deny-path.sh", (HELD_OUT,)),
                                    ("codex", "acceptance-wall.sh", ()),
                                    ("codex", "git-guard.sh", ())):
            with self.subTest(harness=harness, script=name):
                result = self.hook(harness, name, None, *args, raw="this is {not json")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(result.stderr.strip(), "no message on stderr")


class SealRoundTripTests(SealRepo):
    def test_f3_seal_check_unsealed_change_and_revert(self) -> None:
        """O7, O8, O9, O15."""
        put(self.repo / HELD_OUT / "test_widget.py", "def test_doors():\n    assert 1 + 1 == 2\n")
        put(self.repo / HELD_OUT / "fixtures" / "widget.json", '{"doors": 3}\n')
        put(self.repo / "src" / "widget.py", "DOORS = 3\n")
        git(self.repo, "add", "-A")
        before = self.head()
        # O7: a staged path outside tests/acceptance: exit 1, no commit.
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add widget tests")
        self.assertEqual(code, 1, out)
        self.assertIn("error", out)
        self.assertEqual(self.head(), before)

        git(self.repo, "reset", "-q", "--", "src/widget.py")
        expected = self.expected_seal()
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Add widget tests")
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{before}..HEAD"), "1")
        self.assertTrue(same_sha(out["commit"], self.head()), out)
        self.assertEqual(out["seal"], expected)
        message = git(self.repo, "log", "-1", "--format=%B").splitlines()
        self.assertIn(f"Acceptance: {SLUG}", message)
        self.assertIn(f"Dev-Loop-Seal: {expected}", message)

        # O8
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)

        # O9: a commit without the trailer that changes the suite.
        sneak = self.commit({f"{HELD_OUT}/test_extra.py": "def test_more():\n    assert True\n"}, "More tests")
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (1, False), check)
        self.assertIn(f"{HELD_OUT}/test_extra.py", [v["path"] for v in self.reasons(check, "unsealed")], check)

        # O15: a passing project gate with an unsealed commit.
        code, gate = self.cli("gate", "--summary")
        self.assertEqual(code, 1, gate)
        self.assertIs(gate["seal"]["ok"], False, gate)

        # O9: the revert of that commit cancels it.
        git(self.repo, "revert", "--no-edit", sneak)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)


class SealTamperTests(SealRepo):
    def test_f4_amend_later_change_and_uncommitted(self) -> None:
        """O10, O12."""
        path = f"{HELD_OUT}/test_widget.py"
        self.seal({path: "def test_doors():\n    assert count_doors() == 3\n"})
        sealed = self.head()

        # O10: a change to a sealed file in a later commit.
        self.commit({path: "def test_doors():\n    assert count_doors() >= 0\n"}, "Relax the door test")
        code, check = self.seal_check()
        self.assertEqual(code, 1, check)
        self.assertIn(path, [v["path"] for v in self.reasons(check, "changed-after-seal")], check)

        # O10: the same change amended into the seal commit.
        git(self.repo, "reset", "-q", "--hard", sealed)
        put(self.repo / path, "def test_doors():\n    assert count_doors() >= 0\n")
        git(self.repo, "add", "--", path)
        git(self.repo, "commit", "-q", "--amend", "--no-edit")
        amended = self.head()
        code, check = self.seal_check()
        self.assertEqual(code, 1, check)
        self.assertTrue(any(same_sha(v.get("commit") or "", amended) for v in self.reasons(check, "bad-seal")), check)

        # O12: an untracked file under the suite counts only with --uncommitted.
        git(self.repo, "reset", "-q", "--hard", sealed)
        put(self.repo / HELD_OUT / "test_new.py", "def test_new():\n    assert True\n")
        code, check = self.seal_check("--uncommitted")
        self.assertEqual(code, 1, check)
        self.assertIn(f"{HELD_OUT}/test_new.py", [v["path"] for v in self.reasons(check, "uncommitted")], check)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"], check["violations"]), (0, True, []), check)


class SealRebaseTests(SealRepo):
    def test_f5_rebase_onto_main_with_another_features_sealed_file(self) -> None:
        """O11."""
        self.seal({f"{HELD_OUT}/test_widget.py": "def test_doors():\n    assert count_doors() == 3\n"})
        git(self.repo, "checkout", "-q", "main")
        self.seal({f"{HELD_OUT}/test_gadget.py": "def test_lid():\n    assert lid() == 'shut'\n"},
                  slug="gadget", message="Add gadget tests")
        git(self.repo, "checkout", "-q", BRANCH)
        git(self.repo, "rebase", "-q", "main")
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)


class WhoMaySealTests(SealRepo):
    def test_f6_only_spec_tester_seals(self) -> None:
        """O13, both harnesses."""
        trailer = "git commit -m 'Add tests' -m 'Dev-Loop-Seal: 0123456789abcdef'"
        plain = "git commit -m 'Add widget tests'"
        for harness, tester in (("claude", "spec-tester"), ("codex", "spec_tester")):
            with self.subTest(harness=harness):
                main_seal = self.hook(harness, "git-guard.sh", self.pre_tool("Bash", {"command": SEAL_CMD}))
                self.assertTrue(denied(main_seal), main_seal.stdout + main_seal.stderr)
                implementer = self.hook(harness, "git-guard.sh",
                                        self.pre_tool("Bash", {"command": SEAL_CMD}, "implementer"))
                self.assertTrue(denied(implementer), implementer.stdout + implementer.stderr)
                typed = self.hook(harness, "git-guard.sh", self.pre_tool("Bash", {"command": trailer}))
                self.assertTrue(denied(typed), typed.stdout + typed.stderr)
                commit = self.hook(harness, "git-guard.sh", self.pre_tool("Bash", {"command": plain}, tester))
                self.assertTrue(denied(commit), commit.stdout + commit.stderr)
                self.assertIn("devloop.py seal", commit.stdout + commit.stderr)
                seal = self.hook(harness, "git-guard.sh", self.pre_tool("Bash", {"command": SEAL_CMD}, tester))
                self.assertTrue(allowed(seal), seal.stdout + seal.stderr)


class StopHookTests(SealRepo):
    def test_f7_stop_hook_blocks_an_unsealed_commit(self) -> None:
        """O14, both harnesses."""
        self.install_kit()
        sneak = self.commit({f"{HELD_OUT}/test_widget.py": "def test_doors():\n    assert True\n"}, "Tests")
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                stop = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "Stop",
                        "stop_hook_active": False}
                result = self.hook(harness, "seal.sh", stop, "turn", folder=self.kit_hooks(harness))
                self.assertEqual(result.returncode, 0, result.stderr)
                out = json.loads(result.stdout)
                self.assertEqual(out.get("decision"), "block", out)
                self.assertIn(sneak[:7], out.get("reason", ""))
                again = self.hook(harness, "seal.sh", {**stop, "stop_hook_active": True}, "turn",
                                  folder=self.kit_hooks(harness))
                self.assertFalse(denied(again), again.stdout)


class ImplementerAuditTests(SealRepo):
    BASE_FILES = {
        f"{HELD_OUT}/test_widget.py": f"def test_widget():\n    {ACC1}\n    {ACC2}\n",
        f"{HELD_OUT}/test_gadget.py": f"def test_gadget():\n    {ACC3}\n    {ACC4}\n",
        "docs/gadget-notes.md": f"# Gadget notes\n\n    {ACC3}\n    {ACC4}\n",
        "src/widget.py": "def frobnicate_widget(n):\n    return n\n",
    }

    def start(self, agent_id: str, agent_type: str) -> None:
        """Claude SubagentStart: gate-snapshot.sh writes <git-dir>/dev-loop-subagents/<agent_id>.head."""
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStart",
                   "agent_id": agent_id, "agent_type": agent_type}
        self.hook("claude", "gate-snapshot.sh", payload)
        self.assertTrue((self.git_dir() / "dev-loop-subagents" / f"{agent_id}.head").is_file(), agent_id)

    def open_taint(self, check: dict) -> list[str]:
        return [r.get("agent_id") for r in check.get("taint") or []]

    def test_f8_implementer_read_detected_then_remedied(self) -> None:
        """O16, O17, O18, O28, O32."""
        self.start("impl-1", "implementer")
        other = self.commit({"src/other.py": "OTHER = 1\n"}, "Another agent's work")
        tainted = self.commit({"src/widget.py": "def frobnicate_widget(n):\n    return f'blue-zebra-{n:04d}'\n"},
                              "Build widget")
        transcript = self.transcript("impl-1.jsonl", claude_transcript(
            f"def test_widget():\n    {ACC1}\n    {ACC2}\n",
            f"[{BRANCH} {self.short(tainted)}] Build widget\n 1 file changed, 1 insertion(+), 1 deletion(-)\n"))

        # O16, O32: a taint record with commit A only.
        code, out = self.audit("impl-1", "implementer", transcript)
        self.assertEqual(code, 0, out)
        record = out["taint"]
        self.assertIsNotNone(record, out)
        self.assertEqual(record["agent_id"], "impl-1")
        self.assertEqual(record["set"], "acceptance")
        self.assertEqual(record["attribution"], "transcript")
        shas = record_shas(record)
        self.assertTrue(any(same_sha(s, tainted) for s in shas), record)
        self.assertFalse(any(same_sha(s, other) for s in shas), record)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (1, False), check)
        self.assertIn("impl-1", self.open_taint(check))

        # O17: one matched line, or lines also found outside the suite: no taint.
        self.start("impl-2", "implementer")
        code, out = self.audit("impl-2", "implementer", self.transcript("impl-2.jsonl", claude_transcript(
            f"def test_widget():\n    {ACC1}\n")))
        self.assertEqual((code, out["taint"]), (0, None), out)
        self.start("impl-3", "implementer")
        code, out = self.audit("impl-3", "implementer", self.transcript("impl-3.jsonl", claude_transcript(
            f"    {ACC3}\n    {ACC4}\n")))
        self.assertEqual((code, out["taint"]), (0, None), out)
        self.start("impl-4", "implementer")
        code, out = self.audit("impl-4", "implementer", self.transcript("impl-4.jsonl", search_transcript(
            "grep -rn frobnicate .", f"./{HELD_OUT}/test_widget.py:1:def test_widget():\n")))
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(out["taint"], out)

        # O28: a rebase that changes the sha keeps the record open.
        git(self.repo, "checkout", "-q", "main")
        self.commit({"CHANGELOG.md": "# Changelog\n"}, "Unrelated main change")
        git(self.repo, "checkout", "-q", BRANCH)
        git(self.repo, "rebase", "-q", "main")
        self.assertNotEqual(self.head(), tainted)
        code, check = self.seal_check()
        self.assertEqual(check["ok"], False, check)
        self.assertIn("impl-1", self.open_taint(check))
        # O28: the same diff committed again after git reset --soft keeps it open.
        git(self.repo, "reset", "-q", "--soft", "HEAD~1")
        git(self.repo, "commit", "-q", "-m", "Build widget, take two")
        code, check = self.seal_check()
        self.assertEqual(check["ok"], False, check)
        self.assertIn("impl-1", self.open_taint(check))

        # O18: git revert closes it.
        git(self.repo, "revert", "--no-edit", "HEAD")
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)
        self.assertNotIn("impl-1", self.open_taint(check))


class SpecTesterAuditTests(SealRepo):
    BASE_FILES = {
        "src/widget.py": f"def widget(n):\n    {SRC1}\n\n{SRC2}\n",
        f"{HELD_OUT}/test_sprocket.py": f"def test_sprocket():\n    {ACC5}\n    {ACC6}\n",
    }

    def start(self, agent_id: str, agent_type: str) -> None:
        """Codex SubagentStart: seal.sh start writes <git-dir>/dev-loop-subagents/<agent_id>.head."""
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStart",
                   "agent_id": agent_id, "agent_type": agent_type}
        self.hook("codex", "seal.sh", payload, "start", folder=self.kit_hooks("codex"))
        self.assertTrue((self.git_dir() / "dev-loop-subagents" / f"{agent_id}.head").is_file(), agent_id)

    def test_f9_codex_spec_tester_read_detected_remedied_and_skip(self) -> None:
        """O19, O20, O21, O30."""
        self.install_kit()

        # O21: a tainted subagent with no commit: a closed record, in warnings, ok stays true.
        self.start("codex-impl", "implementer")
        code, out = self.audit("codex-impl", "implementer", self.transcript("codex-impl.jsonl", codex_rollout(
            f"def test_sprocket():\n    {ACC5}\n    {ACC6}\n")))
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(out["taint"], out)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)
        self.assertIn("codex-impl", json.dumps(check["warnings"]))

        # O20: a rollout without item_completed lines: no taint, an entry in skipped.
        self.start("codex-legacy", "spec_tester")
        code, out = self.audit("codex-legacy", "spec_tester", self.transcript("codex-legacy.jsonl", legacy_rollout(
            f"def widget(n):\n    {SRC1}\n\n{SRC2}\n")))
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["taint"], out)
        self.assertTrue(out["skipped"], out)

        # O19: a spec_tester rollout that shows 2 distinctive lines of src: set source.
        self.start("codex-tester", "spec_tester")
        sealed = self.seal({f"{HELD_OUT}/test_widget.py": "def test_widget():\n    assert widget(3)['doors'] == 913\n"})
        sha = self.head()
        self.assertTrue(same_sha(sealed["commit"], sha), sealed)
        code, out = self.audit("codex-tester", "spec_tester", self.transcript("codex-tester.jsonl", codex_rollout(
            f"def widget(n):\n    {SRC1}\n\n{SRC2}\n",
            f"[{BRANCH} {self.short(sha)}] Add widget tests\n 1 file changed, 2 insertions(+)\n")))
        self.assertEqual(code, 0, out)
        record = out["taint"]
        self.assertIsNotNone(record, out)
        self.assertEqual(record["set"], "source")
        self.assertTrue(any(same_sha(s, sha) for s in record_shas(record)), record)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (1, False), check)

        # O30: git revert --no-commit, then seal --reverts: the record is closed.
        git(self.repo, "revert", "--no-commit", sha)
        code, out = self.cli("seal", "--feature", SLUG, "--message", "Revert tainted widget tests", "--reverts", sha)
        self.assertEqual(code, 0, out)
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)
        self.assertNotIn("codex-tester", [r.get("agent_id") for r in check.get("taint") or []])


class WeakeningReportTests(SealRepo):
    BASE_FILES = {
        "tests/unit/test_a.py": ("import pytest\n\n\ndef test_one():\n    assert 1 == 1\n    assert 2 == 2\n\n\n"
                                 "def test_two():\n    assert 3 == 3\n"),
    }

    def test_f10_weakening_report(self) -> None:
        """O22, O23."""
        path = "tests/unit/test_a.py"
        text = (self.repo / path).read_text()
        self.commit({path: text.replace("def test_two", "@pytest.mark.skip(reason='flaky')\ndef test_two")},
                    "Skip a flaky test")
        code, gate = self.cli("gate", "--summary")
        self.assertEqual(code, 0, gate)
        self.assertIn({"path": path, "reason": "skip-added"}, gate["weakened"])

        text = (self.repo / path).read_text()
        self.commit({path: text.replace("    assert 2 == 2\n", "")}, "Drop an assertion")
        code, gate = self.cli("gate", "--summary")
        self.assertEqual(code, 0, gate)
        self.assertIn({"path": path, "reason": "assertions-removed"}, gate["weakened"])


class SetupVersionTests(SealRepo):
    def test_f12_setup_checks_the_versions(self) -> None:
        """O26."""
        notes = self.root / "install-notes"
        notes.mkdir()
        project = self.root / "fresh-project"
        project.mkdir()
        git(project, "init", "-q")
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                              cwd=project, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        fake = self.root / "fake-bin"
        for name, version in (("claude", "2.1.294 (Claude Code)"), ("codex", "codex-cli 0.161.0")):
            put(fake / name, f"#!/bin/sh\necho '{version}'\n")
            (fake / name).chmod(0o755)
        env = {**self.env, "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
        for harness, minimum in (("claude", "2.1.295"), ("codex", "0.162.1")):
            with self.subTest(harness=harness):
                result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                         "--repo", str(project)], env=env, capture_output=True, text=True,
                                        timeout=120)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertTrue([line for line in (result.stdout + result.stderr).splitlines() if minimum in line],
                                result.stdout + result.stderr)


class CompositionRuleTests(unittest.TestCase):
    def test_f13_both_spec_tester_agents_state_the_composition_rule(self) -> None:
        """O27."""
        for agent in (CLAUDE_AGENTS / "spec-tester.md", CODEX_AGENTS / "spec-tester.toml"):
            with self.subTest(agent=agent.name):
                text = agent.read_text()
                self.assertIn("Supersedes", text)
                self.assertIn("Acceptance flows", text)
                self.assertRegex(text, re.compile(r"combin[^.]{0,300}earlier feature|earlier feature[^.]{0,300}combin",
                                                  re.IGNORECASE | re.DOTALL))


class RebaseConflictTests(SealRepo):
    BASE_FILES = {f"{HELD_OUT}/test_shared.py": "def test_shared():\n    assert VALUE == 1\n"}

    def test_f14_rebase_conflict_resealed(self) -> None:
        """O31."""
        path = f"{HELD_OUT}/test_shared.py"
        self.seal({path: "def test_shared():\n    assert VALUE == 2\n"})
        git(self.repo, "checkout", "-q", "main")
        self.seal({path: "def test_shared():\n    assert VALUE == 3\n"}, slug="gadget", message="Gadget tests")
        git(self.repo, "checkout", "-q", BRANCH)
        rebase = git_try(self.repo, "rebase", "main")
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        put(self.repo / path, "def test_shared():\n    assert VALUE == 3\n    assert WIDGET_VALUE == 2\n")
        git(self.repo, "add", "--", path)
        code, out = self.cli("seal", "--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebase"], "continued", out)
        git_dir = self.git_dir()
        self.assertFalse((git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists())
        self.assertIn(f"Dev-Loop-Seal: {out['seal']}", git(self.repo, "log", "-1", "--format=%B").splitlines())
        code, check = self.seal_check()
        self.assertEqual((code, check["ok"]), (0, True), check)


if __name__ == "__main__":
    unittest.main()
