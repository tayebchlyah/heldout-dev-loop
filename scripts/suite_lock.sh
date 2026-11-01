# Suite lock of scripts/gate.sh and scripts/acceptance.sh, sourced (#165). suite_lock: one suite run at a time
# across the worktrees of a clone. It takes an exclusive lock on <git common dir>/dev-loop/suite.lock on fd 9,
# held until the script exits, and writes the worktree top into it. A busy lock prints one line on stderr, naming
# the worktree that holds it, then waits. Run each suite with 9>&-: a child left running would keep the lock.
# A nested run (DEVLOOP_SUITE_LOCK_HELD set) and a folder outside git take no lock.
# allow-comment: dependency, macOS has no flock(1): python3 takes flock(2) on the fd it inherits from the shell.
suite_lock() {
  [ -n "${DEVLOOP_SUITE_LOCK_HELD:-}" ] && return 0
  local dir
  dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 0
  mkdir -p "$dir/dev-loop" && exec 9<>"$dir/dev-loop/suite.lock" || return 1
  python3 - "$(git rev-parse --show-toplevel)" <<'EOF' || return 1
import fcntl, os, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    holder = os.pread(9, 4096, 0).decode(errors="replace").strip() or "an unknown worktree"
    print(f"suite lock: waiting for the suite run in {holder}", file=sys.stderr, flush=True)
    fcntl.flock(9, fcntl.LOCK_EX)
os.ftruncate(9, 0)
os.pwrite(9, f"{sys.argv[1]}\n".encode(), 0)
EOF
  export DEVLOOP_SUITE_LOCK_HELD=1
}
