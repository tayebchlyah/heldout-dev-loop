import test from 'node:test'
import { execFileSync, spawnSync } from 'node:child_process'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, readdirSync, readFileSync, readlinkSync, renameSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { entries, fakePackage, git, hookCommands, init, readJson, run, setup, snapshot, tempDir, upgrade } from './helpers.mjs'

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
  assert.equal(codex.some((command) => command.includes('.dev-loop/hooks/codex/session-start.sh"')), false)
  assert.ok(codex.some((command) => command.includes('.dev-loop/hooks/codex/repo-start.sh"')))
  assertNoDanglingHook(repo, '.claude/settings.json')
  assertNoDanglingHook(repo, '.codex/hooks.json')
  const diff = git(repo, 'diff', '--', '.claude/settings.json', '.codex/hooks.json')
  assert.doesNotMatch(diff, /^\+.*(claude\/gate\.sh|session-start\.sh)"/m)
  assert.match(output, /- Stop: "\$\{CLAUDE_PROJECT_DIR:-\$\(git rev-parse --show-toplevel\)\}\/\.dev-loop\/hooks\/claude\/gate\.sh"/)
  assert.match(output, /- SessionStart: .*\/\.dev-loop\/hooks\/codex\/session-start\.sh"/)
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

for (const old of ['Read|Edit|Write|Grep|Glob|Bash', 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash']) test(`upgrade of a Claude project replaces the old deny-acceptance entry (${old}) with one new entry`, () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  const group = custom.hooks.PreToolUse.find((entry) => entry.hooks.some((hook) => hook.command.includes('deny-acceptance-to-subagents.sh')))
  group.matcher = old
  for (const hook of group.hooks) delete hook.onFailure
  custom.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo mine' }] })
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  assert.match(output, /updated PreToolUse: .*deny-acceptance-to-subagents\.sh/)
  const groups = readJson(settings).hooks.PreToolUse
  const own = groups.flatMap((entry) => entry.hooks.map((hook) => ({ matcher: entry.matcher, hook }))).filter(({ hook }) => hook.command.includes('deny-acceptance-to-subagents.sh'))
  assert.equal(own.length, 1)
  assert.equal(own[0].matcher, 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor|LSP|mcp__.*|ListMcpResourcesTool|ReadMcpResourceTool|ReadMcpResourceDirTool')
  assert.equal(own[0].hook.onFailure, 'block')
  assert.ok(groups.some((entry) => entry.hooks.some((hook) => hook.command === 'echo mine')))
  assert.doesNotMatch(upgrade(repo, home, [], fakePackage('99.0.0')), /updated|\+ PreToolUse/)
})

test('upgrade of a Claude project moves the git-guard entry from Bash to Bash|Monitor (#154)', () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=claude'])
  const settings = join(repo, '.claude/settings.json')
  const custom = readJson(settings)
  custom.hooks.PreToolUse.find((entry) => entry.hooks.some((hook) => hook.command.includes('git-guard.sh'))).matcher = 'Bash'
  custom.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo mine' }] })
  writeFileSync(settings, JSON.stringify(custom, null, 2))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  const groups = readJson(settings).hooks.PreToolUse
  const guards = groups.flatMap((entry) => entry.hooks.filter((hook) => hook.command.includes('git-guard.sh')).map(() => entry.matcher))
  assert.deepEqual(guards, ['Bash|Monitor'])
  assert.ok(groups.some((entry) => entry.matcher === 'Bash' && entry.hooks.some((hook) => hook.command === 'echo mine')))
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
  const at = matchers.indexOf('Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor|LSP|mcp__.*|ListMcpResourcesTool|ReadMcpResourceTool|ReadMcpResourceDirTool')
  assert.deepEqual(matchers.slice(at - 1, at + 3), ['MineBefore', 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor|LSP|mcp__.*|ListMcpResourcesTool|ReadMcpResourceTool|ReadMcpResourceDirTool', 'X', 'MineAfter'])
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

test('upgrade of a Codex project replaces the old acceptance-wall entry with one new entry', () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=codex'])
  const hooks = join(repo, '.codex/hooks.json')
  const custom = readJson(hooks)
  const group = custom.hooks.PreToolUse.find((entry) => entry.hooks.some((hook) => hook.command.includes('acceptance-wall.sh')))
  group.matcher = 'Bash|apply_patch'
  custom.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo mine' }] })
  writeFileSync(hooks, JSON.stringify(custom, null, 2))

  upgrade(repo, home, [], fakePackage('99.0.0'))
  const groups = readJson(hooks).hooks.PreToolUse
  const own = groups.flatMap((entry) => entry.hooks.map((hook) => ({ matcher: entry.matcher, hook }))).filter(({ hook }) => hook.command.includes('acceptance-wall.sh'))
  assert.equal(own.length, 1)
  assert.equal(own[0].matcher, 'Bash|apply_patch|view_image|mcp__.*|read_mcp_resource|list_mcp_resources|list_mcp_resource_templates')
  assert.ok(groups.some((entry) => entry.hooks.some((hook) => hook.command === 'echo mine')))
  assert.doesNotMatch(upgrade(repo, home, [], fakePackage('99.0.0')), /updated|\+ PreToolUse/)
})


test('Codex outer hook entries execute main scripts from a feature worktree', () => {
  const { home, repo: root } = setup()
  const repo = join(root, 'main with spaces')
  mkdirSync(repo)
  git(repo, 'init', '-q')
  init(repo, home, ['--yes', '--harness=both'])
  for (const name of readdirSync(join(repo, '.dev-loop/hooks/codex')).filter((name) => name.endsWith('.sh'))) {
    writeFileSync(join(repo, '.dev-loop/hooks/codex', name), '#!/bin/sh\necho main-hook\n')
  }
  git(repo, 'add', '-A')
  git(repo, 'commit', '-qm', 'fixture')
  const feature = join(root, 'feature with spaces')
  git(repo, 'worktree', 'add', '-qb', 'fix/probe', feature)
  const marker = join(feature, 'tampered')
  for (const name of readdirSync(join(feature, '.dev-loop/hooks/codex')).filter((name) => name.endsWith('.sh'))) {
    writeFileSync(join(feature, '.dev-loop/hooks/codex', name), `#!/bin/sh\nprintf tampered >'${marker}'\n`)
  }
  for (const command of hookCommands(join(repo, '.codex/hooks.json'))) {
    const output = execFileSync('/bin/sh', ['-c', command], { cwd: feature, input: '{}', encoding: 'utf8' })
    assert.equal(output.trim(), 'main-hook', command)
    assert.equal(existsSync(marker), false)
  }
})

test('Codex outer hook entries execute main scripts from a nested repo in a feature subfolder (#212)', () => {
  const { home, repo: root } = setup()
  const repo = join(root, 'main')
  mkdirSync(repo)
  git(repo, 'init', '-q')
  init(repo, home, ['--yes', '--harness=both'])
  for (const name of readdirSync(join(repo, '.dev-loop/hooks/codex')).filter((name) => name.endsWith('.sh'))) {
    writeFileSync(join(repo, '.dev-loop/hooks/codex', name), '#!/bin/sh\necho main-hook\n')
  }
  git(repo, 'add', '-A')
  git(repo, 'commit', '-qm', 'fixture')
  const feature = join(repo, '.claude/worktrees/probe')
  git(repo, 'worktree', 'add', '-qb', 'fix/probe', feature)
  const marker = join(root, 'nested-ran')
  const plant = (dir) => {
    mkdirSync(join(dir, '.dev-loop/hooks/codex'), { recursive: true })
    for (const name of readdirSync(join(repo, '.dev-loop/hooks/codex'))) {
      writeFileSync(join(dir, '.dev-loop/hooks/codex', name), `#!/bin/sh\nprintf ran >'${marker}'\n`, { mode: 0o755 })
    }
    git(dir, 'init', '-q')
  }
  const web = join(feature, 'packages/web')
  plant(web)
  // A nested repo whose config names another work tree: the walk follows folders, not what git reports (#212).
  const elsewhere = join(root, 'elsewhere')
  plant(elsewhere)
  const other = join(feature, 'packages/api')
  mkdirSync(other, { recursive: true })
  git(other, 'init', '-q')
  git(other, 'config', 'core.worktree', elsewhere)
  for (const cwd of [web, join(web, 'src'), other]) {
    mkdirSync(cwd, { recursive: true })
    for (const command of hookCommands(join(repo, '.codex/hooks.json'))) {
      const output = execFileSync('/bin/sh', ['-c', command], { cwd, input: '{}', encoding: 'utf8' })
      assert.equal(output.trim(), 'main-hook', `${cwd}: ${command}`)
      assert.equal(existsSync(marker), false)
    }
  }
})

test('a Codex hook command blocks with exit 2 for a bare first worktree or a main checkout with no kit script', () => {
  const { home, repo: root } = setup()
  const source = join(root, 'source')
  mkdirSync(source)
  git(source, 'init', '-q')
  init(source, home, ['--yes', '--harness=both'])
  for (const name of readdirSync(join(source, '.dev-loop/hooks/codex')).filter((name) => name.endsWith('.sh'))) {
    writeFileSync(join(source, '.dev-loop/hooks/codex', name), '#!/bin/sh\necho main-hook\n')
  }
  git(source, 'add', '-A')
  git(source, 'commit', '-qm', 'kit')
  const bare = join(root, 'bare.git')
  git(root, 'clone', '-q', '--bare', source, bare)
  const linked = join(root, 'linked')
  git(bare, 'worktree', 'add', '-q', linked)
  const planted = join(root, 'a-feature')
  git(bare, 'worktree', 'add', '-q', '-b', 'fix/planted', planted)
  const main = join(root, 'main')
  mkdirSync(main)
  git(main, 'init', '-q')
  git(main, 'commit', '-q', '--allow-empty', '-m', 'no kit')
  const feature = join(root, 'feature')
  git(main, 'worktree', 'add', '-qb', 'fix/probe', feature)
  const run = (command, cwd) => spawnSync('/bin/sh', ['-c', command], { cwd, input: '{}', encoding: 'utf8' })
  for (const command of hookCommands(join(source, '.codex/hooks.json'))) {
    for (const [cwd, folder] of [[linked, ''], [planted, ''], [feature, '.*/main']]) {
      const result = run(command, cwd)
      assert.equal(result.status, 2, `${cwd}: ${command}`)
      assert.equal(result.stdout, '')
      assert.match(result.stderr, new RegExp(`^Blocked: dev-loop hook script missing: ${folder}/\\.dev-loop/hooks/codex/[\\w.-]+$`, 'm'))
    }
  }
})

// Each earlier Codex command form (#158, #200, #212), from the parts of a current command: the old
// prefix with the old tail, the old prefix in front of the current tail, and the current tail behind an old prefix.
const OLD_CODEX_FORMS = ['$(git rev-parse --show-toplevel)', "$(git worktree list --porcelain | sed -n '1s/^worktree //p')",
  `$(git worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')`]
  .flatMap((prefix) => [
    [prefix, ({ script, args }) => `"${prefix}/.dev-loop/hooks/codex/${script}"${args}`],
    [prefix, ({ script, command }) => `"${prefix}/.dev-loop/hooks/codex/${command.slice(command.indexOf(`${script}"`))}`],
    [prefix, ({ command }) => `f="${prefix}${command.slice(command.indexOf('/.dev-loop/'))}`],
  ])
const codexParts = (command) => {
  const match = /\/hooks\/codex\/([\w.-]+)".*exec "\$f"(.*)$/s.exec(command)
  return match && { script: match[1], args: match[2], command }
}
// User hooks that run a kit script their own way (#200): wrapped, or from another folder.
const USER_CODEX_HOOKS = ['timeout 60 "$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/gate.sh"',
  '"$(git rev-parse --show-toplevel)/sub/.dev-loop/hooks/codex/gate.sh"']

test('Codex upgrade migrates each earlier command form once and preserves user hooks and Claude bytes', () => {
  for (const [prefix, form] of OLD_CODEX_FORMS) {
    const { home, repo } = setup()
    init(repo, home, ['--yes', '--harness=both'])
    const claude = readFileSync(join(repo, '.claude/settings.json'))
    const path = join(repo, '.codex/hooks.json')
    const hooks = readJson(path)
    const head = hooks.hooks.Stop[0].hooks[0].command.split('/.dev-loop/')[0]
    for (const groups of Object.values(hooks.hooks)) for (const group of groups) for (const hook of group.hooks) {
      hook.command = form(codexParts(hook.command))
    }
    const oldGate = structuredClone(hooks.hooks.Stop[0])
    oldGate.matcher = 'old-matcher'
    const user = USER_CODEX_HOOKS.map((command) => ({ type: 'command', command }))
    hooks.hooks.Stop.push(oldGate, structuredClone(oldGate), { hooks: [{ type: 'command', command: 'echo mine' }, ...user] })
    writeFileSync(path, JSON.stringify(hooks))
    const pkg = fakePackage('99.0.0')
    const output = upgrade(repo, home, [], pkg)
    assert.match(output, /updated.*gate\.sh/, prefix)
    assert.match(output, /\/hooks/)
    const after = readFileSync(path)
    const commands = hookCommands(path)
    assert.equal(commands.filter((command) => command.includes('/codex/gate.sh"; ')).length, 2, prefix)
    for (const [event, groups] of Object.entries(readJson(path).hooks)) {
      const own = groups.flatMap((group) => group.hooks.map((hook) => hook.command)).filter((command) => command.includes('/.dev-loop/'))
        .map((command) => { const parts = codexParts(command); return parts ? parts.script + parts.args : command })
      assert.deepEqual(own, [...new Set(own)], `${prefix} ${event}`)
    }
    const mine = new Set(['echo mine', ...USER_CODEX_HOOKS])
    assert.deepEqual(commands.filter((command) => !mine.has(command) && !command.startsWith(head)), [], prefix)
    for (const command of mine) assert.ok(commands.includes(command), command)
    assert.ok(readFileSync(join(repo, '.claude/settings.json')).equals(claude))
    upgrade(repo, home, [], pkg)
    assert.ok(readFileSync(path).equals(after), prefix)
  }
})

test('Codex init migrates an existing exact legacy hook without replacing a user command', () => {
  const { home, repo } = setup()
  mkdirSync(join(repo, '.codex'))
  const command = '\"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/gate.sh\"'
  writeFileSync(join(repo, '.codex/hooks.json'), JSON.stringify({ hooks: { Stop: [{ hooks: [
    { type: 'command', command }, { type: 'command', command: 'echo custom' },
  ] }] } }))
  init(repo, home, ['--yes', '--harness=both'])
  const commands = hookCommands(join(repo, '.codex/hooks.json'))
  assert.equal(commands.includes(command), false)
  assert.equal(commands.filter((cmd) => cmd.includes('/codex/gate.sh"; ')).length, 2)
  assert.ok(commands.includes('echo custom'))
})

test('Claude hook definitions match for both-harness and Claude-only init and upgrade', () => {
  const outputs = []
  for (const harness of ['both', 'claude']) {
    const { home, repo } = setup()
    init(repo, home, ['--yes', `--harness=${harness}`])
    const before = readFileSync(join(repo, '.claude/settings.json'))
    upgrade(repo, home, [], fakePackage('99.0.0'))
    const after = readFileSync(join(repo, '.claude/settings.json'))
    assert.ok(before.equals(after))
    outputs.push(after)
  }
  assert.ok(outputs[0].equals(outputs[1]))
})
