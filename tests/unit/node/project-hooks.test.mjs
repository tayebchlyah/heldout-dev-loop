import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync, readlinkSync, renameSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { entries, fakePackage, git, hookCommands, init, readJson, run, setup, snapshot, upgrade } from './helpers.mjs'

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
    edit('lib/kit.mjs', "'session-start.sh',", "'repo-start.sh',")
    edit('claude/settings.fragment.json', '/.claude/hooks/gate.sh"', '/.claude/hooks/stop-gate.sh"')
    edit('codex/hooks.fragment.json', '/.codex/hooks/session-start.sh"', '/.codex/hooks/repo-start.sh"')
  })
}

// Every dev-loop project hook command in the file names a kit script on disk.
function assertNoDanglingHook(repo, file) {
  for (const command of hookCommands(join(repo, file))) {
    const script = /\/(\.dev-loop\/hooks\/(?:claude|codex)\/[^"]+)"/.exec(command)?.[1]
    if (script) assert.ok(existsSync(join(repo, script)), `${file} runs a missing ${script}`)
  }
}

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

test('upgrade from 2.0.0 adds the board hook entries once', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    const fragment = readJson(join(root, 'claude/settings.fragment.json'))
    fragment.hooks.SessionStart = [fragment.hooks.SessionStart[0]]
    delete fragment.hooks.UserPromptSubmit
    delete fragment.hooks.PostToolUse
    writeFileSync(join(root, 'claude/settings.fragment.json'), JSON.stringify(fragment, null, 2))
  })
  run(older, 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const board = (file) =>
    JSON.stringify(readJson(file).hooks).split('board.sh').length - 1
  assert.equal(board(settings), 0)
  upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.equal(board(settings), 4)
  upgrade(repo, home, [], fakePackage('99.0.1'))
  assert.equal(board(settings), 4)
})

test('D3: Codex upgrade from 2.0.0 adds the board hook entries once', () => {
  const { home, repo } = setup()
  const older = fakePackage('98.0.0', (root) => {
    const fragment = readJson(join(root, 'codex/hooks.fragment.json'))
    fragment.hooks.SessionStart = [fragment.hooks.SessionStart[0]]
    delete fragment.hooks.UserPromptSubmit
    delete fragment.hooks.PostToolUse
    writeFileSync(join(root, 'codex/hooks.fragment.json'), JSON.stringify(fragment, null, 2))
  })
  run(older, 'init', repo, home, ['--yes', '--harness=codex'])
  const hooks = join(repo, '.codex/hooks.json')
  const board = (file) =>
    JSON.stringify(readJson(file).hooks).split('board.sh').length - 1
  assert.equal(board(hooks), 0)
  upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.equal(board(hooks), 3)
  upgrade(repo, home, [], fakePackage('99.0.1'))
  assert.equal(board(hooks), 3)
})

test('upgrade of a 2.1.1 Claude project replaces the old deny-acceptance entry with one new entry', () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  const group = custom.hooks.PreToolUse.find((entry) => entry.hooks.some((hook) => hook.command.includes('deny-acceptance-to-subagents.sh')))
  group.matcher = 'Read|Edit|Write|Grep|Glob|Bash'
  for (const hook of group.hooks) delete hook.onFailure
  custom.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo mine' }] })
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.match(output, /updated PreToolUse: .*deny-acceptance-to-subagents\.sh/)
  const groups = readJson(settings).hooks.PreToolUse
  const own = groups.flatMap((entry) => entry.hooks.map((hook) => ({ matcher: entry.matcher, hook }))).filter(({ hook }) => hook.command.includes('deny-acceptance-to-subagents.sh'))
  assert.equal(own.length, 1)
  assert.equal(own[0].matcher, 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash')
  assert.equal(own[0].hook.onFailure, 'block')
  assert.ok(groups.some((entry) => entry.hooks.some((hook) => hook.command === 'echo mine')))
  assert.doesNotMatch(upgrade(repo, home, [], fakePackage('99.0.0')), /updated|\+ PreToolUse/)
})

test('upgrade of a 2.1.1 Claude project keeps user groups in order and the empty one, new entry at the old index', () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  const index = custom.hooks.PreToolUse.findIndex((entry) => entry.hooks.some((hook) => hook.command.includes('deny-acceptance-to-subagents.sh')))
  const group = custom.hooks.PreToolUse[index]
  group.hooks = group.hooks.filter((hook) => hook.command.includes('deny-acceptance-to-subagents.sh'))
  group.matcher = 'Read|Edit|Write|Grep|Glob|Bash'
  for (const hook of group.hooks) delete hook.onFailure
  const before = { matcher: 'MineBefore', hooks: [{ type: 'command', command: 'echo before' }] }
  const empty = { matcher: 'X', hooks: [] }
  const after = { matcher: 'MineAfter', hooks: [{ type: 'command', command: 'echo after' }] }
  custom.hooks.PreToolUse.splice(index, 1, before, group, empty, after)
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  const matchers = readJson(settings).hooks.PreToolUse.map((entry) => entry.matcher)
  const at = matchers.indexOf('Read|Edit|Write|NotebookEdit|Grep|Glob|Bash')
  assert.deepEqual(matchers.slice(at - 1, at + 3), ['MineBefore', 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash', 'X', 'MineAfter'])
  assert.doesNotMatch(upgrade(repo, home, [], fakePackage('99.0.0')), /updated|\+ PreToolUse/)
})

for (const name of ['release-audit', 'garden', 'release', 'release-notes']) {
  test(`upgrade from a kit without ${name} adds its files, link and MANIFEST entries`, () => {
    const { home, repo } = setup()
    init(repo, home, ['--yes'])
    const path = join(repo, '.dev-loop/MANIFEST.json')
    const manifest = JSON.parse(readFileSync(path, 'utf8'))
    rmSync(join(repo, `.agents/skills/${name}`), { recursive: true })
    unlinkSync(join(repo, `.claude/skills/${name}`))
    for (const key of Object.keys(manifest.files)) if (key.startsWith(`.agents/skills/${name}/`) || key === `.claude/skills/${name}`) delete manifest.files[key]
    writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`)
    upgrade(repo, home)
    assert.equal(readlinkSync(join(repo, `.claude/skills/${name}`)), `../../.agents/skills/${name}`)
    for (const file of ['SKILL.md', 'agents/openai.yaml']) assert.ok(existsSync(join(repo, `.agents/skills/${name}/${file}`)), file)
    const after = JSON.parse(readFileSync(path, 'utf8'))
    assert.ok(`.agents/skills/${name}/SKILL.md` in after.files)
    assert.ok(`.agents/skills/${name}/agents/openai.yaml` in after.files)
    assert.ok(`.claude/skills/${name}` in after.files)
  })
}
