#!/bin/bash
# StopFailure(rate_limit): write the pause note of the feature worktree, so another session can take the work over.
# Hook input JSON on stdin goes to `devloop.py pause --event`, the CLI of the hook cwd's top level (it skips a cwd that
# is not a feature worktree). Output and exit code are ignored by Claude Code: always silent, exit 0.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$cli" ] || exit 0
printf '%s' "$input" | (cd "$cwd" && python3 "$cli" pause --event) >/dev/null 2>&1
exit 0
