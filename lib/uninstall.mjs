import { existsSync, lstatSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { freeBackupPath, renderTemplate } from './fsops.mjs'
import { removeHookCommands } from './merge-json.mjs'
import { CLAUDE_AGENTS, CLAUDE_HOOKS, CODEX_AGENTS, CODEX_HOOKS } from './plan.mjs'

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
    ...CLAUDE_AGENTS.map((name) => join(home, '.claude', 'agents', name)),
    ...CLAUDE_HOOKS.map((name) => join(home, '.claude', 'hooks', name)),
    ...CODEX_AGENTS.map((name) => join(home, '.codex', 'agents', name)),
    ...CODEX_HOOKS.map((name) => join(home, '.codex', 'hooks', name)),
  ]
}

// D9: the vault keeps a guard when a user hook runs a git-guard.sh that is
// not one of the scripts this command moves away. A command is compared as
// a path once its quotes are gone and a leading ~ or $HOME is expanded.
function hasVaultGuard(texts, devLoopCommands, home, movedFiles) {
  const asPath = (command) => command.replace(/^"(.*)"$/, '$1').replace(/^(~|\$HOME|\$\{HOME\})(?=\/)/, home)
  return texts.some((text) => text !== null && commandsOf(JSON.parse(text)).some((command) => (
    /git-guard\.sh"?$/.test(command) && !devLoopCommands.includes(command) && !movedFiles.includes(asPath(command))
  )))
}

const REFUSAL = 'refusing to remove the global install: no hook of yours runs a git-guard.sh outside the dev-loop install, '
  + 'so the vault would lose its guard. Set up the vault guard first (session G), or rerun with --force'

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

// D8, D9. The guard is read from the files as they are, before any change.
export async function runUninstallGlobal({ flags, home, packageRoot }) {
  const settingsPath = join(home, '.claude', 'settings.json')
  const hooksPath = join(home, '.codex', 'hooks.json')
  const settingsText = readOrNull(settingsPath)
  const hooksText = readOrNull(hooksPath)

  const claudeFragment = JSON.parse(readFileSync(join(packageRoot, 'claude', 'settings.fragment.json'), 'utf8'))
  const codexFragment = JSON.parse(renderTemplate(readFileSync(join(packageRoot, 'codex', 'hooks.fragment.json'), 'utf8'), { home }))
  const claudeCommands = commandsOf(claudeFragment)
  const codexCommands = commandsOf(codexFragment)
  const files = globalFiles(home)

  const guarded = hasVaultGuard([settingsText, hooksText], [...claudeCommands, ...codexCommands], home, files)
  const present = files.filter(occupies)
  const edits = [hookEdit(settingsPath, settingsText, claudeCommands), hookEdit(hooksPath, hooksText, codexCommands)]
    .filter((edit) => edit !== null)
  // With nothing to remove, the vault loses nothing, so there is nothing to refuse.
  if (present.length + edits.length > 0 && !guarded && !flags.force) throw new Error(REFUSAL)

  return [
    ...present.map((target) => moveAside(target, flags.dryRun)),
    ...edits.map((edit) => writeHookEdit(edit, flags.dryRun)),
    ...keptItems(home, settingsText, claudeFragment.permissions?.deny ?? []),
  ]
}
