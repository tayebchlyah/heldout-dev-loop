import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tempDir } from './helpers.mjs'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

function fails(args, check) {
  const home = tempDir('dev-loop-home-')
  assert.throws(
    () => execFileSync('node', [join(repoRoot, 'bin', 'install.mjs'), ...args], {
      cwd: repoRoot,
      encoding: 'utf8',
      stdio: 'pipe',
      env: { ...process.env, HOME: home },
    }),
    (error) => {
      assert.equal(error.status, 1)
      check(error.stdout + error.stderr)
      return true
    },
  )
  assert.equal(existsSync(join(home, '.agents')), false)
}

test('no command prints the usage and exits 1', () => {
  fails(['--yes'], (text) => {
    for (const word of ['init', 'upgrade', 'uninstall-global']) assert.ok(text.includes(word), word)
    assert.ok(!text.includes('--global'))
  })
})

test('--global is an unknown flag and installs nothing', () => {
  fails(['--global', '--yes'], (text) => assert.match(text, /unknown flag: --global/))
})
