import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'

export function configPath(home) {
  return join(home, '.agents', 'dev-loop.config.json')
}

function expand(harness) {
  return harness === 'both' ? ['claude', 'codex'] : [harness]
}

async function resolveVault({ flags, env, ask }) {
  if (flags.vault) return flags.vault
  if (env.DEV_LOOP_VAULT) return env.DEV_LOOP_VAULT
  if (flags.yes) return null
  const answer = await ask('Vault path', '')
  return answer === '' ? null : answer
}

export async function buildConfig({ flags, home, env, ask }) {
  const defaultName = env.USER ?? 'the user'

  const vault = await resolveVault({ flags, env, ask })
  const user_name = flags.yes ? defaultName : await ask('Your name', defaultName)
  const harness = flags.harness ?? (flags.yes ? 'claude' : await ask('Harness', 'claude'))

  return { vault, user_name, harnesses: expand(harness) }
}

export function writeConfig(home, config, { dryRun }) {
  const path = configPath(home)
  const next = JSON.stringify(config, null, 2) + '\n'
  const current = existsSync(path) ? readFileSync(path, 'utf8') : null
  if (current === next) return { path, changed: false }
  if (!dryRun) {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, next)
  }
  return { path, changed: true }
}
