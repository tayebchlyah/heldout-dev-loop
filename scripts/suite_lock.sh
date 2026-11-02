# Suite lock of scripts/gate.sh and scripts/acceptance.sh, sourced (#165). suite_lock: one suite run at a time
# across the worktrees of a clone. It takes an exclusive lock on <git common dir>/dev-loop/suite.lock on fd 9,
# held until the script exits, and writes the worktree top and the shell PID into it. A busy lock prints one line
# on stderr, naming the worktree that holds it, then waits. Run each suite with 9>&-: a child left running would
# keep the lock. A nested run takes no lock: one under the holder PID (a test may clear the env), one with
# DEVLOOP_SUITE_LOCK_HELD set. A folder outside git takes no lock either. A read-only common dir (the Codex hook
# sandbox) opens the existing lock file read-only: flock needs no write access, so the run still serializes with the
# others, and the holder note is skipped. A missing lock file that cannot be created takes no lock.
# allow-comment: dependency, macOS has no flock(1): python3 takes flock(2) on the fd it inherits from the shell.
suite_lock() {
  [ -n "${DEVLOOP_SUITE_LOCK_HELD:-}" ] && return 0
  local dir
  dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 0
  local lock="$dir/dev-loop/suite.lock" readonly=
  if { mkdir -p "$dir/dev-loop" && exec 9<>"$lock"; } 2>/dev/null; then :
  elif { exec 9<"$lock"; } 2>/dev/null; then readonly=1
  else return 0
  fi
  python3 - "$(git rev-parse --show-toplevel)" "$readonly" <<'EOF'
import fcntl, os, subprocess, sys


def ancestors() -> set[int]:
    pid, seen = os.getppid(), set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        pid = int(out) if out.isdigit() else 0
    return seen


try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    lines = os.pread(9, 4096, 0).decode(errors="replace").splitlines()
    if len(lines) > 1 and lines[1].isdigit() and int(lines[1]) in ancestors():
        sys.exit(3)
    holder = lines[0] if lines and lines[0] else "an unknown worktree"
    print(f"suite lock: waiting for the suite run in {holder}", file=sys.stderr, flush=True)
    fcntl.flock(9, fcntl.LOCK_EX)
if not sys.argv[2]:
    os.ftruncate(9, 0)
    os.pwrite(9, f"{sys.argv[1]}\n{os.getppid()}\n".encode(), 0)
EOF
  case $? in
    0) export DEVLOOP_SUITE_LOCK_HELD=1 ;;
    3) exec 9>&- ;;
    *) return 1 ;;
  esac
}
