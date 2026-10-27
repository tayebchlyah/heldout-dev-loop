import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, readlinkSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { entries, fakePackage, git, init, initFails, kitContent, packageRoot, readJson, run, setup, snapshot, upgrade, version } from './helpers.mjs'
import { compareManifest, sha256 } from '../../../lib/manifest.mjs'
import { releaseNotes } from '../../../lib/project.mjs'

function upgradeFails(repo, home, args, pattern, pkg = packageRoot) {
  assert.throws(() => upgrade(repo, home, args, pkg), (error) => {
    assert.equal(error.status, 1)
    assert.match(error.stdout + error.stderr, pattern)
    return true
  })
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

test('upgrade of a 2.1.1 Codex project removes the dead profile tables and prints one removed line each', () => {
  const { home, repo } = setup()
  run(packageRoot, 'init', repo, home, ['--yes', '--harness=codex'])
  const config = join(repo, '.codex/config.toml')
  const legacy = [
    '[permissions.dev-implementer]',
    'description = "Implementation access without held-out acceptance tests."',
    'extends = "dev-workspace"',
    '',
    '[permissions.dev-implementer.filesystem.":workspace_roots"]',
    '"tests/acceptance" = "deny"',
    '"tests/acceptance/**" = "deny"',
    '',
  ].join('\n')
  writeFileSync(config, `${readFileSync(config, 'utf8')}\n${legacy}`)

  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  const lines = output.split('\n').filter((line) => line.includes('removed') && line.includes('dev-implementer'))
  assert.equal(lines.length, 2, output)
  assert.ok(lines[0].includes('removed [permissions.dev-implementer] from .codex/config.toml'), output)
  assert.ok(!readFileSync(config, 'utf8').includes('dev-implementer'))
})

test('upgrade from 1.5 leaves one SessionStart entry', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    const path = join(root, 'claude/settings.fragment.json')
    const fragment = readJson(path)
    fragment.hooks.SessionStart = [fragment.hooks.SessionStart[0]]
    fragment.hooks.SessionStart[0].hooks[0].command = SESSION_START_1_5
    delete fragment.hooks.UserPromptSubmit
    writeFileSync(path, JSON.stringify(fragment, null, 2))
  })
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  custom.hooks.SessionStart[0].hooks.push({ type: 'command', command: 'echo mine' })
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.ok(output.includes(`- SessionStart: ${SESSION_START_1_5}`), output)
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

// A project on the kit before the pause and continue skills.
function initOldKit(repo, home) {
  init(repo, home, ['--yes'])
  const path = join(repo, '.dev-loop/MANIFEST.json')
  const manifest = JSON.parse(readFileSync(path, 'utf8'))
  for (const name of ['pause', 'continue']) {
    rmSync(join(repo, `.agents/skills/${name}`), { recursive: true })
    unlinkSync(join(repo, `.claude/skills/${name}`))
    for (const key of Object.keys(manifest.files)) if (key.startsWith(`.agents/skills/${name}/`) || key === `.claude/skills/${name}`) delete manifest.files[key]
  }
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`)
}

function writeUserSkill(repo, content) {
  mkdirSync(join(repo, '.agents/skills/continue'), { recursive: true })
  writeFileSync(join(repo, '.agents/skills/continue/SKILL.md'), content)
}

test('upgrade refuses a file of yours at a new kit path, names it and --force, and writes nothing', () => {
  const { home, repo } = setup()
  initOldKit(repo, home)
  writeUserSkill(repo, 'mine\n')
  const before = snapshot(repo)
  upgradeFails(repo, home, [], /files of yours at dev-loop paths:\n {2}\.agents\/skills\/continue\/SKILL\.md\n[\s\S]*--force/)
  upgradeFails(repo, home, ['--dry-run'], /\.agents\/skills\/continue\/SKILL\.md/)
  assert.deepEqual(snapshot(repo), before)
})

test('upgrade --force moves a file of yours at a new kit path to a backup beside it, then writes the kit', () => {
  const { home, repo } = setup()
  initOldKit(repo, home)
  writeUserSkill(repo, 'mine\n')
  const output = upgrade(repo, home, ['--force'])
  const backups = readdirSync(join(repo, '.agents/skills/continue')).filter((name) => name.startsWith('SKILL.md.bak-'))
  assert.equal(backups.length, 1)
  assert.equal(readFileSync(join(repo, '.agents/skills/continue', backups[0]), 'utf8'), 'mine\n')
  assert.equal(readFileSync(join(repo, '.agents/skills/continue/SKILL.md'), 'utf8'), kitContent('.agents/skills/continue/SKILL.md'))
  assert.match(output, /backup .*SKILL\.md\.bak-/)
})

test('upgrade accepts a new kit path that already holds the kit content', () => {
  const { home, repo } = setup()
  initOldKit(repo, home)
  writeUserSkill(repo, kitContent('.agents/skills/continue/SKILL.md'))
  upgrade(repo, home)
  assert.equal(readdirSync(join(repo, '.agents/skills/continue')).some((name) => name.includes('.bak')), false)
})

test('upgrade from a kit with the one skill link creates the pause and continue links', () => {
  const { home, repo } = setup()
  initOldKit(repo, home)
  upgrade(repo, home)
  for (const name of ['pause', 'continue']) {
    assert.equal(readlinkSync(join(repo, `.claude/skills/${name}`)), `../../.agents/skills/${name}`)
    assert.ok(existsSync(join(repo, `.agents/skills/${name}/SKILL.md`)), name)
  }
})

test('init refuses a real directory at the pause skill link path, and writes nothing', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.claude/skills/pause'), { recursive: true })
  writeFileSync(join(repo, '.claude/skills/pause/mine.md'), 'mine\n')
  initFails(repo, home, ['--yes', '--harness=claude'], /\.claude\/skills\/pause/)
  assert.equal(readFileSync(join(repo, '.claude/skills/pause/mine.md'), 'utf8'), 'mine\n')
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

function installAt(repo, home, from) {
  run(fakePackage(from), 'init', repo, home, ['--yes', '--harness=claude'])
}

const pattern = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

test('upgrade to a release candidate sets its version in dev-loop.json and MANIFEST.json', () => {
  const { home, repo } = setup()
  installAt(repo, home, '2.1.1')
  upgrade(repo, home, [], fakePackage('3.0.0-rc.1'))
  assert.equal(readJson(join(repo, 'dev-loop.json')).version, '3.0.0-rc.1')
  assert.equal(readJson(join(repo, '.dev-loop/MANIFEST.json')).version, '3.0.0-rc.1')
})

test('upgrade orders release candidates by their number, and a final after its candidates', () => {
  const { home, repo } = setup()
  installAt(repo, home, '3.0.0-rc.9')
  upgrade(repo, home, [], fakePackage('3.0.0-rc.10'))
  assert.equal(readJson(join(repo, 'dev-loop.json')).version, '3.0.0-rc.10')
  upgrade(repo, home, [], fakePackage('3.0.0'))
  assert.equal(readJson(join(repo, 'dev-loop.json')).version, '3.0.0')
})

test('upgrade to an older release candidate or a candidate of the installed final refuses, and writes nothing', () => {
  for (const [from, to] of [['3.0.0-rc.10', '3.0.0-rc.9'], ['3.0.0', '3.0.0-rc.1']]) {
    const { home, repo } = setup()
    installAt(repo, home, from)
    const before = snapshot(repo)
    upgradeFails(repo, home, [], new RegExp(`${pattern(to)}, older than the installed ${pattern(from)}`), fakePackage(to))
    assert.deepEqual(snapshot(repo), before)
  }
})

test('upgrade refuses a package version that is not x.y.z or x.y.z-rc.n, and writes nothing', () => {
  for (const bad of ['3.0.0-beta.1', '3.0', '3.0.0-rc.0', '03.0.0']) {
    const { home, repo } = setup()
    installAt(repo, home, '2.1.1')
    const before = snapshot(repo)
    upgradeFails(repo, home, [], new RegExp(`not a x\\.y\\.z or x\\.y\\.z-rc\\.n version: ${pattern(bad)}`), fakePackage(bad))
    assert.deepEqual(snapshot(repo), before)
  }
})

test('upgrade refuses an installed dev-loop.json version that is not x.y.z or x.y.z-rc.n, and writes nothing', () => {
  const { home, repo } = setup()
  installAt(repo, home, '2.1.1')
  const devLoop = readJson(join(repo, 'dev-loop.json'))
  writeFileSync(join(repo, 'dev-loop.json'), `${JSON.stringify({ ...devLoop, version: '2.1' }, null, 2)}\n`)
  const before = snapshot(repo)
  upgradeFails(repo, home, [], /not a x\.y\.z or x\.y\.z-rc\.n version: 2\.1$/m, fakePackage('3.0.0'))
  assert.deepEqual(snapshot(repo), before)
})

test('releaseNotes keeps release candidate sections and ends a section at a heading that is no version', () => {
  const changelog = [
    '# Changelog', '', '## [3.0.0-rc.1] - 2026-11-01', '', '- rc', '', '## [v3.0.0-beta.1]', '', '- beta', '',
    '## [2.1.1] - 2026-10-20', '', '- one', '', '## [2.1.0]', '', '- zero', '',
  ].join('\n')
  const notes = releaseNotes(changelog, '2.1.1', '3.0.0-rc.1')
  assert.match(notes, /- rc/)
  assert.doesNotMatch(notes, /- one/)
  assert.doesNotMatch(notes, /- beta/)
  assert.match(releaseNotes('## [v3.0.0-rc.2]\n\n- two\n\n## [3.0.0-rc.1]\n\n- rc\n', '3.0.0-rc.1', '3.0.0'), /- two/)
})

test('init with a release candidate package writes its version', () => {
  const { home, repo } = setup()
  run(fakePackage('3.0.0-rc.1'), 'init', repo, home, ['--yes', '--harness=claude'])
  assert.equal(readJson(join(repo, 'dev-loop.json')).version, '3.0.0-rc.1')
})
