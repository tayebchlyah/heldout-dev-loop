#!/bin/sh
# PreToolUse wall. Deny a tool call whose PATH FIELDS name one of the supplied paths.
#
# WHAT THIS IS. A workflow guard, not a security boundary. A shell command can
# always split or wildcard a path past a substring rule. The walls are hooks;
# the transcript audit backs them up. A block means stop and report, never
# rephrase the input until it passes.
# Tool walls (#97): the path fields include filePath and uri (MCP tools), and a file:// value is also
# matched by its path.
# Through acceptance-wall.sh it also walls view_image (by path) and MCP tools (mcp__*, read_mcp_resource,
# list_mcp_resources, list_mcp_resource_templates): denied outright to implementer and spec_tester, the
# name rule for any other subagent. Gaps (D14): code mode has no hook on exec, one on each inner call;
# write_stdin has no hook event, so the kit config turns tty sessions off (#140); for other subagents an
# MCP search tool, an MCP tool that runs code or reads a remote copy, a resource listing (the audit reads
# their results).
#
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches evasion. Path is joined
# to both glob and pattern, because a grep or a glob splits a location that
# way. With a cwd, each candidate token is also joined to the folder a cd or
# pushd earlier in the command has moved to (else the cwd), and the cwd and
# those folders are candidates. Candidates are
# normalized so a trailing slash, a doubled slash, a dot segment or a capital
# letter does not walk past the wall.
. "${0%/*}/git-pin.sh"
input=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$input" ] || fail "empty hook input"
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
candidates=$(printf '%s' "$input" | jq -r '
  # A heredoc body and an exclusion (:!x, :(exclude)x, --exclude-dir=x, grep -v x)
  # name a path without reaching it.
  def find_clean: gsub("(?<o>(^|[ \t])-(path|ipath|wholename|name|iname) +)[^ \t\n;|&]+(?= +-prune)"; "\(.o)")
    | gsub("(?<o>(^|[ \t])(-not|!) +-path +)[^ \t\n;|&]+"; "\(.o)");
  def clean: gsub("<<-?[ \t]*(?<q>[\u0027\"]?)(?<d>[A-Za-z_][A-Za-z0-9_.-]*)\\k<q>(?<r>[^\n]*)\n(?:[^\n]*\n)*?\t*\\k<d>(?=\n|$)"; "<<\(.d)\(.r)")
    | gsub("(:!|:\\^|:\\([^)]*exclude[^)]*\\)|--exclude-dir=)[^ \t\n;|&]*"; "")
    | gsub("(?<g>grep( +-[A-Za-z]+)* +-[A-Za-z]*v[A-Za-z]*) +[^ \t\n;|&]+"; "\(.g)")
    | gsub("(?<o>(^|[ \t])(-g|--glob|--iglob)(=| *)[\u0027\"]?)![^ \t\n;|&\u0027\"]*"; "\(.o)")
    | gsub("(?<s>(^|[;&|\n(])[ \t]*find([ \t][^;&|\n]*)?)"; "\(.s | find_clean)");
  def unless_excluded: if type == "string" and startswith("!") then empty else . end;
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, (.tool_input.command? | strings | clean), (.tool_input.glob? | unless_excluded),
      .tool_input.paths?, .tool_input.files?, .tool_input.url?,
      .tool_input.source?, .tool_input.destination?, .tool_input.directory?,
      .tool_input.filePath?, .tool_input.uri? ]
    | [ .[] | .. | strings | ., (select(startswith("file://")) | sub("^file://(localhost)?"; "")) ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | (((.tool_input.glob? | unless_excluded), .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  # A token walk over each field, in order: the folder starts at the cwd; a cd or pushd token makes the next
  # token not starting with - (options, --) its target, absolute or joined to the folder, and a folder
  # reached; any other token is joined to the folder at that point.
  # Subshell scope is not tracked: after (cd x && ...), a later token still joins to x.
  # Mirrors the Claude wall, deny-acceptance-to-subagents.sh.
  def relative($all):
    if (.cwd // "") == "" then [] else
      .cwd as $c
      | [$c] + [ $all[] | reduce (splits("[\\s;|&()<>\\x27\"`=]+") | select(length > 0)) as $t
          ({w: $c, cd: false, out: []};
            if .cd and ($t | startswith("-")) then .
            elif .cd then .w = (if ($t | startswith("/")) then $t else .w + "/" + $t end) | .cd = false | .out += [.w]
            elif $t == "cd" or $t == "pushd" then .cd = true
            else .out += [.w + "/" + $t] end)
        | .out[] ]
    end;
  (fields + joins) as $all | ($all + relative($all)) | join("\n")' 2>/dev/null) || fail "path extraction failed"
candidates=$(printf '%s' "$candidates" \
  | tr 'A-Z' 'a-z' \
  | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta')

for denied in "$@"; do
  needle=$(printf '%s' "$denied" | tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/$||' -e 's/[][\.*^$+?(){}|]/\\&/g')
  # Whole path segment only: app/ blocks "app/x", "/x/app", "app;", "'app'", "app -n" and "app*",
  # never "mapping" or "application".
  if printf '%s' "$candidates" | grep -qE -- "(^|[^a-z0-9_.-])${needle}(/|[\"'\`;|&)<>*[:space:]]|\$)"; then
    jq -n --arg reason "Blocked: this agent cannot access $denied" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
  fi
done
exit 0
