#!/bin/sh
# SessionStart, UserPromptSubmit, PostToolUse, Stop, SubagentStop: refresh the progress board in the background.
# Usage: board.sh session-start|prompt|post-tool|stop|subagent-stop. Hook input JSON on stdin. Always silent, exit 0.
# post-tool runs only after a command that moves the loop (task-start, task-done, sdd-workspace, git commit,
# git worktree add (also with -C <path>), gh pr create, gh pr merge). The CLI comes from KIT_ROOT (the trusted kit
# copy), else from KIT_MAIN, else from the first worktree of the hook cwd (a direct run).
# The background group holds none of the hook's pipes, so the hook returns at once.
. "${0%/*}/git-pin.sh"
input=$(cat)
if [ "$1" = post-tool ]; then
  command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
  case "$command" in
    *task-start*|*task-done*|*sdd-workspace*|*"git commit"*|*"git worktree add"*|*"gh pr create"*|*"gh pr merge"*) ;;
    *"git -C "*" commit"*|*"git -C "*" worktree add"*) ;;
    *) exit 0 ;;
  esac
fi
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
main=$KIT_MAIN
[ -n "$main" ] || main=$(git -C "$cwd" worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')
kit=${KIT_ROOT:-$main}
[ -n "$kit" ] || exit 0
cli="$kit/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$cli" ] || exit 0
git_pin_repo "$cwd"
{ printf '%s' "$input" | (cd "$cwd" && python3 -I "$cli" board --event "$1"); } </dev/null >/dev/null 2>&1 &
exit 0
