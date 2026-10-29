#!/bin/bash
# PreToolUse(Bash) wall: the git push rule, git reset --hard and rm -rf /.
IN=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty' 2>/dev/null) || fail "command extraction failed"
CWD=$(printf '%s' "$IN" | jq -r '.cwd // empty' 2>/dev/null) || fail "cwd extraction failed"
AGENT=$(printf '%s' "$IN" | jq -r '.agent_type // empty' 2>/dev/null) || fail "agent_type extraction failed"
[ -z "$CMD" ] && exit 0

# Any global options may sit before the subcommand: -C or -c take a value that may
# be quoted and may hold spaces, the rest (--no-pager, --git-dir=x) are one token.
G='git( +-[Cc] +("[^"]+"|'"'"'[^'"'"']+'"'"'|[^ ]+)| +-[^ ]+)* +'

deny() { echo "$1" >&2; exit 2; }
has()  { printf '%s' "$CMD" | grep -Eq -e "$1"; }
# Claude's deny() does the same; block() is in both guards so the seal rules stay
# identical (parity), and Codex too denies them with exit 2 and a stderr line.
block() { echo "$1" >&2; exit 2; }

# 0a. Only spec-tester seals (D5, D10). A typed seal or patch trailer is denied on the raw text,
# a heredoc message included: the key, : or = and a value; a --trailer with that key (any
# separator); git interpret-trailers naming it. Prose that names the trailer passes.
TESTER=spec-tester
KEY='Dev-Loop-Seal(-Patch)?'
if [ "$AGENT" != "$TESTER" ] && { has "$KEY[[:space:]]*[:=][[:space:]]*[^[:space:]]" \
    || has "--trailer[=[:space:]]+['\"]?$KEY([^[:alnum:]_-]|\$)" || { has interpret-trailers && has "$KEY"; }; }; then
  block "Blocked: only devloop.py seal, run by spec-tester, writes the Dev-Loop-Seal trailer."
fi

# A heredoc body is text, not commands: every check below reads the line without it.
# A body whose line names a shell (bash, sh, zsh, ssh, eval) stays. If this step
# fails, the checks read the whole text.
case "$CMD" in *'<<'*) BODYLESS=$(CMD="$CMD" python3 - <<'PY'
import os, re

RUNNERS = {"sh", "bash", "zsh", "eval", "ssh", "su"}
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")


def strip_heredocs(cmd):
    # Drops each heredoc body and its end line, so prose with apostrophes or git words
    # is not read as commands. A body whose line names a shell stays. A heredoc with
    # no end line keeps the rest of the text.
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        feeds = any(os.path.basename(w) in RUNNERS for w in re.split(r"[\s;&|()]+", line))
        for dash, _, end in HEREDOC.findall(line):
            j = i
            while j < len(lines) and (lines[j].lstrip("\t") if dash else lines[j]) != end:
                j += 1
            if j == len(lines):
                return "\n".join(out + lines[i:])
            if feeds:
                out += lines[i:j + 1]
            i = j + 1
    return "\n".join(out)


print(strip_heredocs(os.environ["CMD"]), end="")
PY
) && CMD=$BODYLESS || RAW=1 ;; esac

# 0b. Who runs the seal (D5, D11), on the text without heredoc bodies. The spec-tester's commit
# counts in command position only: the line start or after ; & | (. Any other caller: a
# command segment with a shell word seal (quotes removed, as the shell does) after a word
# that names devloop, whatever wraps the run (env, python3 -I, uv run). A segment that starts
# with a read-only tool (grep, cat, git log) passes, as do "devloop.py seal" and seal-check.
RUN='(^|[;&|(])[[:space:]]*'
if [ "$AGENT" = "$TESTER" ]; then
  has "${RUN}${G}commit([^[:alnum:]_-]|\$)" && block "Blocked: spec-tester commits only through devloop.py seal: python3 .agents/skills/dev-loop/scripts/devloop.py seal --feature <slug> --message <subject>."
else
  # Exit 1 (also a crash or unbalanced quotes) means a seal run: fail closed.
  case "$CMD" in *devloop*seal*) printf '%s' "$CMD" | python3 -c 'import shlex, sys
PUNCT = ";&|()<>" + chr(96)
READ = {"grep", "rg", "cat", "less", "head", "tail", "ls", "find", "echo", "printf"}
lex = shlex.shlex(sys.stdin.read().replace("\n", ";"), posix=True, punctuation_chars=PUNCT)
lex.whitespace_split, lex.commenters = True, ""
seg = []
for tok in [*lex, ";"]:
    if not all(c in PUNCT for c in tok):
        seg.append(tok)
        continue
    run = next((i for i, w in enumerate(seg) if "devloop" in w), None)
    read = seg[:1] and (seg[0] in READ or seg[:2] == ["sed", "-n"]
                        or seg[0] == "git" and seg[1:2] in (["grep"], ["log"], ["show"], ["diff"]))
    if run is not None and not read and "seal" in seg[run + 1:]:
        sys.exit(1)
    seg = []' 2>/dev/null || block "Blocked: only spec-tester may run devloop.py seal." ;; esac
fi

# 1. rm -rf on root only.
has 'rm +-[rf]+ +/( |$)|rm +-[rf]+ +/\*' && deny "Blocked: rm -rf / is never allowed."

# 2. Hard reset destroys work.
has "${G}reset +--hard" && deny "Blocked: git reset --hard discards work. Use git stash or --soft."

# 3. A plain push of a feature branch runs. A push that rewrites, deletes or publishes
# beyond one feature branch asks: force, a + refspec, delete, mirror, all, prune, tags,
# main or master as target, a bare push while main, master or a detached HEAD is
# checked out, or a bare push whose remote config can reach other branches.
# Exception: a bare --force-with-lease (with or without --force-if-includes) to the
# checked-out branch, when that branch has a / and the main session pushes: Stage 7
# pushes a rebased feature branch this way.
# Parsed with shlex, not regex, so quotes, combined short flags (-uf), global options
# (-c, -C, --git-dir), cd and git aliases do not walk past the rule. A git anywhere in
# a segment counts (if, timeout, xargs). Only what the shell runs is checked: a heredoc
# body is text unless its line feeds it to a shell, and a quoted string is a command
# only in a segment that runs a shell (sh -c, eval, ssh, su -c). Fail closed: a push
# line the parser cannot read, an argument the shell would expand ($VAR, $(...),
# globs), arguments fed by xargs or find, a push after a command in the same line that
# moves HEAD or changes refs or config, or a parser crash, asks.
# A release tag push and gh release create also carry the release-check verdict (D14),
# $2: "; release-check: ..." or empty. A subagent is denied without running the check.
AGENT_ID=$(printf '%s' "$IN" | jq -r '.agent_id // empty')
ask() { [ -n "$AGENT_ID" ] && deny "Global rule: $1 needs explicit permission, and a subagent cannot ask for it. Push from the main session."; jq -cn --arg r "Global rule: $1 needs explicit permission each time$2." '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'; exit 0; }
if has '(^|[^[:alnum:]_.-])(git|gh)([^[:alnum:]_.-]|$)'; then
  REASON=$(CMD="$CMD" CWD="$CWD" RAW="$RAW" AGENT_ID="$AGENT_ID" python3 -I "$(dirname "$0")/git-guard.py") || REASON="a push the guard failed to check"
  NL='
'
  WHAT=${REASON%%"$NL"*}
  SUFFIX=${REASON#"$WHAT"}
  [ -n "$REASON" ] && ask "$WHAT" "${SUFFIX#"$NL"}"
fi
exit 0
