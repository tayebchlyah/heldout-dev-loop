#!/usr/bin/env python3
"""`devloop.py second-review`: the other model family's CLI as a read-only reviewer. The locks and the decision
order (D2, D4, D6 rules 1 to 11), the export, sweep and CLI lookup (D7, D9), the child run, its record and its output
audit (D8, D10 to D12)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import devloop
from seal import audit, merge_base

REVIEWER = {"claude": "codex", "codex": "claude"}
EFFORTS = {"codex": ("minimal", "low", "medium", "high", "xhigh"), "claude": ("low", "medium", "high", "xhigh", "max")}
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*")
PREFIX = "devloop-second-review-"
MAX_BLOB = 1_048_576
DIFF = ".second-review.diff"
NAMES = {"agents.md", "agents.override.md", "claude.md", "claude.local.md", ".mcp.json"}
FOLDERS = {".claude", ".codex", ".agents"}
PROMPT = Path(__file__).resolve().parent.parent / "references" / "second-review.md"
ENV_KEYS = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
CODEX_FILESYSTEM = ('permissions.devloop-review.filesystem={":minimal"="read", ":project_roots"="read", '
                    '":tmpdir"="none", ":slash_tmp"="none"}')


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def load_unique(data: bytes | str) -> object:
    """JSON with duplicate keys refused. Bytes must be UTF-8."""
    return json.loads(data.decode("utf-8") if isinstance(data, bytes) else data, object_pairs_hook=_pairs)


def project_lock(repo: Path, ref: str) -> bool | None:
    """`second_review` of `dev-loop.json` at `ref`: False when the file or key is absent, None when bad."""
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:dev-loop.json"], capture_output=True)
    if shown.returncode != 0:
        return False
    try:
        config = load_unique(shown.stdout)
    except ValueError:
        return None
    if not isinstance(config, dict):
        return None
    value = config.get("second_review", False)
    return value if isinstance(value, bool) else None


def _entry_ok(entry: object, reviewer: str) -> bool:
    if entry is False:
        return True
    if not isinstance(entry, dict) or set(entry) - {"model", "effort"}:
        return False
    model = entry.get("model")
    return ((model is None or (isinstance(model, str) and MODEL.fullmatch(model) is not None))
            and entry.get("effort", "high") in EFFORTS[reviewer])


def local_entry(value: object, reviewer: str) -> tuple[str, dict | None]:
    """The D4 rules on the local `second_review` value."""
    if value is False:
        return "user-off", None
    if not isinstance(value, dict) or set(value) - set(EFFORTS) or not all(_entry_ok(v, k) for k, v in value.items()):
        return "bad-config", None
    entry = value.get(reviewer)
    if entry is None:
        return "not-chosen", None
    if entry is False:
        return "user-off", None
    return "ok", {"model": entry.get("model"), "effort": entry.get("effort", "high")}


def main_worktree(repo: Path) -> Path:
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    return Path(common.stdout.strip()).parent if common.returncode == 0 else repo.resolve()


def payload(**fields: object) -> dict:
    out: dict = {"state": None, "reason": None, "message": None, "reviewer": None, "head": None, "base": None,
                 "model": None, "effort": None, "endpoint": "default", "output": None, "files": 0, "left_out": 0,
                 "seconds": 0, "tokens": None, "record": None}
    out.update(fields)
    return out


def sweep(tmp: Path, now: float) -> None:
    """Removes `devloop-second-review-*` folders in `tmp` older than 24 hours."""
    for folder in tmp.glob(PREFIX + "*"):
        try:
            if folder.is_dir() and not folder.is_symlink() and now - folder.lstat().st_mtime > 24 * 3600:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def left_out(path: str) -> bool:
    """The D7 name rules, case-folded."""
    parts = path.casefold().split("/")
    name = parts[-1]
    return (parts[:2] == ["tests", "acceptance"] or name in NAMES or bool(FOLDERS.intersection(parts[:-1]))
            or name == ".env" or name.startswith(".env."))


def export(repo: Path, ref: str, sha: str, dest: Path) -> tuple[list[str], int]:
    """The tree of `sha` into `dest`, less the D7 paths, then `.second-review.diff` of `ref...sha`. Raises on a failure."""
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=dest, capture_output=True).returncode == 0:
        raise RuntimeError(f"export folder is inside a git work tree: {dest}")
    listed = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", "-l", "--full-tree", sha], capture_output=True)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {listed.stderr.decode(errors='replace').strip()}")
    keep, left, seen, dirs = [], 0, set(), set()
    for record in listed.stdout.split(b"\0"):
        if not record:
            continue
        meta, raw = record.split(b"\t", 1)
        mode, _, obj, size = meta.split()
        path = os.fsdecode(raw)
        folded = path.casefold()
        prefixes = ["/".join(folded.split("/")[:i]) for i in range(1, folded.count("/") + 1)]
        # `.git` or `..` parts never reach the export: a `.git` folder there would make it a repo with branch config.
        unsafe = any(part in ("", ".", "..", ".git") for part in folded.split("/"))
        if (mode in (b"120000", b"160000") or int(size) > MAX_BLOB or left_out(path) or unsafe
                or folded in seen or folded in dirs or any(prefix in seen for prefix in prefixes)):
            left += 1
            continue
        seen.add(folded)
        dirs.update(prefixes)
        keep.append((path, obj))
    batch = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert batch.stdin is not None and batch.stdout is not None
    try:
        for path, obj in keep:
            batch.stdin.write(obj + b"\n")
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise RuntimeError(f"git cat-file failed for {path}")
            data = batch.stdout.read(int(header[2]) + 1)[:-1]
            target = dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    finally:
        batch.stdin.close()
        batch.stdout.close()
        batch.wait()
    diff = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", "diff", "--no-ext-diff", "--no-textconv",
                           "--no-color", "-a", f"{ref}...{sha}", "--", ".", ":(exclude,icase)tests/acceptance/**",
                           ":(exclude,glob)**/.env", ":(exclude,glob)**/.env.*"], capture_output=True)
    if diff.returncode != 0:
        raise RuntimeError(f"git diff failed: {diff.stderr.decode(errors='replace').strip()}")
    (dest / DIFF).write_bytes(diff.stdout)
    return [path for path, _ in keep], left


def _inside(path: Path, folders: list[Path]) -> bool:
    # Case-folded: APFS resolves a path in any case to the same folder.
    text = str(path).casefold()
    return any(text == str(f).casefold() or text.startswith(str(f).casefold().rstrip(os.sep) + os.sep) for f in folders)


def worktrees(repo: Path) -> list[Path]:
    """Every worktree of the repo, resolved."""
    listed = devloop.git(repo, "worktree", "list", "--porcelain") or ""
    return [Path(line[len("worktree "):]).resolve() for line in listed.splitlines() if line.startswith("worktree ")]


def lookup(name: str, path_env: str, worktrees: list[Path]) -> Path | None:
    """D9: the first executable `name` in an absolute `PATH` entry outside every worktree, resolved."""
    folders = [w.resolve() for w in worktrees]
    for entry in path_env.split(os.pathsep):
        if not os.path.isabs(entry) or _inside(Path(entry).resolve(), folders):
            continue
        found = Path(entry) / name
        real = found.resolve()
        if real.is_file() and os.access(real, os.X_OK) and not _inside(real, folders):
            return real
    return None


def child_env(env: Mapping[str, str], cli: Path, node: Path | None) -> dict[str, str]:
    """D8: the allowlist of `env`, and a `PATH` of the CLI folder, the `node` folder, then the system folders."""
    out = {key: env[key] for key in ENV_KEYS if key in env}
    out["PATH"] = os.pathsep.join([str(cli.parent), *([str(node.parent)] if node else []), SYSTEM_PATH])
    return out


def command(reviewer: str, export: Path, entry: dict, output: Path) -> list[str]:
    """D10. The CLI path itself is the caller's: argv[0] is the bare name."""
    model = entry.get("model")
    if reviewer == "codex":
        return ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                "-C", str(export), "-c", 'default_permissions="devloop-review"', "-c", CODEX_FILESYSTEM,
                "-c", f'model_reasoning_effort="{entry["effort"]}"', *(["-m", model] if model else []),
                "-o", str(output), "-"]
    return ["claude", "-p", "--restricted", "--strict-mcp-config", "--tools=Read,Grep,Glob", "--output-format", "json",
            "--effort", entry["effort"], *(["--model", model] if model else [])]


def record_dir(repo: Path, reviewer: str, now: datetime) -> Path:
    """D12: a new `<git-common-dir>/dev-loop/second-review/<YYYYMMDDTHHMMSSZ>-<reviewer>[-<n>]/` folder."""
    common = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    parent = Path(common or ".git") / "dev-loop" / "second-review"
    parent.mkdir(parents=True, exist_ok=True)
    name, n = f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{reviewer}", 1
    while True:
        folder = parent / (name if n == 1 else f"{name}-{n}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            n += 1


def tokens(reviewer: str, stdout: str, stderr: str) -> int | None:
    """Claude: `usage` input plus output tokens. Codex: the last `tokens used` number in stderr."""
    if reviewer == "codex":
        found = re.findall(r"tokens used[:\s]*(\d[\d,]*)", stderr)
        return int(found[-1].replace(",", "")) if found else None
    reply = _reply(stdout)
    usage = reply.get("usage") if reply else None
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("input_tokens"), usage.get("output_tokens")]
    return sum(counts) if all(isinstance(c, int) and not isinstance(c, bool) for c in counts) else None  # type: ignore[arg-type]


def _reply(stdout: str) -> dict | None:
    try:
        reply = json.loads(stdout)
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def _stop(child: subprocess.Popen) -> None:
    """SIGTERM to the process group, SIGKILL to what is left of it 5 seconds later."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    try:
        child.wait(5)
    except subprocess.TimeoutExpired:
        pass
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def _taint(repo: Path, record: Path, sha: str, outputs: list[str]) -> bool:
    """D11: 2 or more distinctive acceptance lines in the outputs append a taint record with no commits."""
    top = Path(devloop.git(repo, "rev-parse", "--show-toplevel") or repo)
    lines = audit.matched_lines(top, ("tests/acceptance/",), outputs)
    if len(lines) < audit.MIN_MATCHES:
        return False
    audit.append(top, "records", {"agent_id": record.name, "agent_type": "second-review", "at": audit.now(),
                                  "set": "acceptance", "lines": lines, "start": merge_base(top), "end": sha,
                                  "commits": []})
    return True


def _child(repo: Path, reviewer: str, sha: str, entry: dict, timeout_minutes: float, env: Mapping[str, str],
           cli: Path, node: Path | None, dest: Path, prompt: str, files: list[str], out: dict) -> tuple[dict, int]:
    """Rules 9 to 11: the record, the CLI run, the audit."""
    record = None
    try:
        record = record_dir(repo, reviewer, datetime.now(timezone.utc))
        diff = hashlib.sha256((dest / DIFF).read_bytes()).hexdigest()
        (record / "sent.txt").write_text("".join(f"{path}\n" for path in files) + diff + "\n", encoding="utf-8")
        (record / "record.json").write_text(json.dumps({**out, "state": "running", "record": str(record)}, indent=2) + "\n",
                                            encoding="utf-8")
    except OSError as error:
        if record is not None:
            shutil.rmtree(record, ignore_errors=True)
        return {**out, "state": "failed", "reason": "export", "message": f"cannot write the record: {error}"}, 1
    out["record"] = str(record)
    output, errors = record / "output.md", record / "stderr.log"
    argv = command(reviewer, dest, entry, output)
    timed_out, started = False, time.monotonic()
    with tempfile.TemporaryFile() as stdout, errors.open("wb") as stderr:
        try:
            child = subprocess.Popen([str(cli), *argv[1:]], cwd=dest, env=child_env(env, cli, node), stdin=subprocess.PIPE,
                                     stdout=stdout, stderr=stderr, start_new_session=True)
        except OSError as error:
            # The shell's codes: 127 for a missing file or interpreter, 126 for one that cannot run.
            code = 127 if isinstance(error, FileNotFoundError) else 126
            result = {**out, "state": "failed", "reason": f"exit {code}", "message": str(error).splitlines()[0]}
            (record / "record.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result, 1
        try:
            child.communicate(prompt.encode("utf-8"), timeout=timeout_minutes * 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop(child)
        out["seconds"] = round(time.monotonic() - started, 3)
        stdout.seek(0)
        text = stdout.read().decode("utf-8", errors="replace")
    err = errors.read_text(encoding="utf-8", errors="replace")
    if reviewer == "codex":
        final = output.read_text(encoding="utf-8", errors="replace") if output.is_file() else ""
    else:
        reply = _reply(text)
        final = reply.get("result") if reply and reply.get("is_error") is not True else None
        final = final if isinstance(final, str) else ""
    out["tokens"] = tokens(reviewer, text, err)
    if _taint(repo, record, sha, [final, text, err]):
        output.unlink(missing_ok=True)
        errors.unlink(missing_ok=True)
        result, code = {**out, "state": "tainted"}, 0
    elif timed_out:
        result, code = {**out, "state": "failed", "reason": "timeout",
                        "message": f"the reviewer ran past {timeout_minutes:g} minutes"}, 1
    elif child.returncode != 0:
        tail = [line.strip() for line in err.splitlines() if line.strip()]
        result, code = {**out, "state": "failed", "reason": f"exit {child.returncode}",
                        "message": tail[-1] if tail else None}, 1
    elif not final.strip():
        result, code = {**out, "state": "failed", "reason": "no-output", "message": "the reviewer gave no output"}, 1
    else:
        if reviewer == "claude":
            output.write_text(final, encoding="utf-8")
        result, code = {**out, "state": "done", "output": final}, 0
    (record / "record.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result, code


def _review(repo: Path, reviewer: str, ref: str, sha: str, entry: dict, timeout_minutes: float,
            env: Mapping[str, str], out: dict) -> tuple[dict, int]:
    """Rules 7 to 11: lookup, export, CLI run, audit."""
    trees = worktrees(repo)
    cli = lookup(reviewer, env.get("PATH", ""), trees)
    if cli is None:
        return {**out, "state": "skipped", "reason": "no-cli"}, 0
    node = lookup("node", env.get("PATH", ""), trees)
    try:
        dest = Path(tempfile.mkdtemp(prefix=PREFIX))
    except OSError as error:
        return {**out, "state": "failed", "reason": "export", "message": f"cannot make the export folder: {error}"}, 1
    try:
        try:
            files, left = export(repo, ref, sha, dest)
            prompt = PROMPT.read_text(encoding="utf-8").replace("<sha>", sha)
        except (OSError, RuntimeError, ValueError) as error:
            message = (str(error) or type(error).__name__).splitlines()[0]
            return {**out, "state": "failed", "reason": "export", "message": message}, 1
        out.update(files=len(files), left_out=left)
        return _child(repo, reviewer, sha, entry, timeout_minutes, env, cli, node, dest, prompt, files, out)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def run(repo: Path, harness: str, timeout_minutes: float, env: Mapping[str, str]) -> tuple[dict, int]:
    # Replace refs must not change what the locks read: every git call below inherits this.
    saved = os.environ.get("GIT_NO_REPLACE_OBJECTS")
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        sweep(Path(tempfile.gettempdir()), time.time())
        return _decide(repo, harness, timeout_minutes, env)
    finally:
        if saved is None:
            del os.environ["GIT_NO_REPLACE_OBJECTS"]
        else:
            os.environ["GIT_NO_REPLACE_OBJECTS"] = saved


def _decide(repo: Path, harness: str, timeout_minutes: float, env: Mapping[str, str]) -> tuple[dict, int]:
    reviewer = REVIEWER[harness]
    out = payload(reviewer=reviewer)

    def skip(reason: str) -> tuple[dict, int]:
        return {**out, "state": "skipped", "reason": reason}, 0

    def fail(reason: str, message: str) -> tuple[dict, int]:
        return {**out, "state": "failed", "reason": reason, "message": message}, 1

    sha = devloop.git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    if devloop.git(repo, "rev-parse", "--is-inside-work-tree") != "true" or not sha:
        return fail("git", "not a git worktree or HEAD does not resolve")
    ref = devloop.main_ref(repo)
    if ref is None:
        return skip("no-main-ref")
    out["base"] = devloop.git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    lock = project_lock(repo, ref)
    if lock is None:
        return fail("bad-config", "dev-loop.json second_review is not a boolean, or the file does not parse")
    if not lock:
        return skip("project-off")
    local = main_worktree(repo) / "dev-loop.local.json"
    state, entry = "not-chosen", None
    if local.is_file():
        try:
            config = load_unique(local.read_bytes())
        except (ValueError, OSError):
            config = None
        if not isinstance(config, dict):
            state = "bad-config"
        elif "second_review" in config:
            state, entry = local_entry(config["second_review"], reviewer)
    if state == "bad-config":
        return fail("bad-config", "dev-loop.local.json second_review has a bad shape")
    if entry is None:
        return skip(state)
    out.update(model=entry["model"], effort=entry["effort"])
    git_dir = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-dir")
    if git_dir and any((Path(git_dir) / d).exists() for d in ("rebase-merge", "rebase-apply")):
        return skip("rebase")
    out["head"] = sha
    if devloop.git(repo, "merge-base", ref, sha) is None:
        return fail("no-merge-base", f"no merge base of {ref} and {sha}")
    if devloop.git(repo, "rev-list", "--count", f"{ref}..{sha}") == "0":
        return skip("nothing-to-review")
    return _review(repo, reviewer, ref, sha, entry, timeout_minutes, env, out)
