"""`devloop.py audit`: a subagent's tool outputs against the files it must not read, and the taint records."""

from __future__ import annotations

import argparse
import sys
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import unescape

import devloop
from devloop import emit
from source_dirs import in_dot_folder, matches, read_main
from seal import ACCEPTANCE_DIR, SEARCH_WORD, git_dir, merge_base, run_git
from seal.search import (ASSIGNMENT, GREP_FLAGS, GREP_LONG, HEREDOC, KEYWORDS, PATTERN_GIVEN, RG_FLAGS, RG_LONG, parse_options,
                         shell_tokens)

SOURCE_DIRS = ("src/", "app/", "backend/", "frontend/")
SPEC_TESTER = ("spec-tester", "spec_tester")
MIN_LINE = 30
MIN_MATCHES = 2
MAX_LINES = 3
MAX_BYTES = 1024 * 1024
BINARY_PROBE = 8192
# `[feat/x abc1234] msg` (or `[main (root-commit) abc1234]`) from git commit, `"commit": "<sha>"` from devloop.py seal.
COMMIT_SHA = re.compile(r'^\[\S+(?: \([^)]*\))? ([0-9a-f]{7,64})\]|"commit": "([0-9a-f]{40,64})"', re.MULTILINE)
MCP_TOOLS = ("ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool")
TOOL_USE_ID = re.compile(r"<tool-use-id>(.*?)</tool-use-id>", re.S)
EVENT = re.compile(r"<event>(.*?)</event>", re.S)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tool_outputs(path: Path) -> tuple[list[str], list[str]] | None:
    outputs: list[str] = []
    searches: list[str] = []
    search_ids: set[str] = set()
    monitor_ids: set[str] = set()
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
                if isinstance(item, dict) and item.get("type") == "McpToolCall":
                    found = True
                    result = item.get("result")
                    outputs.append(result_text(result.get("content")) if isinstance(result, dict) else "")
                    searches.append(outputs[-1])
                if isinstance(item, dict) and item.get("type") == "CommandExecution":
                    found = True
                    outputs.append(str(item.get("aggregated_output") or ""))
                    command = item.get("command")
                    if reads_files(" ".join(map(str, command)) if isinstance(command, list) else str(command or "")):
                        searches.append(outputs[-1])
            if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
                found = True
                outputs.append(result_text(payload.get("output")))
            note = entry.get("attachment")
            if (entry.get("type") == "attachment" and isinstance(note, dict) and note.get("type") == "queued_command"
                    and note.get("commandMode") == "task-notification" and isinstance(note.get("prompt"), str)):
                call_id = TOOL_USE_ID.search(note["prompt"])
                if call_id and call_id.group(1) in monitor_ids:
                    found = True
                    for event in map(unescape, EVENT.findall(note["prompt"])):
                        outputs.append(event)
                        if call_id.group(1) in search_ids:
                            searches.append(event)
            message = entry.get("message")
            if entry.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("content"), list):
                for item in message["content"]:
                    if isinstance(item, dict) and item.get("type") == "tool_use" and is_search(item):
                        search_ids.add(str(item.get("id")))
                    if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "Monitor":
                        monitor_ids.add(str(item.get("id")))
            if entry.get("type") == "user" and isinstance(message, dict) and isinstance(message.get("content"), list):
                for item in message["content"]:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        found = True
                        outputs.append(result_text(item.get("content")))
                        if str(item.get("tool_use_id")) in search_ids:
                            searches.append(outputs[-1])
    return (outputs, searches) if found else None


def is_search(call: dict) -> bool:
    tool_input = call.get("input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    name = str(call.get("name"))
    return (name in ("Grep", "Glob", "LSP", *MCP_TOOLS) or name.startswith("mcp__")
            or (name in ("Bash", "Monitor") and isinstance(command, str) and reads_files(command)))


def is_filter(name: str, args: list[str]) -> bool:
    grep = name in ("grep", "egrep", "fgrep")
    options, operands, _ = parse_options(args, "efmABCdD" if grep else "efgtTmABCjMrdE", GREP_LONG if grep else RG_LONG,
                                         GREP_FLAGS if grep else RG_FLAGS)
    names = {option for option, _ in options}
    if "?" in names or names & {"-r", "-R", "-d", "--recursive", "--dereference-recursive", "--directories", "--files"}:
        return False
    return len(operands) <= (0 if names & PATTERN_GIVEN else 1)


def reads_files(command: str) -> bool:
    if not SEARCH_WORD.search(command):
        return False
    try:
        tokens = shell_tokens(HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", command))
    except ValueError:
        return True
    words: list[str] = []
    piped = False
    for kind, text in [*tokens, ("op", ";")]:
        if kind == "word":
            words.append(text)
            continue
        while words and (words[0] in KEYWORDS or ASSIGNMENT.match(words[0])):
            words = words[1:]
        if words and SEARCH_WORD.search(" ".join(words)):
            name = words[0].rsplit("/", 1)[-1]
            if name in ("bash", "sh", "zsh") and len(words) >= 3 and re.fullmatch(r"-\w*c", words[1]):
                if reads_files(words[2]):
                    return True
            elif not (piped and name in ("grep", "egrep", "fgrep", "rg") and is_filter(name, words[1:])):
                return True
        words, piped = [], text in ("|", "|&")
    return False


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


def matched_lines(repo: Path, protected: tuple[str, ...] | Callable[[str], bool], outputs: list[str],
                  skip_dot_folders: bool = False) -> list[str]:
    listed = [path for path in dict.fromkeys(run_git(repo, "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0"))
              if path and not (skip_dot_folders and in_dot_folder(path))]
    is_own = protected if callable(protected) else lambda path: path.startswith(protected)
    own = {path: text for path in listed if is_own(path) and (text := read_text(repo / path)) is not None}
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


def suite_paths(repo: Path, outputs: list[str]) -> list[str]:
    listed = run_git(repo, "ls-files", "-co", "--exclude-standard", "-z", "--", f":(top){ACCEPTANCE_DIR}/").stdout
    text = "\n".join(outputs)
    found = []
    for path in sorted(set(filter(None, listed.split("\0")))):
        rest = re.escape(path[len(ACCEPTANCE_DIR) + 1:])
        suite = re.escape(ACCEPTANCE_DIR)
        if re.search(rf"^(?:\S*/)?{suite}/{rest}(?=$|[:\-\0\s])|^(?:\./)?acceptance/{rest}(?=$|[:\-\0\s])", text, re.M):
            found.append(path)
    return found[:MAX_LINES]


def patch_ids(repo: Path, *diff: str) -> list[tuple[str, str]]:
    """`git patch-id --stable` of a `git <diff...>` output: (patch id, commit) pairs. Bytes: any file encoding."""
    git = ["git", "-C", str(repo), "-c", "core.quotepath=off"]
    patch = subprocess.run([*git, *diff, "--no-color", "--no-ext-diff", "--no-textconv"], capture_output=True).stdout
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
        found = tool_outputs(transcript)
        if found is None:
            return skip("no-tool-output", f"no tool output in {transcript}")
        outputs, searches = found
        source = agent_type in SPEC_TESTER
        main = read_main(repo) if source else None
        if main and main.state == "configured":
            entries = [entry for entry in main.entries if not in_dot_folder(entry)]
            lines = matched_lines(repo, lambda path: matches(entries, path) is not None, outputs, skip_dot_folders=True)
        else:
            lines = matched_lines(repo, SOURCE_DIRS if source else (ACCEPTANCE_DIR + "/",), outputs)
        paths = [] if source else suite_paths(repo, searches)
        if len(lines) < MIN_MATCHES and not paths:
            return {"taint": None, "skipped": []}
        head_file = git_dir(repo) / "dev-loop-subagents" / f"{agent_id}.head"
        # No .head (D2): from `<base>`, else the root, never an empty range that closes the record.
        start = head_file.read_text().strip() if agent_id and head_file.is_file() else merge_base(repo)
        shas, attribution = tainted_commits(repo, start, end, outputs)
        record = {**base, "set": "source" if source else "acceptance", "lines": lines, "paths": paths, "start": start,
                  "end": end,
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


def cmd_audit(args: argparse.Namespace) -> int:
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    if not isinstance(hook, dict):  # An audit error is a warning: exit 0, as on every skip.
        return emit(unparsed(Path.cwd()), 0)
    return emit(audit(hook), 0)
