#!/bin/bash
# Wall around tests/acceptance for every caller but spec-tester (D2).
# agent_id is present only inside a subagent call.
# Main session: no Edit, Write or NotebookEdit under tests/acceptance/. Its reads stay
# open: Stage 6 reads a failing test to classify it. A Bash write is left to seal-check.
# Subagents: the net below, for those that do not use the implementer definition.
# spec-tester is exempt: it authors tests/acceptance. It gets the source wall instead: no path
# names src/, app/, backend/ or frontend/ (the segment match of deny-path.sh, in this folder).
# Its frontmatter hook holds the same rule; this hook holds it where frontmatter hooks are skipped (#139).
# source_dirs (#302): the same step as deny-path.sh --source-dirs, first in the spec-tester branch, on the
# original input. A source_dirs key at the main ref: devloop.py source-wall of the kit copy that holds this hook
# decides (deny, allow, or mode default: the four-folder match); no CLI or a CLI error blocks a path or command
# tool with a message naming source_dirs. No key: the four-folder match, no CLI call.
#
# WHAT THIS IS. A workflow guard against a cooperative agent wandering into the
# held-out suite, not a security boundary. A subagent that wants past it can
# always hide a folder in a variable (`d=tests; cd $d && cat acceptance/x`) or
# wildcard the suite's own name out of the path (`cat tests/*/x`, or a Grep glob
# of `tests/*/**`). No substring rule closes those; only a resolve-and-compare
# check would. The walls are hooks; the transcript audit backs them up.
# Treat a block as a signal to stop and report, never as something to work
# around: an agent that disguises its input has defeated the hook wall. One exception: after a
# search-scope block, search again on named source folders or with the suite excluded.
#
# Match path-bearing fields only, never the whole tool input: a subagent that
# writes a doc or a config whose BODY names the suite is not reading it, and
# blocking that produces a false positive that teaches evasion.
# Every field a tool reaches a path through is listed, and path is joined to
# both glob and pattern, because Grep and Glob each split a location that way.
# Each candidate token is also resolved against the folder a cd or pushd
# earlier in the command has moved to (else the subagent's working directory),
# and the cwd and those folders are candidates: a shell that sits in tests/ or inside the suite,
# or a `cd tests && cat acceptance/x`, cannot reach it through a relative path.
# Edit, Write and NotebookEdit of an installed kit file are denied to every
# caller: kit files change only by upgrade.
# Candidates are normalized before matching, so a trailing slash, a doubled
# slash, a dot segment or a capital letter does not walk past the wall.
# Search rule (#91): a subagent but spec-tester cannot run a recursive search (Grep, Glob, grep -r,
# rg, git grep, find) whose root is tests/acceptance or a folder above it, unless it excludes the
# suite; the engine is devloop.py search-wall of the kit, skipped when no kit CLI is found, and a
# CLI error blocks. Not walled: wrappers (xargs, env, command, sh -c, bash -c, eval), and ls -R, tree,
# fd, ag, ack, git ls-files. The audit path rule backs up the search tools.
# Tool walls (#97): LSP is denied to every subagent, spec-tester included: its results name files the
# call does not. MCP tools (mcp__*, ListMcpResourcesTool, ReadMcpResourceTool, ReadMcpResourceDirTool)
# are denied to implementer and spec-tester; any other subagent gets the name rule on the path fields,
# filePath and uri included, a file:// value by its path. Monitor is walled as Bash on its command: the
# name rule, the search rule and the audit. Open for other subagents: an MCP search tool, an MCP tool
# that runs code or reads a remote copy of the repo, a resource listing; the audit reads their results.
# WebFetch, WebSearch and Agent are denied to implementer and spec-tester (#240): the public
# repository on the web holds both walled sides, a nested agent has no wall; their disallowedTools
# list them too. The denial names the NEEDS_FACT exit: an outside fact the brief and the repository
# do not give is reported, never guessed. Other subagents keep them, with the name rule on url. A
# project agent's frontmatter hooks are skipped in an untrusted folder or a -p or SDK session; this
# hook holds every rule of the implementer and spec-tester walls (#139).
# Nested routes (#273): implementer and spec-tester get a closed tool list: the tools key of their agent
# file (Read, Edit, Write, NotebookEdit, Grep, Glob, Bash, Monitor, Skill, ToolSearch; spec-tester: no Skill)
# plus SubagentHandback. Any other tool is denied, SendMessage, RemoteTrigger and a future tool included,
# so the settings entry has matcher .*. The LSP, MCP, web and Agent denials stay and come first.
# Skill: implementer may name mattpocock-skills:tdd, mattpocock-skills:diagnosing-bugs or
# mattpocock-skills:codebase-design (tdd calls it) with no check. Any other name goes to devloop.py
# skill-wall of the kit (#274): the walled_skills list of dev-loop.json at the main ref must hold it. No kit
# CLI, a CLI error or output that is not a JSON object: a deny. Checked in Claude Code 2.1.310: a shell line
# of a skill runs outside the Bash wall, and Skill loads the main checkout copy in an EnterWorktree session,
# the worktree copy in a session that starts there. So every checkout Skill can load from must equal the main
# ref: the worktree of cwd, the session start folder ($CLAUDE_PROJECT_DIR) and the main checkout. The scan
# denies context: fork, a shell line, a hooks key, an @ and an odd frontmatter shape. An @ in Skill args
# expands to a file attachment past every hook and the audit, through a symlink too, so any @ in the args
# of an implementer Skill call is denied before the name test; other subagents get the name rule on args. spec-tester: every Skill call is denied. A skill denial
# is no stop. The main session and every other subagent keep these tools. Not walled: a general-purpose
# subagent can still start a spec-tester fork; a Bash edit of the plugin cache copy of an allowed skill; a
# project skill folder whose name imitates an allowed name; a personal skill with a listed name; a change of
# the file between the check and the load; a local move of the main ref; a branch edit of the kit devloop.py; a cached skill body, until the skill watcher reloads it, passes the check
# after the file is restored; a skills: field of an agent file preloads text with no Skill call; a session
# started in a subfolder loads no root settings, so it has no wall.
# No wall on a nested harness started from the shell (claude -p, codex exec, another agent CLI): the
# child has no agent_id, so it gets the main session rules, reads open. The audit does not read its
# transcript, only what it prints to the parent. A deny by name does not close the route.
. "${0%/*}/git-pin.sh"
IN=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
normalize() { tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta'; }
WORD='(^|[^[:alnum:]_.-])([efr]?grep|rg|find)([^[:alnum:]_.-]|$)'
kit_cli() {
  local dir top
  for dir in "$(printf '%s' "$1" | jq -r '.cwd // empty')" "$CLAUDE_PROJECT_DIR"; do
    [ -n "$dir" ] || continue
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || continue
    [ -f "$top/.agents/skills/dev-loop/scripts/devloop.py" ] && { printf '%s' "$top/.agents/skills/dev-loop/scripts/devloop.py"; return; }
  done
}
search_wall() {
  local cli out
  cli=$(kit_cli "$1")
  [ -n "$cli" ] || return 0
  out=$(printf '%s' "$1" | python3 "$cli" search-wall 2>/dev/null) || fail "search check failed"
  printf '%s' "$out" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "search check failed"
  [ "$(printf '%s' "$out" | jq -r '.deny')" = true ] || return 0
  printf '%s\n' "$(printf '%s' "$out" | jq -r '.reason')" >&2
  exit 2
}
source_key() {
  local r listed
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 1
  ref=
  for r in refs/remotes/origin/main refs/heads/main; do
    git -C "$top" rev-parse --verify -q "$r^{commit}" >/dev/null 2>&1 && { ref=$r; break; }
  done
  [ -n "$ref" ] || return 1
  if listed=$(git -C "$top" ls-tree --full-tree --name-only "$ref" -- dev-loop.json 2>/dev/null); then
    [ -n "$listed" ] || return 1
    git -C "$top" cat-file blob "$ref:dev-loop.json" 2>/dev/null \
      | jq -e 'type == "object" and (has("source_dirs") | not)' >/dev/null 2>&1 && return 1
  fi
  return 0
}
source_wall() {
  local repo top ref d cli= out detail
  case "$(printf '%s' "$IN" | jq -r '.tool_name // empty')" in
    Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor) ;;
    *) return 0 ;;
  esac
  repo=${CLAUDE_PROJECT_DIR:-$(printf '%s' "$IN" | jq -r '.cwd // empty')}
  [ -n "$repo" ] && source_key "$repo" || return 0
  d=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
  case "$d" in
    */.dev-loop/hooks/claude) cli=${d%/.dev-loop/hooks/*}/.agents/skills/dev-loop/scripts/devloop.py ;;
    */claude/hooks) cli=${d%/*/hooks}/skill/scripts/devloop.py ;;
    *) cli=$(kit_cli "$IN") ;;
  esac
  [ -n "$cli" ] && [ -f "$cli" ] || cli=
  if [ -z "$cli" ]; then
    detail="no kit CLI"
  elif out=$(printf '%s' "$IN" | python3 -I "$cli" source-wall --repo "$top" 2>/dev/null) \
    && printf '%s' "$out" | jq -se 'length == 1 and (.[0] | type == "object" and (.deny | type) == "boolean")' >/dev/null 2>&1; then
    printf '%s' "$out" | jq -e '.mode == "default"' >/dev/null && return 0
    printf '%s' "$out" | jq -e '.deny' >/dev/null || exit 0
    printf '%s\n' "$(printf '%s' "$out" | jq -r '.reason | tostring | gsub("[\r\n]+"; " ")')" >&2
    exit 2
  else
    detail=$(printf '%s' "$out" | jq -sr 'if length == 1 and (.[0] | type == "object" and (.error | type) == "string") then .[0].error | gsub("[\r\n]+"; " ") else "" end' 2>/dev/null)
    detail=${detail:-the kit CLI failed}
  fi
  echo "Blocked: source_dirs wall: $detail. Every spec-tester call that names a path is denied until it is fixed." >&2
  exit 2
}
skill_reason() {
  local cli out reason
  cli=$(kit_cli "$1")
  [ -n "$cli" ] || { echo "no kit CLI"; return; }
  out=$(printf '%s' "$1" | python3 "$cli" skill-wall 2>/dev/null) || { echo "skill check failed"; return; }
  printf '%s' "$out" | jq -se 'length == 1 and (.[0] | type == "object" and .deny == false)' >/dev/null 2>&1 && return
  reason=$(printf '%s' "$out" | jq -sr 'if length == 1 and (.[0] | type == "object" and (.reason | type) == "string") then .[0].reason else "" end' 2>/dev/null)
  echo "${reason:-skill check failed}"
}

# Installed kit files: the folders, the rules file and the agents init writes (kit list in lib/kit.mjs),
# matched on repo-relative paths only: a user-level ~/.claude/agents/implementer.md is not a kit file.
KIT='^(\.agents/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.claude/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.dev-loop)(/|$)|^\.codex/rules/dev-loop\.rules$|^\.claude/agents/(implementer|spec-tester)\.md$|^\.codex/agents/(final-reviewer|implementer|planner|security-reviewer|spec-tester|task-reviewer)\.toml$'
# Normalized paths on stdin, repo top in $1: a path under the top (or its /private variant) becomes
# repo-relative, any other absolute path is dropped (not a kit path), a leading ./ goes.
repo_rel() { awk -v t="$1" 'BEGIN { p = index(t, "/private/") == 1 ? substr(t, 9) : "/private" t }
    /^\// { if (t != "" && index($0, t "/") == 1) $0 = substr($0, length(t) + 2)
            else if (t != "" && index($0, p "/") == 1) $0 = substr($0, length(p) + 2)
            else next }
    { sub(/^(\.\/)+/, ""); print }'; }

TOOL=$(echo "$IN" | jq -r '.tool_name // empty')
case "$TOOL" in
  Edit|Write|NotebookEdit)
    # The path as given, and joined to cwd when relative.
    TARGETS=$(echo "$IN" | jq -r '(.cwd // "") as $c
      | (.tool_input.file_path // .tool_input.notebook_path // "") | select(. != "")
      | ., (select(startswith("/") | not) | select($c != "") | $c + "/" + .)' 2>/dev/null) || fail "path extraction failed"
    # Every caller: a kit file changes only by upgrade. A shell write is left to seal-check (kit-changed).
    # Repo top: the git top of the cwd, else the cwd itself. A relative path with no cwd stays repo-relative.
    C=$(echo "$IN" | jq -r '.cwd // empty')
    T=
    [ -z "$C" ] || T=$(git -C "$C" rev-parse --show-toplevel 2>/dev/null) || T=$C
    T=$(printf '%s' "${T%/}" | normalize)
    if echo "$IN" | jq -r '(.cwd // "") as $c | (.tool_input.file_path // .tool_input.notebook_path // "")
      | select(. != "") | if startswith("/") or $c == "" then . else $c + "/" + . end' 2>/dev/null \
      | normalize | repo_rel "$T" | grep -Eq "$KIT"; then
      echo "Blocked: installed dev-loop kit file. Kit files change only by upgrade: change dev-loop, then run upgrade." >&2
      exit 2
    fi ;;
esac

if [ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ]; then
  case "$TOOL" in Edit|Write|NotebookEdit) ;; *) exit 0 ;; esac
  if printf '%s\n' "$TARGETS" | normalize | grep -Eq '(^|/)tests/acceptance(/|$)'; then
    echo "Blocked: held-out suite. Only spec-tester writes tests/acceptance. The main session may read it." >&2
    exit 2
  fi
  exit 0
fi
case "$TOOL" in
  LSP) echo "Blocked: LSP is closed to subagents: its results name files the call does not. Search named folders with Grep or Bash." >&2; exit 2 ;;
  mcp__*|ListMcpResourcesTool|ReadMcpResourceTool|ReadMcpResourceDirTool)
    case "$(echo "$IN" | jq -r '.agent_type // empty')" in
      implementer|spec-tester) echo "Blocked: MCP tools are closed to this agent. Read and search named folders with the file tools or the shell." >&2; exit 2 ;;
    esac ;;
  WebFetch|WebSearch|Agent)
    case "$(echo "$IN" | jq -r '.agent_type // empty')" in
      implementer|spec-tester) echo "Blocked: $TOOL is closed to this agent: the web holds a public copy of the repository, a nested agent has no wall. For an outside fact the brief and the repository do not give, stop and report NEEDS_FACT: <question>." >&2; exit 2 ;;
    esac ;;
esac
AGENT=$(echo "$IN" | jq -r '.agent_type // empty')
case "$AGENT:$TOOL" in
  implementer:Skill)
    if printf '%s' "$IN" | jq -e '.tool_input.args? | strings | contains("@")' >/dev/null 2>&1; then
      echo "Blocked: an @ in Skill args attaches a file past every hook. Drop the @, or use Read." >&2
      exit 2
    fi
    if ! printf '%s' "$IN" | jq -e '.tool_input.skill? | IN("mattpocock-skills:tdd", "mattpocock-skills:diagnosing-bugs", "mattpocock-skills:codebase-design")' >/dev/null 2>&1; then
      REASON=$(skill_reason "$IN")
      if [ -n "$REASON" ]; then
        SKILL=$(printf '%s' "$IN" | jq -r '.tool_input.skill? // "" | tostring | gsub("[\r\n]+"; " ")')
        echo "Blocked: skill ${SKILL:-(none)} is closed to this agent: the open skills are mattpocock-skills:tdd, mattpocock-skills:diagnosing-bugs and mattpocock-skills:codebase-design. Use an allowed name, or go on without the skill. A project skill: read its SKILL.md with Read. Reason: $REASON." >&2
        exit 2
      fi
    fi ;;
  implementer:*|spec-tester:*)
    case "$TOOL" in
      Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor|ToolSearch|SubagentHandback) ;;
      *) echo "Blocked: $TOOL is closed to this agent: only the tools of its agent file are open. For an outside fact the brief and the repository do not give, stop and report NEEDS_FACT: <question>." >&2; exit 2 ;;
    esac ;;
esac
TESTER=
if [ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ]; then
  source_wall
  TESTER=1
  # Relative paths resolve against the cwd as a repo path, so a repo that sits under a folder named
  # src or app is not walled whole. Outside a git repo: the cwd itself. Never empty: the command is
  # split into tokens only with a cwd.
  CWD=$(echo "$IN" | jq -r '.cwd // empty')
  # An absolute path inside the repo is matched as its repo-relative path, for the same reason.
  BASE= TOP=
  [ -z "$CWD" ] || BASE=$(git -C "$CWD" rev-parse --show-prefix 2>/dev/null) || BASE=$CWD
  [ -z "$CWD" ] || TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null) || TOP=
  IN=$(echo "$IN" | jq --arg b "${BASE:-.}" --arg t "$TOP" '
  ($t | if . == "" then [] else [., (if startswith("/private/") then .[8:] else "/private" + . end)] end
    | sort_by(-length)) as $tops
  # Mirrors the rel prelude of codex/hooks/acceptance-wall.sh (separate harness payloads).
  # "<top>/" becomes "./", never "": a path glued to a flag (-I<top>/src) keeps its segment.
  # The bare repo top becomes ".".
  | def rel: reduce $tops[] as $x (.; split($x + "/") | join("./") | split($x) | join("."));
  .cwd = $b | .tool_input |= walk(if type == "string" then rel else . end)' 2>/dev/null) || fail "cannot rewrite paths"
fi

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
      .tool_input.source?, .tool_input.destination?, .tool_input.directory?,
      .tool_input.filePath?, .tool_input.uri?, .tool_input.args? ]
    | [ .[] | .. | strings | ., (select(startswith("file://")) | ltrimstr("file://") | sub("^localhost(?=/)"; "")) ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | (((.tool_input.glob? | unless_excluded), .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  # A token walk over each field, in order: the folder starts at the cwd; a cd or pushd token makes the next
  # token not starting with - (options, --) its target, absolute or joined to the folder, and a folder
  # reached; any other token is joined to the folder at that point.
  # Subshell scope is not tracked: after (cd x && ...), a later token still joins to x.
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
CANDIDATES=$(printf '%s' "$CANDIDATES" | normalize)

if [ -n "$TESTER" ]; then
  CALL=$(printf '%s' "$CANDIDATES" | jq -Rn '{tool_input: {paths: [inputs]}}' 2>/dev/null) || fail "cannot pass the paths on"
  printf '%s' "$CALL" | bash "$(dirname "$0")/deny-path.sh" src/ app/ backend/ frontend/ || exit 2
  exit 0
fi

if echo "$CANDIDATES" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
case "$(printf '%s' "$IN" | jq -r '.tool_name // empty')" in
  Grep|Glob) search_wall "$IN" ;;
  Bash|Monitor) printf '%s' "$IN" | jq -r '.tool_input.command // empty' | grep -Eq "$WORD" && search_wall "$IN" ;;
esac
exit 0
