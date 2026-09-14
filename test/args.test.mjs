import test from 'node:test'
import assert from 'node:assert/strict'
import { parseArgs } from '../lib/args.mjs'

test('no flag gives the default shape', () => {
  assert.deepEqual(parseArgs([]), {
    yes: false, dryRun: false, link: false, harness: null, vault: null,
  })
})

test('every flag parses', () => {
  const flags = parseArgs(['--yes', '--dry-run', '--link', '--harness=codex', '--vault=/tmp/v'])
  assert.deepEqual(flags, {
    yes: true, dryRun: true, link: true, harness: 'codex', vault: '/tmp/v',
  })
})

test('an unknown flag throws', () => {
  assert.throws(() => parseArgs(['--nope']), /unknown flag: --nope/)
})

test('an invalid harness throws', () => {
  assert.throws(() => parseArgs(['--harness=vim']), /harness must be claude, codex or both/)
})
