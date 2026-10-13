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
    flags: { yes: true, harness: null },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: noAsk,
  })
  assert.deepEqual(config, {
    user_name: 'ada',
    harnesses: ['claude'],
  })
})

test('a flag beats a prompt', async () => {
  const config = await buildConfig({
    flags: { yes: true, harness: 'both' },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: noAsk,
  })
  assert.deepEqual(config.harnesses, ['claude', 'codex'])
})

test('a prompt answer wins over the default, and no prompt asks for a vault', async () => {
  const answers = { 'Your name': 'grace', 'Harness': 'codex' }
  const questions = []
  const config = await buildConfig({
    flags: { yes: false, harness: null },
    home: '/home/x',
    env: { USER: 'ada' },
    ask: async (question, fallback) => {
      questions.push(question)
      return answers[question] ?? fallback
    },
  })
  assert.equal(config.user_name, 'grace')
  assert.deepEqual(config.harnesses, ['codex'])
  assert.deepEqual(questions, ['Your name', 'Harness'])
  assert.equal('vault' in config, false)
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

test('a re-run keeps an existing vault key as it is, and a fresh config has none', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-'))
  const fresh = await buildConfig({ flags: { yes: true, harness: null }, home, env: {}, ask: noAsk })
  assert.equal('vault' in fresh, false)

  mkdirSync(join(home, '.agents'), { recursive: true })
  writeFileSync(configPath(home), JSON.stringify({ vault: '/vaults/kept', user_name: 'ada', harnesses: ['claude'] }))
  const kept = await buildConfig({ flags: { yes: true, harness: null }, home, env: {}, ask: noAsk })
  assert.equal(kept.vault, '/vaults/kept')

  writeFileSync(configPath(home), JSON.stringify({ vault: null, user_name: 'ada', harnesses: ['claude'] }))
  const nulled = await buildConfig({ flags: { yes: true, harness: null }, home, env: {}, ask: noAsk })
  assert.equal(nulled.vault, null)
})

test('writeConfig backs up the file it replaces', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-'))
  writeConfig(home, { vault: '/v', user_name: 'ada', harnesses: ['claude'] }, { dryRun: false })
  const second = writeConfig(home, { vault: '/w', user_name: 'ada', harnesses: ['claude'] }, { dryRun: false })
  assert.ok(second.backup, 'the replaced config must be backed up')
  assert.equal(JSON.parse(readFileSync(second.backup, 'utf8')).vault, '/v')
  assert.equal(JSON.parse(readFileSync(configPath(home), 'utf8')).vault, '/w')
})
