import test from 'node:test'
import assert from 'node:assert/strict'
import { parseArgs } from '../lib/args.mjs'

test('no flag gives the default shape', () => {
  assert.deepEqual(parseArgs([]), {
    command: null, global: false, yes: false, dryRun: false, link: false, force: false, harness: null, vault: null, notes: null,
  })
})

test('every flag parses', () => {
  const flags = parseArgs(['--global', '--yes', '--dry-run', '--link', '--force', '--harness=codex', '--vault=/tmp/v', '--notes=/tmp/n'])
  assert.deepEqual(flags, {
    command: null, global: true, yes: true, dryRun: true, link: true, force: true, harness: 'codex', vault: '/tmp/v', notes: '/tmp/n',
  })
})

test('an unknown flag throws', () => {
  assert.throws(() => parseArgs(['--nope']), /unknown flag: --nope/)
})

test('an invalid harness throws', () => {
  assert.throws(() => parseArgs(['--harness=vim']), /harness must be claude, codex or both/)
})

test('each command is parsed', () => {
  for (const command of ['init', 'upgrade', 'uninstall-global']) {
    const flags = parseArgs([command, '--yes'])
    assert.equal(flags.command, command)
    assert.equal(flags.global, false)
    assert.equal(flags.yes, true)
  }
})

test('--notes and --force parse with a command', () => {
  const flags = parseArgs(['init', '--notes=/tmp/n', '--force'])
  assert.equal(flags.notes, '/tmp/n')
  assert.equal(flags.force, true)
})

test('two commands throw', () => {
  assert.throws(() => parseArgs(['init', 'upgrade']), /one command at a time/)
})

test('a command with --global throws', () => {
  assert.throws(() => parseArgs(['init', '--global']), /--global cannot be combined with a command/)
  assert.throws(() => parseArgs(['--global', 'upgrade']), /--global cannot be combined with a command/)
})

test('init --link and upgrade --link throw', () => {
  assert.throws(() => parseArgs(['init', '--link']), /--link works only with --global/)
  assert.throws(() => parseArgs(['upgrade', '--link']), /--link works only with --global/)
})
