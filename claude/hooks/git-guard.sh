#!/bin/bash
# PreToolUse(Bash) wall for the global git rules.
IN=$(cat)
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty')
CWD=$(printf '%s' "$IN" | jq -r '.cwd // empty')
[ -z "$CMD" ] && exit 0

VAULT=$(python3 -c 'import json,os,pathlib
p = pathlib.Path(os.path.expanduser("~/.agents/dev-loop.config.json"))
print(json.loads(p.read_text()).get("vault", "") if p.is_file() else "")' 2>/dev/null)
# Any global options may sit before the subcommand: -C or -c take a value that may
# be quoted and may hold spaces, the rest (--no-pager, --git-dir=x) are one token.
G='git( +-[Cc] +("[^"]+"|'"'"'[^'"'"']+'"'"'|[^ ]+)| +-[^ ]+)* +'
WRITE="${G}(commit|push|add|checkout|switch|reset|rebase|merge|stash|tag|cherry-pick|am|apply)( |$)|${G}branch +-[dD]( |$)"

deny() { echo "$1" >&2; exit 2; }
has()  { printf '%s' "$CMD" | grep -Eq "$1"; }

# 1. rm -rf on root only.
has 'rm +-[rf]+ +/( |$)|rm +-[rf]+ +/\*' && deny "Blocked: rm -rf / is never allowed."

# 2. No git write of any kind inside the Obsidian vault.
# The repo a command acts on is not always the session cwd: a leading "cd <path> &&"
# or a "git -C <path>" retargets it. Resolve those, so work in another repo is not
# blocked just because the session started in the vault. Anything unresolved falls
# back to the cwd, so the vault stays protected by default.
# An unquoted path may hold spaces (the vault path does), so cut the argument at
# the next shell operator rather than at the next space, then strip quotes.
arg() { printf '%s' "$1" | sed -E 's/[[:space:]]*(&&|\|\||;|\|).*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'; }
TARGET="$CWD"
lead_cd=$(printf '%s' "$CMD" | sed -nE 's/^[[:space:]]*cd[[:space:]]+(.*)$/\1/p')
[ -n "$lead_cd" ] && TARGET=$(arg "$lead_cd")
# "git -C <path>" is followed by the subcommand, so this argument ends at a space
# unless it is quoted.
git_c=$(printf '%s' "$CMD" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+("[^"]+"|'"'"'[^'"'"']+'"'"'|[^[:space:]]+).*/\1/p')
[ -n "$git_c" ] && TARGET=$(arg "$git_c")
case "$TARGET" in /*) ;; *) TARGET="$CWD/$TARGET" ;; esac

if [ -n "$VAULT" ]; then
  case "$TARGET" in
    "$VAULT"|"$VAULT"/*)
      has "$WRITE" && deny "Never run git inside the vault. The Obsidian Git plugin owns commits."
      ;;
  esac
fi

# 3. Hard reset destroys work.
has "${G}reset +--hard" && deny "Blocked: git reset --hard discards work. Use git stash or --soft."

# 4. A plain push of a feature branch runs. A push that rewrites, deletes or publishes
# beyond one feature branch asks: force, a + refspec, delete, mirror, all, prune, tags,
# main or master as target, a bare push while main, master or a detached HEAD is
# checked out, or a bare push whose remote config can reach other branches.
# Parsed with shlex, not regex, so quotes, combined short flags (-uf), global options
# (-c, -C, --git-dir), sh -c, cd and git aliases do not walk past the rule. Fail
# closed: a command the parser cannot read, or a parser crash, asks.
ask() { printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Global rule: %s needs explicit permission each time."}}\n' "$1"; exit 0; }
if has '(^|[^[:alnum:]_.-])git([^[:alnum:]_.-]|$)'; then
  REASON=$(CMD="$CMD" CWD="$CWD" python3 - <<'PY'
import os, shlex, subprocess, sys

FORCE = {"--force", "--force-with-lease", "--force-if-includes", "--delete", "--mirror",
         "--all", "--branches", "--prune", "--tags", "--follow-tags"}
PUSH_ARG = {"--push-option", "--repo", "--receive-pack", "--exec"}
GLOBAL_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
WRAPPERS = {"env", "command", "exec", "builtin", "sudo", "nohup", "time", "then", "do",
            "else", "{", "!"}


def ask(reason):
    print(reason)
    sys.exit(0)


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def segments(cmd):
    lex = shlex.shlex(cmd.replace("\n", ";"), posix=True, punctuation_chars=";&|()`")
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok and all(c in ";&|()`" for c in tok):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def check_push(repo, args):
    pos, j = [], 0
    while j < len(args):
        a = args[j]
        if a == "--":
            pos += args[j + 1:]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if name in FORCE:
                ask("a force, delete, mirror, all, prune or tag push")
            if name in PUSH_ARG and "=" not in a:
                j += 1
        elif a.startswith("-") and len(a) > 1:
            if "f" in a[1:] or "d" in a[1:]:
                ask("a force or delete push")
            if a.endswith("o"):
                j += 1
        else:
            pos.append(a)
        j += 1
    _, branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote, specs = (pos[0] if pos else ""), pos[1:]
    if not specs:
        r = remote or git(repo, "config", f"branch.{branch}.remote")[1] or "origin"
        if (git(repo, "config", "--get-all", f"remote.{r}.push")[1]
                or git(repo, "config", f"remote.{r}.mirror")[1] == "true"
                or git(repo, "config", "push.default")[1] == "matching"
                or git(repo, "config", f"branch.{branch}.merge")[1].removeprefix("refs/heads/") in ("main", "master")):
            ask("a bare push whose remote config can reach other branches")
        specs = ["HEAD"]
    for spec in specs:
        if spec.startswith(("+", ":")) or "*" in spec:
            ask("a forced, deleting or wildcard refspec")
        src, _, dst = spec.partition(":")
        if src in ("HEAD", "@"):
            src = branch
        dst = dst or src
        if (src.startswith("refs/tags/") or dst.startswith("refs/tags/")
                or (src and git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{src}")[0] == 0)):
            ask("a tag push")
        if not dst:
            ask("a push from a detached HEAD")
        if dst.removeprefix("refs/heads/") in ("main", "master"):
            ask("a push to main or master")


def check(cmd, cwd):
    try:
        segs = list(segments(cmd))
    except ValueError:
        ask("a git command the guard cannot parse")
    for seg in segs:
        config = False
        while seg and (seg[0] in WRAPPERS or ("=" in seg[0] and not seg[0].startswith("-"))):
            config = config or seg[0].startswith("GIT_")
            seg = seg[1:]
        if not seg:
            continue
        head = os.path.basename(seg[0])
        if head == "cd" and len(seg) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(seg[1]))
        elif head in ("sh", "bash", "zsh") and "-c" in seg[1:-1]:
            check(seg[seg.index("-c") + 1], cwd)
        elif head == "eval":
            check(" ".join(seg[1:]), cwd)
        elif head == "git":
            repo, i = cwd, 1
            while i < len(seg) and seg[i].startswith("-"):
                if seg[i] in GLOBAL_ARG and i + 1 < len(seg):
                    if seg[i] == "-C":
                        repo = os.path.join(repo, os.path.expanduser(seg[i + 1]))
                    config = config or seg[i] in ("-c", "--config-env")
                    i += 1
                config = config or seg[i].startswith("--config-env=")
                i += 1
            sub = seg[i] if i < len(seg) else ""
            if sub != "push":
                if sub and "push" in git(repo, "config", "--get", f"alias.{sub}")[1]:
                    ask("a git alias that pushes")
                continue
            if config:
                ask("a push with config overrides")
            check_push(repo, seg[i + 1:])


check(os.environ["CMD"], os.environ["CWD"])
PY
) || REASON="a push the guard failed to check"
  [ -n "$REASON" ] && ask "$REASON"
fi
exit 0
