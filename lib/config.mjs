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
// merging, so a re-run has to start from what is already there: otherwise
// `node bin/install.mjs --link` with no --vault would silently drop a
// configured vault, and both git-guard.sh hooks read that value.
function readConfig(home) {
  const path = configPath(home)
  if (!existsSync(path)) return {}
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    throw new Error(`${path}: ${error.message}`)
  }
}

// Precedence: an explicit --vault wins, then DEV_LOOP_VAULT, then the value
// already in the config file. An explicit but empty `--vault=` is a
// deliberate answer, not an absent one: it clears the vault, and never
// falls through to the environment variable or to the prompt.
async function resolveVault({ flags, env, ask, current }) {
  if (flags.vault !== null) return flags.vault === '' ? null : flags.vault
  if (env.DEV_LOOP_VAULT) return env.DEV_LOOP_VAULT
  const fallback = current ?? null
  if (flags.yes) return fallback
  const answer = await ask('Vault path', fallback ?? '')
  return answer === '' ? null : answer
}

export async function buildConfig({ flags, home, env, ask }) {
  const current = readConfig(home)
  const defaultName = current.user_name ?? env.USER ?? 'the user'

  const vault = await resolveVault({ flags, env, ask, current: current.vault })
  const user_name = flags.yes ? defaultName : await ask('Your name', defaultName)
  const harness = flags.harness ?? (flags.yes ? 'claude' : await ask('Harness', 'claude'))

  return { vault, user_name, harnesses: expand(harness) }
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
