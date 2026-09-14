import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildConfig, configPath, writeConfig } from '../lib/config.mjs'

const noAsk = async () => {
  throw new Error('ask must not run when --yes is set')
}

test('--yes takes every default without a prompt', async () => {
  const config = await buildConfig({
    flags: { yes: true, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: noAsk,
  })
  assert.deepEqual(config, {
    vault: null,
    user_name: 'ada',
    harnesses: ['claude'],
  })
})

test('a flag beats a prompt', async () => {
  const config = await buildConfig({
    flags: { yes: true, harness: 'both', vault: '/vaults/mine' },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: noAsk,
  })
  assert.equal(config.vault, '/vaults/mine')
  assert.deepEqual(config.harnesses, ['claude', 'codex'])

  const fromEnv = await buildConfig({
    flags: { yes: true, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada', DEV_LOOP_VAULT: '/vaults/from-env' },
    ask: noAsk,
  })
  assert.equal(fromEnv.vault, '/vaults/from-env')

  const flagBeatsEnv = await buildConfig({
    flags: { yes: true, harness: null, vault: '/vaults/mine' },
    home: '/home/x',
    env: { USER: 'ada', DEV_LOOP_VAULT: '/vaults/from-env' },
    ask: noAsk,
  })
  assert.equal(flagBeatsEnv.vault, '/vaults/mine')

  const emptyEnv = await buildConfig({
    flags: { yes: true, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada', DEV_LOOP_VAULT: '' },
    ask: noAsk,
  })
  assert.equal(emptyEnv.vault, null)
})

test('a prompt answer wins over the default', async () => {
  const answers = { 'Vault path': '/vaults/typed', 'Your name': 'grace', 'Harness': 'codex' }
  const config = await buildConfig({
    flags: { yes: false, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: async (question, fallback) => answers[question] ?? fallback,
  })
  assert.equal(config.vault, '/vaults/typed')
  assert.equal(config.user_name, 'grace')
  assert.deepEqual(config.harnesses, ['codex'])

  const empty = await buildConfig({
    flags: { yes: false, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: async () => '',
  })
  assert.equal(empty.vault, null)

  let asked = false
  const emptyEnvPrompts = await buildConfig({
    flags: { yes: false, harness: null, vault: null },
    home: '/home/x',
    env: { USER: 'ada', DEV_LOOP_VAULT: '' },
    ask: async (question, fallback) => {
      if (question === 'Vault path') asked = true
      return answers[question] ?? fallback
    },
  })
  assert.equal(asked, true)
  assert.equal(emptyEnvPrompts.vault, '/vaults/typed')
})

test('writeConfig writes the file, and --dry-run writes nothing', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-'))
  const config = { vault: '/v', user_name: 'ada', harnesses: ['claude'] }

  const dry = writeConfig(home, config, { dryRun: true })
  assert.equal(dry.changed, true)
  assert.equal(existsSync(configPath(home)), false)

  const wet = writeConfig(home, config, { dryRun: false })
  assert.equal(wet.changed, true)
  assert.deepEqual(JSON.parse(readFileSync(configPath(home), 'utf8')), config)

  const again = writeConfig(home, config, { dryRun: false })
  assert.equal(again.changed, false)
})
