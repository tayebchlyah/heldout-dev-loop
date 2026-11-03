import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

export const CLAUDE_AGENTS = ['implementer.md', 'spec-tester.md']
export const CLAUDE_HOOKS = ['gate.sh', 'gate-snapshot.sh', 'tree-hash.sh', 'git-pin.sh', 'deny-path.sh', 'deny-acceptance-to-subagents.sh', 'git-guard.sh', 'git-guard.py', 'board.sh', 'seal.sh', 'deny-ask.sh', 'pause.sh', 'worktree-wall.sh']
export const KIT_CODEX_AGENTS = [
  'implementer.toml', 'spec-tester.toml', 'task-reviewer.toml',
  'final-reviewer.toml', 'security-reviewer.toml', 'planner.toml',
]
export const CODEX_HOOKS = ['gate.sh', 'git-pin.sh', 'sandbox.py', 'common.py', 'macos.py', 'macos_policy.py', 'deny-path.sh', 'git-guard.sh', 'git-guard.py', 'session-start.sh', 'board.sh', 'seal.sh', 'acceptance-wall.sh', 'kit-main.sh']

const SKILL_PATH = '.agents/skills/dev-loop'
// Each folder `skills/<name>/` of the package goes to `.agents/skills/<name>/`, for every harness.
export const KIT_SKILLS = ['pause', 'continue', 'release-audit', 'garden', 'release', 'release-notes']
const LINKS = ['dev-loop', ...KIT_SKILLS].map((name) => ({ path: `.claude/skills/${name}`, target: `../../.agents/skills/${name}` }))

// A global hook command is `~/.claude/hooks/<script><args>` or
// `<home>/.codex/hooks/<script><args>`, where <home> is either rendered or
// still the raw `{{HOME}}` token. Any other command (the Claude SessionStart
// one-liner) has no hook path and stays as it is.
const HOOK_COMMAND = /^(\S+)\/\.(claude|codex)\/hooks\/([\w.-]+)(.*)$/s

const CLAUDE_PREFIX = '${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}'

// The Codex hook scripts of the main checkout, the first worktree; a bare one gives none, so the hook blocks (#200).
// Each folder from the hook cwd up to / that holds a .git is a repo. The innermost wins, unless an outer folder is
// the first worktree of its own repo (a gitfile it holds can name a gitdir built elsewhere, whose first worktree is then not
// the folder), that worktree has .dev-loop, and a hook trust entry of the Codex config names a path that resolves to
// it: then the outermost such wins.
// A repo an agent creates below or above the project cannot select the scripts, and the walk reads folders, never a
// work tree its git config names (#212, #222).
const CODEX_MAIN = `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); if [ -z "$i" ]; then m=$w; elif [ -d "$w/.dev-loop" ] && [ "$(cd "$w" 2>/dev/null && pwd -P)" = "$d" ] && sed -n -E 's|^[[]hooks[.]state[.]"(/.*)/[.]codex/hooks[.]json:[^"]*"[]][[:space:]]*$|\\1|p' "\${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | { while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$w" ] && exit 0; done; exit 1; }; then m=$w; fi; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`
const CODEX_HEAD = `f="${CODEX_MAIN}/.dev-loop/hooks/codex/`
const CODEX_RUN = 'exec "$f"'

const OLD_CODEX_PREFIXES = ['$(git rev-parse --show-toplevel)', "$(git worktree list --porcelain | sed -n '1s/^worktree //p')",
  `$(git worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')`,
  `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); { [ -z "$i" ] || [ -d "$w/.dev-loop" ]; } && m=$w; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`,
  `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); { [ -z "$i" ] || { [ -d "$w/.dev-loop" ] && grep -qsF "[hooks.state.\\"$w/.codex/hooks.json:" "\${CODEX_HOME:-$HOME/.codex}/config.toml"; }; } && m=$w; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`,
  `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); if [ -z "$i" ]; then m=$w; elif [ -d "$w/.dev-loop" ] && git -C "$d" worktree list --porcelain | sed -n 's/^worktree //p' | { while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$d" ] && exit 0; done; exit 1; } && sed -n -E 's|^[[]hooks[.]state[.]"(/.*)/[.]codex/hooks[.]json:[^"]*"[]][[:space:]]*$|\\1|p' "\${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | { while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$w" ] && exit 0; done; exit 1; }; then m=$w; fi; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`]

const CODEX_GUARD = '"; [ -x "$f" ] || { echo "Blocked: dev-loop hook script missing: $f" >&2; exit 2; }; '

export function projectCommand(command) {
  const match = HOOK_COMMAND.exec(command)
  if (!match) return command
  const [, home, harness, script, args] = match
  if (harness === 'claude') return home === '~' ? `"${CLAUDE_PREFIX}/.dev-loop/hooks/claude/${script}"${args}` : command
  return `${CODEX_HEAD}${script}${CODEX_GUARD}${CODEX_RUN}${args}`
}

// Each head a kit command starts with; a Codex one, from any version (#158, #200).
const HOOK_HEADS = [['claude', `"${CLAUDE_PREFIX}/.dev-loop/hooks/claude/`], ['codex', CODEX_HEAD],
  ...OLD_CODEX_PREFIXES.flatMap((prefix) => [['codex', `"${prefix}/.dev-loop/hooks/codex/`], ['codex', `f="${prefix}/.dev-loop/hooks/codex/`]])]

// The script and arguments of a Codex kit command, any version (#158, #200): the
// upgrade replaces a command with the same key. A user command (another head, or more
// text than the guard between the script and its run) has none.
export function codexHookKey(command) {
  const head = HOOK_HEADS.find(([harness, start]) => harness === 'codex' && command.startsWith(start))?.[1]
  const script = head && /^([\w.-]+)"/.exec(command.slice(head.length))?.[1]
  if (!script) return null
  const rest = command.slice(head.length + script.length)
  return `${script}"${rest.startsWith(CODEX_GUARD + CODEX_RUN) ? rest.slice(CODEX_GUARD.length + CODEX_RUN.length) : rest.slice(1)}`
}

// The hook commands of a JSON hooks file that run a dev-loop project hook
// script (the form projectCommand writes) missing from `kitPaths`.
export function droppedHookCommands(text, kitPaths) {
  const commands = []
  for (const groups of Object.values(JSON.parse(text).hooks ?? {})) {
    if (!Array.isArray(groups)) continue
    for (const group of groups) {
      if (!Array.isArray(group?.hooks)) continue
      for (const hook of group.hooks) {
        if (typeof hook?.command !== 'string') continue
        for (const [harness, head] of HOOK_HEADS) {
          if (!hook.command.startsWith(head)) continue
          const script = /^([\w.-]+)"/.exec(hook.command.slice(head.length))?.[1]
          if (script && !kitPaths.has(`.dev-loop/hooks/${harness}/${script}`)) commands.push(hook.command)
        }
      }
    }
  }
  return commands
}

// The project command holds double quotes, so a rewritten frontmatter value
// moves to a YAML single-quoted string (the commands hold no single quote).
function rewriteAgent(text) {
  return text.replace(/^(\s*command: )"(.*)"$/gm, (line, key, command) => {
    const next = projectCommand(command)
    return next === command ? line : `${key}'${next}'`
  })
}

function isExecutable(path) {
  return (statSync(path).mode & 0o111) !== 0
}

function walk(dir, relative = '') {
  const files = []
  for (const entry of readdirSync(join(dir, relative), { withFileTypes: true })) {
    const path = relative ? `${relative}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      if (entry.name !== '__pycache__') files.push(...walk(dir, path))
    } else if (!entry.name.endsWith('.pyc') && entry.name !== '.DS_Store') {
      files.push(path)
    }
  }
  return files
}

function kitFile(source, path, rewrite = (text) => text) {
  return { path, content: rewrite(readFileSync(source, 'utf8')), executable: isExecutable(source) }
}

export function buildKit({ packageRoot, harnesses }) {
  const skillRoot = join(packageRoot, 'skill')
  const files = walk(skillRoot)
    .map((path) => kitFile(join(skillRoot, path), `${SKILL_PATH}/${path}`))
  for (const name of KIT_SKILLS) {
    const root = join(packageRoot, 'skills', name)
    files.push(...walk(root).map((path) => kitFile(join(root, path), `.agents/skills/${name}/${path}`)))
  }

  if (harnesses.includes('claude')) {
    for (const name of CLAUDE_AGENTS) {
      files.push(kitFile(join(packageRoot, 'claude', 'agents', name), `.claude/agents/${name}`, rewriteAgent))
    }
    for (const name of CLAUDE_HOOKS) {
      files.push(kitFile(join(packageRoot, 'claude', 'hooks', name), `.dev-loop/hooks/claude/${name}`))
    }
  }

  if (harnesses.includes('codex')) {
    for (const name of KIT_CODEX_AGENTS) {
      files.push(kitFile(join(packageRoot, 'codex', 'agents', name), `.codex/agents/${name}`))
    }
    for (const name of CODEX_HOOKS) {
      files.push(kitFile(join(packageRoot, 'codex', 'hooks', name), `.dev-loop/hooks/codex/${name}`))
    }
    files.push(kitFile(join(packageRoot, 'codex', 'rules', 'default.rules'), '.codex/rules/dev-loop.rules'))
  }

  return { files, links: harnesses.includes('claude') ? LINKS.map((link) => ({ ...link })) : [] }
}

function rewriteHooks(text) {
  const fragment = JSON.parse(text)
  for (const groups of Object.values(fragment.hooks ?? {})) {
    for (const group of groups) {
      for (const hook of group.hooks ?? []) {
        if (typeof hook.command === 'string') hook.command = projectCommand(hook.command)
      }
    }
  }
  return `${JSON.stringify(fragment, null, 2)}\n`
}

// The Claude setup contract for a repo (skill/references/claude.md): the walls
// check_setup.py requires in the project .claude/settings.json. Project only:
// the global fragment must not deny these agents in every repository.
const CLAUDE_REPO_CONTRACT = {
  deny: ['Agent(caveman:cavecrew-reviewer)', 'Agent(caveman:cavecrew-builder)', 'Agent(caveman:cavecrew-investigator)'],
  env: { ENABLE_STOP_REVIEW: '0', ENABLE_COMMIT_REVIEW: '0', SG_PUSH_SWEEP: '0', CLAUDE_CODE_ENABLE_TODO_TOOLS: '1' },
}

function rewriteClaudeSettings(text) {
  const fragment = JSON.parse(rewriteHooks(text))
  fragment.permissions = { ...fragment.permissions, deny: [...(fragment.permissions?.deny ?? []), ...CLAUDE_REPO_CONTRACT.deny] }
  fragment.env = { ...fragment.env, ...CLAUDE_REPO_CONTRACT.env }
  return `${JSON.stringify(fragment, null, 2)}\n`
}

export function projectFragments({ packageRoot, harnesses }) {
  const actions = []
  if (harnesses.includes('claude')) {
    actions.push({
      kind: 'merge-json',
      merge: 'settings',
      source: join(packageRoot, 'claude', 'settings.fragment.json'),
      target: '.claude/settings.json',
      rewrite: rewriteClaudeSettings,
    })
  }
  if (harnesses.includes('codex')) {
    actions.push({
      kind: 'merge-json',
      merge: 'hooks',
      source: join(packageRoot, 'codex', 'hooks.fragment.json'),
      target: '.codex/hooks.json',
      rewrite: rewriteHooks,
    })
    actions.push({
      kind: 'merge-toml',
      source: join(packageRoot, 'codex', 'config.permissions.toml'),
      target: '.codex/config.toml',
      rewrite: (text) => text,
    })
  }
  return actions
}
