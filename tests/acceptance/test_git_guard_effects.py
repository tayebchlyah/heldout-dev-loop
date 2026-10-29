#!/usr/bin/env python3
"""Held-out acceptance tests for the git guard effects (spec: 20261029 Git Guard Effects, issue #89).

One test per `## Acceptance flows` item (F<n>). Each docstring names the `## Acceptance oracle` lines
(O<n>) it proves; each oracle line is a subtest. The only seams are the two kit hooks run from the source
tree as subprocesses (`claude/hooks/git-guard.sh` with bash, `codex/hooks/git-guard.sh` with sh), fed hook
JSON on stdin in a scratch repo. Every name in the scratch repo is invented.

The scratch repo (the oracle preamble): `main` with commits A, B, C; an annotated tag `v1.0.0` and a
lightweight tag `release-1` at A; `refs/remotes/origin/main` at C; branch `feat/x` at C, checked out;
remote `origin`; a stub `release-check` CLI in the main worktree.

"Refused": Claude main session `ask` with a reason that holds the quoted text; Claude with `agent_id`:
exit 2, stderr holds `a subagent cannot ask for it`; Codex with and without `agent_id`: `deny`.
"Allowed": exit 0 and empty stdout from either guard, with and without `agent_id`.

Fixture choices the spec leaves open:
- The stub CLI is `.agents/skills/dev-loop/scripts/devloop.py` in the main worktree, the path the guards
  run `release-check` from (Release Security Audit O51). It appends its argv as one JSON line to
  `calls.jsonl` outside the repo, prints a `no-audit` verdict and exits 1.
- `origin` is a bare repo in the temp folder; `refs/remotes/origin/main` is set with `update-ref`.
- `HOME` and `XDG_CONFIG_HOME` point to a temp folder for every hook run, `GIT_CONFIG_NOSYSTEM=1`.
- A sha in a reason is matched by its first 7 hex digits (a full sha holds them).
- O19 allows the exact lease push "as CI Follow-up oracle 39", a main session rule; CI Follow-up oracle 43
  (still held, Supersedes) denies it to a subagent. So that one command is checked without `agent_id`.
- F1 lists only lines whose results hold today; its literal-sha merge is checked beside the `$SHA`
  spelling of O11, the one Stage 7 spelling that changes.
- Each flow checks the parts of its oracle lines in the sessions the flow names: F2 the subagent of each
  harness, F3 and F4 the main session of each harness, F1 and F5 all four.
- Oracle lines in no flow (7, 10, 15, 20, 31 to 35) are left to the spec's unit seams (D19).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
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
GUARDS = {"claude": ("bash", REPO_ROOT / "claude" / "hooks" / "git-guard.sh"),
          "codex": ("sh", REPO_ROOT / "codex" / "hooks" / "git-guard.sh")}
SUBAGENT_TEXT = "a subagent cannot ask for it"

CLAUDE_MAIN, CLAUDE_AGENT, CODEX_MAIN, CODEX_AGENT = (("claude", False), ("claude", True),
                                                      ("codex", False), ("codex", True))
ALL = (CLAUDE_MAIN, CLAUDE_AGENT, CODEX_MAIN, CODEX_AGENT)
MAINS = (CLAUDE_MAIN, CODEX_MAIN)
AGENTS = (CLAUDE_AGENT, CODEX_AGENT)

API_WRITE = "a gh api write"
UNREADABLE = "a gh command the guard cannot read"
WIDE_PUSH = "a force, delete, mirror, all, prune or tag push"
MAIN_PUSH = "a push to main or master"
NOT_FEATURE = "a push to a ref that is not a feature branch"

STUB_CLI = """import json
import sys
with open({calls!r}, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({{"ok": False, "reasons": [{{"reason": "no-audit"}}]}}))
sys.exit(1)
"""


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "TMPDIR": str(tmp),
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true",
                "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def gh_api_writes(a: str) -> list[str]:
    """O1, O2, O3: refused, `a gh api write`."""
    return ["gh api -X POST repos/o/r/releases -f tag_name=v9.9.9",
            f"gh api --method=POST repos/o/r/git/refs -f ref=refs/tags/v9.9.9 -f sha={a}",
            "gh api -XPATCH repos/o/r/git/refs/heads/main -F force=true",
            "gh api -X DELETE repos/o/r/git/refs/tags/v1.0.0",
            "gh api -X delete repos/o/r/branches/main/protection",
            "gh api -iX PUT repos/o/r/x",
            "gh api -X GET -X POST repos/o/r/x"]


O2_COMMANDS = ["gh api repos/o/r/releases -f tag_name=v9.9.9", "gh api repos/o/r/releases --input body.json"]


def o3_commands(a: str) -> list[str]:
    return ["gh api graphql -f query='mutation{createRef(input:{repositoryId:\"R\",name:\"refs/tags/v9\",oid:\""
            + a + "\"}){ref{name}}}'",
            "gh api graphql -F query=@q.graphql",
            "gh api graphql --input q.json"]


O5_COMMANDS = ["gh api $ARGS", "gh api repos/o/r/x $(echo -X POST)", "gh api {-X,POST} repos/o/r/x",
               "gh api --unknown graphql repos/o/r -f a=b", "gh api graphql repos/o/r -f a=b"]

O6_COMMANDS = [("gh release delete v1.0.0 --cleanup-tag --yes", "a gh release delete"),
               ("gh release edit v1.0.0 --draft=false", "a gh release edit"),
               ("gh release upload v1.0.0 x.tgz --clobber", "a gh release upload"),
               ("gh release delete-asset v1.0.0 x.tgz", "a gh release delete-asset"),
               ("gh release -R o/r delete v1.0.0", "a gh release delete"),
               ("gh release frob v1.0.0", "a gh release frob")]

O9_COMMANDS = [("gh alias set rel 'release create'", "a gh alias set"),
               ("gh alias delete rel", "a gh alias delete"),
               ("gh alias import a.yml", "a gh alias import"),
               ("gh repo edit --default-branch x", "a gh repo edit"),
               ("gh repo delete o/r --yes", "a gh repo delete"),
               ("gh repo sync", "a gh repo sync"),
               ("gh repo fork", "a gh repo fork"),
               ("gh repo set-default o/r", "a gh repo set-default"),
               ("gh repo deploy-key add k.pub --allow-write", "a gh repo deploy-key add"),
               ("gh repo autolink create x y", "a gh repo autolink create"),
               ("gh workflow run release.yml", "a gh workflow run"),
               ("gh workflow disable ci.yml", "a gh workflow disable"),
               ("gh secret set X", "a gh secret set"),
               ("gh secret delete X", "a gh secret delete"),
               ("gh variable set X", "a gh variable set"),
               ("gh variable delete X", "a gh variable delete"),
               ("gh ruleset frob 1", "a gh ruleset frob")]

O11_COMMANDS = [("gh pr merge 12 --admin --rebase", "a merge that bypasses branch protection"),
                ("gh pr merge --admin=true 12", "a merge that bypasses branch protection"),
                ("gh pr -R o/r merge 12 --admin", "a merge that bypasses branch protection"),
                ("gh pr merge 12 --rebase --match-head-commit $SHA", UNREADABLE)]

O13_COMMANDS = [("gh rel v9.9.9", UNREADABLE), ("gh co 12", UNREADABLE), ("gh my-ext", UNREADABLE),
                ("gh $CMD", UNREADABLE),
                ("gh alias set rel 'release create' && gh rel v9.9.9", "a gh alias set"),
                ("gh extension exec my-ext", "a gh extension or agent run"),
                ("gh copilot", "a gh extension or agent run")]

O16_COMMANDS = ["sh -c 'gh api -X POST repos/o/r/releases -f tag_name=v9'",
                "timeout 30 gh release delete v1.0.0",
                "/opt/homebrew/bin/gh release delete v1.0.0",
                "command gh api -X DELETE repos/o/r/git/refs/tags/v1.0.0",
                "GH_REPO=o/r gh release delete v1.0.0",
                "X=$(gh api -X POST repos/o/r/releases -f tag_name=v9)",
                "sudo -u me gh release delete v1.0.0",
                "env -i gh release delete v1.0.0",
                "nice -n 5 gh release delete v1.0.0",
                "exec gh release delete v1.0.0",
                "if gh release delete v1.0.0; then :; fi"]

O17_COMMANDS = ["echo 'api -X POST repos/o/r/releases' | xargs gh", "echo v1.0.0 | xargs gh release view"]

O18_COMMANDS = ["git push --m origin", "git push --mi origin", "git push --d origin feat/x",
                "git push --de origin feat/x", "git push --a origin", "git push --al origin", "git push --b origin",
                "git push --br origin", "git push --t", "git push --ta origin", "git push --p origin",
                "git push --pr origin", "git push --f", "git push --fo origin feat/x", "git push --fol"]

O22_COMMANDS = ["git push origin heads/main", "git push origin refs/heads/main", "git push origin HEAD:heads/main",
                "git push origin feat/x:heads/main", "git push origin HEAD:refs/heads/master"]


def o24_commands(a: str) -> list[str]:
    return ["git push origin HEAD:remotes/origin/main", "git push origin HEAD:refs/remotes/origin/main",
            "git push origin HEAD:refs/notes/x", "git push origin feat/x:HEAD", "git push origin origin/main",
            "git push origin nosuch", f"git push origin {a}"]


class GuardCase(unittest.TestCase):
    """The scratch repo of the oracle preamble, with `feat/x` checked out."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        for folder in (self.home / ".config", self.root / "tmp"):
            folder.mkdir(parents=True)
        self.env = base_env(self.home, self.root / "tmp")
        self.repo = self.root / "parcel-desk"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        self.A = self.step("A")
        self.B = self.step("B")
        self.C = self.step("C")
        self.git("tag", "-a", "-m", "Release v1.0.0", "v1.0.0", self.A)
        self.git("tag", "release-1", self.A)
        origin = self.root / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("update-ref", "refs/remotes/origin/main", self.C)
        self.git("checkout", "-q", "-b", "feat/x")
        self.calls_file = self.root / "calls.jsonl"
        cli = self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        cli.parent.mkdir(parents=True)
        cli.write_text(STUB_CLI.format(calls=str(self.calls_file)))

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def step(self, name: str) -> str:
        (self.repo / f"{name.lower()}.txt").write_text(f"step {name}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    @contextmanager
    def config(self, *pairs: tuple[str, str], scope: str = "--local"):
        """Git config set in an earlier call; removed when the block ends."""
        for key, value in pairs:
            self.git("config", scope, key, value)
        try:
            yield
        finally:
            for key, _ in pairs:
                self.git("config", scope, "--unset-all", key)

    @contextmanager
    def checked_out(self, branch: str):
        self.git("checkout", "-q", branch)
        try:
            yield
        finally:
            self.git("checkout", "-q", "feat/x")

    def calls(self) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines() if line.strip()]

    def clear_calls(self) -> None:
        self.calls_file.unlink(missing_ok=True)

    def run_guard(self, harness: str, agent: bool, command: str) -> subprocess.CompletedProcess[str]:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            payload.update({"agent_id": "agent-implementer", "agent_type": "implementer"})
        shell, script = GUARDS[harness]
        return subprocess.run([shell, str(script)], cwd=self.repo, input=json.dumps(payload), env=self.env,
                              capture_output=True, text=True, timeout=60)

    @staticmethod
    def decision(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str]:
        try:
            out = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            out = {}
        specific = (out.get("hookSpecificOutput") or {}) if isinstance(out, dict) else {}
        return specific.get("permissionDecision"), str(specific.get("permissionDecisionReason") or "")

    def refused(self, command: str, what: str | tuple[str, ...] | None = None, sessions=ALL) -> str:
        """Assert the oracle's "Refused" in each session; return the Claude main reason ('' if not run)."""
        whats = (what,) if isinstance(what, str) else (what or ())
        main_reason = ""
        for harness, agent in sessions:
            result = self.run_guard(harness, agent, command)
            where = (harness, "agent" if agent else "main", command, result.returncode, result.stdout,
                     result.stderr)
            decision, reason = self.decision(result)
            if harness == "claude" and agent:
                self.assertEqual(result.returncode, 2, where)
                self.assertIn(SUBAGENT_TEXT, result.stderr, where)
            elif harness == "claude":
                self.assertEqual((result.returncode, decision), (0, "ask"), where)
                if whats:
                    self.assertTrue(any(text in reason for text in whats), where)
                main_reason = reason
            else:
                self.assertEqual((result.returncode, decision), (0, "deny"), where)
        return main_reason

    def allowed(self, command: str, sessions=ALL) -> None:
        for harness, agent in sessions:
            result = self.run_guard(harness, agent, command)
            where = (harness, "agent" if agent else "main", command, result.stdout, result.stderr)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, ""), where)

    def sha_arg(self, argv: list[str], flag: str) -> str | None:
        for i, word in enumerate(argv):
            if word == flag and i + 1 < len(argv):
                return argv[i + 1]
            if word.startswith(flag + "="):
                return word.split("=", 1)[1]
        return None

    def assert_called_tag(self, tag: str) -> None:
        calls = self.calls()
        self.assertTrue(any("release-check" in argv and self.sha_arg(argv, "--tag") == tag for argv in calls), calls)

    def assert_called_sha(self, sha: str) -> None:
        calls = self.calls()
        found = [self.sha_arg(argv, "--sha") for argv in calls if "release-check" in argv]
        self.assertTrue(any(value and len(value) >= 7 and sha.startswith(value) for value in found), calls)


class EverydayStage7Tests(GuardCase):
    def test_f1_everyday_stage7_runs_with_no_prompt(self) -> None:
        """F1 (spec line 117): a feature branch push, `gh pr create`, `gh pr view`, `gh api` reads, the lease
        push after a rebase and `gh pr merge --rebase --match-head-commit <sha>` with a literal sha run with
        no prompt. Oracle 4, 12, 14, 19, 25. The flow's "with a literal sha" is checked against the expansion
        spelling of the same Stage 7 merge, which asks (O11, D3, D18)."""
        with self.subTest(oracle=4):
            for command in ("gh api repos/o/r/pulls/7/comments",
                            "gh api 'repos/{owner}/{repo}/pulls?state=open' --paginate --jq '.[].number'",
                            "gh api -X GET search/issues -f q=repo:o/r", "gh api --method HEAD repos/o/r",
                            "gh api graphql -f query='query{viewer{login}}'"):
                self.allowed(command)
        with self.subTest(oracle=12):
            self.allowed(f"gh pr merge 12 --rebase --match-head-commit {self.A}")
            self.allowed("gh pr merge --auto --rebase 12")
        with self.subTest(oracle=11):
            self.refused("gh pr merge 12 --rebase --match-head-commit $SHA", UNREADABLE)
        with self.subTest(oracle=14):
            for command in ('gh pr create --title x --body "$(cat notes.md)"', "gh pr view 7 --json state",
                            "gh issue view 89", "gh run list", "gh run watch 1", "gh auth status", "gh label list",
                            "gh help api", "gh --version", "gh extension list"):
                self.allowed(command)
        with self.subTest(oracle=19):
            self.refused("git push --force-w origin feat/x")
            self.refused("git push --force-i --force-with-lease origin feat/x")
            self.allowed("git push --force-with-lease --force-if-includes", sessions=MAINS)
        with self.subTest(oracle=25):
            for command in ("git push origin feat/x", "git push origin heads/feat/x",
                            "git push origin refs/heads/feat/x", "git push origin HEAD:feat/y",
                            "git push origin HEAD:refs/heads/feat/y", f"git push origin {self.A}:refs/heads/feat/y"):
                self.allowed(command)


class SubagentAbuseTests(GuardCase):
    def test_f2_subagent_gh_writes_and_push_spellings_are_denied(self) -> None:
        """F2 (spec line 118): an implementer subagent tries each gh write and each push spelling; every one
        is denied in Claude and in Codex. Oracle 1 to 3, 5, 6, 8, 9, 11, 13, 16 to 18, 21 to 24, 26 to 30."""
        deny = AGENTS
        with self.subTest(oracle="1 to 3"):
            for command in gh_api_writes(self.A) + O2_COMMANDS + o3_commands(self.A):
                self.refused(command, sessions=deny)
        with self.subTest(oracle=5):
            for command in O5_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=6):
            for command, _ in O6_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=8):
            self.refused(f"gh release create v1.0.0 --target {self.B}", sessions=deny)
            self.refused(f"gh release create v1.0.0 --target {self.A}", sessions=deny)
        with self.subTest(oracle=9):
            for command, _ in O9_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=11):
            for command, _ in O11_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=13):
            for command, _ in O13_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle="16, 17"):
            for command in O16_COMMANDS + O17_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=18):
            for command in O18_COMMANDS:
                self.refused(command, sessions=deny)
        with self.subTest(oracle=21):
            with self.checked_out("main"):
                self.refused("git push --recurse-submodules no origin", sessions=deny)
                self.refused("git push --recurse-sub no origin", sessions=deny)
            self.allowed("git push --recurse-submodules no origin", sessions=deny)
        with self.subTest(oracle="22 to 24"):
            for command in O22_COMMANDS + ["git push origin tags/v1.0.0", "git push origin refs/tags/v1.0.0",
                                           "git push origin HEAD:tags/v9.9.9"] + o24_commands(self.A):
                self.refused(command, sessions=deny)
        with self.subTest(oracle=26):
            self.git("branch", "v2", self.B)
            self.git("tag", "v2", self.B)
            self.refused("git push origin v2", sessions=deny)
        with self.subTest(oracle=27):
            with self.config(("remote.origin.push", "refs/heads/feat/x:refs/heads/main")):
                self.refused("git push origin feat/x", sessions=deny)
                self.allowed("git push origin feat/x:refs/heads/feat/x", sessions=deny)
        with self.subTest(oracle=28):
            with self.config(("push.default", "upstream"), ("branch.feat/x.merge", "refs/tags/v9")):
                self.refused("git push", sessions=deny)
            with self.config(("push.default", "upstream"), ("branch.feat/x.merge", "refs/heads/feat/x")):
                self.allowed("git push", sessions=deny)
        with self.subTest(oracle=29):
            with self.config(("push.followTags", "true")):
                for command in ("git push", "git push origin feat/x", "git push -u origin feat/x",
                                "git push --no-follow origin feat/x"):
                    self.refused(command, sessions=deny)
                self.allowed("git push --no-follow-tags origin feat/x", sessions=deny)
        with self.subTest(oracle=30):
            with self.config(("push.followTags", "true"), scope="--global"):
                self.refused("git push origin feat/x", sessions=deny)
            with self.config(("push.followTags", "maybe")):
                self.refused("git push origin feat/x", sessions=deny)
            with self.config(("push.followTags", "false")):
                self.allowed("git push origin feat/x", sessions=deny)


class MainSessionRerouteTests(GuardCase):
    def test_f3_main_session_reroutes_each_ask_with_its_rule(self) -> None:
        """F3 (spec line 119): after a denied tag push or main push, the main session reroutes through
        `gh api` refs, `gh release` edits, an alias, `tags/vX`, `heads/main`, `--ta`, or `push.followTags`
        set in an earlier call; each one asks with its rule. Oracle 1, 6, 13, 18, 22, 23, 29."""
        self.refused("git push origin v1.0.0", "a tag push", sessions=MAINS)
        self.refused("git push origin main", MAIN_PUSH, sessions=MAINS)
        with self.subTest(oracle=1):
            for command in gh_api_writes(self.A):
                self.refused(command, API_WRITE, sessions=MAINS)
        with self.subTest(oracle=6):
            for command, what in O6_COMMANDS:
                self.refused(command, what, sessions=MAINS)
        with self.subTest(oracle=13):
            for command, what in O13_COMMANDS:
                self.refused(command, what, sessions=MAINS)
        with self.subTest(oracle=18):
            for command in O18_COMMANDS:
                self.refused(command, WIDE_PUSH, sessions=MAINS)
        with self.subTest(oracle=22):
            for command in O22_COMMANDS:
                self.refused(command, MAIN_PUSH, sessions=MAINS)
        with self.subTest(oracle=23):
            for command in ("git push origin tags/v1.0.0", "git push origin refs/tags/v1.0.0"):
                self.clear_calls()
                reason = self.refused(command, "a tag push", sessions=MAINS)
                self.assertIn("release-check:", reason, command)
                self.assert_called_tag("v1.0.0")
            self.clear_calls()
            self.refused("git push origin HEAD:tags/v9.9.9", "a tag push of " + self.C[:7], sessions=MAINS)
            self.assert_called_sha(self.C)
        with self.subTest(oracle=29):
            with self.config(("push.followTags", "true")):
                for command in ("git push", "git push origin feat/x", "git push -u origin feat/x"):
                    reason = self.refused(command, "push.followTags", sessions=MAINS)
                    self.assertNotIn("release-check", reason, command)


class ReleaseTests(GuardCase):
    def test_f4_release_asks_with_the_release_check_verdict(self) -> None:
        """F4 (spec line 120): `/release` stop 2 pushes the tag and runs `gh release create`; both ask with
        the `release-check` verdict. `/release-notes` runs `gh release edit` and gets one more ask. In Codex
        all are denied. Oracle 6, 8, Release Security Audit 46 to 52."""
        with self.subTest(step="tag push"):
            self.clear_calls()
            reason = self.refused("git push origin v1.0.0", "a tag push", sessions=MAINS)
            self.assertIn("release-check:", reason)
            self.assert_called_tag("v1.0.0")
        with self.subTest(step="release create"):
            reason = self.refused("gh release create v1.0.0", "a release create", sessions=MAINS)
            self.assertIn("release-check:", reason)
        with self.subTest(oracle=6, step="release notes"):
            self.refused("gh release edit v1.0.0 --draft=false", "a gh release edit", sessions=MAINS)
        with self.subTest(oracle=8):
            self.clear_calls()
            reason = self.refused(f"gh release create v1.0.0 --target {self.B}", "release-check failed",
                                  sessions=MAINS)
            self.assertIn(self.A[:7], reason)
            self.assertIn(self.B[:7], reason)
            self.assertEqual(self.calls(), [])
            reason = self.refused(f"gh release create v1.0.0 --target {self.A}",
                                  "a release create at " + self.A[:7], sessions=MAINS)
            self.assertIn("release-check:", reason)


class ConfigInEarlierCallTests(GuardCase):
    def test_f5_config_set_earlier_makes_the_next_plain_push_ask(self) -> None:
        """F5 (spec line 121): config set in an earlier call (`remote.origin.push`, `branch.<b>.merge`,
        `push.followTags`) makes the next plain push ask. Oracle 27 to 30."""
        with self.subTest(oracle=27):
            with self.config(("remote.origin.push", "refs/heads/feat/x:refs/heads/main")):
                self.refused("git push origin feat/x", "a push whose remote config can reach other branches")
                self.allowed("git push origin feat/x:refs/heads/feat/x")
        with self.subTest(oracle=28):
            with self.config(("push.default", "upstream"), ("branch.feat/x.merge", "refs/tags/v9")):
                self.refused("git push", "a bare push whose remote config can reach other branches")
            with self.config(("push.default", "upstream"), ("branch.feat/x.merge", "refs/heads/feat/x")):
                self.allowed("git push")
        with self.subTest(oracle=29):
            with self.config(("push.followTags", "true")):
                for command in ("git push", "git push origin feat/x", "git push -u origin feat/x"):
                    reason = self.refused(command, "push.followTags")
                    self.assertNotIn("release-check", reason, command)
                self.allowed("git push --no-follow-tags origin feat/x")
                self.refused("git push --no-follow origin feat/x")
        with self.subTest(oracle=30):
            with self.config(("push.followTags", "true"), scope="--global"):
                self.refused("git push origin feat/x")
            with self.config(("push.followTags", "maybe")):
                self.refused("git push origin feat/x")
            with self.config(("push.followTags", "false")):
                self.allowed("git push origin feat/x")


if __name__ == "__main__":
    sys.exit(unittest.main())
