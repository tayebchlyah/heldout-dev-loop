"""`devloop.py audit`: a subagent's tool outputs against the files it must not read, and the taint records."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from seal import ACCEPTANCE_DIR, git_dir, merge_base, run_git

SOURCE_DIRS = ("src/", "app/", "backend/", "frontend/")
SPEC_TESTER = ("spec-tester", "spec_tester")
MIN_LINE = 30
MIN_MATCHES = 2
MAX_LINES = 3
MAX_BYTES = 1024 * 1024
BINARY_PROBE = 8192
# `[feat/x abc1234] msg` (or `[main (root-commit) abc1234]`) from git commit, `"commit": "<sha>"` from devloop.py seal.
COMMIT_SHA = re.compile(r'^\[\S+(?: \([^)]*\))? ([0-9a-f]{7,64})\]|"commit": "([0-9a-f]{40,64})"', re.MULTILINE)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tool_outputs(path: Path) -> list[str] | None:
    """Claude `tool_result` texts of `type:"user"` lines, Codex `aggregated_output` of `CommandExecution` items."""
    outputs: list[str] = []
    found = False
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # A broken or half-flushed line: the others still count.
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "item_completed":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "CommandExecution":
                    found = True
                    outputs.append(str(item.get("aggregated_output") or ""))
            message = entry.get("message")
            if entry.get("type") == "user" and isinstance(message, dict) and isinstance(message.get("content"), list):
                for item in message["content"]:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        found = True
                        outputs.append(result_text(item.get("content")))
    return outputs if found else None


def result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def read_text(path: Path) -> str | None:
    """The text of a regular file, None for a binary file (a NUL byte in the first 8 KB) or one over 1 MB."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data[:BINARY_PROBE] else data.decode("utf-8", errors="replace")


def feature_docs(repo: Path) -> list[str]:
    """The feature's spec and plan, from the branch `<prefix>/<slug>`. Best effort: any failure gives none."""
    try:
        branch = run_git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        if "/" not in branch:
            return []
        slug = branch.split("/", 1)[1].lower()
        import devloop  # Lazy: devloop.py imports this package lazily too.
        from notes_path import resolve
        notes = resolve(repo, os.environ, Path.home())
        docs = []
        for name, spec in devloop.specs(notes):
            if name == slug:
                docs.append(spec)
                plan = devloop.find_plan(notes, spec)
                if plan:
                    docs.append(plan)
        return [text for text in (read_text(doc) for doc in docs) if text]
    except Exception:
        return []


def matched_lines(repo: Path, protected: tuple[str, ...], outputs: list[str]) -> list[str]:
    listed = [path for path in dict.fromkeys(run_git(repo, "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0"))
              if path]
    own = {path: text for path in listed if path.startswith(protected) and (text := read_text(repo / path)) is not None}
    output = "\n".join(outputs)
    # Output lines first, in file then line order: the costly checks below see only this small set.
    # shortcut: `line in output` per protected line, O(lines x output size); Aho-Corasick if a hook times out.
    seen = [line for line in dict.fromkeys(raw.strip() for text in own.values() for raw in text.splitlines())
            if len(line) >= MIN_LINE and line in output and sum(line in text for text in own.values()) == 1]
    if len(seen) < MIN_MATCHES:
        return []  # The other files are never read: no taint is possible.
    others = feature_docs(repo) + [text for path in listed if path not in own and (text := read_text(repo / path))]
    # Distinctive: in one protected file only, and in no other file, spec or plan.
    return [line for line in seen if not any(line in text for text in others)][:MAX_LINES]


def patch_ids(repo: Path, *diff: str) -> list[tuple[str, str]]:
    """`git patch-id --stable` of a `git <diff...>` output: (patch id, commit) pairs. Bytes: any file encoding."""
    git = ["git", "-C", str(repo), "-c", "core.quotepath=off"]
    patch = subprocess.run([*git, *diff, "--no-color", "--no-ext-diff"], capture_output=True).stdout
    out = subprocess.run([*git, "patch-id", "--stable"], input=patch, capture_output=True).stdout.decode()
    return [tuple(line.split()[:2]) for line in out.splitlines() if line.strip()]  # type: ignore[misc]


def commit_record(repo: Path, sha: str) -> dict:
    parent = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^1").stdout.strip() or \
        run_git(repo, "hash-object", "-t", "tree", "--stdin", stdin="").stdout.strip()
    forward = patch_ids(repo, "diff", parent, sha)
    inverse = patch_ids(repo, "diff", sha, parent)
    return {"sha": sha, "patch_id": forward[0][0] if forward else None,
            "inverse_patch_id": inverse[0][0] if inverse else None}


def tainted_commits(repo: Path, start: str | None, end: str, outputs: list[str]) -> tuple[list[str], str]:
    """D24: the commits of start..end whose sha is in the subagent's own output, else all of them.

    No start: all of the history of end."""
    in_range = run_git(repo, "rev-list", "--reverse", f"{start}..{end}" if start else end).stdout.split() \
        if start != end else []
    shown = {a or b for text in outputs for a, b in COMMIT_SHA.findall(text)}
    own = [sha for sha in in_range if any(sha.startswith(short) for short in shown)]
    return (own, "transcript") if own else (in_range, "range" if in_range else "transcript")


def taint_file(repo: Path) -> Path:
    return git_dir(repo) / "dev-loop" / "taint.json"


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"records": data.get("records", []), "skipped": data.get("skipped", [])}


def append(repo: Path, key: str, entry: dict) -> None:
    """Add one entry under the lock (parallel SubagentStop hooks), written through a temp file and a rename.

    An entry of the same agent under the same key is replaced: each re-stop audits the whole transcript again."""
    path = taint_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name("taint.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path)
        agent_id = entry.get("agent_id")
        data[key] = [old for old in data[key] if agent_id is None or old.get("agent_id") != agent_id] + [entry]
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".taint.")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        os.replace(tmp, path)


def audit(hook: dict) -> dict:
    agent_id, agent_type = hook.get("agent_id"), hook.get("agent_type")
    repo = Path(hook.get("cwd") or ".")
    top = run_git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return {"taint": None, "skipped": [{"agent_id": agent_id, "agent_type": agent_type, "at": now(),
                                            "reason": "error", "message": f"not a git worktree: {repo}"}]}
    repo = Path(top.stdout.strip())
    end = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    base = {"agent_id": agent_id, "agent_type": agent_type, "at": now()}

    def skip(reason: str, message: str) -> dict:
        entry = {**base, "reason": reason, "message": message, "end": end}
        try:
            append(repo, "skipped", entry)
        except Exception:
            pass  # An unreadable taint.json: the skip still reaches the hook output.
        return {"taint": None, "skipped": [entry]}

    try:
        transcript = Path(hook.get("agent_transcript_path") or "")
        if not hook.get("agent_transcript_path") or not transcript.is_file():
            return skip("no-transcript", f"no transcript file: {hook.get('agent_transcript_path')}")
        outputs = tool_outputs(transcript)
        if outputs is None:
            return skip("no-tool-output", f"no tool output in {transcript}")
        source = agent_type in SPEC_TESTER
        lines = matched_lines(repo, SOURCE_DIRS if source else (ACCEPTANCE_DIR + "/",), outputs)
        if len(lines) < MIN_MATCHES:
            return {"taint": None, "skipped": []}
        head_file = git_dir(repo) / "dev-loop-subagents" / f"{agent_id}.head"
        # No .head (D2): from `<base>`, else the root, never an empty range that closes the record.
        start = head_file.read_text().strip() if agent_id and head_file.is_file() else merge_base(repo)
        shas, attribution = tainted_commits(repo, start, end, outputs)
        record = {**base, "set": "source" if source else "acceptance", "lines": lines, "start": start, "end": end,
                  "commits": [commit_record(repo, sha) for sha in shas], "attribution": attribution}
        append(repo, "records", record)
        return {"taint": record, "skipped": []}
    except Exception as exc:  # An audit error is a warning, never a block.
        return skip("error", f"{type(exc).__name__}: {exc}")


def unparsed(cwd: Path) -> dict:
    """The skip for hook input that is no JSON object, written (best effort) to the repo of the process cwd."""
    entry = {"reason": "error", "message": "hook input is not a JSON object"}
    top = run_git(cwd, "rev-parse", "--show-toplevel")
    if top.returncode == 0:
        repo = Path(top.stdout.strip())
        entry = {"agent_id": None, "agent_type": None, "at": now(), **entry,
                 "end": run_git(repo, "rev-parse", "HEAD").stdout.strip()}
        try:
            append(repo, "skipped", entry)
        except Exception:
            pass  # An unreadable taint.json: the skip still reaches the hook output.
    return {"taint": None, "skipped": [entry]}


def taint_state(repo: Path) -> dict:
    """Each record open or closed against the patch ids of `<base>..HEAD`, and the audit skips."""
    data = load(taint_file(repo))
    base = merge_base(repo)
    # No base: the full HEAD history, so a tainted commit stays open.
    counts = Counter(pid for pid, _ in patch_ids(repo, "log", "-p", "--format=commit %H", f"{base}..HEAD" if base else "HEAD"))
    state: dict[str, list] = {"open": [], "closed": [], "skipped": data["skipped"]}
    for record in data["records"]:
        is_open = any(c["patch_id"] and counts[c["patch_id"]] > counts[c["inverse_patch_id"]] for c in record["commits"])
        state["open" if is_open else "closed"].append(record)
    return state
