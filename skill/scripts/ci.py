"""`devloop.py ci wait`: one verdict for the PR of the local HEAD. `check_setup.py`: the GitHub settings Stage 7 needs."""

from __future__ import annotations

FAILED_CHECK_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}
BLOCKED_REASONS = {"REVIEW_REQUIRED": "review-required", "CHANGES_REQUESTED": "changes-requested"}


def check_state(item: dict) -> str:
    """`pass`, `fail` or `pending` for one `statusCheckRollup` item. An unknown kind is pending, never a pass."""
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


def open_threads(data: dict) -> list[dict]:
    """The review threads not resolved, outdated ones too, from the `gh api graphql` answer."""
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    threads = []
    for node in nodes:
        if node.get("isResolved"):
            continue
        comments = (node.get("comments") or {}).get("nodes") or [{}]
        first = comments[0] or {}
        body = first.get("body") or ""
        threads.append({"path": node.get("path"), "line": node.get("line"),
                        "author": (first.get("author") or {}).get("login"), "url": first.get("url"),
                        "text": (body.splitlines() or [""])[0][:200]})
    return threads


def verdict(view: dict, threads: list[dict]) -> dict | None:
    """Spec D4 for one pass: `state`, `reason`, `failed` (first failed check), `pending` (names), or None to poll again."""
    checks = view.get("statusCheckRollup") or []
    states = [check_state(c) for c in checks]
    failed = next((c for c, s in zip(checks, states) if s == "fail"), None)
    pending = [check_name(c) for c, s in zip(checks, states) if s == "pending"]

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
