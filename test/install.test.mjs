import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, readdirSync, readlinkSync, existsSync, lstatSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function run(home, args) {
  return execFileSync('node', [join(repoRoot, 'bin', 'install.mjs'), ...args], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USER: 'ada' },
  })
}

test('a copy install places every Claude file and merges the settings', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=claude'])

  assert.ok(existsSync(join(home, '.agents/skills/dev-loop/SKILL.md')))
  assert.ok(lstatSync(join(home, '.claude/skills/dev-loop')).isSymbolicLink())
  assert.ok(existsSync(join(home, '.claude/agents/implementer.md')))
  assert.ok(lstatSync(join(home, '.claude/hooks/gate.sh')).mode & 0o111)

  const settings = JSON.parse(readFileSync(join(home, '.claude/settings.json'), 'utf8'))
  assert.ok(settings.hooks.Stop.length > 0)
  assert.ok(settings.permissions.deny.includes('Read(./.env)'))

  const config = JSON.parse(readFileSync(join(home, '.agents/dev-loop.config.json'), 'utf8'))
  assert.equal(config.user_name, 'ada')
})

test('a second install changes nothing', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=claude'])
  const before = readFileSync(join(home, '.claude/settings.json'), 'utf8')
  const output = run(home, ['--yes', '--harness=claude'])
  assert.equal(readFileSync(join(home, '.claude/settings.json'), 'utf8'), before)
  assert.match(output, /unchanged/)
})

test('an install keeps a foreign hook and a foreign deny rule', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.claude'), { recursive: true })
  writeFileSync(join(home, '.claude/settings.json'), JSON.stringify({
    model: 'opus',
    hooks: { Stop: [{ hooks: [{ type: 'command', command: 'mine.sh' }] }] },
    permissions: { deny: ['Bash(rm -rf /)'] },
  }, null, 2))

  run(home, ['--yes', '--harness=claude'])
  const settings = JSON.parse(readFileSync(join(home, '.claude/settings.json'), 'utf8'))
  assert.equal(settings.model, 'opus')
  assert.ok(settings.permissions.deny.includes('Bash(rm -rf /)'))
  const commands = settings.hooks.Stop.flatMap((group) => group.hooks.map((hook) => hook.command))
  assert.ok(commands.includes('mine.sh'))
  assert.ok(commands.includes('~/.claude/hooks/gate.sh'))
})

test('a dry run writes nothing', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=both', '--dry-run'])
  assert.equal(existsSync(join(home, '.agents/skills/dev-loop')), false)
  assert.equal(existsSync(join(home, '.claude/settings.json')), false)
})

test('a Codex install writes the real home into the hook, with no token left', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=codex'])
  const hooks = readFileSync(join(home, '.codex/hooks.json'), 'utf8')
  assert.ok(!hooks.includes('{{HOME}}'))
  assert.ok(hooks.includes(home))
  const guard = readFileSync(join(home, '.codex/hooks/git-guard.sh'), 'utf8')
  assert.ok(!guard.includes('{{HOME}}'))
  assert.ok(readFileSync(join(home, '.codex/config.toml'), 'utf8').includes('[permissions.dev-spec-tester]'))
})

test('link mode links the skill folder into the clone', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=claude', '--link'])
  const target = join(home, '.agents/skills/dev-loop')
  assert.ok(lstatSync(target).isSymbolicLink())
  assert.ok(existsSync(join(target, 'SKILL.md')))
  assert.ok(lstatSync(join(home, '.claude/agents/implementer.md')).isSymbolicLink())
})

test('link mode fails outside a clone', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const elsewhere = mkdtempSync(join(tmpdir(), 'dev-loop-elsewhere-'))
  assert.throws(() => execFileSync('node', [join(repoRoot, 'bin', 'install.mjs'), '--yes', '--link'], {
    cwd: elsewhere,
    encoding: 'utf8',
    env: { ...process.env, HOME: home },
  }), /--link needs a Git clone of dev-loop/)
})

// --- Additional cases from the controller rulings and known failure modes ---

test('a Codex install over an existing rules file keeps the user rule and appends dev-loop\'s blocks', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.codex/rules'), { recursive: true })
  const userRule = 'prefix_rule(\n    pattern=["deploy"],\n    decision="prompt",\n    justification="My own deploy rule.",\n    match=["deploy"],\n)\n'
  writeFileSync(join(home, '.codex/rules/default.rules'), userRule)

  run(home, ['--yes', '--harness=codex'])
  const text = readFileSync(join(home, '.codex/rules/default.rules'), 'utf8')
  assert.ok(text.includes('My own deploy rule.'))
  const fragment = readFileSync(join(repoRoot, 'codex/rules/default.rules'), 'utf8')
  const blockCount = (fragment.match(/^(prefix_rule|command_rule)\(/gm) ?? []).length
  const addedCount = (text.match(/^(prefix_rule|command_rule)\(/gm) ?? []).length - 1
  assert.equal(addedCount, blockCount)
})

test('a Codex install over a config.toml that already declares [features] keeps the user value and names the skipped table', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.codex'), { recursive: true })
  writeFileSync(join(home, '.codex/config.toml'), 'default_permissions = "dev-workspace"\n\n[features]\nhooks = false\n')

  const output = run(home, ['--yes', '--harness=codex'])
  const text = readFileSync(join(home, '.codex/config.toml'), 'utf8')
  assert.match(text, /hooks = false/)
  assert.match(output, /\[features\]/)
  assert.match(output, /config\.toml/)
})

test('an install with no vault set prints a line about --vault', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const output = run(home, ['--yes', '--harness=claude'])
  assert.match(output, /--vault/)
  assert.match(output, /DEV_LOOP_VAULT/)
})

test('a second install of everything reports every action as unchanged and leaves no new backup', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=both'])
  run(home, ['--yes', '--harness=both'])
  const output = run(home, ['--yes', '--harness=both'])
  assert.match(output, /0 changed/)
  assert.doesNotMatch(output, /backup/)
})

test('a malformed settings.json names the file in the error, and a fixed re-run recovers', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.claude'), { recursive: true })
  writeFileSync(join(home, '.claude/settings.json'), '{ not json at all')

  assert.throws(
    () => run(home, ['--yes', '--harness=claude']),
    (error) => {
      assert.match(error.message, /\.claude\/settings\.json/)
      return true
    },
  )

  writeFileSync(join(home, '.claude/settings.json'), '{}\n')
  const output = run(home, ['--yes', '--harness=claude'])
  assert.match(output, /1 changed/)
  const settings = JSON.parse(readFileSync(join(home, '.claude/settings.json'), 'utf8'))
  assert.ok(settings.hooks.Stop.length > 0)
})

test('a target that is a directory instead of a file names the path in the error', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  mkdirSync(join(home, '.claude/agents/implementer.md'), { recursive: true })

  assert.throws(
    () => run(home, ['--yes', '--harness=claude']),
    (error) => {
      assert.match(error.message, /\.claude\/agents\/implementer\.md/)
      return true
    },
  )
})

test('--link twice in a row is idempotent: no new link and no backup on the second run', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=claude', '--link'])
  const output = run(home, ['--yes', '--harness=claude', '--link'])
  assert.doesNotMatch(output, /backup/)
  const target = join(home, '.claude/agents/implementer.md')
  assert.ok(lstatSync(target).isSymbolicLink())
})

test('a re-run with no --vault keeps the configured vault, and a changed vault is backed up', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const vault = mkdtempSync(join(tmpdir(), 'dev-loop-vault-'))
  run(home, ['--yes', '--harness=claude', `--vault=${vault}`])

  run(home, ['--yes', '--harness=claude'])
  const configFile = join(home, '.agents/dev-loop.config.json')
  assert.equal(JSON.parse(readFileSync(configFile, 'utf8')).vault, vault)

  const other = mkdtempSync(join(tmpdir(), 'dev-loop-vault-'))
  const output = run(home, ['--yes', '--harness=claude', `--vault=${other}`])
  assert.match(output, /backup/)
  assert.equal(JSON.parse(readFileSync(configFile, 'utf8')).vault, other)
  const backup = readdirSync(join(home, '.agents')).find((name) => name.startsWith('dev-loop.config.json.bak-'))
  assert.ok(backup, 'the replaced config must leave a backup')
  assert.equal(JSON.parse(readFileSync(join(home, '.agents', backup), 'utf8')).vault, vault)
})

test('link mode links every Codex hook into the clone, and the hooks file still carries the real home', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  run(home, ['--yes', '--harness=both', '--link'])
  for (const name of ['gate.sh', 'deny-path.sh', 'git-guard.sh', 'session-start.sh']) {
    const target = join(home, '.codex/hooks', name)
    assert.ok(lstatSync(target).isSymbolicLink(), `${name} must be a link in link mode`)
    assert.equal(readlinkSync(target), join(repoRoot, 'codex/hooks', name))
  }
  const hooks = readFileSync(join(home, '.codex/hooks.json'), 'utf8')
  assert.ok(!hooks.includes('{{HOME}}'))
  assert.ok(hooks.includes(join(home, '.codex/hooks/git-guard.sh')))
})
