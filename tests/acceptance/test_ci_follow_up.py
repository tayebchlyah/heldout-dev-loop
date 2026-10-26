#!/usr/bin/env python3
"""Held-out acceptance tests for the CI follow-up (spec: 20261025 CI Follow-up).

One test per `## Acceptance flows` item (F<n>). Each docstring names the `## Acceptance oracle` lines
(O<n>, with the spec file line) it proves. The tests use only the declared seams: the stdout JSON and
exit code of `devloop.py ci wait` and the output of `check_setup.py`, both with a stub `gh` on `PATH`;
the Claude and Codex `git-guard.sh` hooks fed hook JSON on stdin; the kit text of `skill/SKILL.md`.
The kit runs from the source tree. Every name in the temp repos is invented.

Fixture choices the spec leaves open:
- The stub `gh` is a script in a temp folder outside the repo. It appends its argv to `calls.jsonl`
  and answers by the first words of its argv: `pr view`, `api graphql`,
  `api repos/<owner>/<repo>/compare/...`, `run view`, `api repos/<owner>/<repo>`. The n-th call of a
  kind gets the n-th answer of its list (the last one repeats). Any other call exits 1.
- The stub applies a `--jq` that is a plain `.field` path the way gh does (a string raw, any other
  value as JSON), so `api repos/o/r --jq .allow_rebase_merge` prints `false` or `true`.
- The `gh api graphql` answer has the standard GitHub shape:
  `data.repository.pullRequest.reviewThreads.nodes[]`, each thread with `isResolved`, `isOutdated`,
  `path`, `line` and `comments.nodes[0]` (`author.login`, `body`, `url`).
- `first_error` is read as text (a list of lines is joined with line breaks).
- Every `ci wait` run passes `--timeout 1`, so a wrong poll loop ends within a minute.
- `check_setup.py` passes for Claude after `init` once the home holds a `security-audit` skill whose
  tree id `DEVLOOP_TEST_SECURITY_AUDIT_TREE` names.
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
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"
GUARDS = {"claude": ("bash", REPO_ROOT / "claude" / "hooks" / "git-guard.sh"),
          "codex": ("sh", REPO_ROOT / "codex" / "hooks" / "git-guard.sh")}
REFUSED = {"claude": "ask", "codex": "deny"}

PR_URL = "https://github.com/o/r/pull/7"
FAILED_LINK = "https://github.com/o/r/actions/runs/99/job/5"
ERROR_LINE = "Error: assertion failed in test_ship_order"

STUB_SOURCE = r'''
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = sys.argv[1:]


def kind(argv):
    if argv[:2] == ["pr", "view"]:
        return "pr view"
    if argv[:2] == ["api", "graphql"]:
        return "api graphql"
    if argv[:2] == ["run", "view"]:
        return "run view"
    if len(argv) > 1 and argv[0] == "api" and argv[1].startswith("repos/"):
        return "compare" if "/compare/" in argv[1] else "repo"
    return "other"


log = os.path.join(HERE, "calls.jsonl")
earlier = []
if os.path.exists(log):
    with open(log) as handle:
        earlier = [json.loads(line) for line in handle if line.strip()]
with open(log, "a") as handle:
    handle.write(json.dumps(ARGV) + "\n")
key = kind(ARGV)
with open(os.path.join(HERE, "answers.json")) as handle:
    answers = json.load(handle).get(key) or [{"exit": 1, "stderr": "stub gh: no answer for " + " ".join(ARGV)}]
answer = answers[min(sum(1 for argv in earlier if kind(argv) == key), len(answers) - 1)]
out = answer.get("stdout", "")
for flag in ("--jq", "-q"):
    if flag in ARGV[:-1]:
        expr = ARGV[ARGV.index(flag) + 1]
        if re.fullmatch(r"(\.[A-Za-z_][A-Za-z0-9_]*)+", expr):
            try:
                value = json.loads(out)
                for part in expr.split(".")[1:]:
                    value = value.get(part) if isinstance(value, dict) else None
                out = (value if isinstance(value, str) else json.dumps(value)) + "\n"
            except ValueError:
                pass
sys.stdout.write(out)
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("exit", 0))
'''


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0",
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def init_repo(path: Path, env: dict[str, str], branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, env, "init", "-q", "-b", branch)
    for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(path, env, "config", key, value)


def commit(path: Path, env: dict[str, str], files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        put(path / rel, text)
    git(path, env, "add", "-A")
    git(path, env, "commit", "-q", "-m", message)
    return git(path, env, "rev-parse", "HEAD")


def ok(value: object) -> dict:
    return {"exit": 0, "stdout": value if isinstance(value, str) else json.dumps(value) + "\n"}


def fails(stderr: str) -> dict:
    return {"exit": 1, "stdout": "", "stderr": stderr + "\n"}


def check(name: str, status: str = "COMPLETED", conclusion: str = "SUCCESS", url: str | None = None) -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status,
            "conclusion": conclusion if status == "COMPLETED" else "",
            "detailsUrl": url or f"https://github.com/o/r/actions/runs/50/job/{len(name)}"}


def view(head: str, **changes: object) -> dict:
    """The default `gh pr view` answer of the oracle preamble, with the changes the line names."""
    answer = {"number": 7, "url": PR_URL, "headRefOid": head, "baseRefName": "main", "isDraft": False,
              "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "", "latestReviews": [],
              "statusCheckRollup": [check("test"), check("acceptance")]}
    answer.update(changes)
    return answer


def thread(resolved: bool, path: str, line: int, author: str, body: str, url: str, outdated: bool = False) -> dict:
    return {"isResolved": resolved, "isOutdated": outdated, "path": path, "line": line,
            "comments": {"nodes": [{"author": {"login": author}, "body": body, "url": url}]}}


def threads_doc(*nodes: dict) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(nodes)}}}}}


def compare_doc(behind: int) -> dict:
    return {"status": "diverged" if behind else "ahead", "ahead_by": 1, "behind_by": behind}


FAILED_LOG = "".join(
    [f"acceptance\tSet up job\t2026-10-25T10:00:0{n}.0000000Z {text}\n"
     for n, text in enumerate(("Runner ready", "Checking out the branch", "collected 40 items"))]
    + [f"acceptance\tRun tests\t2026-10-25T10:00:10.0000000Z {ERROR_LINE}\n"]
    + [f"acceptance\tRun tests\t2026-10-25T10:00:11.0000000Z context line {n}\n" for n in range(40)])


class StubGh:
    """The stub `gh`: a script outside the repo that records its argv and answers by kind and pass."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True)
        script = folder / "gh"
        script.write_text(f"#!{sys.executable}\n{STUB_SOURCE}")
        script.chmod(0o755)
        self.answers: dict[str, list[dict]] = {}
        self.save()

    def save(self) -> None:
        (self.folder / "answers.json").write_text(json.dumps(self.answers))

    def set(self, **answers: list[dict]) -> None:
        """Keys: pr_view, api_graphql, compare, run_view, repo. Clears the recorded calls."""
        self.answers = {key.replace("_", " "): value for key, value in answers.items()}
        self.save()
        (self.folder / "calls.jsonl").unlink(missing_ok=True)

    def calls(self) -> list[list[str]]:
        log = self.folder / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    def of(self, *words: str) -> list[list[str]]:
        return [argv for argv in self.calls() if argv[:len(words)] == list(words)]

    def compare_calls(self) -> list[list[str]]:
        return [argv for argv in self.calls()
                if len(argv) > 1 and argv[0] == "api" and argv[1].startswith("repos/") and "/compare/" in argv[1]]


def as_text(value: object) -> str | None:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return value if isinstance(value, str) else None


def section(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise AssertionError(f"no section matching {pattern!r} in SKILL.md")
    return match.group(0)


def stage7() -> str:
    return section(SKILL_MD.read_text(), r"(?ms)^## 7 .*?(?=^## |\Z)")


def stop_list() -> str:
    lines = [line for line in SKILL_MD.read_text().splitlines() if "Stop and report only for" in line]
    if not lines:
        raise AssertionError("SKILL.md has no 'Stop and report only for' list")
    return lines[0]


def hook_decision(result: subprocess.CompletedProcess[str]) -> str | None:
    """The permissionDecision a guard gave (JSON on stdout, else exit 2: deny), None when it gave none."""
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        out = {}
    decision = None
    if isinstance(out, dict):
        decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision")
    if decision is None and result.returncode == 2:
        decision = "deny"
    return decision


class CiCase(unittest.TestCase):
    """A git repo with one commit <H> on `feat/x`, a stub `gh` on `PATH`, `DEVLOOP_TEST_CI_POLL=0`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("home", "tmp"):
            (self.root / name).mkdir()
        self.env = base_env(self.root / "home", self.root / "tmp")
        self.stub = StubGh(self.root / "stub-bin")
        self.repo = self.root / "order-desk"
        init_repo(self.repo, self.env, "feat/x")
        self.H = commit(self.repo, self.env, {"README.md": "# Order desk\n",
                                              "dev-loop.json": json.dumps({"version": "2.1.1",
                                                                           "harnesses": ["claude"]}) + "\n"},
                        "Order desk")

    def defaults(self, head: str | None = None, **changes: list[dict]) -> None:
        answers = {"pr_view": [ok(view(head or self.H))], "api_graphql": [ok(threads_doc())],
                   "compare": [ok(compare_doc(0))], "run_view": [ok(FAILED_LOG)]}
        answers.update(changes)
        self.stub.set(**answers)

    def ci_wait(self, *args: str, cwd: Path | None = None) -> tuple[int, dict]:
        env = {**self.env, "DEVLOOP_TEST_CI_POLL": "0",
               "PATH": f"{self.stub.folder}{os.pathsep}{self.env.get('PATH', '')}"}
        result = subprocess.run([sys.executable, str(DEVLOOP), "ci", "wait", "--timeout", "1", *args],
                                cwd=cwd or self.repo, env=env, capture_output=True, text=True, timeout=180)
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {"unparsed_stdout": result.stdout}
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr[-2000:])
        return result.returncode, payload


class GreenPrTests(CiCase):
    def test_f1_green_pr(self) -> None:
        """F1: `ci wait` polls until the checks pass and gives `pass`.
        O18 (spec line 89), O1 (spec line 72), O26 (spec line 97), O27 (spec line 98)."""
        self.defaults(pr_view=[ok(view(self.H, statusCheckRollup=[check("test", status="IN_PROGRESS"),
                                                                     check("acceptance")])),
                               ok(view(self.H))])
        code, payload = self.ci_wait("--pr", "7")
        with self.subTest(oracle=18):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("state"), "pass", payload)
            self.assertEqual(len(self.stub.of("pr", "view")), 2, self.stub.calls())
        with self.subTest(oracle=1):
            self.assertEqual(payload.get("head"), self.H, payload)
            self.assertIsNone(payload.get("reason", "missing"), payload)
            self.assertEqual(payload.get("threads"), [], payload)
            self.assertIsNone(payload.get("stale_base", "missing"), payload)
            self.assertIsNone(payload.get("failed_job", "missing"), payload)
            self.assertEqual(self.stub.compare_calls(), [], self.stub.calls())
        with self.subTest(oracle=26):
            for argv in self.stub.of("pr", "view"):
                self.assertEqual(argv[2:3], ["7"], argv)
        with self.subTest(oracle=27):
            for argv in self.stub.calls():
                self.assertFalse("checks" in argv and "--watch" in argv, argv)
                self.assertNotIn("run rerun", " ".join(argv), argv)


class MainMovedTests(CiCase):
    """The repo of `setUp` is the main worktree; the PR branch `feat/x` sits in a linked worktree that
    tracks `origin/feat/x` of a bare `origin`."""

    def setUp(self) -> None:
        super().setUp()
        self.origin = self.root / "origin.git"
        git(self.root, self.env, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(self.repo, self.env, "branch", "-m", "main")
        git(self.repo, self.env, "remote", "add", "origin", str(self.origin))
        git(self.repo, self.env, "push", "-q", "origin", "main")
        git(self.repo, self.env, "fetch", "-q", "origin")
        self.wt = self.root / "order-desk-feat-x"
        git(self.repo, self.env, "worktree", "add", "-q", "-b", "feat/x", str(self.wt))
        self.H = commit(self.wt, self.env, {"src/ship.py": "def ship(order):\n    return order\n"}, "Ship orders")
        git(self.wt, self.env, "push", "-q", "-u", "origin", "feat/x")

    def guard(self, harness: str, command: str, agent: bool = False) -> str | None:
        payload = {"session_id": "session-1", "cwd": str(self.wt), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            payload.update({"agent_id": "agent-implementer", "agent_type": "implementer"})
        shell, script = GUARDS[harness]
        result = subprocess.run([shell, str(script)], cwd=self.wt, input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=60)
        return hook_decision(result)

    def test_f2_main_moved_rebase_lease_push_wait_again(self) -> None:
        """F2: `conflicts` at once, or `fail` with `stale_base` true; the agent rebases and pushes with
        `--force-with-lease --force-if-includes` (the guard allows it), then waits again; the first pass
        sees the old head and polls again.
        O2 (spec line 73), O13 (spec line 84), O39 (spec line 116), O40 (spec line 117), O41 (spec line 118),
        O42 (spec line 119), O43 (spec line 120), O28 (spec line 99), O44 (spec line 124), O45 (spec line 125)."""
        with self.subTest(oracle=2):
            self.defaults(pr_view=[ok(view(self.H, mergeable="CONFLICTING",
                                           statusCheckRollup=[check("test", status="IN_PROGRESS"),
                                                              check("acceptance")]))])
            code, payload = self.ci_wait(cwd=self.wt)
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "conflicts", payload)
            self.assertEqual(len(self.stub.of("pr", "view")), 1, self.stub.calls())

        with self.subTest(oracle=13):
            self.defaults(pr_view=[ok(view(self.H, statusCheckRollup=[
                check("test"), check("acceptance", conclusion="FAILURE", url=FAILED_LINK)]))],
                compare=[ok(compare_doc(2))])
            code, payload = self.ci_wait(cwd=self.wt)
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "fail", payload)
            self.assertIs(payload.get("stale_base"), True, payload)
            self.assertEqual([argv[1] for argv in self.stub.compare_calls()],
                             [f"repos/o/r/compare/main...{self.H}"], self.stub.calls())

        # The agent's rebase: main moved on origin, the branch rebases onto it.
        commit(self.repo, self.env, {"src/menu.py": "MENU = ['tea']\n"}, "Menu")
        git(self.repo, self.env, "push", "-q", "origin", "main")
        git(self.wt, self.env, "fetch", "-q", "origin")
        git(self.wt, self.env, "rebase", "-q", "origin/main")
        rebased = git(self.wt, self.env, "rev-parse", "HEAD")
        self.assertNotEqual(rebased, self.H)

        with self.subTest(oracle=39):
            for harness in GUARDS:
                for command in ("git push --force-with-lease", "git push --force-with-lease --force-if-includes",
                                "git push --force-with-lease --force-if-includes origin HEAD",
                                "git push --force-with-lease origin feat/x",
                                "git push --force-with-lease origin HEAD:feat/x"):
                    self.assertIsNone(self.guard(harness, command), (harness, command))
        with self.subTest(oracle="40 to 42"):
            for harness in GUARDS:
                for command in ("git push --force", "git push -uf origin feat/x",
                                "git push --force-with-lease=feat/x:abc origin feat/x",
                                "git push --force-with-lease origin +feat/x", "git push --force-with-lease --tags",
                                "git push --force-with-lease origin main",
                                "git push --force-with-lease origin HEAD:main",
                                "git push --force-with-lease origin feat/other",
                                "git push --force-with-lease origin HEAD:feat/other",
                                "git rebase origin/main && git push --force-with-lease"):
                    self.assertEqual(self.guard(harness, command), REFUSED[harness], (harness, command))
        with self.subTest(oracle=43):
            for harness in GUARDS:
                self.assertEqual(self.guard(harness, "git push --force-with-lease", agent=True), "deny", harness)

        with self.subTest(oracle=28):
            self.defaults(head=rebased, pr_view=[ok(view(self.H)), ok(view(rebased))])
            code, payload = self.ci_wait(cwd=self.wt)
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("state"), "pass", payload)
            self.assertEqual(payload.get("head"), rebased, payload)
            self.assertEqual(len(self.stub.of("pr", "view")), 2, self.stub.calls())

        with self.subTest(oracle=44):
            text = stage7()
            for name in ("conflicts", "stale_base", "--force-with-lease --force-if-includes"):
                self.assertIn(name, text)
        with self.subTest(oracle=45):
            stops = stop_list()
            self.assertIn("lease push", stops)
            self.assertRegex(stops, r"guard[^.;]*\b(asks|refuses)\b")


class ReviewThreadTests(CiCase):
    def test_f3_review_thread_stops_without_reply(self) -> None:
        """F3: `ci wait` gives `threads` with the thread list; the agent stops and does not reply.
        O4 (spec line 75), O44 (spec line 124), O45 (spec line 125)."""
        self.defaults(api_graphql=[ok(threads_doc(
            thread(True, "skill/old.py", 3, "carol", "Fixed already", "https://github.com/o/r/pull/7#r0"),
            thread(False, "skill/x.py", 12, "bot", "Line one\nLine two", "https://github.com/o/r/pull/7#r1")))])
        code, payload = self.ci_wait()
        with self.subTest(oracle=4):
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "threads", payload)
            self.assertEqual(payload.get("threads"), [{"path": "skill/x.py", "line": 12, "author": "bot",
                                                       "url": "https://github.com/o/r/pull/7#r1",
                                                       "text": "Line one"}], payload)
            graphql = self.stub.of("api", "graphql")
            self.assertTrue(graphql, self.stub.calls())
            self.assertIn("owner=o", graphql[0], graphql)
            self.assertIn("name=r", graphql[0], graphql)
        with self.subTest(oracle=44):
            text = stage7()
            self.assertIn("threads", text)
            self.assertRegex(text, r"(?is)\bnever\b[^.]*\breply\b[^.]*\bresolve")
        with self.subTest(oracle=45):
            self.assertIn("threads", stop_list())


class RedCiTests(CiCase):
    def test_f4_red_ci_on_a_current_base(self) -> None:
        """F4: `ci wait` gives `fail` with `failed_job`, `first_error` and `stale_base` false; no rerun.
        O10 (spec line 81), O13 (spec line 84), O27 (spec line 98), O44 (spec line 124)."""
        self.defaults(pr_view=[ok(view(self.H, statusCheckRollup=[
            check("test", status="IN_PROGRESS"), check("acceptance", conclusion="FAILURE", url=FAILED_LINK)]))])
        code, payload = self.ci_wait()
        with self.subTest(oracle=10):
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "fail", payload)
            self.assertEqual(len(self.stub.of("pr", "view")), 1, self.stub.calls())
            self.assertEqual(payload.get("failed_job"), "acceptance", payload)
            self.assertEqual(payload.get("url"), FAILED_LINK, payload)
            runs = self.stub.of("run", "view")
            self.assertEqual(len(runs), 1, self.stub.calls())
            self.assertEqual(runs[0][2], "99", runs)
            self.assertIn("--log-failed", runs[0], runs)
            first = as_text(payload.get("first_error"))
            self.assertIsNotNone(first, payload)
            lines = first.splitlines()
            self.assertTrue(lines and ERROR_LINE in lines[0], first)
            self.assertNotIn("Runner ready", first)
            self.assertLessEqual(len(lines), 30, first)
        with self.subTest(oracle=13):
            self.assertIs(payload.get("stale_base"), False, payload)
            self.assertEqual([argv[1] for argv in self.stub.compare_calls()],
                             [f"repos/o/r/compare/main...{self.H}"], self.stub.calls())
        with self.subTest(oracle=27):
            self.assertEqual([argv for argv in self.stub.calls() if "rerun" in argv], [], self.stub.calls())
        with self.subTest(oracle=44):
            self.assertIn("gh run rerun", stage7())


class MergeBlockTests(CiCase):
    def test_f5_merge_block_with_a_reason(self) -> None:
        """F5: `ci wait` gives `blocked` with a reason; `behind` rebases, another reason stops.
        O15 (spec line 86), O17 (spec line 88), O32 (spec line 103), O44 (spec line 124), O45 (spec line 125)."""
        with self.subTest(oracle=15):
            self.defaults(pr_view=[ok(view(self.H, mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED"))])
            code, payload = self.ci_wait()
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "blocked", payload)
            self.assertEqual(payload.get("reason"), "review-required", payload)
            self.assertEqual(len(self.stub.compare_calls()), 1, self.stub.calls())
        with self.subTest(oracle=17):
            self.defaults(pr_view=[ok(view(self.H, mergeStateStatus="BEHIND"))], compare=[ok(compare_doc(3))])
            code, payload = self.ci_wait()
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "blocked", payload)
            self.assertEqual(payload.get("reason"), "behind", payload)
            self.assertIs(payload.get("stale_base"), True, payload)
        with self.subTest(oracle=32):
            self.defaults(pr_view=[ok(view(self.H, latestReviews=[
                {"author": {"login": "carol"}, "state": "CHANGES_REQUESTED"}],
                statusCheckRollup=[check("test", status="IN_PROGRESS"), check("acceptance")]))])
            code, payload = self.ci_wait()
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("state"), "blocked", payload)
            self.assertEqual(payload.get("reason"), "changes-requested", payload)
            self.assertEqual(len(self.stub.of("pr", "view")), 1, self.stub.calls())
        with self.subTest(oracle=44):
            text = stage7()
            for name in ("blocked", "behind"):
                self.assertIn(name, text)
        with self.subTest(oracle=45):
            self.assertIn("blocked", stop_list())


class SetupTests(unittest.TestCase):
    """An installed project (`init`, both harnesses) where `check_setup.py --harness claude` passes,
    a stub `gh` on `PATH`, and an `origin` remote."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        for folder in (self.home, self.root / "tmp", self.root / "notes" / "specs"):
            folder.mkdir(parents=True)
        self.env = base_env(self.home, self.root / "tmp")
        self.project = self.root / "tea-shop"
        init_repo(self.project, self.env)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both",
                               f"--notes={self.root / 'notes'}"], cwd=self.project, env=self.env,
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.env["DEVLOOP_TEST_SECURITY_AUDIT_TREE"] = self.install_security_audit()
        self.stub = StubGh(self.root / "stub-bin")

    def install_security_audit(self) -> str:
        """A test security-audit skill at both harness paths (Claude through a link). Returns its tree id."""
        folder = self.home / ".agents" / "skills" / "security-audit"
        put(folder / "SKILL.md", "---\nname: security-audit\ndescription: Audit a codebase.\n---\n\n# Audit\n")
        link = self.home / ".claude" / "skills" / "security-audit"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(folder, target_is_directory=True)
        scratch = self.root / "tree-scratch"
        git(self.root, self.env, "init", "-q", str(scratch))
        tree_env = {**self.env, "GIT_DIR": str(scratch / ".git"), "GIT_WORK_TREE": str(folder)}
        git(folder, tree_env, "add", "-A", ".")
        return git(folder, tree_env, "write-tree")

    def checker(self, harness: str) -> tuple[int, str]:
        env = {**self.env, "PATH": f"{self.stub.folder}{os.pathsep}{self.env.get('PATH', '')}"}
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(self.project)], env=env, capture_output=True, text=True, timeout=180)
        return result.returncode, result.stdout + result.stderr

    @staticmethod
    def user_actions(output: str) -> list[str]:
        lines = output.splitlines()
        heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
        return lines[heads[0] + 1:] if heads else []

    def test_f6_setup_names_the_rebase_merge_setting(self) -> None:
        """F6: `check_setup.py` names the rebase merge setting when GitHub does not allow it.
        O34 (spec line 108), O35 (spec line 109)."""
        git(self.project, self.env, "remote", "add", "origin", "git@github.com:o/r.git")
        repo_answer = {"full_name": "o/r", "allow_rebase_merge": False, "allow_merge_commit": True}
        self.stub.set(repo=[ok(repo_answer)])
        for harness in ("claude", "codex"):
            with self.subTest(oracle=34, harness=harness):
                code, output = self.checker(harness)
                self.assertEqual(code, 1, output)
                lines = [line for line in self.user_actions(output) if "o/r" in line and "gh pr merge --rebase" in line]
                self.assertTrue(lines, output)
                self.assertTrue([argv for argv in self.stub.calls() if argv[:2] == ["api", "repos/o/r"]],
                                self.stub.calls())
        with self.subTest(oracle=35):
            self.stub.set(repo=[ok({**repo_answer, "allow_rebase_merge": True})])
            code, output = self.checker("claude")
            self.assertEqual(code, 0, output)
            self.assertIn("PASS", output)
            self.assertFalse([line for line in output.splitlines() if "gh pr merge --rebase" in line], output)


if __name__ == "__main__":
    unittest.main()
