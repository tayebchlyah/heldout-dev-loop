#!/bin/bash
# PreToolUse(Edit|Write|NotebookEdit) wall: Stages 3 to 7 run with the session cwd in the feature worktree, so an edit
# into a feature worktree from any other checkout is denied. Target checkout: git top level of the nearest existing
# directory of the target, physical. Feature worktree: as deny-ask.sh, and the branch not under release/.
# Three forms of the target are checked: as given, with . and .. folded as text, and with its own symlinks followed.
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

feature_top() {
  local dir=${1%/*} phys top git_dir common
  while [ -n "$dir" ] && [ ! -d "$dir" ]; do dir=${dir%/*}; done
  phys=$(cd -P -- "${dir:-/}" 2>/dev/null && pwd -P) || return
  top=$(git -C "$phys" rev-parse --show-toplevel 2>/dev/null) || return
  top=$(cd -P -- "$top" 2>/dev/null && pwd -P) || return
  git_dir=$(git -C "$top" rev-parse --absolute-git-dir 2>/dev/null) || return
  common=$(cd -P -- "$top" && cd -P -- "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P) || return
  [ "$(cd -P -- "$git_dir" && pwd -P)" != "$common" ] || return
  case "$(git -C "$top" symbolic-ref --short -q HEAD 2>/dev/null)" in release/*) return ;; */*) ;; *) return ;; esac
  [ -f "$top/dev-loop.json" ] || return
  printf '%s' "$top"
}

folded() {
  local out="" part parts=()
  IFS=/ read -r -d '' -a parts < <(printf '%s' "$1")
  for part in "${parts[@]}"; do
    case "$part" in ''|.) ;; ..) out=${out%/*} ;; *) out="$out/$part" ;; esac
  done
  printf '%s' "${out:-/}"
}

linked=$target
n=0
while [ -L "$linked" ] && [ "$n" -lt 40 ]; do
  link=$(readlink -- "$linked") || break
  case "$link" in /*) linked=$link ;; *) linked="${linked%/*}/$link" ;; esac
  n=$((n + 1))
done

here=$(cd -P -- "$cwd" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
[ -n "$here" ] && here=$(cd -P -- "$here" 2>/dev/null && pwd -P)
for form in "$target" "$(folded "$target")" "$linked"; do
  top=$(feature_top "$form")
  [ -n "$top" ] && [ "$top" != "$here" ] || continue
  jq -cn --arg r "EnterWorktree $top first: Stages 3 to 7 run with the session cwd in the feature worktree." \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  exit 0
done
exit 0
