#!/bin/sh
# PreToolUse wall. Deny a tool call whose PATH FIELDS name one of the supplied paths.
#
# WHAT THIS IS. A workflow guard, not a security boundary. A shell command can
# always split or wildcard a path past a substring rule; the Codex permission
# profiles in config.toml are the real filesystem wall. A block means stop and
# report, never rephrase the input until it passes.
#
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches evasion. Path is joined
# to both glob and pattern, because a grep or a glob splits a location that
# way, and candidates are normalized so a trailing slash, a doubled slash, a
# dot segment or a capital letter does not walk past the wall.
input=$(cat)
candidates=$(printf '%s' "$input" | jq -r '
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, .tool_input.command?, .tool_input.glob?,
      .tool_input.paths?, .tool_input.files?, .tool_input.url?,
      .tool_input.source?, .tool_input.destination?, .tool_input.directory? ]
    | [ .[] | .. | strings ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | ((.tool_input.glob?, .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  (fields + joins) | join("\n")' 2>/dev/null) || exit 2
candidates=$(printf '%s' "$candidates" \
  | tr 'A-Z' 'a-z' \
  | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta')

for denied in "$@"; do
  needle=$(printf '%s' "$denied" | tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/$||')
  if printf '%s' "$candidates" | grep -Fq -- "$needle"; then
    jq -n --arg reason "Blocked: this agent cannot access $denied" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
  fi
done
exit 0
