#!/usr/bin/env python3
"""Run one command in a locked-down Docker container and copy its /scratch files back.

The command is target code under audit: treat it as hostile. Exit: the command's own
code, 124 on timeout, 125 with one `sandbox-run: <reason>` stderr line on a runner error.
The image needs `env`, `mkdir`, `sleep`, `tar`, `chmod` and `sh`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SIZE_RE = re.compile(r"^[1-9][0-9]*[bkmg]?$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class RunnerError(Exception):
    pass


class Interrupted(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse would exit 2
        raise RunnerError(f"bad option: {message}")


def positive(kind):
    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a positive number: {text}") from None
        if not value > 0 or value != value or value == float("inf"):
            raise argparse.ArgumentTypeError(f"not a positive number: {text}")
        return value
    return parse


def size(text: str) -> str:
    if not SIZE_RE.match(text):
        raise argparse.ArgumentTypeError(f"not a size: {text}")
    return text


def env_pair(text: str) -> str:
    if not ENV_RE.match(text):
        raise argparse.ArgumentTypeError(f"not NAME=VALUE: {text}")
    return text


def parse(argv: list[str]) -> argparse.Namespace:
    if "--" not in argv:
        raise RunnerError("bad option: no -- before the command")
    split = argv.index("--")
    cmd = argv[split + 1:]
    if not cmd:
        raise RunnerError("bad option: no command")
    p = Parser(prog="sandbox-run.py")
    p.add_argument("--image", required=True)
    p.add_argument("--scratch", required=True)
    p.add_argument("--target")
    p.add_argument("--workdir", default="/target")
    p.add_argument("--timeout", type=positive(float), default=120)
    p.add_argument("--memory", type=size, default="512m")
    p.add_argument("--pids", type=positive(int), default=128)
    p.add_argument("--cpus", type=positive(float), default=1)
    p.add_argument("--file-size", type=positive(int), default=67108864)
    p.add_argument("--scratch-size", type=size, default="64m")
    p.add_argument("--scratch-max-bytes", type=positive(int), default=67108864)
    p.add_argument("--env", type=env_pair, action="append", default=[])
    args = p.parse_args(argv[:split])
    args.cmd = cmd
    if not args.workdir.startswith("/"):
        raise RunnerError("bad option: --workdir must be absolute")
    return args


def resolve_paths(args: argparse.Namespace) -> None:
    args.scratch = Path(args.scratch).resolve()
    if not args.scratch.is_dir():
        raise RunnerError(f"bad option: --scratch is not a folder: {args.scratch}")
    if args.target is None:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if top.returncode != 0:
            raise RunnerError("bad option: no --target and the current folder is not in a git worktree")
        args.target = top.stdout.strip()
    args.target = Path(args.target).resolve()
    if not args.target.is_dir():
        raise RunnerError(f"bad option: --target is not a folder: {args.target}")
    if ":" in str(args.target) or "," in str(args.target):
        raise RunnerError("bad option: --target path holds ':' or ','")


def image_for(target: Path, name: str) -> str:
    try:
        config = json.loads((target / "dev-loop.json").read_text(encoding="utf-8"))
        audit = config["security_audit"]
        images = audit["images"]
        if (set(audit) != {"images"} or not isinstance(images, dict)
                or not all(NAME_RE.match(k) and isinstance(v, str) and v.strip() for k, v in images.items())):
            raise ValueError
        return images[name]
    except (OSError, ValueError, KeyError, TypeError):
        raise RunnerError(f"unknown image {name}") from None


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def image_path(image: str) -> str:
    found = docker("image", "inspect", "--format", "{{json .Config.Env}}", image)
    if found.returncode != 0:
        raise RunnerError(f"image missing {image}")
    for item in json.loads(found.stdout) or []:
        if item.startswith("PATH="):
            return item[5:]
    return DEFAULT_PATH


def failed(step: str, done: subprocess.CompletedProcess) -> RunnerError:
    lines = done.stderr.strip().splitlines()
    return RunnerError(f"{step} failed: {lines[-1] if lines else f'exit {done.returncode}'}")


def kill_leftovers(ctr: str) -> None:
    # A process the command left behind could stop tar or write into its stdout: none may survive.
    for _ in range(20):
        killed = docker("exec", "-u", "65534:65534", ctr, "sh", "-c", "kill -9 -1")
        if killed.returncode in (126, 127):  # no `sh` in the image; 1 only means nothing was left to kill
            raise failed("kill in the container", killed)
        top = docker("top", ctr, "-o", "pid,stat")
        alive = [line for line in top.stdout.splitlines()[1:] if not line.split()[1:2] or not line.split()[1].startswith("Z")]
        if top.returncode == 0 and len(alive) == 1:  # only PID 1, `sleep infinity`
            return
        time.sleep(0.1)
    raise RunnerError("leftover processes")


def read_scratch(ctr: str, cap: int, limit: float) -> bytes:
    # Root has no DAC override under --cap-drop ALL: open every file to its owner, 65534, then copy as it.
    # Safe only after kill_leftovers: no other uid 65534 process is left to open tar's fds or signal it.
    # /scratch itself is root's: chmod each top entry, never through a top symlink (-R skips deeper ones).
    opened = docker("exec", "-u", "65534:65534", "-w", "/scratch", ctr, "sh", "-c",
                    'for f in * .[!.]* ..?*; do [ -L "$f" ] || [ ! -e "$f" ] || chmod -R u+rwX -- "$f" || exit 1; done')
    if opened.returncode != 0:
        raise failed("chmod of /scratch", opened)
    proc = subprocess.Popen(["docker", "exec", "-u", "65534:65534", ctr, "tar", "-C", "/scratch", "-cf", "-", "."],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + limit
    data = bytearray()
    eof = False
    try:
        while len(data) <= cap:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise RunnerError("scratch copy timed out")
            chunk = os.read(fd, min(1 << 20, cap + 1 - len(data)))
            if not chunk:
                eof = True
                break
            data += chunk
    finally:
        if not eof:
            proc.kill()
        proc.stdout.close()
        code = proc.wait()
    if len(data) > cap:
        raise RunnerError(f"scratch over {cap} bytes")
    if code != 0:
        raise RunnerError(f"tar of /scratch failed: exit {code}")
    return bytes(data)


def extract(data: bytes, scratch: Path, cap: int) -> None:
    # Only plain regular files; their folders are made as needed. Links, devices, fifos and sparse files are dropped.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        members, total = [], 0
        for m in tar:  # the sum is checked per header: a lying size refuses before tar reads past the stream
            if m.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not m.issparse():
                total += m.size
                if total > cap:
                    raise RunnerError(f"scratch over {cap} bytes")
                members.append(m)
        for member in members:
            name = posixpath.normpath(member.name)
            parts = name.split("/")
            if name.startswith("/") or ".." in parts or name == ".":
                continue
            path = scratch
            safe = True
            for part in parts[:-1]:
                path = path / part
                if path.is_symlink() or (path.exists() and not path.is_dir()):
                    safe = False
                    break
            dest = scratch.joinpath(*parts)
            if not safe or dest.is_symlink() or not dest.resolve().is_relative_to(scratch):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(src, out)


def run(args: argparse.Namespace) -> int:
    if shutil.which("docker") is None or docker("info").returncode != 0:
        raise RunnerError("unavailable")
    resolve_paths(args)
    image = image_for(args.target, args.image)
    path = image_path(image)
    ctr = f"devloop-sandbox-{secrets.token_hex(6)}"
    try:
        # Signals wait until the daemon has answered, so `docker rm -f` below always sees the container.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        started = docker(
            "run", "-d", "--rm", "--pull", "never", "--name", ctr, "--label", "devloop.sandbox=1",
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--memory", args.memory, "--memory-swap", args.memory,
            "--pids-limit", str(args.pids), "--cpus", str(args.cpus),
            "--ulimit", f"fsize={args.file_size}", "--ulimit", "nofile=256:256", "--ulimit", "core=0",
            "--tmpfs", f"/scratch:size={args.scratch_size},mode=1777",
            "-v", f"{args.target}:/target:ro", "--entrypoint", "sleep", image, "infinity")
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        if started.returncode != 0:
            raise failed("docker run", started)
        made = docker("exec", ctr, "mkdir", "-p", "/scratch/home", "/scratch/tmp", "/scratch/cache")
        if made.returncode != 0:
            raise failed("mkdir in the container", made)
        exec_cmd = ["docker", "exec", "-i", "-u", "65534:65534", "-w", args.workdir, ctr,
                    "env", "-i", f"PATH={path}", "HOME=/scratch/home", "TMPDIR=/scratch/tmp",
                    "XDG_CACHE_HOME=/scratch/cache", "LANG=C.UTF-8", *args.env, *args.cmd]
        try:
            code = subprocess.run(exec_cmd, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            return 124
        kill_leftovers(ctr)
        try:
            # shortcut: the copy gets a fresh --timeout; a full 64 MB tmpfs copies in about a second.
            extract(read_scratch(ctr, args.scratch_max_bytes, args.timeout), args.scratch, args.scratch_max_bytes)
        except (tarfile.TarError, OSError) as err:
            raise RunnerError(f"scratch copy failed: {err}") from None
        return code
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        subprocess.run(["docker", "rm", "-f", ctr], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv: list[str]) -> int:
    def interrupt(signum: int, _frame: object) -> None:
        raise Interrupted(signum)

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    try:
        return run(parse(argv))
    except RunnerError as err:
        print(f"sandbox-run: {err}", file=sys.stderr)
        return 125
    except Interrupted as sig:
        return 128 + sig.signum


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
