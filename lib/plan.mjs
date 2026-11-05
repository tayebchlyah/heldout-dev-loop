import { existsSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { freeBackupPath } from './fsops.mjs'
import { mergeSettings, mergeHooksFile, removeHookCommands } from './merge-json.mjs'
import { mergeCodexConfig } from './merge-toml.mjs'

export function runPlan(actions, { dryRun }) {
  const reports = []
  for (const action of actions) {
    try {
      reports.push(runMerge(action, { dryRun }))
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

// Each action names its own merge, rather than the code guessing it from
// the target's file name: the caller who built the action already knows
// whether it is the Claude settings file, the Codex hooks file or the Codex
// TOML config, and says so directly.
function runMergeResult(action, currentText, fragmentText) {
  if (action.kind === 'merge-toml') return mergeCodexConfig(currentText, fragmentText)
  if (action.merge === 'hooks') return mergeHooksFile(currentText, fragmentText)
  return mergeSettings(currentText, JSON.parse(fragmentText))
}

function runMerge(action, { dryRun }) {
  const currentText = existsSync(action.target) ? readFileSync(action.target, 'utf8') : null
  let fragmentText = readFileSync(action.source, 'utf8')
  if (action.rewrite) fragmentText = action.rewrite(fragmentText)

  // `dropCommands` names the hook commands to remove before the merge. The
  // backup still holds the original text.
  let baseText = currentText
  let removed = []
  if (action.dropCommands && currentText !== null) {
    const result = removeHookCommands(currentText, action.dropCommands(currentText))
    if (result.removed.length > 0) ({ text: baseText, removed } = result)
  }

  const result = runMergeResult(action, baseText, fragmentText)
  removed = [...removed, ...(result.removed ?? [])]
  const changed = 'changed' in result ? result.changed : result.added.length > 0 || removed.length > 0 || (result.updated ?? []).length > 0
  const skipped = result.skipped ?? []
  const notes = result.notes ?? []

  if (!changed) return { target: action.target, action: 'skip', backup: null, added: [], skipped, notes }

  const backup = currentText === null ? null : freeBackupPath(action.target)
  if (!dryRun) {
    if (backup !== null) writeFileSyncSafe(backup, currentText)
    writeFileSyncSafe(action.target, result.text)
  }
  return { target: action.target, action: currentText === null ? 'create' : 'replace', backup, added: result.added ?? [], removed, updated: result.updated ?? [], skipped, notes }
}

function writeFileSyncSafe(path, text) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, text)
}
