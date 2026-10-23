#!/bin/sh
# PreToolUse(Bash|apply_patch) wall around the held-out suite and the sources (D9).
# agent_id and agent_type are present only inside a spawned subagent call.
# spec_tester: no path that names src/, app/, backend/ or frontend/.
# Any other subagent: no path that names tests/acceptance.
# Main session: no apply_patch of a file under tests/acceptance/. Its Bash stays open:
# Stage 6 reads a failing test to classify it, and seal-check finds a Bash write.
#
# WHAT THIS IS. A workflow guard, not a security boundary (see deny-path.sh, which
# holds the subagent path match). A block means stop and report.
#
# apply_patch paths are the patch file headers only, never the body: a patch whose
# text names the suite does not touch it. Deny: exit 2 and one stderr line.
input=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$input" ] || fail "empty hook input"
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
agent=$(printf '%s' "$input" | jq -r '.agent_id // empty' 2>/dev/null) || fail "agent_id extraction failed"
type=$(printf '%s' "$input" | jq -r '.agent_type // empty' 2>/dev/null) || fail "agent_type extraction failed"
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null) || fail "tool_name extraction failed"

# Bash goes to deny-path.sh as it is. A patch goes as its header paths, each as given
# and joined to cwd when relative.
probe=$input
if [ "$tool" != Bash ]; then
  probe=$(printf '%s' "$input" | jq -c '(.cwd // "") as $c
    | [ ((.tool_input.command | strings) // (.tool_input.patch | strings) // "")
        | splits("\n")
        | capture("^\\*\\*\\* (?:(?:Add|Update|Delete) File|Move to): (?<p>.*)$").p
        | sub("\\s+$"; "") | select(. != "")
        | ., (select(startswith("/") | not) | select($c != "") | $c + "/" + .) ]
    | {cwd: $c, tool_input: {paths: .}}' 2>/dev/null) || fail "patch header extraction failed"
fi

if [ -z "$agent" ]; then
  [ "$tool" = Bash ] && exit 0
  if printf '%s' "$probe" | jq -r '.tool_input.paths[]' | tr 'A-Z' 'a-z' \
    | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta' \
    | grep -Eq '(^|/)tests/acceptance(/|$)'; then
    echo "Blocked: held-out suite. Only spec_tester writes tests/acceptance. The main session may read it." >&2
    exit 2
  fi
  exit 0
fi

if [ "$type" = spec_tester ]; then
  set -- src/ app/ backend/ frontend/
else
  set -- tests/acceptance
fi
out=$(printf '%s' "$probe" | sh "$(dirname "$0")/deny-path.sh" "$@") || exit 2
if [ -n "$out" ]; then
  printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecisionReason' >&2
  exit 2
fi
exit 0
