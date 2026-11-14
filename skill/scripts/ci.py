"""`devloop.py ci wait`: one verdict for the PR of the local HEAD. `check_setup.py`: the GitHub settings Stage 7 needs."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

from board import store
from devloop import emit
from notes_path import main_ref

POLL_SECONDS = 30
GH_TIMEOUT = 60
VIEW_FIELDS = ("number,url,headRefOid,headRefName,baseRefName,isDraft,mergeable,mergeStateStatus,reviewDecision,latestReviews,"
               "statusCheckRollup")
THREADS_QUERY = ("query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {"
                 " pullRequest(number: $number) { reviewThreads(first: 100) { nodes { isResolved path line"
                 " comments(first: 1) { nodes { author { login } authorAssociation body url } } } } } } }")
TEXT_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
FAILED_CHECK_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}
BLOCKED_REASONS = {"REVIEW_REQUIRED": "review-required", "CHANGES_REQUESTED": "changes-requested"}


def check_state(item: dict) -> str:
    kind = item.get("__typename")
    if kind == "CheckRun":
        if item.get("conclusion") in FAILED_CHECK_STATES:
            return "fail"
        ok = item.get("status") == "COMPLETED" and item.get("conclusion") in ("SUCCESS", "NEUTRAL", "SKIPPED")
        return "pass" if ok else "pending"
    if kind == "StatusContext":
        state = item.get("state")
        return "pass" if state == "SUCCESS" else "fail" if state in ("FAILURE", "ERROR") else "pending"
    return "pending"


def check_name(item: dict) -> str | None:
    return item.get("name") if item.get("__typename") == "CheckRun" else item.get("context")


def check_link(item: dict) -> str | None:
    return item.get("detailsUrl") if item.get("__typename") == "CheckRun" else item.get("targetUrl")


def pending_checks(view: dict) -> list[str | None]:
    return [check_name(c) for c in view.get("statusCheckRollup") or [] if check_state(c) == "pending"]


def open_threads(data: dict) -> list[dict]:
    try:
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (KeyError, TypeError):
        raise ValueError("unexpected gh api graphql answer") from None
    threads = []
    for node in nodes:
        if node.get("isResolved"):
            continue
        comments = (node.get("comments") or {}).get("nodes") or [{}]
        first = comments[0] or {}
        body = first.get("body") or ""
        text = (body.splitlines() or [""])[0][:200] if first.get("authorAssociation") in TEXT_ASSOCIATIONS else None
        threads.append({"path": node.get("path"), "line": node.get("line"),
                        "author": (first.get("author") or {}).get("login"), "url": first.get("url"), "text": text})
    return threads


def verdict(view: dict, threads: list[dict]) -> dict | None:
    checks = view.get("statusCheckRollup") or []
    failed = next((c for c in checks if check_state(c) == "fail"), None)
    pending = pending_checks(view)

    def result(state: str, reason: str | None = None) -> dict:
        return {"state": state, "reason": reason, "failed": failed if state == "fail" else None, "pending": pending}

    if view.get("mergeable") == "CONFLICTING" or view.get("mergeStateStatus") == "DIRTY":
        return result("conflicts")
    if threads:
        return result("threads")
    if any(r.get("state") == "CHANGES_REQUESTED" for r in view.get("latestReviews") or []):
        return result("blocked", "changes-requested")
    if failed:
        return result("fail")
    if pending or not checks or "UNKNOWN" in (view.get("mergeable"), view.get("mergeStateStatus")):
        return None
    if view.get("isDraft"):
        return result("blocked", "draft")
    if view.get("mergeStateStatus") == "BEHIND":
        return result("blocked", "behind")
    if view.get("mergeStateStatus") == "BLOCKED":
        return result("blocked", BLOCKED_REASONS.get(view.get("reviewDecision") or "", "rule"))
    return result("pass")


def first_error(log: str) -> str | None:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if "error" in line.lower() or "FAIL" in line:
            return "\n".join(lines[i:i + 30])
    return None


def gh(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout or GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["gh", *args], 1, "", f"gh failed: {exc}")


def stale_base(slug: str, base: str, head: str) -> bool | None:
    behind = gh("api", f"repos/{slug}/compare/{base}...{head}", "--jq", ".behind_by")
    try:
        number = json.loads(behind.stdout) if behind.returncode == 0 else None
    except ValueError:
        return None
    return number > 0 if isinstance(number, int) and not isinstance(number, bool) else None


def record(view: dict) -> None:
    number = view.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return
    found = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], capture_output=True, text=True)
    if found.returncode != 0:
        return
    text = json.dumps({"number": number, "url": str(view.get("url") or ""),
                       "branch": str(view.get("headRefName") or "")}) + "\n"
    fds: list[int] = []
    try:
        fds.append(os.open(found.stdout.strip(), os.O_RDONLY | os.O_DIRECTORY))
        with contextlib.suppress(FileExistsError):
            os.mkdir("dev-loop", 0o755, dir_fd=fds[0])
        fds.append(os.open("dev-loop", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[0]))
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISREG(os.stat("pr.json", dir_fd=fds[1], follow_symlinks=False).st_mode):
                return
        store.write_in(fds[1], "pr.json", text)
    except OSError:
        return
    finally:
        for fd in fds:
            os.close(fd)


def wait(pr: int | None, timeout_minutes: float) -> tuple[int, dict]:
    """Poll the PR of the local HEAD until a final verdict or the deadline."""
    payload: dict = {"state": "error", "reason": None, "url": None, "head": None, "failed_job": None,
                     "first_error": None, "stale_base": None, "threads": [], "pending": []}
    local = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    if local.returncode != 0:
        return 1, {**payload, "message": "not a git repository"}
    head = local.stdout.strip()
    poll = float(os.environ.get("DEVLOOP_TEST_CI_POLL") or POLL_SECONDS)
    deadline = time.monotonic() + timeout_minutes * 60
    first = True
    while True:
        shown = gh("pr", "view", *([str(pr)] if pr else []), "--json", VIEW_FIELDS)
        if shown.returncode != 0:
            if first and "no pull requests found" in shown.stderr:
                return 1, {**payload, "state": "no-pr"}
            return 1, {**payload, "message": shown.stderr.strip()}
        first = False
        try:
            view = json.loads(shown.stdout)
        except ValueError:
            return 1, {**payload, "message": "gh pr view did not return JSON"}
        record(view)
        payload.update(url=view.get("url"), head=view.get("headRefOid"))
        match = re.search(r"/([^/]+)/([^/]+)/pull/\d+", view.get("url") or "")
        owner, name = match.groups() if match else ("", "")
        result = threads = None
        if view.get("headRefOid") == head:
            found = gh("api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}", "-F",
                       f"number={view.get('number')}", "-f", f"query={THREADS_QUERY}")
            if found.returncode != 0:
                return 1, {**payload, "message": found.stderr.strip()}
            try:
                threads = open_threads(json.loads(found.stdout))
            except ValueError as exc:
                return 1, {**payload, "message": f"gh api graphql: {exc}"}
            result = verdict(view, threads)
        if result is None:
            now = time.monotonic()
            if now >= deadline:
                pending = pending_checks(view) if view.get("headRefOid") == head else []
                return 1, {**payload, "state": "timeout", "pending": pending}
            time.sleep(min(poll, deadline - now))
            continue
        state = result["state"]
        payload.update(state=state, reason=result["reason"], threads=threads if state == "threads" else [])
        if state == "fail":
            failed = result["failed"]
            link = check_link(failed) or ""
            payload.update(failed_job=check_name(failed), url=link or payload["url"])
            run = re.search(r"/runs/(\d+)", link)
            if run:
                payload["first_error"] = first_error(gh("run", "view", run.group(1), "--log-failed").stdout)
        if state in ("fail", "blocked"):
            payload["stale_base"] = stale_base(f"{owner}/{name}", view.get("baseRefName") or "", head)
        return (0 if state == "pass" else 1), payload


def check_github(repo: Path) -> list[str]:
    """The "User actions required" lines of the GitHub repo of `origin`: rebase merge off, head branch delete off."""
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True, text=True)
    match = re.match(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$",
                     origin.stdout.strip()) if origin.returncode == 0 else None
    if not match:
        return []
    slug = "/".join(match.groups())
    lines = []
    allowed = gh("api", f"repos/{slug}", "--jq", ".allow_rebase_merge", timeout=10)
    if allowed.returncode == 0 and allowed.stdout.strip() == "false":
        lines.append(f"allow rebase merging in the GitHub settings of {slug} (Stage 7 runs gh pr merge --rebase)")
    if delete_waived(repo):
        return lines
    delete = gh("api", f"repos/{slug}", "--jq", ".delete_branch_on_merge", timeout=10)
    if delete.returncode == 0 and delete.stdout.strip() == "false":
        lines.append(f'turn on "Automatically delete head branches" in the GitHub settings of {slug} (Stage 7 leaves '
                     f"the merged branch on GitHub): gh api -X PATCH repos/{slug} -F delete_branch_on_merge=true, "
                     f'or waive it with "delete_branch_on_merge": false in dev-loop.json')
    return lines


def delete_waived(repo: Path) -> bool:
    ref = main_ref(repo)
    if ref:
        shown = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:dev-loop.json"], capture_output=True)
        data = shown.stdout if shown.returncode == 0 else b""
    else:
        path = repo / "dev-loop.json"
        data = path.read_bytes() if path.is_file() else b""
    try:
        config = json.loads(data)
    except ValueError:
        return False
    return isinstance(config, dict) and config.get("delete_branch_on_merge") is False


def cmd_ci_wait(args: argparse.Namespace) -> int:
    code, payload = wait(args.pr, args.timeout)
    return emit(payload, code)
