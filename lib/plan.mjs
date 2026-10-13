import { existsSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { placeFile, placeDir, freeBackupPath, renderTemplate } from './fsops.mjs'
import { mergeSettings, mergeHooksFile, removeHookCommands } from './merge-json.mjs'
import { mergeCodexConfig } from './merge-toml.mjs'
import { mergeRules } from './merge-lines.mjs'

export const CLAUDE_AGENTS = ['implementer.md', 'spec-tester.md']
export const CLAUDE_HOOKS = ['gate.sh', 'gate-snapshot.sh', 'tree-hash.sh', 'deny-path.sh', 'deny-acceptance-to-subagents.sh', 'git-guard.sh']
export const CODEX_AGENTS = [
  'implementer.toml', 'spec-tester.toml', 'task-reviewer.toml',
  'simplifier.toml', 'final-reviewer.toml', 'security-reviewer.toml',
]
export const CODEX_HOOKS = ['gate.sh', 'deny-path.sh', 'git-guard.sh', 'session-start.sh']

export function assertLinkSource(repoRoot) {
  const isClone = existsSync(join(repoRoot, '.git')) && existsSync(join(repoRoot, 'skill', 'SKILL.md'))
  if (!isClone) {
    throw new Error('--link needs a Git clone of dev-loop as the working directory')
  }
}

export function buildPlan({ repoRoot, home, harnesses, mode }) {
  const actions = []
  const skillTarget = join(home, '.agents', 'skills', 'dev-loop')

  actions.push({ kind: 'dir', source: join(repoRoot, 'skill'), target: skillTarget, mode })

  if (harnesses.includes('claude')) {
    actions.push({ kind: 'dir', source: skillTarget, target: join(home, '.claude', 'skills', 'dev-loop'), mode: 'link' })
    for (const name of CLAUDE_AGENTS) {
      actions.push({ kind: 'file', source: join(repoRoot, 'claude', 'agents', name), target: join(home, '.claude', 'agents', name), mode })
    }
    for (const name of CLAUDE_HOOKS) {
      actions.push({ kind: 'file', source: join(repoRoot, 'claude', 'hooks', name), target: join(home, '.claude', 'hooks', name), mode, executable: true })
    }
    actions.push({
      kind: 'merge-json',
      merge: 'settings',
      source: join(repoRoot, 'claude', 'settings.fragment.json'),
      target: join(home, '.claude', 'settings.json'),
    })
  }

  if (harnesses.includes('codex')) {
    for (const name of CODEX_AGENTS) {
      actions.push({ kind: 'file', source: join(repoRoot, 'codex', 'agents', name), target: join(home, '.codex', 'agents', name), mode: 'copy' })
    }
    for (const name of CODEX_HOOKS) {
      actions.push({ kind: 'file', source: join(repoRoot, 'codex', 'hooks', name), target: join(home, '.codex', 'hooks', name), mode, executable: true })
    }
    // Ruling R6b: ~/.codex/rules/default.rules is the user's own command
    // allowlist. It is never placed by copy or link, in either mode; it is
    // always merged so a user's own rules survive.
    actions.push({
      kind: 'merge-lines',
      source: join(repoRoot, 'codex', 'rules', 'default.rules'),
      target: join(home, '.codex', 'rules', 'default.rules'),
    })
    actions.push({
      kind: 'merge-json',
      merge: 'hooks',
      source: join(repoRoot, 'codex', 'hooks.fragment.json'),
      target: join(home, '.codex', 'hooks.json'),
      template: true,
    })
    actions.push({
      kind: 'merge-toml',
      source: join(repoRoot, 'codex', 'config.permissions.toml'),
      target: join(home, '.codex', 'config.toml'),
    })
  }

  return actions
}

export function runPlan(actions, { dryRun, home }) {
  const reports = []
  for (const action of actions) {
    try {
      reports.push(runAction(action, { dryRun, home }))
    } catch (error) {
      // A bare error message (e.g. "EISDIR: illegal operation on a
      // directory, read") names no file, so the user has no way to tell
      // which of their configs stopped the install. Carry the target path
      // on every failure without losing the original message.
      throw new Error(`${action.target}: ${error.message}`)
    }
  }
  return reports
}

function runAction(action, { dryRun, home }) {
  if (action.kind === 'dir') {
    return { ...placeDir({ ...action, dryRun }), added: [], skipped: [], notes: [] }
  }
  if (action.kind === 'file') {
    return { ...placeFile({ ...action, dryRun, home: action.template ? home : null }), added: [], skipped: [], notes: [] }
  }
  return runMerge(action, { dryRun, home })
}

// Each action names its own merge, rather than the code guessing it from
// the target's file name: the caller who built the action already knows
// whether it is the Claude settings file, the Codex hooks file, the Codex
// TOML config, or a plain-text rules file, and says so directly.
function runMergeResult(action, currentText, fragmentText) {
  if (action.kind === 'merge-toml') return mergeCodexConfig(currentText, fragmentText)
  if (action.kind === 'merge-lines') return mergeRules(currentText, fragmentText)
  if (action.merge === 'hooks') return mergeHooksFile(currentText, fragmentText)
  return mergeSettings(currentText, JSON.parse(fragmentText))
}

function runMerge(action, { dryRun, home }) {
  const currentText = existsSync(action.target) ? readFileSync(action.target, 'utf8') : null
  let fragmentText = readFileSync(action.source, 'utf8')
  // The rewrite matches the raw `{{HOME}}` token: a rendered home path with
  // a space in it would not match.
  if (action.rewrite) fragmentText = action.rewrite(fragmentText)
  if (action.template) fragmentText = renderTemplate(fragmentText, { home })

  // `dropCommands` names the hook commands to remove before the merge. The
  // backup still holds the original text.
  let baseText = currentText
  let removed = []
  if (action.dropCommands && currentText !== null) {
    const result = removeHookCommands(currentText, action.dropCommands(currentText))
    if (result.removed.length > 0) ({ text: baseText, removed } = result)
  }

  const result = runMergeResult(action, baseText, fragmentText)
  const changed = 'changed' in result ? result.changed : result.added.length > 0 || removed.length > 0
  const skipped = result.skipped ?? []
  const notes = result.notes ?? []

  if (!changed) return { target: action.target, action: 'skip', backup: null, added: [], skipped, notes }

  let backup = null
  if (!dryRun) {
    if (currentText !== null) {
      backup = freeBackupPath(action.target)
      writeFileSyncSafe(backup, currentText)
    }
    writeFileSyncSafe(action.target, result.text)
  }
  return { target: action.target, action: currentText === null ? 'create' : 'replace', backup, added: result.added ?? [], removed, skipped, notes }
}

function writeFileSyncSafe(path, text) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, text)
}
