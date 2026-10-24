import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

export const CLAUDE_AGENTS = ['implementer.md', 'spec-tester.md']
export const CLAUDE_HOOKS = ['gate.sh', 'gate-snapshot.sh', 'tree-hash.sh', 'deny-path.sh', 'deny-acceptance-to-subagents.sh', 'git-guard.sh', 'board.sh', 'seal.sh', 'deny-ask.sh', 'pause.sh']
export const KIT_CODEX_AGENTS = [
  'implementer.toml', 'spec-tester.toml', 'task-reviewer.toml',
  'final-reviewer.toml', 'security-reviewer.toml',
]
export const CODEX_HOOKS = ['gate.sh', 'deny-path.sh', 'git-guard.sh', 'session-start.sh', 'board.sh', 'seal.sh', 'acceptance-wall.sh']

const SKILL_PATH = '.agents/skills/dev-loop'
const LINK = { path: '.claude/skills/dev-loop', target: '../../.agents/skills/dev-loop' }

// A global hook command is `~/.claude/hooks/<script><args>` or
// `<home>/.codex/hooks/<script><args>`, where <home> is either rendered or
// still the raw `{{HOME}}` token. Any other command (the Claude SessionStart
// one-liner) has no hook path and stays as it is.
const HOOK_COMMAND = /^(\S+)\/\.(claude|codex)\/hooks\/([\w.-]+)(.*)$/s

const PROJECT_PREFIX = {
  claude: '${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}',
  codex: '$(git rev-parse --show-toplevel)',
}

export function projectCommand(command) {
  const match = HOOK_COMMAND.exec(command)
  if (!match) return command
  const [, home, harness, script, args] = match
  if (harness === 'claude' && home !== '~') return command
  return `"${PROJECT_PREFIX[harness]}/.dev-loop/hooks/${harness}/${script}"${args}`
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
        for (const [harness, prefix] of Object.entries(PROJECT_PREFIX)) {
          const head = `"${prefix}/.dev-loop/hooks/${harness}/`
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
    } else if (!entry.name.endsWith('.pyc')) {
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

  return { files, link: harnesses.includes('claude') ? { ...LINK } : null }
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
