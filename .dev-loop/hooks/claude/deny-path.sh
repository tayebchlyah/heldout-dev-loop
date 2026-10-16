#!/bin/bash
# Block any tool call whose PATH FIELDS name one of the given fragments.
# Usage in a subagent frontmatter hook: deny-path.sh <fragment> [<fragment> ...]
#
# WHAT THIS IS. A workflow guard, not a security boundary. A shell command can
# always split or wildcard a path past a substring rule. A block means stop and
# report, never rephrase the input until it passes.
#
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches evasion. Path is joined
# to both glob and pattern, because Grep and Glob split a location that way,
# and candidates are normalized so a trailing slash, a doubled slash, a dot
# segment or a capital letter does not walk past the wall.
IN=$(cat)
CANDIDATES=$(echo "$IN" | jq -r '
  # A heredoc body and an exclusion (:!x, :(exclude)x, --exclude-dir=x, grep -v x)
  # name a path without reaching it.
  def clean: gsub("<<-?[ \t]*(?<q>[\u0027\"]?)(?<d>[A-Za-z_][A-Za-z0-9_.-]*)\\k<q>(?<r>[^\n]*)\n(?:[^\n]*\n)*?\t*\\k<d>(?=\n|$)"; "<<\(.d)\(.r)")
    | gsub("(:!|:\\([^)]*exclude[^)]*\\)|--exclude-dir=)[^ \t\n;|&]*"; "")
    | gsub("(?<g>grep( +-[A-Za-z]+)* +-[A-Za-z]*v[A-Za-z]*) +[^ \t\n;|&]+"; "\(.g)");
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, (.tool_input.command? | strings | clean), .tool_input.glob?,
      .tool_input.paths?, .tool_input.files?, .tool_input.url?,
      .tool_input.source?, .tool_input.destination?, .tool_input.directory? ]
    | [ .[] | .. | strings ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | ((.tool_input.glob?, .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  (fields + joins) | join("\n")' \
  | tr 'A-Z' 'a-z' \
  | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta')

for FRAG in "$@"; do
  NEEDLE=$(printf '%s' "$FRAG" | tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/$||' -e 's/[][\.*^$+?(){}|]/\\&/g')
  # Whole path segment only: app/ blocks "app/x", "/x/app", "app;" and "'app'",
  # never "application" or "App Services". A space does not end a segment, so
  # prose that starts with the name stays allowed.
  if echo "$CANDIDATES" | grep -qE -- "(^|[^a-z0-9_.-])${NEEDLE}(/|[\"'\`;|&)<>]|\$)"; then
    echo "Blocked: $FRAG is outside this agent's territory." >&2
    exit 2
  fi
done
exit 0
