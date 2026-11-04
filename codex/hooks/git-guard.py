# The checker of git-guard.sh: the git push rule and the gh rule (#89: gh api, release, family writes,
# pr merge --admin, extension and agent runs, and gh words it cannot read).
# Run as python3 -I git-guard.py, with the env CMD (the command line), CWD (the hook cwd),
# RAW (set when the heredoc pass failed) and AGENT_ID (set for a subagent).
# Prints nothing, or the rule on line 1 and the release-check suffix on line 2.
import fnmatch, json, os, re, shlex, subprocess, sys, urllib.parse

FORCE = {"--force", "--force-with-lease", "--force-if-includes", "--delete", "--mirror",
         "--all", "--branches", "--prune", "--tags", "--follow-tags"}
LEASE = {"--force-with-lease", "--force-if-includes"}
PUSH_ARG = {"--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"}
GLOBAL_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# Earlier commands in the same line that change what a later push resolves to.
MOVES_HEAD = {"checkout", "switch", "reset", "rebase", "worktree", "init", "clone", "branch"}
CHANGES_REFS = {"config", "remote", "tag", "update-ref", "symbolic-ref"}
FEEDERS = {"xargs", "parallel", "find", "fd"}
# gh release create options that take a value.
GH_ARG = {"--target", "-t", "--title", "-n", "--notes", "-F", "--notes-file", "--discussion-category",
          "--notes-start-tag", "-R", "--repo"}
# allow-comment: dependency, the built-ins of gh 2.103.2. gh runs a built-in before an alias or extension of that name.
GH_BUILTINS = {"agent-task", "alias", "api", "attestation", "auth", "browse", "cache", "codespace", "completion",
               "config", "copilot", "discussion", "extension", "gist", "gpg-key", "help", "issue", "label",
               "licenses", "org", "pr", "preview", "project", "release", "repo", "ruleset", "run", "search",
               "secret", "skill", "ssh-key", "status", "variable", "version", "workflow"}
GH_RELEASE_READ = {"list", "view", "download", "verify", "verify-asset"}
GH_READ = {"alias": {"list"}, "ruleset": {"list", "view", "check"}, "repo": {"view", "list", "clone", "read-dir", "read-file"},
           "workflow": {"list", "view"}, "run": {"list", "view", "watch", "download"}, "secret": {"list"}, "variable": {"list", "get"}}
GH_NESTED = {"deploy-key": {"list"}, "autolink": {"list", "view"}, "gitignore": {"list", "view"}, "license": {"list", "view"}}
CANNOT = "a gh command the guard cannot read"
TEXT = {"echo", "printf", "grep", "egrep", "fgrep", "rg", "which", "whereis", "ls", "mkdir", "brew"}
WRAPPERS = {"sudo", "command", "exec", "env", "timeout", "nice", "nohup", "time"}
WRAPPER_VALUE = {"sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from"},
                 "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
                 "nice": {"-n", "--adjustment"}, "timeout": {"-s", "--signal", "-k", "--kill-after"}}
SHELL_WORDS = {"!", "{", "if", "then", "elif", "else", "do", "while", "until"}
# allow-comment: dependency, the gh 2.103.2 api flags, True for a flag that takes a value.
API_FLAGS = {**dict.fromkeys(["-i", "--include", "--paginate", "--slurp", "--silent", "--verbose",
                              "--allow-escape-sequences"], False),
             **dict.fromkeys(["-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "-q", "--jq",
                              "-t", "--template", "-p", "--preview", "--hostname", "--cache", "--input"], True)}
# allow-comment: dependency, the characters a quoted string keeps literal that the shell reads as syntax elsewhere.
MASK = "$" + chr(96) + "{},*?[<>"
# Commands that run a string or a heredoc body as shell commands.
RUNNERS = {"sh", "bash", "zsh", "eval", "ssh", "su"}
EXPANDS = set("${}*?[~" + chr(96))
PUNCT = ";&|()" + chr(96)
START = os.environ["CWD"]
# Git refuses an alias that shadows a builtin. Empty when git cannot list them, so
# every command from an unknown folder asks.
_r = subprocess.run(["git", "--list-cmds=builtins"], capture_output=True, text=True)
BUILTINS = set(_r.stdout.split()) if _r.returncode == 0 else set()


def ask(reason, suffix=""):
    # The rule on the first line, the release-check text (one line) on the second.
    print(reason + ("\n" + suffix if suffix else ""))
    sys.exit(0)


# Variables the line surely set to an absolute path before this point.
VARS = {}


def expand(to):
    # $NAME, ${NAME} or $NAME/x, for a NAME in VARS.
    m = re.fullmatch(r"\$(\w+|\{\w+\})(/.*)?", to)
    name = m and m.group(1).strip("{}")
    return VARS[name] + (m.group(2) or "") if name in VARS else to


def record(words):
    # A segment of only NAME=/abs words (after an optional export) sets shell
    # variables. Kept only when the line sets NAME once and uses it in no other way
    # than $NAME or ${NAME}: no for NAME, read NAME, unset NAME, NAME+=.
    for w in words:
        name, value = w.split("=", 1)
        line = os.environ["CMD"]
        once = (len(re.findall(rf"(?<![\w$]){name}=", line)) == 1
                and not re.search(rf"(?<![\w${{]){name}(?![\w=}}])", line))
        # A ~ after a word character is text (iCloud~md~obsidian). An assignment
        # also expands a ~ after : or =, so any other ~ makes the value unknown.
        if (once and os.path.isabs(value) and not (EXPANDS - {"~"}) & set(value)
                and not re.search(r"(?<!\w)~", value)):
            VARS[name] = value


def resolve(bases, to):
    # Every place a cd or -C can land, None for one the parser cannot follow (cd $X
    # for an X not in VARS, cd -). A ~ expands only at the start of a token: a path
    # may hold ~ characters (iCloud~md~obsidian).
    to = expand(to)
    target = os.path.expanduser(to)
    if to == "-" or (EXPANDS - {"~"}) & set(to) or target.startswith("~"):
        return {None}
    if os.path.isabs(target):
        return {target}
    return {None if b is None else os.path.join(b, target) for b in bases}


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


# allow-comment: dependency, the shell reads 2>&1, >&2, &>x and >|x as one redirect, not as separators.
REDIRECTS = {"&>>": chr(0xE101), "&>": chr(0xE102), ">&": chr(0xE100), ">|": chr(0xE103)}
REDIRECT = re.compile(r"(?<![\\&])(&>>|&>|>&|>\|)")


def keep_redirects(text):
    return REDIRECT.sub(lambda m: REDIRECTS[m.group(1)], text)


def restore_redirects(word):
    for op, c in REDIRECTS.items():
        word = word.replace(c, op)
    return word


def segments(cmd):
    # Yields each command with the operator that follows it. An operator with no
    # command before it, such as an opening (, comes with an empty one. No comment
    # syntax: a # must not hide the rest of the line from the guard.
    lex = shlex.shlex(keep_redirects(cmd.replace("\n", ";")), posix=True, punctuation_chars=PUNCT)
    lex.whitespace_split = True
    lex.commenters = ""
    seg = []
    for tok in lex:
        if tok and all(c in PUNCT for c in tok):
            yield seg, tok
            seg = []
        else:
            seg.append(restore_redirects(tok))
    if seg:
        yield seg, ""


# A backslash escape, a single-quoted string, a double-quoted string.
QUOTED = re.compile(r"\\.|'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def unreliable(cmd, segs):
    # segments() reads punctuation inside quotes or after a backslash as an operator,
    # a comment and a heredoc body as commands, and a case pattern's ) as the end of
    # a subshell. True when any of these can sit in cmd. Quoted and escaped text is
    # replaced by one word character, so "a"# is still no comment. A heredoc body is
    # left only on a line that feeds a shell, or when the first pass failed (RAW).
    bare = keep_redirects(QUOTED.sub("x", cmd.replace("\n", ";")))
    body = "<<" in bare and (os.environ["RAW"] or RUNNERS & {os.path.basename(w) for w in re.split(r"[\s;&|()]+", bare)})
    if body or re.search(r"(^|[\s;&|()])#", bare) or re.search(r"(^|[\s;&|(" + chr(96) + r"])case\s", bare):
        return True
    # More operator characters than the bare text holds: a quoted or escaped one
    # became an operator.
    return sum(len(sep) for _, sep in segs) > sum(bare.count(c) for c in PUNCT)


def other_repo(repo):
    # The command targets a repository other than the hook's own project.
    common = ("rev-parse", "--path-format=absolute", "--git-common-dir")
    return git(repo, *common) != git(START, *common)


def release_suffix(repo, args):
    # #227: the release-check verdict of the CLI in KIT_ROOT (the trusted kit copy), else in the main
    # worktree of the hook's own project, 3 s of the hook's 5. Another repository's CLI never runs: it may be planted.
    if os.environ["AGENT_ID"]:
        return ""
    if other_repo(repo):
        return "; release-check failed: another repository"
    main = os.environ.get("KIT_MAIN") or git(START, "worktree", "list", "--porcelain")[1].partition("\n")[0].removeprefix("worktree ")
    cli = os.path.join(os.environ.get("KIT_ROOT") or main, ".agents", "skills", "dev-loop", "scripts", "devloop.py")
    if not main or not os.path.isfile(cli):
        return "; release-check failed: no CLI"
    try:
        r = subprocess.run(["python3", "-I", cli, "release-check", "--no-build", "--repo", repo, *args],
                           capture_output=True, text=True, errors="replace", timeout=3)
    except subprocess.TimeoutExpired:
        return "; release-check failed: timeout"
    out = None
    try:
        out = json.loads(r.stdout)
        if out["ok"] is True:
            return "; release-check: ok"
        if out["reasons"]:
            return "; release-check: " + ", ".join(x["reason"] for x in out["reasons"])
    except (ValueError, TypeError, KeyError):
        pass
    error = out.get("error") if isinstance(out, dict) else None
    return "; release-check failed: " + (r.stderr.strip().splitlines() or [str(error or f"exit {r.returncode}")])[0]


def check_release(args, cwds):
    # gh release create tags a local tag of that name, else --target, else what
    # origin/main points at. Any tag name asks: a release is always the user's.
    tag, target, j = None, None, 0
    while j < len(args):
        a = args[j]
        if a.split("=", 1)[0] == "--target":
            target = a.split("=", 1)[1] if "=" in a else (args[j + 1] if j + 1 < len(args) else "")
        if a in GH_ARG:
            j += 1
        elif not a.startswith("-") and tag is None:
            tag = a
        j += 1
    if None in cwds or any(EXPANDS & set(v) for v in (tag or "", target or "")):
        ask("a release create", "; release-check failed: a folder or argument the guard cannot read")
    repo = min(cwds)
    rc, sha = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}") if tag else (1, "")
    if not rc and target is not None:
        # allow-comment: dependency, gh uses --target only for a tag GitHub lacks, so the local tag must not hide it.
        trc, target_sha = git(repo, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        if trc:
            ask("a release create", f"; release-check failed: target {target} does not resolve")
        if target_sha != sha:
            ask("a release create", f"; release-check failed: local tag {tag} at {sha}, --target at {target_sha}")
    if rc and target is not None:
        rc, sha = git(repo, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        if rc:
            ask("a release create", f"; release-check failed: target {target} does not resolve")
    elif rc:
        rc, sha = git(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
        if rc:
            ask("a release create", "; release-check failed: refs/remotes/origin/main does not resolve")
    ask(f"a release create at {sha}", release_suffix(repo, ["--sha", sha]))


def unreadable(word):
    return bool({"$", chr(96), "*", "?", "["} & set(word) or re.search(r"\{[^{}]*(,|\.\.)[^{}]*\}", word))


def command_index(seg):
    wrapper, duration, i = None, False, 0
    while i < len(seg):
        w = seg[i]
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w) or w in SHELL_WORDS:
            pass
        elif os.path.basename(w) in WRAPPERS:
            wrapper = os.path.basename(w)
            duration = wrapper == "timeout"
            if wrapper == "command" and seg[i + 1:i + 2] in (["-v"], ["-V"]):
                return None
        elif wrapper and w.startswith("-"):
            if w in WRAPPER_VALUE.get(wrapper, ()):
                i += 1
        elif duration:
            duration = False
        else:
            return i
        i += 1
    return None


def strip_redirects(args):
    out, i = [], 0
    while i < len(args):
        m = re.fullmatch(r"([^<>&]*)(&>>?|[<>]+[&|]?)(.*)", args[i])
        if not m:
            out.append(args[i])
        elif m.group(1) and not re.fullmatch(r"\d", m.group(1)):
            out.append(m.group(1))
        i += 2 if m and not m.group(3) else 1
    return out


def next_word(args, i):
    while i < len(args) and (args[i].startswith("-") or i and args[i - 1] in ("-R", "--repo")):
        i += 1
    return i


def mask_quoted(cmd):
    out, state, i = [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and state != "'":
            pair = cmd[i:i + 2]
            out.append(c + chr(0xE000 + MASK.index(pair[1])) if pair[1:] and pair[1] in "<>*?[" else pair)
            i += 2
            continue
        if c == "'" and state != '"':
            state = None if state else "'"
        elif c == '"' and state != "'":
            state = None if state else '"'
        elif state and c in MASK and not (state == '"' and c in "$" + chr(96)):
            c = chr(0xE000 + MASK.index(c))
        out.append(c)
        i += 1
    return "".join(out)


def unmask(word):
    return "".join(MASK[ord(c) - 0xE000] if 0xE000 <= ord(c) < 0xE000 + len(MASK) else c for c in word)


def gh_api(args, masked):
    flags, pos, j = [], [], 0
    while j < len(args):
        a = args[j]
        j += 1
        if a == "--":
            pos += args[j:]
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name not in API_FLAGS or API_FLAGS[name] and not eq and j == len(args):
                ask(CANNOT)
            if API_FLAGS[name] and not eq:
                value, j = args[j], j + 1
            flags.append((name, value))
        elif a.startswith("-") and len(a) > 1:
            for k in range(1, len(a)):
                name = "-" + a[k]
                if name not in API_FLAGS:
                    ask(CANNOT)
                if not API_FLAGS[name]:
                    flags.append((name, ""))
                    continue
                value = a[k + 1:].removeprefix("=")
                if not a[k + 1:]:
                    if j == len(args):
                        ask(CANNOT)
                    value, j = args[j], j + 1
                flags.append((name, value))
                break
        else:
            pos.append(a)
    if len(pos) > 1:
        ask(CANNOT)
    endpoint = pos[0] if pos else ""
    methods = [v.upper() for n, v in flags if n in ("-X", "--method")]
    fields = [(n, v) for n, v in flags if n in ("-f", "--raw-field", "-F", "--field")]
    graphql_read = ((endpoint == "graphql" or "://" in endpoint and urllib.parse.urlsplit(endpoint).path == "/graphql")
                    and not any("mutation" in a.lower() for a in args)
                    and not any(n in ("-F", "--field") and v.partition("=")[2].startswith("@") for n, v in fields))
    if (any(m not in ("GET", "HEAD") for m in methods) or any(n == "--input" for n, _ in flags)
            or fields and "GET" not in methods and not graphql_read):
        ask("a gh api write")
    if any(unreadable(a) for a in masked):
        ask(CANNOT)


def gh_command(args, cwds, fed):
    if fed:
        ask(CANNOT)
    i = next_word(args, 0)
    if any(unreadable(a) for a in args[:i + 1]):
        ask(CANNOT)
    if i == len(args):
        return
    real = [unmask(a) for a in args]
    word = real[i]
    if word not in GH_BUILTINS:
        ask(CANNOT)
    if word == "copilot":
        ask("a gh extension or agent run")
    if word == "api":
        gh_api(real[i + 1:], args[i + 1:])
    j = next_word(args, i + 1)
    sub = real[j] if j < len(args) else None
    if word in ("release", "pr", "extension", *GH_READ) and any(unreadable(a) for a in args[i + 1:j + 1]):
        if word == "release":
            ask("a gh release command", "; release-check failed: a gh release command the guard cannot read")
        ask(CANNOT)
    if word == "release" and sub is not None and sub not in GH_RELEASE_READ:
        if sub in ("create", "new"):
            if "GH_REPO=" in os.environ["CMD"] or os.environ.get("GH_REPO") or any(
                    a in ("-R", "--repo") or a.startswith("--repo=") or a.startswith("-R") for a in real):
                ask("a gh release on another repository")
            check_release(real[j + 1:], cwds)
        ask(f"a gh release {sub}")
    if word == "extension" and sub == "exec":
        ask("a gh extension or agent run")
    if word == "pr" and sub == "merge":
        if any(unreadable(a) for a in args[i + 1:]):
            ask(CANNOT)
        if any(a == "--admin" or a.startswith("--admin=") for a in real[i + 1:]):
            ask("a merge that bypasses branch protection")
    if word in GH_READ and sub is not None and sub not in GH_READ[word]:
        path = [word, sub]
        if word == "repo" and sub in GH_NESTED:
            k = next_word(args, j + 1)
            if k == len(args):
                return
            if any(unreadable(a) for a in args[j + 1:k + 1]):
                ask(CANNOT)
            if real[k] in GH_NESTED[sub]:
                return
            path.append(real[k])
        ask("a gh " + " ".join(path))


def qualify(name, tag):
    if name.startswith("refs/"):
        return name
    if name.startswith(("heads/", "tags/", "remotes/")):
        return "refs/" + name
    return ("refs/tags/" if tag else "refs/heads/") + name


def refspec(repo, spec, branch):
    src, colon, dst = spec.partition(":")
    if src in ("HEAD", "@"):
        full = f"refs/heads/{branch}" if branch else ""
    elif git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{src}")[0] == 0:
        # allow-comment: dependency, rev-parse prints nothing for a name that is both a branch and a tag.
        full = f"refs/tags/{src}"
    elif git(repo, "rev-parse", "--git-dir")[0]:
        full = qualify(src, False)
    else:
        rc, out = git(repo, "rev-parse", "--symbolic-full-name", src)
        full = out if rc == 0 and out.startswith("refs/") else ""
    if not colon:
        return full, full if full.startswith(("refs/heads/", "refs/tags/")) else ""
    return full, dst if dst in ("HEAD", "@") else qualify(dst, full.startswith("refs/tags/"))


def tag_names(repo, specs, branch):
    # Every tag name the push writes on the remote: the destination of x:refs/tags/v1,
    # a local tag pushed by name, tag <name>. A wildcard counts as many. Each maps to
    # the <src> of a <src>:<dst> refspec, else None (a tag pushed by name).
    names = {}
    for spec in specs:
        spec = spec.lstrip("+")
        if "*" in spec:
            return {"*": None, "**": None}
        dst = refspec(repo, spec, branch)[1]
        if dst.startswith("refs/tags/"):
            names[dst.removeprefix("refs/tags/")] = spec.partition(":")[0] if ":" in spec else None
    return names


def check_push(repo, args, drift):
    if any(EXPANDS & set(a) for a in args):
        ask("a push with an argument the shell expands")
    if "refs" in drift:
        ask("a push after a command that changes refs or config")
    pos, lease, j, no_follow, repo_opt = [], set(), 0, False, None
    while j < len(args):
        a = args[j]
        if a == "--":
            pos += args[j + 1:]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            no_follow |= a == "--no-follow-tags"
            if a in LEASE:
                lease.add(a)
            elif len(name) > 2 and any(m.startswith(name) for m in FORCE):
                ask("a force, delete, mirror, all, prune or tag push")
            # allow-comment: dependency, git takes a unique prefix of an option.
            named = [m for m in PUSH_ARG if m.startswith(name)] if len(name) > 2 else []
            if named == ["--repo"]:
                repo_opt = a.partition("=")[2] or (args[j + 1] if j + 1 < len(args) else "")
            if "=" not in a and len(named) == 1:
                j += 1
        elif a.startswith("-") and len(a) > 1:
            if "f" in a[1:] or "d" in a[1:]:
                ask("a force or delete push")
            if a.endswith("o"):
                j += 1
        else:
            pos.append(a)
        j += 1
    # allow-comment: dependency, push.followTags makes every push a tag push. "/" reads the global and
    # system scopes for a folder made earlier in the line.
    rc, follow = git(repo if os.path.isdir(repo) else "/", "config", "--type=bool", "--get", "push.followTags")
    if (follow == "true" or rc not in (0, 1)) and not no_follow:
        ask("a tag push (push.followTags is set)")
    _, branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    # allow-comment: dependency, with --repo git reads every positional word as a refspec.
    if repo_opt is not None:
        pos = [repo_opt, *pos]
    remote, rest, specs = (pos[0] if pos else ""), pos[1:], []
    while rest:
        w = rest.pop(0)
        specs.append("refs/tags/" + rest.pop(0) if w == "tag" and rest else w)
    if "head" in drift and (not specs or any(s.partition(":")[0] in ("HEAD", "@") for s in specs)):
        ask("a push of HEAD after a command that moves it")
    pushes_to = (git(repo, "config", f"branch.{branch}.pushRemote")[1] or git(repo, "config", "remote.pushDefault")[1]
                 or git(repo, "config", f"branch.{branch}.remote")[1] or "origin")
    if not specs:
        r = remote or pushes_to
        merge = git(repo, "config", f"branch.{branch}.merge")[1]
        if (git(repo, "config", "--get-all", f"remote.{r}.push")[1]
                or git(repo, "config", f"remote.{r}.mirror")[1] == "true"
                or git(repo, "config", "push.default")[1] == "matching"
                or merge and (not merge.startswith("refs/heads/") or merge.removeprefix("refs/heads/") in ("", "main", "master"))):
            ask("a bare push whose remote config can reach other branches")
        specs = ["HEAD"]
    if lease and ("--force-with-lease" not in lease or "/" not in branch or os.environ["AGENT_ID"]
                  or any(s not in ("HEAD", "@", branch, f"HEAD:{branch}") for s in specs)):
        ask("a force, delete, mirror, all, prune or tag push")
    if lease:
        r = pushes_to
        merge = git(repo, "config", f"branch.{branch}.merge")[1]
        if (len(specs) > 1 or remote not in ("", r)
                or git(repo, "config", "--get-all", f"remote.{r}.push")[1]
                or git(repo, "config", f"remote.{r}.mirror")[1] == "true"
                or (merge and merge != f"refs/heads/{branch}")):
            ask("a force, delete, mirror, all, prune or tag push")
    for spec in specs:
        if spec.startswith(("+", ":")) or "*" in spec:
            ask("a forced, deleting or wildcard refspec")
        if ":" not in spec and remote and git(repo, "config", "--get-all", f"remote.{remote}.push")[1]:
            ask("a push whose remote config can reach other branches")
        src, dst = refspec(repo, spec, branch)
        if not src and ":" not in spec and "head" in drift:
            src = dst = qualify(spec, False)
        if src.startswith("refs/tags/") or dst.startswith("refs/tags/"):
            names = tag_names(repo, specs, branch)
            if len(names) != 1 or not fnmatch.fnmatchcase(next(iter(names)), "v*"):
                ask("a tag push")
            (name, src), = names.items()
            # x:refs/tags/v1 tags the commit of x, whatever a local v1 points at.
            if src is None or os.environ["AGENT_ID"] or other_repo(repo):
                ask("a tag push", release_suffix(repo, ["--tag", name]))
            rc, sha = git(repo, "rev-parse", "--verify", "--quiet", f"{src}^{{commit}}")
            if rc:
                ask("a tag push", f"; release-check failed: {src} does not resolve")
            ask(f"a tag push of {sha}", release_suffix(repo, ["--sha", sha]))
        if not dst and spec in ("HEAD", "@"):
            ask("a push from a detached HEAD" if git(repo, "rev-parse", "--git-dir")[0] == 0
                else "a push from an unknown repository")
        if dst in ("refs/heads/main", "refs/heads/master"):
            ask("a push to main or master")
        if not dst.startswith("refs/heads/") or dst == "refs/heads/":
            ask("a push to a ref that is not a feature branch")


def check_git(args, cwds, drift, fed):
    repos, i, config, gitdir = cwds, 0, [], False
    while i < len(args) and args[i].startswith("-"):
        # --git-dir and --work-tree point git at a repo the guard does not resolve.
        gitdir |= args[i].split("=", 1)[0] in ("--git-dir", "--work-tree")
        if args[i] in GLOBAL_ARG and i + 1 < len(args):
            if args[i] == "-C":
                repos = resolve(repos, args[i + 1])
            if args[i] in ("-c", "--config-env"):
                config.append(args[i + 1])
            i += 1
        elif args[i].startswith("--config-env="):
            config.append(args[i])
        i += 1
    sub = args[i] if i < len(args) else ""
    if gitdir:
        repos = repos | {None}
    if sub == "send-pack" or (sub == "subtree" and "push" in args[i + 1:]):
        ask("a send-pack or subtree push")
    if fed and (not sub or sub[0] in "<>"):
        ask("a git command whose subcommand comes from another command")
    if sub != "push":
        # The alias may live in any repo the line can be in. From a folder the guard
        # cannot follow, any command that may be an alias asks.
        if None in repos and sub and sub not in BUILTINS:
            ask("a git command from a folder the guard cannot follow")
        aliases = [c for c in config if c.startswith("alias.")]
        if sub and "push" in " ".join(aliases + [git(START if r is None else r, "config", "--get", f"alias.{sub}")[1]
                                                 for r in repos]):
            ask("a git alias that pushes")
        if sub in MOVES_HEAD:
            drift.add("head")
        if sub in CHANGES_REFS or config:
            drift.add("refs")
        return
    if fed:
        ask("a push whose arguments come from another command")
    if config:
        ask("a push with config overrides")
    if gitdir:
        ask("a push with --git-dir or --work-tree")
    if None in repos:
        ask("a push from a folder the guard cannot follow")
    for r in sorted(repos):
        check_push(r, args[i + 1:], drift)


def check(cmd, cwds, drift, top=False):
    # cwds holds every directory the line can be in at this point. A cd (or
    # pushd) replaces it only when all of these hold, the one case where the next
    # command surely runs in the new directory: the line start, "&&" or ";" comes
    # before it and "&&" follows; its target is absolute or starts with ~; cd is
    # the first word after any NAME=value words; its first argument is no option
    # (-P, --) or stack move (+1); and no HOME= sits anywhere in the line when the
    # target is ~ or ~/x. Else the old ones stay possible, plus None for a target
    # the guard cannot read: a cd before ";" may never have run (after && or ||,
    # inside if, a loop or a function body), true || cd x && git push is
    # (true || cd x) && git push, a push without the cd, and CDPATH, set in the
    # line or the user's profile, can send a relative cd anywhere. A cd that is
    # not the first word (builtin cd, if cd, eval cd) adds None.
    # A ( ), $( ) or backtick body is a subshell: its end adds the cwds from
    # before it back. A ) that closes no subshell means a stray ) (a case pattern,
    # an escaped paren) closed one too early, so the parse is unreliable: cwds
    # becomes every folder seen so far, and no later cd narrows it. The same holds
    # for the whole line when unreliable() finds a quote, escape, comment, heredoc
    # or case that can fool the parser: a cd then only adds its targets.
    try:
        segs = list(segments(mask_quoted(cmd)))
    except ValueError:
        if re.search(r"(?<![\w.-])gh(?![\w.-])", re.sub(r"['\"\\]", "", cmd)):
            ask(CANNOT)
        if re.search(r"(?<![\w.-])git(?![\w.-]).*(\bpush\b|send-pack)", cmd):
            ask("a push the guard cannot parse")
        return
    subshells, seen, lost, prev = [], set(cwds), unreliable(cmd, segs), ""
    for masked, sep in segs:
        seg = [unmask(t) for t in masked]
        if any(os.path.basename(w) in RUNNERS for w in seg):
            for tok in seg:
                if any(c in tok for c in " \t;&|"):
                    check(tok, cwds, drift)
        # NAME=value words before a cd do not stop it from running. CDPATH, HOME
        # or OLDPWD change where it lands, so its target becomes unknown.
        words, moved = [unmask(t) for t in strip_redirects(masked)], False
        # An assignment surely ran before the next command only at the line start or
        # after ";", outside a subshell and a shell script (sh -c), on a line the
        # parser reads reliably.
        sets = seg[1:] if seg[:1] == ["export"] else seg
        if sets and all(re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w) for w in sets) \
                and prev in ("", ";") and top and not subshells and not lost:
            record(sets)
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            moved |= words[0].split("=", 1)[0] in ("CDPATH", "HOME", "OLDPWD")
            words = words[1:]
        if words and words[0] in ("cd", "pushd", "popd"):
            # A bare pushd, an option (cd -P) and a stack move (pushd +1) land in a
            # folder the guard cannot read.
            to = expand(words[1]) if len(words) > 1 else "~" if words[0] == "cd" else "+"
            # A HOME= anywhere in the line may have moved ~ before this cd.
            moved |= "HOME=" in os.environ["CMD"] and (to == "~" or to.startswith("~/"))
            new = {None} if words[0] == "popd" or moved or to.startswith(("-", "+")) else resolve(cwds, to)
            # A relative target never narrows. CDPATH, set earlier in the line or in
            # the user's profile, can send it elsewhere, except after ./ or ../.
            rooted = os.path.isabs(to) or to.startswith("~")
            if not rooted and to.split("/")[0] not in (".", ".."):
                new = new | {None}
            cwds = new if prev in ("", "&&", ";") and sep == "&&" and rooted and not lost else cwds | new
            seen |= cwds
        else:
            # A cd behind a wrapper (builtin, command, eval, if, {, !) is not
            # followed: the line may be anywhere after it.
            if any(w in ("cd", "pushd", "popd") for w in words[1:]):
                cwds = cwds | {None}
                seen |= cwds
            if any(t.startswith("GIT_") and "=" in t for t in seg):
                drift.add("refs")
            gargs = strip_redirects(masked)
            gwords = [unmask(t) for t in gargs]
            # allow-comment: dependency, in <(x) main the shell passes main to git, the parser sees a later segment.
            if sep == "(" and masked and re.search(r"[<>]$", masked[-1]) and "push" in gwords \
                    and any(os.path.basename(t) == "git" for t in gwords):
                ask("a push whose arguments the guard cannot read")
            # Every git word counts, not only the first: in "sudo -u git git commit" the
            # first one is a user name.
            for k, tok in enumerate(gwords):
                if os.path.basename(tok) == "git":
                    fed = any(os.path.basename(t) in FEEDERS or t in ("-exec", "-execdir") for t in gwords[:k])
                    # env -C or --chdir before git runs it in a folder the guard does not follow.
                    chdir = any(t in ("-C", "--chdir") or t.startswith("--chdir=") for t in gwords[:k])
                    check_git(gwords[k + 1:], cwds | {None} if chdir else cwds, drift, fed)
            c = command_index(gwords)
            if c is None or os.path.basename(gwords[c]) not in TEXT:
                for k, tok in enumerate(gwords):
                    if os.path.basename(tok) == "gh":
                        fed = any(os.path.basename(t) in FEEDERS or t in ("-exec", "-execdir") for t in gwords[:k])
                        gh_command(gargs[k + 1:], cwds, fed)
        for c in sep:
            if c == ")" or (c == chr(96) and subshells and subshells[-1][0] == c):
                # A union, not a swap: a ) that closes no subshell (a case pattern,
                # an escaped paren) must not drop a cd that ran.
                if subshells:
                    cwds = subshells.pop()[1] | cwds
                else:
                    cwds, lost = set(seen), True
            elif c in "(" + chr(96):
                subshells.append((c, cwds))
        prev = sep


check(os.environ["CMD"], {os.environ["CWD"]}, set(), top=True)
