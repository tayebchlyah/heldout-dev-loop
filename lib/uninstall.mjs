import { existsSync, lstatSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { freeBackupPath } from './fsops.mjs'
import { LEGACY_SESSION_START, removeHookCommands } from './merge-json.mjs'

// What the 1.4 global install (and its 1.5.x rerun) wrote, frozen: the kit
// moves on, these lists do not.
const LEGACY_CLAUDE_AGENTS = ['implementer.md', 'spec-tester.md']
const LEGACY_CLAUDE_HOOKS = ['gate.sh', 'gate-snapshot.sh', 'tree-hash.sh', 'deny-path.sh', 'deny-acceptance-to-subagents.sh', 'git-guard.sh']
const LEGACY_CODEX_AGENTS = [
  'implementer.toml', 'spec-tester.toml', 'task-reviewer.toml',
  'simplifier.toml', 'final-reviewer.toml', 'security-reviewer.toml',
]
const LEGACY_CODEX_HOOKS = ['gate.sh', 'deny-path.sh', 'git-guard.sh', 'session-start.sh']
const LEGACY_CLAUDE_COMMANDS = [
  ...LEGACY_SESSION_START,
  ...['git-guard.sh', 'deny-acceptance-to-subagents.sh', 'gate.sh', 'gate-snapshot.sh'].map((name) => `~/.claude/hooks/${name}`),
]
const legacyCodexCommands = (home) => ['session-start.sh', 'git-guard.sh', 'gate.sh'].map((name) => `${home}/.codex/hooks/${name}`)
const LEGACY_DENY = ['Skill(superpowers:verification-before-completion)', 'Read(./.env)', 'Read(./.env.*)']

const entry = (target, action, backup = null, removed = []) => ({ target, action, backup, added: [], removed, skipped: [], notes: [] })

function occupies(path) {
  try {
    lstatSync(path)
    return true
  } catch {
    return false
  }
}

const readOrNull = (path) => (existsSync(path) ? readFileSync(path, 'utf8') : null)

function commandsOf(value) {
  return Object.values(value?.hooks ?? {})
    .filter(Array.isArray)
    .flat()
    .filter((group) => Array.isArray(group?.hooks))
    .flatMap((group) => group.hooks.map((hook) => hook?.command))
    .filter((command) => typeof command === 'string')
}

// The exact paths the 1.4 global install writes, each with its own dev-loop
// name. Nothing else under ~/.agents, ~/.claude or ~/.codex is touched.
function globalFiles(home) {
  return [
    join(home, '.agents', 'skills', 'dev-loop'),
    join(home, '.claude', 'skills', 'dev-loop'),
    ...LEGACY_CLAUDE_AGENTS.map((name) => join(home, '.claude', 'agents', name)),
    ...LEGACY_CLAUDE_HOOKS.map((name) => join(home, '.claude', 'hooks', name)),
    ...LEGACY_CODEX_AGENTS.map((name) => join(home, '.codex', 'agents', name)),
    ...LEGACY_CODEX_HOOKS.map((name) => join(home, '.codex', 'hooks', name)),
  ]
}

// A path in a command whose file name is exactly git-guard.sh: the path
// starts the command or follows a quote or a space, the name follows a / or
// starts the path, and it ends the command or comes before a quote or a space.
const GUARD_PATH = /(?:^|(?<=["'\s]))((?:[^"'\s]*\/)?git-guard\.sh)(?=$|["'\s])/g

// The vault keeps a guard when a user hook runs a git-guard.sh that is not
// one of the two this command moves away. A leading ~ or $HOME is expanded
// before the comparison.
function hasVaultGuard(texts, home) {
  const moved = [join(home, '.claude', 'hooks', 'git-guard.sh'), join(home, '.codex', 'hooks', 'git-guard.sh')]
  const isVaultGuard = (path) => !moved.includes(path.replace(/^(~|\$HOME|\$\{HOME\})(?=\/)/, home))
  return texts.some((text) => text !== null && commandsOf(JSON.parse(text)).some((command) => (
    [...command.matchAll(GUARD_PATH)].some((match) => isVaultGuard(match[1]))
  )))
}

const REFUSAL = 'refusing to remove the global install: no hook of yours runs a git-guard.sh outside the dev-loop install, '
  + 'so the vault would lose its guard. Move the vault guard to a hook outside ~/.claude/hooks/ first, or rerun with --force'

// The hook entries to remove from one file, computed before any write.
function hookEdit(target, text, commands) {
  if (text === null) return null
  const { text: next, removed } = removeHookCommands(text, commands)
  return removed.length === 0 ? null : { target, text, next, removed }
}

function writeHookEdit({ target, text, next, removed }, dryRun) {
  let backup = null
  if (!dryRun) {
    backup = freeBackupPath(target)
    writeFileSync(backup, text)
    writeFileSync(target, next)
  }
  return entry(target, 'replace', backup, removed)
}

function moveAside(target, dryRun) {
  if (dryRun) return entry(target, 'remove')
  const backup = freeBackupPath(target)
  mkdirSync(dirname(backup), { recursive: true })
  renameSync(target, backup)
  return entry(target, 'remove', backup)
}

function removeKitCopies(target, dryRun) {
  if (!dryRun) rmSync(target, { recursive: true, force: true })
  return entry(target, 'remove')
}

function keptItems(home, settingsText, denyRules) {
  const kept = []
  const settings = join(home, '.claude', 'settings.json')
  const deny = settingsText === null ? [] : JSON.parse(settingsText).permissions?.deny ?? []
  if (denyRules.some((rule) => deny.includes(rule))) kept.push({ target: settings, item: 'the Claude deny rules in' })
  const config = join(home, '.codex', 'config.toml')
  if (existsSync(config)) kept.push({ target: config, item: 'the dev-loop tables and default_permissions in' })
  for (const target of [join(home, '.codex', 'rules', 'default.rules'), join(home, '.agents', 'dev-loop.config.json')]) {
    if (existsSync(target)) kept.push({ target, item: null })
  }
  return kept.map(({ target, item }) => ({ ...entry(target, 'kept'), item }))
}

// The guard is read from the files as they are, before any change.
export async function runUninstallGlobal({ flags, home }) {
  const settingsPath = join(home, '.claude', 'settings.json')
  const hooksPath = join(home, '.codex', 'hooks.json')
  const settingsText = readOrNull(settingsPath)
  const hooksText = readOrNull(hooksPath)
  const files = globalFiles(home)

  const guarded = hasVaultGuard([settingsText, hooksText], home)
  const present = files.filter(occupies)
  const edits = [hookEdit(settingsPath, settingsText, LEGACY_CLAUDE_COMMANDS), hookEdit(hooksPath, hooksText, legacyCodexCommands(home))]
    .filter((edit) => edit !== null)
  // With nothing to remove, the vault loses nothing, so there is nothing to refuse.
  if (present.length + edits.length > 0 && !guarded && !flags.force) throw new Error(REFUSAL)

  const kitCopies = join(home, '.codex', 'dev-loop', 'kit')
  const kitEntry = occupies(kitCopies) ? [removeKitCopies(kitCopies, flags.dryRun)] : []

  return [
    ...present.map((target) => moveAside(target, flags.dryRun)),
    ...kitEntry,
    ...edits.map((edit) => writeHookEdit(edit, flags.dryRun)),
    ...keptItems(home, settingsText, LEGACY_DENY),
  ]
}
