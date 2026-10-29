#!/bin/sh
# Codex PreToolUse wall (D9), matcher Bash|apply_patch. agent_id is present only in a subagent call.
# spec_tester: no path names src/, app/, backend/ or frontend/. Any other subagent: no path names
# tests/acceptance. Main session: no apply_patch of a file under tests/acceptance/; its Bash stays open.
# apply_patch (and its Edit, Write aliases): the paths are the patch file headers, never the body.
# Every caller: no apply_patch of an installed kit file (kit files change only by upgrade).
# Deny: exit 2 and one stderr line. The path match is deny-path.sh, in this folder.
#
# A workflow guard, not a security boundary: a block means stop and report, never rephrase the call.
input=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$input" ] || fail "empty hook input"
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
field() { printf '%s' "$input" | jq -r "$1 // empty"; }
agent_id=$(field .agent_id) agent_type=$(field .agent_type) tool=$(field .tool_name) cwd=$(field .cwd)

if [ -n "$agent_id" ] && [ "$agent_type" = spec_tester ]; then
  set -- src/ app/ backend/ frontend/
else
  set -- tests/acceptance
fi
# Relative paths resolve against the cwd as a repo path, so a repo that sits under a folder named src
# or app is not walled whole. Outside a git repo: the cwd itself. Never empty: deny-path.sh splits
# the command into tokens only with a cwd. An absolute path inside the repo is matched as its
# repo-relative path, for the same reason.
base= top=
[ -z "$cwd" ] || base=$(git -C "$cwd" rev-parse --show-prefix 2>/dev/null) || base=$cwd
[ -z "$cwd" ] || top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || top=
base=${base:-.}
# jq prelude of both filters below: $tops (the repo top, also without or with /private, longest first)
# and rel, which turns "<top>/" into "./" and a bare <top> into ".". Never into "": a path glued to a
# flag (-I<top>/src) must keep the slash before its first segment.
rel='($t | if . == "" then [] else [., (if startswith("/private/") then .[8:] else "/private" + . end)] end
    | sort_by(-length)) as $tops
  | def rel: reduce $tops[] as $x (.; split($x + "/") | join("./") | split($x) | join("."));'

if [ "$tool" = Bash ]; then
  [ -n "$agent_id" ] || exit 0
  call=$(printf '%s' "$input" | jq --arg b "$base" --arg t "$top" "$rel"'
  .cwd = $b | .tool_input |= walk(if type == "string" then rel else . end)' 2>/dev/null) || fail "cannot rewrite cwd"
else
  call=$(printf '%s' "$input" | jq --arg b "$base" --arg t "$top" "$rel"'
    {tool_input: {paths: [ (.tool_input.command // .tool_input.patch // "") | strings | splits("\n")
      | capture("^\\*\\*\\* (Add File|Update File|Delete File|Move to): (?<p>.+)$").p | sub("[ \t\r]+$"; "")
      | if startswith("/") then rel else $b + "/" + . end ]}}' 2>/dev/null) || fail "cannot read the patch headers"
fi
# Every caller: an installed kit file changes only by upgrade (the same list as the Claude wall),
# matched on repo-relative paths only: a user-level ~/.codex/agents/implementer.toml is not a kit file.
# The header paths come out as ./x (absolute in the repo) or <base>/x (relative); outside a git repo
# base is the cwd, so the repo top is the cwd. Any other absolute path is dropped (not a kit path).
# A shell write is left to seal-check (kit-changed).
if [ "$tool" != Bash ]; then
  kit='^(\.agents/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.claude/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.dev-loop)(/|$)|^\.codex/rules/dev-loop\.rules$|^\.claude/agents/(implementer|spec-tester)\.md$|^\.codex/agents/(final-reviewer|implementer|planner|security-reviewer|spec-tester|task-reviewer)\.toml$'
  normalize() { tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta'; }
  repo_rel() { awk -v t="$1" 'BEGIN { p = index(t, "/private/") == 1 ? substr(t, 9) : "/private" t }
    /^\// { if (t != "" && index($0, t "/") == 1) $0 = substr($0, length(t) + 2)
            else if (t != "" && index($0, p "/") == 1) $0 = substr($0, length(p) + 2)
            else next }
    { sub(/^(\.\/)+/, ""); print }'; }
  kit_top=$(printf '%s' "${top:-$cwd}" | sed 's|/*$||' | normalize)
  if printf '%s' "$call" | jq -r '.tool_input.paths[]' | normalize | repo_rel "$kit_top" | grep -Eq "$kit"; then
    echo "Blocked: installed dev-loop kit file. Kit files change only by upgrade: change dev-loop, then run upgrade." >&2
    exit 2
  fi
fi
verdict=$(printf '%s' "$call" | sh "$(dirname "$0")/deny-path.sh" "$@") || exit 2
[ -z "$verdict" ] && exit 0
reason=$(printf '%s' "$verdict" | jq -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null) \
  || fail "unreadable deny-path verdict"
echo "$reason" >&2
exit 2
