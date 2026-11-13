"""`devloop.py mutation`: the changed critical lines of each `mutation` entry of dev-loop.json."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
import subprocess
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path, PurePosixPath

import source_dirs
import stacks
from devloop import emit

ADAPTERS: tuple[str, ...] = ("mutmut", "stryker", "mutago")
ENTRY_KEYS = frozenset({"path", "critical", "adapter", "command"})
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
DIFF = ("diff", "--no-ext-diff", "--no-color", "-U0", "-M", "--diff-filter=AMR", "--src-prefix=a/",
        "--dst-prefix=b/", "--ignore-submodules")

PY_STRING = re.compile(r"""[rRbBuUfF]{0,2}('''|\"\"\"|'|")(?:\\.|(?!\1).)*\1""", re.S)
GO_STRING = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`')
JS_STRING = re.compile(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`""", re.S)
GO_LOG = r"\b(?:log|slog)\.\w+\s*\("
JS_LOG = r"\bconsole\.\w+\s*\("
CLASSIFIERS = {
    **dict.fromkeys((".py", ".pyi"), (
        PY_STRING,
        re.compile(r"\b(?:log(?:ger)?|logging|print|warnings?|console)\b[\w.]*\s*\("),
        re.compile(r"^\s*raise\b|^\s*assert\b.*,|\b(?:detail|message|msg|description|help|reason|summary)\s*=(?!=)"),
    )),
    ".go": (GO_STRING, re.compile(GO_LOG), re.compile(rf"\berrors\.New\(|\bfmt\.Errorf\(|\bpanic\(|{GO_LOG}")),
    **dict.fromkeys((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"), (
        JS_STRING,
        re.compile(JS_LOG),
        re.compile(rf"\bthrow\b|\bnew Error\(|{JS_LOG}|\b(?:message|msg|reason|description)\s*:"),
    )),
}


def classify(file: str, diff: str | None) -> str:
    rules = CLASSIFIERS.get(PurePosixPath(file).suffix)
    if rules is None or diff is None:
        return "decision"
    string, log_call, context = rules
    rows = [row for row in diff.splitlines() if row.startswith(("-", "+")) and not row.startswith(("---", "+++"))]
    if not rows:
        return "decision"
    texts = [row[1:] for row in rows]
    if all(log_call.search(text) for text in texts):
        return "message"

    def blank(sign: str) -> list[str]:
        return [string.sub('""', row[1:]).strip() for row in rows if row[0] == sign]

    if blank("-") == blank("+") and all(context.search(text) for text in texts):
        return "message"
    return "decision"


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def validate(value: object) -> str | None:
    if value is False:
        return None
    if not isinstance(value, list):
        return "must be false or a list of entries"
    seen: list[str] = []
    for entry in value:
        shown = json.dumps(entry, ensure_ascii=False)
        if not isinstance(entry, dict) or not {"path", "critical"} <= set(entry) <= ENTRY_KEYS:
            return f"{shown} must hold the keys path, critical and one of adapter, command"
        if ("adapter" in entry) == ("command" in entry):
            return f"{shown} must hold exactly one of adapter and command"
        for key in ("adapter", "command"):
            if key in entry and not _non_empty(entry[key]):
                return f"{key} of {shown} must be a non-empty string"
        path = entry["path"]
        if path != "." and (rule := source_dirs.validate([path])):
            return rule
        critical = entry["critical"]
        if not isinstance(critical, list) or not critical or not all(_non_empty(g) for g in critical):
            return f"critical of {shown} must be a non-empty list of non-empty strings"
        if all(g.startswith("!") for g in critical):
            return f"critical of {shown} needs one glob without !"
        folded = source_dirs.fold(path)
        if folded in seen:
            return f"{json.dumps(path, ensure_ascii=False)} repeats another entry"
        seen.append(folded)
    return None


@lru_cache(maxsize=None)
def _segment(pattern: str) -> re.Pattern:
    return re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern),
                      re.DOTALL)


def glob_match(path: str, pattern: str) -> bool:
    parts, globs = tuple(path.split("/")), tuple(pattern.split("/"))

    @lru_cache(maxsize=None)
    def walk(i: int, j: int) -> bool:
        if j == len(globs):
            return i == len(parts)
        if globs[j] == "**":
            return walk(i, j + 1) or (i < len(parts) and walk(i + 1, j))
        return i < len(parts) and _segment(globs[j]).fullmatch(parts[i]) and walk(i + 1, j + 1)

    return walk(0, 0)


def in_critical(path: str, globs: list[str]) -> bool:
    hit = any(glob_match(path, g) for g in globs if not g.startswith("!"))
    return hit and not any(glob_match(path, g[1:]) for g in globs if g.startswith("!"))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _unquote(text: str) -> str:
    raw, i = bytearray(), 1
    while i < len(text) - 1:
        ch = text[i]
        if ch != "\\":
            raw += ch.encode()
            i += 1
        elif text[i + 1] in "01234567":
            raw.append(int(text[i + 1:i + 4], 8))
            i += 4
        else:
            raw.append(ESCAPES.get(text[i + 1], ord(text[i + 1])))
            i += 2
    return raw.decode("utf-8", errors="replace")


def _target(rest: str) -> str:
    if rest.startswith('"'):
        return _unquote(rest).removeprefix("b/")
    return rest.removesuffix("\t").removeprefix("b/")


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def changed_lines(repo: Path, base: str, head: str) -> dict[str, list[list[int]]]:
    done = _git(repo, "-c", "core.quotePath=false", *DIFF, f"{base}..{head}")
    found: dict[str, list[list[int]]] = {}
    path: str | None = None
    in_hunk = False
    for line in done.stdout.decode("utf-8", errors="replace").split("\n"):
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif in_hunk and not line.startswith("@@"):
            continue
        elif not in_hunk and line.startswith("+++ "):
            path = _target(line[4:])
        elif match := HUNK.match(line):
            in_hunk = True
            start, count = int(match[1]), int(match[2] if match[2] is not None else 1)
            span = [max(start, 1), max(start, 1)] if count == 0 else [start, start + count - 1]
            if path is not None:
                found.setdefault(path, []).append(span)
    return {name: _merge(ranges) for name, ranges in found.items()}


def _resolve(repo: Path, rev: str) -> str | None:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return done.stdout.decode().strip() if done.returncode == 0 else None


def _read_config(repo: Path, head: str) -> dict | str:
    shown = _git(repo, "show", f"{head}:dev-loop.json")
    if shown.returncode != 0:
        return {}
    try:
        data = json.loads(shown.stdout)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else f"dev-loop.json at {head} is not a JSON object"


def _setup(cause: str) -> int:
    return emit({"status": "setup", "cause": cause}, 1)


def _check_entries(repo: Path, head: str, value: list) -> str | None:
    for entry in value:
        name = entry.get("adapter")
        if name is not None and name not in ADAPTERS:
            return f"mutation entry {entry['path']}: unknown adapter {name}"
    for entry in value:
        if entry["path"] != "." and _git(repo, "cat-file", "-t", f"{head}:{entry['path']}").stdout.strip() != b"tree":
            return f"mutation in dev-loop.json is invalid: {entry['path']} is not a folder at {head}"
    return None


def _holds(entry_path: str, path: str) -> bool:
    return entry_path == "." or path.startswith(entry_path)


def _select(entries: list[dict], lines: dict[str, list[list[int]]]) -> tuple[list[dict], list[str]]:
    selected = [{"path": e["path"], **{k: e[k] for k in ("adapter", "command") if k in e}, "files": {}}
                for e in entries]
    outside: list[str] = []
    for path, ranges in sorted(lines.items()):
        owners = [i for i, e in enumerate(entries) if _holds(e["path"], path)]
        if not owners:
            continue
        owner = max(owners, key=lambda i: 0 if entries[i]["path"] == "." else len(entries[i]["path"]))
        folder = entries[owner]["path"]
        rel = path if folder == "." else path[len(folder):]
        if in_critical(rel, entries[owner]["critical"]):
            selected[owner]["files"][rel] = ranges
        else:
            outside.append(path)
    return selected, outside


def _uncovered(config: dict, entries: list[dict]) -> list[str]:
    listed = config.get("stacks")
    if not isinstance(listed, list) or stacks.validate(listed):
        return []
    return [s["path"] for s in listed
            if not any(_holds(source_dirs.fold(e["path"]), source_dirs.fold(s["path"])) for e in entries)]


def _argv(entry: dict) -> list[str]:
    if "adapter" in entry:
        return [sys.executable, str(Path(__file__).resolve().parent / f"{entry['adapter']}.py")]
    return ["sh", "-c", entry["command"]]


def _clean(text: str) -> str:
    return "".join(" " if unicodedata.category(c) == "Cc" else c for c in text)


def _cause(text: str) -> str:
    return _clean(text).strip()[:200]


def _call(folder: Path, argv: list[str], request: dict) -> tuple[int, bytes, str]:
    proc = subprocess.Popen(argv, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    last = [""]

    def feed() -> None:
        try:
            proc.stdin.write(json.dumps(request).encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass

    def copy() -> None:
        for raw in proc.stderr:
            sys.stderr.buffer.write(raw)
            sys.stderr.buffer.flush()
            if line := raw.decode("utf-8", errors="replace").strip():
                last[0] = line

    threads = [threading.Thread(target=feed), threading.Thread(target=copy)]
    for thread in threads:
        thread.start()
    stdout = proc.stdout.read()
    proc.wait()
    for thread in threads:
        thread.join()
    return proc.returncode, stdout, last[0]


STATUSES = ("killed", "survived", "no-tests", "inconclusive")
UNIT_RULE = "is not a unit (a non-empty string, no control character, no leading -)"


def _unit(value: object) -> bool:
    return (_non_empty(value) and not value.startswith("-")
            and not any(unicodedata.category(c) == "Cc" for c in value))


def _count(value: object) -> bool:
    return type(value) is int and value >= 1


def _file_fault(item: dict, files: dict) -> str | None:
    file = item["file"]
    return None if isinstance(file, str) and file in files else f"file {json.dumps(file)} is not a selected file"


def _mutant_fault(m: object, files: dict, units: list, only: list | None) -> str | None:
    if not isinstance(m, dict):
        return "is not a JSON object"
    if missing := [k for k in ("unit", "file", "line", "status", "diff") if k not in m]:
        return f"has no {missing[0]}"
    if not _unit(m["unit"]):
        return f"unit {json.dumps(m['unit'])} {UNIT_RULE}"
    if fault := _file_fault(m, files):
        return fault
    if not _count(m["line"]):
        return f"line {json.dumps(m['line'])} is not an integer of 1 or more"
    if m["status"] not in STATUSES:
        return f"status {json.dumps(m['status'])} is not one of {', '.join(STATUSES)}"
    diff = m["diff"]
    if diff is not None and not isinstance(diff, str):
        return "diff must be null or a string"
    rows = [r for r in diff.split("\n") if not r.startswith(("-", "+", " "))] if diff is not None else []
    if rows:
        return f"diff row {json.dumps(rows[0])} does not start with -, + or a space"
    if "detail" in m and not isinstance(m["detail"], str):
        return "detail must be a string"
    if m["unit"] not in units:
        return f"unit {m['unit']} is not in units"
    if only is not None and m["unit"] not in only:
        return f"unit {m['unit']} is not in only"
    return None


def _gap_fault(gap: object, files: dict) -> str | None:
    if not isinstance(gap, dict):
        return "is not a JSON object"
    if missing := [k for k in ("file", "start", "end", "reason") if k not in gap]:
        return f"has no {missing[0]}"
    if fault := _file_fault(gap, files):
        return fault
    if not (_count(gap["start"]) and _count(gap["end"])):
        return f"start {json.dumps(gap['start'])} and end {json.dumps(gap['end'])} must be integers of 1 or more"
    if gap["start"] > gap["end"]:
        return f"start {gap['start']} is after end {gap['end']}"
    if not _non_empty(gap["reason"]):
        return "reason must be a non-empty string"
    return None


def check_output(text: str, request: dict) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, "adapter output is not one JSON object"
    required = ("units", "mutants", "not_covered") if request["run"] else ("units",)
    for key in ("units", "mutants", "not_covered"):
        if (key in required or key in data) and not isinstance(data.get(key), list):
            return None, f"adapter output {key} must be a list"
    if bad := [u for u in data["units"] if not _unit(u)]:
        return None, f"adapter output units: {json.dumps(bad[0])} {UNIT_RULE}"
    files = request["files"]
    named: set[str] = set()
    for i, m in enumerate(data.get("mutants", [])):
        fault = _mutant_fault(m, files, data["units"], request["only"])
        if fault:
            return None, f"adapter mutant {i}: {fault}"
        named.add(m["file"])
    for i, gap in enumerate(data.get("not_covered", [])):
        fault = _gap_fault(gap, files)
        if fault:
            return None, f"adapter not_covered {i}: {fault}"
        named.add(gap["file"])
    if request["run"] and (missing := sorted(set(files) - named)):
        return None, f"adapter output names no mutant and no not_covered entry for {missing[0]}"
    return data, None


def _needs(mutant: dict) -> bool:
    return mutant["status"] == "no-tests" or (
        mutant["status"] == "survived" and classify(mutant["file"], mutant["diff"]) == "decision")


def _needs_test(output: dict) -> set[str]:
    return {m["unit"] for m in output.get("mutants", []) if _needs(m)}


def run_entry(repo: Path, entry: dict, request: dict) -> dict:
    folder = repo if entry["path"] == "." else repo / entry["path"]
    code, stdout, last = _call(folder, _argv(entry), request)
    if code != 0:
        status = "setup" if code in (2, 126, 127) else "failed"
        return {"status": status, "cause": _cause(last or f"exit {code}"), "output": None}
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    output, fault = check_output(text, request)
    if fault:
        return {"status": "setup", "cause": _cause(fault), "output": None}
    return {"status": "survivors" if _needs_test(output) else "pass", "cause": None, "output": output}


ORDER = ("setup", "failed", "survivors", "pass")


def _key(mutant: dict) -> tuple:
    return mutant["file"], mutant["line"], mutant["diff"] or ""


def _diff_rows(mutant: dict) -> list[str]:
    return [] if mutant["diff"] is None else [f"      {row}" for row in mutant["diff"].split("\n")]


def _entry_report(entry: dict, result: dict) -> list[str]:
    path = entry["path"]
    label = entry.get("adapter") or entry["command"]
    prefix = "" if path == "." else path
    output = result["output"] or {"mutants": [], "not_covered": []}
    mutants = sorted(output["mutants"], key=_key)
    listed: dict[str, list[dict]] = {}
    message, inconclusive = [], {}
    for m in mutants:
        if m["status"] == "inconclusive":
            name = _clean(m.get("detail") or "inconclusive")
            inconclusive[name] = inconclusive.get(name, 0) + 1
        elif _needs(m):
            listed.setdefault(m["unit"], []).append(m)
        elif m["status"] == "survived":
            message.append(m)
    lines = [f"entry {path}: {_clean(label)} {result['status']}", f"{path} mutants run: {len(mutants)}",
             f"{path} units that need one unit test: {len(listed)}"]
    for unit, rows in sorted(listed.items(), key=lambda item: (_key(item[1][0]), item[0])):
        lines.append(f"  {unit}")
        for m in rows:
            word = "survived" if m["status"] == "survived" else "no tests"
            lines += [f"    {prefix}{m['file']}:{m['line']} {word}", *_diff_rows(m)]
    lines.append(f"{path} message-only survivors, to Deferred: {len(message)}")
    for m in message:
        lines += [f"  {m['unit']} {prefix}{m['file']}:{m['line']}", *_diff_rows(m)]
    lines.append(f"{path} inconclusive: {sum(inconclusive.values())}")
    lines += [f"  {name}: {count}" for name, count in sorted(inconclusive.items())]
    gaps = sorted(output["not_covered"], key=lambda g: (g["file"], g["start"], g["end"]))
    lines.append(f"{path} not covered by the selection: {len(gaps)}")
    lines += [f"  {prefix}{g['file']}:{g['start']}-{g['end']} {_clean(g['reason'])}" for g in gaps]
    if result["cause"]:
        lines.append(f"{path} {result['status']}: {result['cause']}")
    return lines


def _report(base: str, head: str, selected: list[dict], results: list[dict], outside: list[str],
            none: list[str]) -> str:
    lines = [f"base: {base}", f"head: {head}"]
    for entry, result in zip(selected, results):
        lines += _entry_report(entry, result)
    lines.append(f"outside the critical modules: {len(outside)}")
    lines += [f"  {path}" for path in outside]
    lines += [f"mutation: none for {path}" for path in none]
    return "\n".join(lines)


def _run(repo: Path, base: str, head: str, selected: list[dict], outside: list[str], none: list[str]) -> int:
    results = []
    for entry in selected:
        if not entry["files"]:
            results.append({"status": "pass", "cause": None, "output": None})
            continue
        request = {"protocol": 1, "run": True, "base": base, "head": head, "files": entry["files"], "only": None}
        results.append(run_entry(repo, entry, request))
    rows = [{"path": e["path"], "status": r["status"], "cause": r["cause"],
             "mutants": len(r["output"]["mutants"]) if r["output"] else 0,
             "need_tests": len(_needs_test(r["output"])) if r["output"] else 0} for e, r in zip(selected, results)]
    status = min((r["status"] for r in rows), key=ORDER.index, default="pass")
    report = _report(base, head, selected, results, outside, none)
    return emit({"status": status, "base": base, "head": head, "report": report, "entries": rows,
                 "outside": outside, "none": none}, 0 if status in ("pass", "survivors") else 1)


def cmd_mutation(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    head = _resolve(repo, args.head)
    if head is None:
        return emit({"error": f"--head: {args.head} is not a commit"}, 2)
    if args.base:
        base = _resolve(repo, args.base)
    else:
        merged = _git(repo, "merge-base", args.main, head)
        base = merged.stdout.decode().strip() if merged.returncode == 0 else None
    if base is None:
        return emit({"error": "--base: no base commit (give --base or a --main branch that exists)"}, 2)
    config = _read_config(repo, head)
    if isinstance(config, str):
        return _setup(config)
    value = config.get("mutation", False)
    rule = validate(value)
    if rule:
        return _setup(f"mutation in dev-loop.json is invalid: {rule}")
    entries = value or []
    if args.run_adapters and not entries:
        return emit({"status": "pass", "base": base, "head": head, "report": "mutation: off", "entries": [],
                     "outside": [], "none": []}, 0)
    if cause := _check_entries(repo, head, entries):
        return _setup(cause)
    selected, outside = _select(entries, changed_lines(repo, base, head) if entries else {})
    if args.run_adapters:
        return _run(repo, base, head, selected, outside, _uncovered(config, entries))
    return emit({"base": base, "head": head, "entries": selected, "outside": outside,
                 "none": _uncovered(config, entries) if entries else []}, 0)
