#!/bin/bash
# PreToolUse(AskUserQuestion) wall: Delivery runs under the autonomy grant, so a feature worktree gets no question.
# Feature worktree: a linked worktree, on a branch with a `/`, whose top level holds dev-loop.json. No devloop status
# call: the hook has 5 s. Fails open (no jq, no cwd, no git): Shaping and other repos must keep their questions.
IN=$(cat)
command -v jq >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0
cwd=$(printf '%s' "$IN" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
git_dir=$(git -C "$cwd" rev-parse --absolute-git-dir 2>/dev/null) || exit 0
common=$(cd "$cwd" && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P) || exit 0
[ "$(cd "$git_dir" && pwd -P)" != "$common" ] || exit 0
case "$(git -C "$cwd" symbolic-ref --short -q HEAD 2>/dev/null)" in */*) ;; *) exit 0 ;; esac
top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$top/dev-loop.json" ] || exit 0
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' \
  "Delivery autonomy grant: no questions. Decide, log it with devloop.py decide, and go on. A stop-list item: stop and report it as your final message."
exit 0
