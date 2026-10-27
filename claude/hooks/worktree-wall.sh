#!/bin/bash
# PreToolUse(Edit|Write|NotebookEdit) wall: Stages 3 to 7 run with the session cwd in the feature worktree, so an edit
# into a feature worktree from any other checkout is denied. Target checkout: git top level of the nearest existing
# directory of the target, physical. Feature worktree: as deny-ask.sh, and the branch not under release/.
# Fails open (no jq, no git, bad input). A path goes only as a quoted argument, never through eval.
IN=$(cat)
command -v jq >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0
cwd=$(printf '%s' "$IN" | jq -r '.cwd | strings' 2>/dev/null)
case "$cwd" in /*) ;; *) exit 0 ;; esac
target=$(printf '%s' "$IN" | jq -r 'if .tool_name == "NotebookEdit" then .tool_input.notebook_path
  else .tool_input.file_path end | strings' 2>/dev/null)
[ -n "$target" ] || exit 0
case "$target" in /*) ;; *) target="$cwd/$target" ;; esac
dir=${target%/*}
while [ -n "$dir" ] && [ ! -d "$dir" ]; do dir=${dir%/*}; done
phys=$(cd -P -- "${dir:-/}" 2>/dev/null && pwd -P) || exit 0
top=$(git -C "$phys" rev-parse --show-toplevel 2>/dev/null) || exit 0
top=$(cd -P -- "$top" 2>/dev/null && pwd -P) || exit 0
git_dir=$(git -C "$top" rev-parse --absolute-git-dir 2>/dev/null) || exit 0
common=$(cd -P -- "$top" && cd -P -- "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P) || exit 0
[ "$(cd -P -- "$git_dir" && pwd -P)" != "$common" ] || exit 0
case "$(git -C "$top" symbolic-ref --short -q HEAD 2>/dev/null)" in release/*) exit 0 ;; */*) ;; *) exit 0 ;; esac
[ -f "$top/dev-loop.json" ] || exit 0
here=$(cd -P -- "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
[ -n "$here" ] && here=$(cd -P -- "$here" 2>/dev/null && pwd -P)
[ "$here" != "$top" ] || exit 0
jq -cn --arg r "EnterWorktree $top first: Stages 3 to 7 run with the session cwd in the feature worktree." \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
exit 0
