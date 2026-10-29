#!/bin/bash
# Block any tool call whose PATH FIELDS name one of the given fragments.
# Usage in a subagent frontmatter hook: deny-path.sh <fragment> [<fragment> ...]
#
# WHAT THIS IS. A workflow guard, not a security boundary. A shell command can
# always split or wildcard a path past a substring rule. A block means stop and
# report, never rephrase the input until it passes, except after a search-scope
# block: search again on named source folders or with the suite excluded.
#
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches evasion. Path is joined
# to both glob and pattern, because Grep and Glob split a location that way,
# and candidates are normalized so a trailing slash, a doubled slash, a dot
# segment or a capital letter does not walk past the wall.
# Search rule (#91), when a fragment is tests/acceptance: a subagent but spec-tester cannot run a recursive search (Grep, Glob, grep -r,
# rg, git grep, find) whose root is tests/acceptance or a folder above it, unless it excludes the
# suite; the engine is devloop.py search-wall of the kit, skipped when no kit CLI is found, and a
# CLI error blocks. Not walled: wrappers (xargs, env, command, sh -c, bash -c, eval), and ls -R, tree,
# fd, ag, ack, git ls-files. The audit path rule backs up the search tools.
IN=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
WORD='(^|[^[:alnum:]_.-])([efr]?grep|rg|find)([^[:alnum:]_.-]|$)'
search_wall() {
  local dir top cli= out
  for dir in "$(printf '%s' "$1" | jq -r '.cwd // empty')" "$CLAUDE_PROJECT_DIR"; do
    [ -n "$dir" ] || continue
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || continue
    [ -f "$top/.agents/skills/dev-loop/scripts/devloop.py" ] && { cli=$top/.agents/skills/dev-loop/scripts/devloop.py; break; }
  done
  [ -n "$cli" ] || return 0
  out=$(printf '%s' "$1" | python3 "$cli" search-wall 2>/dev/null) || fail "search check failed"
  printf '%s' "$out" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "search check failed"
  [ "$(printf '%s' "$out" | jq -r '.deny')" = true ] || return 0
  printf '%s\n' "$(printf '%s' "$out" | jq -r '.reason')" >&2
  exit 2
}
CANDIDATES=$(echo "$IN" | jq -r '
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
      .tool_input.source?, .tool_input.destination?, .tool_input.directory? ]
    | [ .[] | .. | strings ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | (((.tool_input.glob? | unless_excluded), .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  (fields + joins) | join("\n")' 2>/dev/null) || fail "path extraction failed"
CANDIDATES=$(printf '%s' "$CANDIDATES" \
  | tr 'A-Z' 'a-z' \
  | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta')

for FRAG in "$@"; do
  NEEDLE=$(printf '%s' "$FRAG" | tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/$||' -e 's/[][\.*^$+?(){}|]/\\&/g')
  # Whole path segment only: app/ blocks "app/x", "/x/app", "app;", "'app'" and "app*",
  # never "application" or "App Services". A space does not end a segment, so
  # prose that starts with the name stays allowed.
  if echo "$CANDIDATES" | grep -qE -- "(^|[^a-z0-9_.-])${NEEDLE}(/|[\"'\`;|&)<>*]|\$)"; then
    echo "Blocked: $FRAG is outside this agent's territory." >&2
    exit 2
  fi
done
for FRAG in "$@"; do
  [ "$(printf '%s' "$FRAG" | tr 'A-Z' 'a-z' | sed 's|/*$||')" = tests/acceptance ] || continue
  case "$(printf '%s' "$IN" | jq -r '.tool_name // empty')" in
    Grep|Glob) search_wall "$IN" ;;
    Bash) printf '%s' "$IN" | jq -r '.tool_input.command // empty' | grep -Eq "$WORD" && search_wall "$IN" ;;
  esac
  break
done
exit 0
