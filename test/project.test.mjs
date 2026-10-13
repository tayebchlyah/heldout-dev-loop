import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, readlinkSync, realpathSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { assertValidToml } from './helpers.mjs'
import { buildKit } from '../lib/kit.mjs'
import { compareManifest, sha256 } from '../lib/manifest.mjs'
import { readVersion, releaseNotes, writeKit } from '../lib/project.mjs'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const version = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')).version

function setup() {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const repo = realpathSync(mkdtempSync(join(tmpdir(), 'dev-loop-repo-')))
  execFileSync('git', ['init', '-q', repo])
  return { home, repo }
}

function init(repo, home, args, { cwd = repo, input } = {}) {
  return execFileSync('node', [join(packageRoot, 'bin', 'install.mjs'), 'init', ...args], {
    cwd,
    input,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home },
  })
}

function initFails(repo, home, args, pattern, options) {
  assert.throws(() => init(repo, home, args, options), (error) => {
    assert.equal(error.status, 1)
    assert.match(error.stdout + error.stderr, pattern)
    return true
  })
}

// Every entry of the repository except .git, so a test can prove a failed
// or dry run left the repository as it was.
function entries(repo) {
  return readdirSync(repo).filter((name) => name !== '.git').sort()
}

const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))

test('init --yes writes every path of the layout, and the manifest agrees with the disk', () => {
  const { home, repo } = setup()
  const output = init(repo, home, ['--yes'])

  for (const path of [
    'dev-loop.json', '.gitignore', '.dev-loop/MANIFEST.json',
    '.dev-loop/hooks/claude/gate.sh', '.dev-loop/hooks/claude/gate-snapshot.sh', '.dev-loop/hooks/claude/tree-hash.sh',
    '.dev-loop/hooks/claude/deny-path.sh', '.dev-loop/hooks/claude/deny-acceptance-to-subagents.sh', '.dev-loop/hooks/claude/git-guard.sh',
    '.dev-loop/hooks/codex/gate.sh', '.dev-loop/hooks/codex/deny-path.sh', '.dev-loop/hooks/codex/git-guard.sh', '.dev-loop/hooks/codex/session-start.sh',
    '.agents/skills/dev-loop/SKILL.md',
    '.claude/agents/implementer.md', '.claude/agents/spec-tester.md', '.claude/settings.json',
    '.codex/agents/implementer.toml', '.codex/agents/security-reviewer.toml', '.codex/hooks.json', '.codex/config.toml',
    '.codex/rules/dev-loop.rules',
  ]) {
    assert.ok(existsSync(join(repo, path)), path)
  }
  assert.equal(readlinkSync(join(repo, '.claude/skills/dev-loop')), '../../.agents/skills/dev-loop')
  assert.ok(existsSync(join(repo, '.claude/skills/dev-loop/SKILL.md')))
  assert.equal(existsSync(join(repo, 'dev-loop.local.json')), false)

  const devLoop = readFileSync(join(repo, 'dev-loop.json'), 'utf8')
  assert.equal(devLoop, `${JSON.stringify({ version, harnesses: ['claude', 'codex'] }, null, 2)}\n`)

  const manifestText = readFileSync(join(repo, '.dev-loop/MANIFEST.json'), 'utf8')
  assert.ok(manifestText.endsWith('}\n'))
  const manifest = JSON.parse(manifestText)
  assert.equal(manifest.version, version)
  assert.deepEqual(compareManifest(repo, manifest), { changed: [], missing: [] })
  const kit = buildKit({ packageRoot, harnesses: ['claude', 'codex'] })
  assert.deepEqual(Object.keys(manifest.files).sort(), [...kit.files.map((file) => file.path), kit.link.path].sort())

  assert.match(output, /installed/)
  assert.match(output, / \.claude\/settings\.json/)
  assert.ok(!output.includes(repo), 'report paths are relative to the repository')
})

test('init --harness=claude writes no Codex file', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(existsSync(join(repo, '.codex')), false)
  assert.equal(existsSync(join(repo, '.dev-loop/hooks/codex')), false)
  assert.deepEqual(readJson(join(repo, 'dev-loop.json')).harnesses, ['claude'])
})

test('init --harness=codex writes no Claude file', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=codex'])
  assert.equal(existsSync(join(repo, '.claude')), false)
  assert.equal(existsSync(join(repo, '.dev-loop/hooks/claude')), false)
  assert.ok(existsSync(join(repo, '.agents/skills/dev-loop/SKILL.md')))
  assert.deepEqual(readJson(join(repo, 'dev-loop.json')).harnesses, ['codex'])
})

test('init --dry-run writes nothing and says what would change', () => {
  const { home, repo } = setup()
  const output = init(repo, home, ['--yes', '--dry-run'])
  assert.deepEqual(entries(repo), [])
  assert.match(output, /dry run, nothing written/)
  assert.match(output, /would change/)
})

test('init keeps the hooks and deny rules of an existing .claude/settings.json', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude'))
  writeFileSync(join(repo, '.claude/settings.json'), JSON.stringify({
    model: 'opus',
    hooks: {
      Stop: [{ hooks: [{ type: 'command', command: 'mine.sh' }] }],
      PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'lint.sh' }] }],
    },
    permissions: { deny: ['Bash(rm -rf /)'] },
  }, null, 2))

  init(repo, home, ['--yes', '--harness=claude'])
  const settings = readJson(join(repo, '.claude/settings.json'))
  assert.equal(settings.model, 'opus')
  assert.ok(settings.permissions.deny.includes('Bash(rm -rf /)'))
  assert.ok(settings.permissions.deny.includes('Read(./.env)'))
  const commands = (event) => settings.hooks[event].flatMap((group) => group.hooks.map((hook) => hook.command))
  assert.ok(commands('Stop').includes('mine.sh'))
  assert.ok(commands('PreToolUse').includes('lint.sh'))
  assert.ok(commands('Stop').some((command) => command.includes('/.dev-loop/hooks/claude/gate.sh')))
})

test('init writes a .gitignore when there is none', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(readFileSync(join(repo, '.gitignore'), 'utf8'), 'dev-loop.local.json\n')
})

test('init appends to a .gitignore with no newline at its end', () => {
  const { home, repo } = setup()
  writeFileSync(join(repo, '.gitignore'), 'node_modules')
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(readFileSync(join(repo, '.gitignore'), 'utf8'), 'node_modules\ndev-loop.local.json\n')
})

test('init does not add the .gitignore line twice', () => {
  const { home, repo } = setup()
  writeFileSync(join(repo, '.gitignore'), 'dev-loop.local.json\nnode_modules\n')
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(readFileSync(join(repo, '.gitignore'), 'utf8'), 'dev-loop.local.json\nnode_modules\n')
})

test('init from a subfolder refuses, names the top level and writes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, 'sub'))
  initFails(repo, home, ['--yes'], new RegExp(repo.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), { cwd: join(repo, 'sub') })
  assert.deepEqual(entries(repo), ['sub'])
  assert.deepEqual(readdirSync(join(repo, 'sub')), [])
})

test('init outside a git repository refuses', () => {
  const home = mkdtempSync(join(tmpdir(), 'dev-loop-home-'))
  const dir = mkdtempSync(join(tmpdir(), 'dev-loop-plain-'))
  initFails(dir, home, ['--yes'], /git repository/)
  assert.deepEqual(readdirSync(dir), [])
})

test('init --notes with an existing folder writes dev-loop.local.json with the path as given', () => {
  const { home, repo } = setup()
  mkdirSync(join(home, 'notes'))
  init(repo, home, ['--yes', '--harness=claude', '--notes=~/notes'])
  assert.equal(readFileSync(join(repo, 'dev-loop.local.json'), 'utf8'), `${JSON.stringify({ notes: '~/notes' }, null, 2)}\n`)
})

test('init --notes with a relative path resolves it from the repository', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, 'docs'))
  init(repo, home, ['--yes', '--harness=claude', '--notes=docs'])
  assert.deepEqual(readJson(join(repo, 'dev-loop.local.json')), { notes: 'docs' })
})

test('init --notes with a missing folder fails and writes nothing', () => {
  const { home, repo } = setup()
  initFails(repo, home, ['--yes', '--notes=~/nowhere'], /nowhere/)
  assert.deepEqual(entries(repo), [])
})

test('a second init fails and names upgrade', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  const before = readFileSync(join(repo, 'dev-loop.json'), 'utf8')
  initFails(repo, home, ['--yes', '--harness=codex'], /upgrade/)
  assert.equal(existsSync(join(repo, '.codex')), false)
  assert.equal(readFileSync(join(repo, 'dev-loop.json'), 'utf8'), before)
})

test('init with a malformed .claude/settings.json fails and writes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude'))
  writeFileSync(join(repo, '.claude/settings.json'), '{ not json')
  initFails(repo, home, ['--yes', '--harness=claude'], /settings\.json/)
  assert.deepEqual(entries(repo), ['.claude'])
  assert.deepEqual(readdirSync(join(repo, '.claude')), ['settings.json'])
})

test('init makes every hook script executable', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  for (const harness of ['claude', 'codex']) {
    for (const name of readdirSync(join(repo, '.dev-loop/hooks', harness))) {
      assert.ok(lstatSync(join(repo, '.dev-loop/hooks', harness, name)).mode & 0o111, `${harness}/${name}`)
    }
  }
})

test('init writes Codex files that parse and hook commands that point into the project', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])

  const commands = (hooks) => Object.values(hooks).flat().flatMap((group) => group.hooks.map((hook) => hook.command))

  const codexHooks = readJson(join(repo, '.codex/hooks.json'))
  for (const command of commands(codexHooks.hooks)) {
    assert.ok(command.startsWith('"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/'), command)
  }
  assertValidToml(readFileSync(join(repo, '.codex/config.toml'), 'utf8'))

  const settings = readJson(join(repo, '.claude/settings.json'))
  const hookCommands = commands(settings.hooks).filter((command) => command.includes('hooks/'))
  assert.ok(hookCommands.length > 0)
  for (const command of hookCommands) assert.ok(command.includes('/.dev-loop/hooks/claude/'), command)
  assert.ok(!readFileSync(join(repo, '.claude/settings.json'), 'utf8').includes('~/.claude/hooks'))
  assert.ok(!readFileSync(join(repo, '.codex/hooks.json'), 'utf8').includes(home))
})

test('init asks for the harnesses and the notes folder without --yes', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, 'notes'))
  const output = init(repo, home, [], { input: 'codex\nnotes\n' })
  assert.match(output, /Harnesses \(claude, codex, both\) \[both\]/)
  assert.match(output, /Notes folder \(empty to skip\)/)
  assert.deepEqual(readJson(join(repo, 'dev-loop.json')).harnesses, ['codex'])
  assert.deepEqual(readJson(join(repo, 'dev-loop.local.json')), { notes: 'notes' })
})

test('init refuses an unknown harness answer and writes nothing', () => {
  const { home, repo } = setup()
  initFails(repo, home, [], /claude, codex or both/, { input: 'emacs\n\n' })
  assert.deepEqual(entries(repo), [])
})

test('readVersion reads the package.json version', () => {
  assert.equal(readVersion(packageRoot), version)
})

test('writeKit writes changed files, skips equal ones, links, and deletes files the new kit dropped', () => {
  const repo = mkdtempSync(join(tmpdir(), 'dev-loop-kit-'))
  mkdirSync(join(repo, 'a'), { recursive: true })
  writeFileSync(join(repo, 'a/same.txt'), 'same\n')
  writeFileSync(join(repo, 'a/old.txt'), 'edited\n')
  writeFileSync(join(repo, 'a/gone.txt'), 'gone\n')
  mkdirSync(join(repo, 'skill'))
  mkdirSync(join(repo, 'links'))
  symlinkSync('../skill', join(repo, 'links/kept'))

  const kit = {
    files: [
      { path: 'a/same.txt', content: 'same\n', executable: false },
      { path: 'a/old.txt', content: 'new\n', executable: false },
      { path: 'b/run.sh', content: '#!/bin/sh\n', executable: true },
    ],
    link: { path: 'links/kept', target: '../skill' },
  }
  const oldManifest = { version: '0.0.1', files: { 'a/same.txt': 'x', 'a/old.txt': 'x', 'a/gone.txt': sha256('gone\n'), 'links/kept': 'link:../skill' } }

  const dry = writeKit({ repo, kit, oldManifest, dryRun: true })
  assert.equal(readFileSync(join(repo, 'a/old.txt'), 'utf8'), 'edited\n')
  assert.ok(existsSync(join(repo, 'a/gone.txt')))
  assert.equal(existsSync(join(repo, 'b')), false)

  const reports = writeKit({ repo, kit, oldManifest, dryRun: false })
  assert.deepEqual(dry, reports)
  const byTarget = Object.fromEntries(reports.map((entry) => [entry.target, entry.action]))
  assert.deepEqual(byTarget, {
    [join(repo, 'a/same.txt')]: 'skip',
    [join(repo, 'a/old.txt')]: 'replace',
    [join(repo, 'b/run.sh')]: 'create',
    [join(repo, 'links/kept')]: 'skip',
    [join(repo, 'a/gone.txt')]: 'delete',
  })
  for (const entry of reports) assert.deepEqual([entry.backup, entry.added, entry.skipped, entry.notes], [null, [], [], []])

  assert.equal(readFileSync(join(repo, 'a/old.txt'), 'utf8'), 'new\n')
  assert.equal(lstatSync(join(repo, 'b/run.sh')).mode & 0o777, 0o755)
  assert.equal(existsSync(join(repo, 'a/gone.txt')), false)
  assert.equal(readdirSync(join(repo, 'a')).some((name) => name.includes('.bak')), false)
})

const kitContent = (path) => buildKit({ packageRoot, harnesses: ['claude'] }).files.find((file) => file.path === path).content

test('init refuses a kit file path that holds other content, lists every such path, and writes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/agents'), { recursive: true })
  writeFileSync(join(repo, '.claude/agents/implementer.md'), 'mine\n')
  writeFileSync(join(repo, '.claude/agents/spec-tester.md'), 'also mine\n')
  initFails(repo, home, ['--yes', '--harness=claude'], /\.claude\/agents\/implementer\.md[\s\S]*--force/)
  initFails(repo, home, ['--yes', '--harness=claude'], /\.claude\/agents\/spec-tester\.md/)
  assert.deepEqual(entries(repo), ['.claude'])
  assert.deepEqual(readdirSync(join(repo, '.claude/agents')), ['implementer.md', 'spec-tester.md'])
  assert.equal(readFileSync(join(repo, '.claude/agents/implementer.md'), 'utf8'), 'mine\n')
})

test('init --force moves a kit file path that holds other content to a backup, then writes the kit', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/agents'), { recursive: true })
  writeFileSync(join(repo, '.claude/agents/implementer.md'), 'mine\n')
  const output = init(repo, home, ['--yes', '--harness=claude', '--force'])
  const backups = readdirSync(join(repo, '.claude/agents')).filter((name) => name.startsWith('implementer.md.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readFileSync(join(repo, '.claude/agents', backups[0]), 'utf8'), 'mine\n')
  assert.equal(readFileSync(join(repo, '.claude/agents/implementer.md'), 'utf8'), kitContent('.claude/agents/implementer.md'))
  assert.match(output, /backup .*implementer\.md\.bak-/)
})

test('init accepts a kit file path that already holds the kit content', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/agents'), { recursive: true })
  writeFileSync(join(repo, '.claude/agents/implementer.md'), kitContent('.claude/agents/implementer.md'))
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(readdirSync(join(repo, '.claude/agents')).some((name) => name.includes('.bak')), false)
})

test('writeKit replaces a link that points elsewhere', () => {
  const repo = mkdtempSync(join(tmpdir(), 'dev-loop-kit-'))
  mkdirSync(join(repo, 'links'))
  symlinkSync('../other', join(repo, 'links/kept'))
  const reports = writeKit({ repo, kit: { files: [], link: { path: 'links/kept', target: '../skill' } }, oldManifest: null, dryRun: false })
  assert.deepEqual(reports.map((entry) => entry.action), ['replace'])
  assert.equal(readlinkSync(join(repo, 'links/kept')), '../skill')
})

test('init refuses a real directory at the skill link path, names it, and writes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/skills/dev-loop'), { recursive: true })
  writeFileSync(join(repo, '.claude/skills/dev-loop/mine.md'), 'mine\n')
  initFails(repo, home, ['--yes', '--harness=claude'], /\.claude\/skills\/dev-loop/)
  assert.deepEqual(entries(repo), ['.claude'])
  assert.equal(readFileSync(join(repo, '.claude/skills/dev-loop/mine.md'), 'utf8'), 'mine\n')
})

test('init replaces a skill link that points elsewhere', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/skills'), { recursive: true })
  symlinkSync('../../elsewhere', join(repo, '.claude/skills/dev-loop'))
  init(repo, home, ['--yes', '--harness=claude'])
  assert.equal(readlinkSync(join(repo, '.claude/skills/dev-loop')), '../../.agents/skills/dev-loop')
})

test('init refuses a symlinked .codex folder that leads out of the repository, and writes nothing', () => {
  const { home, repo } = setup()
  const outside = mkdtempSync(join(tmpdir(), 'dev-loop-outside-'))
  symlinkSync(outside, join(repo, '.codex'))
  initFails(repo, home, ['--yes'], /\.codex/)
  assert.deepEqual(readdirSync(outside), [])
  assert.deepEqual(entries(repo), ['.codex'])
})

test('init refuses a .claude/settings.json that is a symlink, and writes nothing', () => {
  const { home, repo } = setup()
  const outside = mkdtempSync(join(tmpdir(), 'dev-loop-outside-'))
  writeFileSync(join(outside, 'settings.json'), '{}\n')
  mkdirSync(join(repo, '.claude'))
  symlinkSync(join(outside, 'settings.json'), join(repo, '.claude/settings.json'))
  initFails(repo, home, ['--yes', '--harness=claude'], /\.claude\/settings\.json/)
  assert.equal(readFileSync(join(outside, 'settings.json'), 'utf8'), '{}\n')
  assert.deepEqual(entries(repo), ['.claude'])
})

test('init refuses a symlinked skill folder and a symlinked .gitignore', () => {
  for (const path of ['.agents/skills/dev-loop', '.gitignore']) {
    const { home, repo } = setup()
    const outside = mkdtempSync(join(tmpdir(), 'dev-loop-outside-'))
    mkdirSync(dirname(join(repo, path)), { recursive: true })
    symlinkSync(outside, join(repo, path))
    initFails(repo, home, ['--yes', '--harness=claude'], new RegExp(path.replace(/\./g, '\\.')))
    assert.deepEqual(readdirSync(outside), [])
  }
})

test('init does not expand ~user in the notes path', () => {
  const { home, repo } = setup()
  mkdirSync(join(home, 'user/x'), { recursive: true })
  initFails(repo, home, ['--yes', '--harness=claude', '--notes=~user/x'], /~user\/x/)
  assert.deepEqual(entries(repo), [])
})

test('init backs up an existing dev-loop.local.json before replacing it', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, 'docs'))
  writeFileSync(join(repo, 'dev-loop.local.json'), '{"notes": "old"}\n')
  init(repo, home, ['--yes', '--harness=claude', '--notes=docs'])
  assert.deepEqual(readJson(join(repo, 'dev-loop.local.json')), { notes: 'docs' })
  const backups = readdirSync(repo).filter((name) => name.startsWith('dev-loop.local.json.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readFileSync(join(repo, backups[0]), 'utf8'), '{"notes": "old"}\n')
})

// A copy of the package with another version, so that upgrade has something
// to move to. `edit` changes the copy before it is used.
function fakePackage(nextVersion, edit = () => {}) {
  const root = mkdtempSync(join(tmpdir(), 'dev-loop-pkg-'))
  for (const name of ['bin', 'lib', 'skill', 'claude', 'codex', 'package.json']) {
    cpSync(join(packageRoot, name), join(root, name), { recursive: true })
  }
  const pkg = readJson(join(root, 'package.json'))
  writeFileSync(join(root, 'package.json'), `${JSON.stringify({ ...pkg, version: nextVersion }, null, 2)}\n`)
  edit(root)
  return root
}

function run(pkg, command, repo, home, args) {
  return execFileSync('node', [join(pkg, 'bin', 'install.mjs'), command, ...args], {
    cwd: repo,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home },
  })
}

const upgrade = (repo, home, args = [], pkg = packageRoot) => run(pkg, 'upgrade', repo, home, args)

function upgradeFails(repo, home, args, pattern, pkg = packageRoot) {
  assert.throws(() => upgrade(repo, home, args, pkg), (error) => {
    assert.equal(error.status, 1)
    assert.match(error.stdout + error.stderr, pattern)
    return true
  })
}

// Every path under the repository (except .git) with its content or link
// target, so a test can prove a run changed nothing.
function snapshot(repo, relative = '') {
  const result = {}
  for (const name of readdirSync(join(repo, relative)).sort()) {
    if (relative === '' && name === '.git') continue
    const path = relative ? `${relative}/${name}` : name
    const stat = lstatSync(join(repo, path))
    if (stat.isSymbolicLink()) result[path] = `link:${readlinkSync(join(repo, path))}`
    else if (stat.isDirectory()) Object.assign(result, { [path]: 'dir' }, snapshot(repo, path))
    else result[path] = `${stat.mode}:${readFileSync(join(repo, path), 'utf8')}`
  }
  return result
}

test('upgrade to a newer package sets the new version in dev-loop.json and MANIFEST.json, and keeps other keys', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  const devLoop = readJson(join(repo, 'dev-loop.json'))
  writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ ...devLoop, extra: { keep: true } }, null, 2)}\n`)

  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.deepEqual(readJson(join(repo, 'dev-loop.json')), { version: '99.0.0', harnesses: ['claude'], extra: { keep: true } })
  const manifest = readJson(join(repo, '.dev-loop/MANIFEST.json'))
  assert.equal(manifest.version, '99.0.0')
  assert.deepEqual(compareManifest(repo, manifest), { changed: [], missing: [] })
  assert.equal(existsSync(join(repo, '.codex')), false, 'upgrade reads the harnesses from dev-loop.json')
  assert.doesNotMatch(output, /## \[/, 'no CHANGELOG.md in the package prints no notes')
})

test('upgrade replaces a kit file the package changed, with no backup', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  const pkg = fakePackage('99.0.0', (root) => writeFileSync(join(root, 'skill/SKILL.md'), 'new skill\n'))
  upgrade(repo, home, [], pkg)
  assert.equal(readFileSync(join(repo, '.agents/skills/dev-loop/SKILL.md'), 'utf8'), 'new skill\n')
  assert.equal(readdirSync(join(repo, '.agents/skills/dev-loop')).some((name) => name.includes('.bak')), false)
})

test('upgrade deletes a kit file the package dropped, and the folder it leaves empty', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    mkdirSync(join(root, 'skill/extra/deep'), { recursive: true })
    writeFileSync(join(root, 'skill/extra/deep/notes.md'), 'notes\n')
  })
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  assert.ok(existsSync(join(repo, '.agents/skills/dev-loop/extra/deep/notes.md')))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.equal(existsSync(join(repo, '.agents/skills/dev-loop/extra')), false)
  assert.ok(existsSync(join(repo, '.agents/skills/dev-loop/SKILL.md')))
  assert.equal('.agents/skills/dev-loop/extra/deep/notes.md' in readJson(join(repo, '.dev-loop/MANIFEST.json')).files, false)
})

test('upgrade of a project with the 1.5.0 simplifier agent deletes it', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    writeFileSync(join(root, 'codex/agents/simplifier.toml'), 'name = "simplifier"\n')
    const plan = join(root, 'lib/kit.mjs')
    writeFileSync(plan, readFileSync(plan, 'utf8').replace("'final-reviewer.toml',", "'simplifier.toml', 'final-reviewer.toml',"))
  })
  run(older, 'init', repo, home, ['--yes', '--harness=codex'])
  assert.ok(existsSync(join(repo, '.codex/agents/simplifier.toml')))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.equal(existsSync(join(repo, '.codex/agents/simplifier.toml')), false)
  assert.ok(existsSync(join(repo, '.codex/agents/final-reviewer.toml')))
})

// The SessionStart command a 1.5.0 project holds in its .claude/settings.json.
const SESSION_START_1_5 = 'd="${CLAUDE_PROJECT_DIR:-.}"; { [ -f "$d/dev-loop.json" ] || grep -qs \'^vault_notes:\' "$d/AGENTS.md"; } && echo \'This repo runs the dev-loop skill. Invoke it before any other action.\'; exit 0'

test('upgrade from 1.5 leaves one SessionStart entry', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    const path = join(root, 'claude/settings.fragment.json')
    const fragment = readJson(path)
    fragment.hooks.SessionStart[0].hooks[0].command = SESSION_START_1_5
    writeFileSync(path, JSON.stringify(fragment, null, 2))
  })
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  custom.hooks.SessionStart[0].hooks.push({ type: 'command', command: 'echo mine' })
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  const commands = readJson(settings).hooks.SessionStart.flatMap((group) => group.hooks.map((hook) => hook.command))
  const current = readJson(join(packageRoot, 'claude/settings.fragment.json')).hooks.SessionStart[0].hooks[0].command
  assert.deepEqual(commands.filter((command) => command.includes('dev-loop.json')), [current])
  assert.ok(commands.includes('echo mine'))
})

test('upgrade refuses a local edit to a kit file, names it, and writes nothing', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  writeFileSync(join(repo, '.claude/agents/implementer.md'), 'mine\n')
  const before = snapshot(repo)
  const pkg = fakePackage('99.0.0', (root) => writeFileSync(join(root, 'skill/SKILL.md'), 'new skill\n'))
  upgradeFails(repo, home, [], /\.claude\/agents\/implementer\.md[\s\S]*move the change to dev-loop first/, pkg)
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade refuses when the user deleted a kit file, as for an edit', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  rmSync(join(repo, '.dev-loop/hooks/codex/gate.sh'))
  const before = snapshot(repo)
  upgradeFails(repo, home, [], /\.dev-loop\/hooks\/codex\/gate\.sh[\s\S]*move the change to dev-loop first/, fakePackage('99.0.0'))
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade --force replaces an edited kit file and restores a deleted one', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const original = readFileSync(join(repo, '.claude/agents/implementer.md'), 'utf8')
  writeFileSync(join(repo, '.claude/agents/implementer.md'), 'mine\n')
  rmSync(join(repo, '.dev-loop/hooks/codex/gate.sh'))
  upgrade(repo, home, ['--force'], fakePackage('99.0.0'))
  assert.equal(readFileSync(join(repo, '.claude/agents/implementer.md'), 'utf8'), original)
  assert.ok(lstatSync(join(repo, '.dev-loop/hooks/codex/gate.sh')).mode & 0o111)
  assert.deepEqual(compareManifest(repo, readJson(join(repo, '.dev-loop/MANIFEST.json'))), { changed: [], missing: [] })
})

test('upgrade to an older package refuses, names git revert, and writes nothing', () => {
  const { home, repo } = setup()
  run(fakePackage('99.1.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const before = snapshot(repo)
  upgradeFails(repo, home, [], /git revert/, fakePackage('99.0.10'))
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade compares versions as three numbers', () => {
  const { home, repo } = setup()
  run(fakePackage('99.9.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  upgrade(repo, home, [], fakePackage('99.10.0'))
  assert.equal(readJson(join(repo, 'dev-loop.json')).version, '99.10.0')
})

test('upgrade with the same version applies the kit again', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const before = snapshot(repo)
  const output = upgrade(repo, home)
  assert.match(output, /0 changed/)
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade with no dev-loop.json refuses and names init', () => {
  const { home, repo } = setup()
  upgradeFails(repo, home, [], /dev-loop\.json not found: run init first/)
  assert.deepEqual(entries(repo), [])
})

test('upgrade --dry-run writes nothing', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const before = snapshot(repo)
  const pkg = fakePackage('99.0.0', (root) => writeFileSync(join(root, 'skill/SKILL.md'), 'new skill\n'))
  const output = upgrade(repo, home, ['--dry-run'], pkg)
  assert.match(output, /dry run, nothing written/)
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade refuses a symlinked .codex folder that leads out of the repository, and writes nothing', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const outside = join(mkdtempSync(join(tmpdir(), 'dev-loop-outside-')), 'codex')
  renameSync(join(repo, '.codex'), outside)
  symlinkSync(outside, join(repo, '.codex'))
  const before = snapshot(outside)
  upgradeFails(repo, home, [], /\.codex/, fakePackage('99.0.0'))
  assert.deepEqual(snapshot(outside), before)
})

test('upgrade prints the release notes of the package CHANGELOG.md', () => {
  const { home, repo } = setup()
  run(fakePackage('99.0.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const pkg = fakePackage('99.2.0', (root) => writeFileSync(join(root, 'CHANGELOG.md'), [
    '# Changelog', '', '## [99.2.0] - 2026-10-13', '', '- two', '', '## [v99.1.0]', '', '- one', '', '## [99.0.0]', '', '- zero', '',
  ].join('\n')))
  const output = upgrade(repo, home, [], pkg)
  assert.match(output, /- two/)
  assert.match(output, /- one/)
  assert.doesNotMatch(output, /- zero/)
})

test('releaseNotes returns the sections after the old version, up to and including the new one', () => {
  const changelog = [
    '# Changelog',
    '',
    '## [Unreleased]',
    '',
    '- later',
    '',
    '## [v1.6.0] - 2026-11-01',
    '',
    '- six',
    '',
    '## [1.5.1] - 2026-10-20',
    '',
    '- five one',
    '',
    '## [v1.5.0] - 2026-10-13',
    '',
    '- five',
    '',
    '## [1.4.0] - 2026-09-01',
    '',
    '- four',
    '',
  ].join('\n')
  assert.equal(releaseNotes(changelog, '1.4.0', '1.5.1'), '## [1.5.1] - 2026-10-20\n\n- five one\n\n## [v1.5.0] - 2026-10-13\n\n- five')
  assert.equal(releaseNotes(changelog, '1.5.0', '1.6.0'), '## [v1.6.0] - 2026-11-01\n\n- six\n\n## [1.5.1] - 2026-10-20\n\n- five one')
  assert.equal(releaseNotes(changelog, '1.5.1', '1.5.1'), '')
  assert.equal(releaseNotes(changelog, '1.4.0', '1.10.0'), releaseNotes(changelog, '1.4.0', '1.6.0'))
})

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function addManifestEntry(repo, key, value) {
  const path = join(repo, '.dev-loop/MANIFEST.json')
  const manifest = readJson(path)
  manifest.files[key] = value
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`)
}

test('upgrade refuses an old manifest path outside the kit locations, with and without --force, and deletes nothing', () => {
  for (const force of [[], ['--force']]) {
    const victim = mkdtempSync(join(tmpdir(), 'dev-loop-victim-'))
    writeFileSync(join(victim, 'keep.txt'), 'keep\n')
    const cases = [
      [`../${victim.split('/').pop()}`, 'sha256:0'],
      [`../${victim.split('/').pop()}/keep.txt`, 'sha256:0'],
      [join(victim, 'keep.txt'), 'sha256:0'],
      ['a/../../x', 'sha256:0'],
      ['.agents/skills/dev-loop/../../../x', 'sha256:0'],
      ['.claude/agents/./x', 'sha256:0'],
      ['.claude/agents//x', 'sha256:0'],
      ['.claude/agents\\x', 'sha256:0'],
      ['', 'sha256:0'],
      ['README.md', 'sha256:0'],
      ['.agents/skills/dev-loop', 'sha256:0'],
      ['..', 'link:..'],
    ]
    for (const [key, value] of cases) {
      const { home, repo } = setup()
      init(repo, home, ['--yes'])
      writeFileSync(join(repo, 'README.md'), 'readme\n')
      addManifestEntry(repo, key, value)
      const before = snapshot(repo)
      upgradeFails(repo, home, force, new RegExp(`does not own: ${escape(key)}$`, 'm'), fakePackage('99.0.0'))
      assert.deepEqual(snapshot(repo), before, key)
      assert.equal(readFileSync(join(victim, 'keep.txt'), 'utf8'), 'keep\n', key)
    }
  }
})

test('upgrade refuses a folder at a dropped kit file entry, with --force, and writes nothing', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => writeFileSync(join(root, 'skill/extra.md'), 'extra\n'))
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  rmSync(join(repo, '.agents/skills/dev-loop/extra.md'))
  mkdirSync(join(repo, '.agents/skills/dev-loop/extra.md/mine'), { recursive: true })
  writeFileSync(join(repo, '.agents/skills/dev-loop/extra.md/mine/keep.txt'), 'keep\n')
  const before = snapshot(repo)
  upgradeFails(repo, home, ['--force'], /\.agents\/skills\/dev-loop\/extra\.md/, fakePackage('99.0.0'))
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade --force removes a dropped kit file that is a symlink, and keeps the file it points to', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => writeFileSync(join(root, 'skill/extra.md'), 'extra\n'))
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  const outside = join(mkdtempSync(join(tmpdir(), 'dev-loop-outside-')), 'target.txt')
  writeFileSync(outside, 'outside\n')
  rmSync(join(repo, '.agents/skills/dev-loop/extra.md'))
  symlinkSync(outside, join(repo, '.agents/skills/dev-loop/extra.md'))
  upgrade(repo, home, ['--force'], fakePackage('99.0.0'))
  assert.equal(lstatOrNull(join(repo, '.agents/skills/dev-loop/extra.md')), null)
  assert.equal(readFileSync(outside, 'utf8'), 'outside\n')
})

test('upgrade refuses a dev-loop.json with bad harnesses, and writes nothing', () => {
  for (const harnesses of [undefined, [], ['emacs'], 'claude', ['claude', 'both']]) {
    const { home, repo } = setup()
    init(repo, home, ['--yes'])
    const devLoop = readJson(join(repo, 'dev-loop.json'))
    writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ ...devLoop, harnesses })}\n`)
    const before = snapshot(repo)
    upgradeFails(repo, home, [], /dev-loop\.json harnesses must be a non-empty list of claude and codex/, fakePackage('99.0.0'))
    assert.deepEqual(snapshot(repo), before)
  }
})

function lstatOrNull(path) {
  try {
    return lstatSync(path)
  } catch {
    return null
  }
}

test('upgrade --force refuses a real folder at a dropped skill link, and writes nothing', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ version, harnesses: ['codex'] }, null, 2)}\n`)
  unlinkSync(join(repo, '.claude/skills/dev-loop'))
  mkdirSync(join(repo, '.claude/skills/dev-loop'))
  writeFileSync(join(repo, '.claude/skills/dev-loop/mine.md'), 'mine\n')
  const before = snapshot(repo)
  upgradeFails(repo, home, ['--force'], /\.claude\/skills\/dev-loop is a folder/, fakePackage('99.0.0'))
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade removes the skill link when dev-loop.json drops the claude harness', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ version, harnesses: ['codex'] }, null, 2)}\n`)
  upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.equal(lstatOrNull(join(repo, '.claude/skills')), null)
  assert.equal(lstatOrNull(join(repo, '.claude/agents')), null)
  assert.ok(existsSync(join(repo, '.claude/settings.json')))
})

test('upgrade --force moves a dropped path that no longer matches the old manifest to a backup', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  writeFileSync(join(repo, '.claude/agents/mine.md'), 'mine\n')
  addManifestEntry(repo, '.claude/agents/mine.md', 'sha256:00')
  upgrade(repo, home, ['--force'], fakePackage('99.0.0'))
  assert.equal(lstatOrNull(join(repo, '.claude/agents/mine.md')), null)
  const backups = readdirSync(join(repo, '.claude/agents')).filter((name) => name.startsWith('mine.md.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readFileSync(join(repo, '.claude/agents', backups[0]), 'utf8'), 'mine\n')
})

test('upgrade --force keeps the backup of a dropped path in a skills folder beside it, never through a backups link', () => {
  const { home, repo } = setup()
  const out = mkdtempSync(join(tmpdir(), 'dev-loop-out-'))
  init(repo, home, ['--yes', '--harness=claude'])
  mkdirSync(join(repo, '.agents/skills/dev-loop/skills'))
  writeFileSync(join(repo, '.agents/skills/dev-loop/skills/x'), 'x\n')
  symlinkSync(out, join(repo, '.agents/skills/dev-loop/backups'))
  addManifestEntry(repo, '.agents/skills/dev-loop/skills/x', 'sha256:00')
  upgrade(repo, home, ['--force'], fakePackage('99.0.0'))
  assert.deepEqual(readdirSync(out), [])
  const backups = readdirSync(join(repo, '.agents/skills/dev-loop/skills')).filter((name) => name.startsWith('x.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readFileSync(join(repo, '.agents/skills/dev-loop/skills', backups[0]), 'utf8'), 'x\n')
})

test('upgrade --force keeps the backup of a dropped skill link beside it, never through a backups link', () => {
  const { home, repo } = setup()
  const out = mkdtempSync(join(tmpdir(), 'dev-loop-out-'))
  init(repo, home, ['--yes'])
  symlinkSync(out, join(repo, '.claude/backups'))
  unlinkSync(join(repo, '.claude/skills/dev-loop'))
  symlinkSync('/anywhere', join(repo, '.claude/skills/dev-loop'))
  writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ version, harnesses: ['codex'] }, null, 2)}\n`)
  upgrade(repo, home, ['--force'], fakePackage('99.0.0'))
  assert.deepEqual(readdirSync(out), [])
  assert.equal(lstatOrNull(join(repo, '.claude/skills/dev-loop')), null)
  const backups = readdirSync(join(repo, '.claude/skills')).filter((name) => name.startsWith('dev-loop.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readlinkSync(join(repo, '.claude/skills', backups[0])), '/anywhere')
})

test('init --force --dry-run names the backup it would make and changes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/agents'), { recursive: true })
  writeFileSync(join(repo, '.claude/agents/implementer.md'), 'mine\n')
  const output = init(repo, home, ['--yes', '--harness=claude', '--force', '--dry-run'])
  assert.match(output, /backup .*\.claude\/agents\/implementer\.md\.bak-/)
  assert.deepEqual(readdirSync(join(repo, '.claude/agents')), ['implementer.md'])
  assert.equal(readFileSync(join(repo, '.claude/agents/implementer.md'), 'utf8'), 'mine\n')
})

// A package whose kit renames the Claude gate.sh to stop-gate.sh and the
// Codex session-start.sh to repo-start.sh, with the fragments following.
function renamedHooksPackage(nextVersion) {
  return fakePackage(nextVersion, (root) => {
    const edit = (path, from, to) => {
      const text = readFileSync(join(root, path), 'utf8')
      assert.ok(text.includes(from), `${path} holds ${from}`)
      writeFileSync(join(root, path), text.split(from).join(to))
    }
    renameSync(join(root, 'claude/hooks/gate.sh'), join(root, 'claude/hooks/stop-gate.sh'))
    renameSync(join(root, 'codex/hooks/session-start.sh'), join(root, 'codex/hooks/repo-start.sh'))
    edit('lib/kit.mjs', "CLAUDE_HOOKS = ['gate.sh'", "CLAUDE_HOOKS = ['stop-gate.sh'")
    edit('lib/kit.mjs', "'session-start.sh']", "'repo-start.sh']")
    edit('claude/settings.fragment.json', '/.claude/hooks/gate.sh"', '/.claude/hooks/stop-gate.sh"')
    edit('codex/hooks.fragment.json', '/.codex/hooks/session-start.sh"', '/.codex/hooks/repo-start.sh"')
  })
}

function hookCommands(path) {
  return Object.values(readJson(path).hooks).flat().flatMap((group) => group.hooks.map((hook) => hook.command))
}

// Every dev-loop project hook command in the file names a kit script on disk.
function assertNoDanglingHook(repo, file) {
  for (const command of hookCommands(join(repo, file))) {
    const script = /\/(\.dev-loop\/hooks\/(?:claude|codex)\/[^"]+)"/.exec(command)?.[1]
    if (script) assert.ok(existsSync(join(repo, script)), `${file} runs a missing ${script}`)
  }
}

const git = (repo, ...args) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@example.com', ...args], { cwd: repo, encoding: 'utf8' })

test('upgrade removes the hook entries of kit scripts the package renamed, and adds the new ones', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  git(repo, 'add', '-A')
  git(repo, 'commit', '-qm', 'init')

  const output = upgrade(repo, home, [], renamedHooksPackage('99.0.0'))
  const claude = hookCommands(join(repo, '.claude/settings.json'))
  assert.equal(claude.some((command) => command.endsWith('.dev-loop/hooks/claude/gate.sh"')), false)
  assert.ok(claude.some((command) => command.endsWith('.dev-loop/hooks/claude/stop-gate.sh"')))
  const codex = hookCommands(join(repo, '.codex/hooks.json'))
  assert.equal(codex.some((command) => command.endsWith('.dev-loop/hooks/codex/session-start.sh"')), false)
  assert.ok(codex.some((command) => command.endsWith('.dev-loop/hooks/codex/repo-start.sh"')))
  assertNoDanglingHook(repo, '.claude/settings.json')
  assertNoDanglingHook(repo, '.codex/hooks.json')
  const diff = git(repo, 'diff', '--', '.claude/settings.json', '.codex/hooks.json')
  assert.doesNotMatch(diff, /^\+.*(claude\/gate\.sh|session-start\.sh)"/m)
  assert.match(output, /- Stop: "\$\{CLAUDE_PROJECT_DIR:-\$\(git rev-parse --show-toplevel\)\}\/\.dev-loop\/hooks\/claude\/gate\.sh"/)
  assert.match(output, /- SessionStart: "\$\(git rev-parse --show-toplevel\)\/\.dev-loop\/hooks\/codex\/session-start\.sh"/)
  for (const file of ['.claude', '.codex']) {
    assert.equal(readdirSync(join(repo, file)).filter((name) => name.includes('.bak-')).length, 1, `one backup in ${file}`)
  }
})

test('upgrade keeps a foreign hook in the group of a removed kit hook entry', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=claude'])
  const path = join(repo, '.claude/settings.json')
  const settings = readJson(path)
  settings.hooks.Stop[0].hooks.push({ type: 'command', command: 'echo mine' })
  writeFileSync(path, `${JSON.stringify(settings, null, 2)}\n`)

  upgrade(repo, home, [], renamedHooksPackage('99.0.0'))
  const stop = readJson(path).hooks.Stop
  const commands = stop.flatMap((group) => group.hooks.map((hook) => hook.command))
  assert.ok(commands.includes('echo mine'))
  assert.equal(commands.some((command) => command.endsWith('/claude/gate.sh"')), false)
  assertNoDanglingHook(repo, '.claude/settings.json')
})

test('upgrade --dry-run reports the removed hook entries and writes nothing', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const before = snapshot(repo)
  const output = upgrade(repo, home, ['--dry-run'], renamedHooksPackage('99.0.0'))
  assert.match(output, /dry run, nothing written/)
  assert.match(output, /- Stop: .*\/\.dev-loop\/hooks\/claude\/gate\.sh"/)
  assert.match(output, /- SessionStart: .*\/\.dev-loop\/hooks\/codex\/session-start\.sh"/)
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade with no hook change leaves the hook files byte-identical', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes'])
  const files = ['.claude/settings.json', '.codex/hooks.json']
  const before = files.map((file) => readFileSync(join(repo, file)))
  upgrade(repo, home, [], fakePackage('99.0.0'))
  files.forEach((file, index) => assert.ok(readFileSync(join(repo, file)).equals(before[index]), file))
  for (const dir of ['.claude', '.codex']) {
    assert.equal(readdirSync(join(repo, dir)).some((name) => name.includes('.bak-')), false)
  }
})
