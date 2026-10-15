#!/bin/sh
# SessionStart, UserPromptSubmit, Stop, SubagentStop: refresh the progress board in the background.
# Usage: board.sh session-start|prompt|stop|subagent-stop. Hook input JSON on stdin. Always silent, exit 0.
# The background group holds none of the hook's pipes, so the hook returns at once.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$cli" ] || exit 0
{ printf '%s' "$input" | (cd "$cwd" && python3 "$cli" board --event "$1"); } </dev/null >/dev/null 2>&1 &
exit 0
