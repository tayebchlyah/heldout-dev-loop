import test from 'node:test'
import assert from 'node:assert/strict'
import { parseArgs } from '../lib/args.mjs'

test('no flag gives the default shape', () => {
  assert.deepEqual(parseArgs([]), {
    command: null, yes: false, dryRun: false, force: false, harness: null, notes: null,
  })
})

test('every flag parses', () => {
  const flags = parseArgs(['--yes', '--dry-run', '--force', '--harness=codex', '--notes=/tmp/n'])
  assert.deepEqual(flags, {
    command: null, yes: true, dryRun: true, force: true, harness: 'codex', notes: '/tmp/n',
  })
})

test('an unknown flag throws', () => {
  assert.throws(() => parseArgs(['--nope']), /unknown flag: --nope/)
})

test('--vault= is an unknown flag', () => {
  assert.throws(() => parseArgs(['--vault=/tmp/v']), /unknown flag: --vault=\/tmp\/v/)
})

test('an invalid harness throws', () => {
  assert.throws(() => parseArgs(['--harness=vim']), /harness must be claude, codex or both/)
})

test('each command is parsed', () => {
  for (const command of ['init', 'upgrade', 'uninstall-global']) {
    const flags = parseArgs([command, '--yes'])
    assert.equal(flags.command, command)
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

test('the global flag is gone, and --link with it', () => {
  assert.throws(() => parseArgs(['--global']), /unknown flag: --global/)
  assert.throws(() => parseArgs(['init', '--link']), /unknown flag: --link/)
})
