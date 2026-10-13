import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { CLAUDE_AGENTS, CLAUDE_HOOKS, KIT_CODEX_AGENTS, LEGACY_CODEX_AGENTS, CODEX_HOOKS } from '../lib/plan.mjs'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const bin = join(packageRoot, 'bin', 'install.mjs')

function run(home, args) {
  return execFileSync('node', [bin, ...args], {
    cwd: packageRoot,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home, USER: 'ada' },
  })
}

function fails(home, args, pattern) {
  assert.throws(() => run(home, args), (error) => {
    assert.equal(error.status, 1)
    assert.match(error.stdout + error.stderr, pattern)
    return true
  })
}

const DEV_LOOP_FILES = [
  '.agents/skills/dev-loop',
  '.claude/skills/dev-loop',
  ...CLAUDE_AGENTS.map((name) => `.claude/agents/${name}`),
  ...CLAUDE_HOOKS.map((name) => `.claude/hooks/${name}`),
  ...KIT_CODEX_AGENTS.map((name) => `.codex/agents/${name}`),
  ...CODEX_HOOKS.map((name) => `.codex/hooks/${name}`),
]

const FOREIGN_GUARD = '/opt/vault-guard/git-guard.sh'

// A full 1.4 install of both harnesses, then a foreign agent and, unless
// told otherwise, a foreign vault guard in the user settings.
function globalInstall({ guard = true } = {}) {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--global', '--yes', '--harness=both'])
  writeFileSync(join(home, '.claude/agents/other.md'), 'mine\n')
  if (guard) {
    const path = join(home, '.claude/settings.json')
    const settings = JSON.parse(readFileSync(path, 'utf8'))
    settings.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: FOREIGN_GUARD }] })
    writeFileSync(path, JSON.stringify(settings, null, 2))
  }
  return home
}

// Every path under `home`, links not followed, with each file's content,
// so a test can prove a run changed nothing.
function snapshot(home) {
  const result = {}
  const walk = (dir) => {
    for (const name of readdirSync(dir).sort()) {
      const path = join(dir, name)
      const stat = lstatSync(path)
      if (stat.isDirectory()) walk(path)
      else result[path] = stat.isSymbolicLink() ? 'link' : readFileSync(path, 'utf8')
    }
  }
  walk(home)
  return result
}

const commandsIn = (path) => Object.values(JSON.parse(readFileSync(path, 'utf8')).hooks)
  .flat().flatMap((group) => group.hooks.map((hook) => hook.command))

test('uninstall-global moves every dev-loop file to a backup and removes the hook entries', () => {
  const home = globalInstall()
  const output = run(home, ['uninstall-global'])

  assert.match(output, /^uninstalled$/m)
  for (const path of DEV_LOOP_FILES) {
    assert.equal(existsSync(join(home, path)), false, path)
    let present = true
    try { lstatSync(join(home, path)) } catch { present = false }
    assert.equal(present, false, `${path} is gone, link included`)
  }
  const skillBackups = readdirSync(join(home, '.agents/backups/skills'))
  assert.equal(skillBackups.length, 1)
  assert.match(skillBackups[0], /^dev-loop\.bak-/)
  assert.ok(existsSync(join(home, '.agents/backups/skills', skillBackups[0], 'SKILL.md')))
  assert.match(readdirSync(join(home, '.claude/backups/skills'))[0], /^dev-loop\.bak-/)
  for (const name of CLAUDE_HOOKS) {
    assert.ok(readdirSync(join(home, '.claude/hooks')).some((file) => file.startsWith(`${name}.bak-`)), name)
  }
  for (const name of KIT_CODEX_AGENTS) {
    assert.ok(readdirSync(join(home, '.codex/agents')).some((file) => file.startsWith(`${name}.bak-`)), name)
  }

  assert.deepEqual(commandsIn(join(home, '.claude/settings.json')).filter((command) => command.includes('/.claude/hooks/')), [])
  assert.ok(commandsIn(join(home, '.claude/settings.json')).includes(FOREIGN_GUARD))
  assert.ok(readdirSync(join(home, '.claude')).some((file) => file.startsWith('settings.json.bak-')))
  assert.deepEqual(commandsIn(join(home, '.codex/hooks.json')), [])
  assert.ok(readdirSync(join(home, '.codex')).some((file) => file.startsWith('hooks.json.bak-')))

  const settings = JSON.parse(readFileSync(join(home, '.claude/settings.json'), 'utf8'))
  for (const rule of ['Skill(superpowers:verification-before-completion)', 'Read(./.env)', 'Read(./.env.*)']) {
    assert.ok(settings.permissions.deny.includes(rule), rule)
  }
  assert.ok(readFileSync(join(home, '.codex/config.toml'), 'utf8').includes('[permissions.dev-spec-tester]'))
  assert.ok(existsSync(join(home, '.codex/rules/default.rules')))
  assert.ok(existsSync(join(home, '.agents/dev-loop.config.json')))
  assert.equal(readFileSync(join(home, '.claude/agents/other.md'), 'utf8'), 'mine\n')

  for (const item of ['deny rules', '.codex/config.toml', '.codex/rules/default.rules', '.agents/dev-loop.config.json']) {
    assert.match(output, new RegExp(`^\\s*kept .*${item.replace(/\./g, '\\.')}`, 'm'), item)
  }
})

test('uninstall-global still removes the 1.4 simplifier agent', () => {
  assert.ok(LEGACY_CODEX_AGENTS.includes('simplifier.toml'))
  assert.ok(!KIT_CODEX_AGENTS.includes('simplifier.toml'))
  const home = globalInstall()
  writeFileSync(join(home, '.codex/agents/simplifier.toml'), 'name = "simplifier"\n')
  run(home, ['uninstall-global'])
  assert.equal(existsSync(join(home, '.codex/agents/simplifier.toml')), false)
  assert.ok(readdirSync(join(home, '.codex/agents')).some((file) => file.startsWith('simplifier.toml.bak-')))
})

test('uninstall-global with no vault guard refuses, names the guard, where to move it and --force, and changes nothing', () => {
  const home = globalInstall({ guard: false })
  const before = snapshot(home)
  fails(home, ['uninstall-global'], /git-guard\.sh[\s\S]*outside ~\/\.claude\/hooks\/[\s\S]*--force/)
  assert.throws(() => run(home, ['uninstall-global']), (error) => !/session G/.test(error.stderr))
  assert.deepEqual(snapshot(home), before)
})

test('uninstall-global --dry-run still reports the refusal', () => {
  const home = globalInstall({ guard: false })
  const before = snapshot(home)
  fails(home, ['uninstall-global', '--dry-run'], /--force/)
  assert.deepEqual(snapshot(home), before)
})

test('the dev-loop git-guard hook does not count as the vault guard', () => {
  const home = globalInstall({ guard: false })
  const path = join(home, '.claude/settings.json')
  const settings = JSON.parse(readFileSync(path, 'utf8'))
  settings.hooks.Stop.push({ hooks: [{ type: 'command', command: `"${home}/.claude/hooks/git-guard.sh"` }] })
  writeFileSync(path, JSON.stringify(settings, null, 2))
  fails(home, ['uninstall-global'], /--force/)
})

function addHook(home, command) {
  const path = join(home, '.claude/settings.json')
  const settings = JSON.parse(readFileSync(path, 'utf8'))
  settings.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command }] })
  writeFileSync(path, JSON.stringify(settings, null, 2))
}

test('a hook whose script is only named like git-guard.sh does not count as the vault guard', () => {
  const home = globalInstall({ guard: false })
  addHook(home, '~/.claude/hooks/vault-git-guard.sh')
  fails(home, ['uninstall-global'], /--force/)
})

test('a vault git-guard.sh hook with arguments counts as the vault guard', () => {
  const home = globalInstall({ guard: false })
  addHook(home, '/vault/.agents/hooks/git-guard.sh --x')
  run(home, ['uninstall-global'])
  for (const path of DEV_LOOP_FILES) assert.equal(existsSync(join(home, path)), false, path)
})

test('the dev-loop git-guard hooks with arguments, in any HOME form, do not count as the vault guard', () => {
  for (const command of ['~/.claude/hooks/git-guard.sh --x', '$HOME/.codex/hooks/git-guard.sh', '"${HOME}/.claude/hooks/git-guard.sh" --x']) {
    const home = globalInstall({ guard: false })
    addHook(home, command)
    fails(home, ['uninstall-global'], /--force/)
  }
})

test('uninstall-global --force removes the global install with no vault guard', () => {
  const home = globalInstall({ guard: false })
  run(home, ['uninstall-global', '--force'])
  for (const path of DEV_LOOP_FILES) assert.equal(existsSync(join(home, path)), false, path)
})

test('uninstall-global --dry-run changes nothing and says what would change', () => {
  const home = globalInstall()
  const before = snapshot(home)
  const output = run(home, ['uninstall-global', '--dry-run'])
  assert.match(output, /^dry run, nothing written$/m)
  assert.match(output, /would change/)
  assert.deepEqual(snapshot(home), before)
})

test('uninstall-global on a machine with no global install reports nothing to remove and exits 0', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const output = run(home, ['uninstall-global'])
  assert.match(output, /nothing to remove/)
  assert.deepEqual(readdirSync(home), [])
})

test('uninstall-global moves a dangling skill link aside', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.claude/skills'), { recursive: true })
  symlinkSync(join(home, 'nowhere'), join(home, '.claude/skills/dev-loop'))
  run(home, ['uninstall-global', '--force'])
  assert.equal(readdirSync(join(home, '.claude/skills')).length, 0)
  assert.equal(readdirSync(join(home, '.claude/backups/skills')).length, 1)
})
