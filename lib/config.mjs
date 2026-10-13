import { mkdirSync, readFileSync, writeFileSync, existsSync, renameSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { freeBackupPath } from './fsops.mjs'

export function configPath(home) {
  return join(home, '.agents', 'dev-loop.config.json')
}

function expand(harness) {
  return harness === 'both' ? ['claude', 'codex'] : [harness]
}

// The installer's own config file is the only one it writes without
// merging, so a re-run has to start from what is already there.
function readConfig(home) {
  const path = configPath(home)
  if (!existsSync(path)) return {}
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    throw new Error(`${path}: ${error.message}`)
  }
}

export async function buildConfig({ flags, home, env, ask }) {
  const current = readConfig(home)
  const defaultName = current.user_name ?? env.USER ?? 'the user'

  const user_name = flags.yes ? defaultName : await ask('Your name', defaultName)
  const harness = flags.harness ?? (flags.yes ? 'claude' : await ask('Harness', 'claude'))

  const config = { user_name, harnesses: expand(harness) }
  // Legacy 1.4 key, read by the notes_path.py fallback. Remove in 1.6.
  if ('vault' in current) config.vault = current.vault
  return config
}

export function writeConfig(home, config, { dryRun }) {
  const path = configPath(home)
  const next = JSON.stringify(config, null, 2) + '\n'
  const current = existsSync(path) ? readFileSync(path, 'utf8') : null
  if (current === next) return { path, changed: false, backup: null }

  // This file is replaced wholesale, never merged, so it gets the same
  // timestamped backup as every other file the installer replaces.
  let backup = null
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true })
    if (current !== null) {
      backup = freeBackupPath(path)
      renameSync(path, backup)
    }
    writeFileSync(path, next)
  }
  return { path, changed: true, backup }
}
