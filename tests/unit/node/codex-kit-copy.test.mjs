import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { fakePackage, git, hookCommands, init, readJson, run, setup, upgrade } from './helpers.mjs'

const WALLS = ['git-guard.sh', 'acceptance-wall.sh']
const MANIFEST = '.dev-loop/MANIFEST.json'
const D6 = ['.dev-loop/hooks/codex/', '.agents/skills/dev-loop/scripts/']
const CHANGED = ['.dev-loop/hooks/codex/board.sh', '.agents/skills/dev-loop/scripts/devloop.py', '.agents/skills/dev-loop/scripts/notes_path.py']
const hasZsh = spawnSync('zsh', ['-c', 'true']).status === 0

const sha = (data) => createHash('sha256').update(data).digest('hex')
const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)

// #227: main checkout (a space in its path) with the kit committed, a linked feature worktree, a temp CODEX_HOME with a space.
function fixture() {
  const { home, repo: root } = setup()
  const main = join(root, 'main checkout')
  mkdirSync(main)
  git(main, 'init', '-q')
  init(main, home, ['--yes', '--harness=both'])
  git(main, 'add', '-A')
  git(main, 'commit', '-qm', 'init')
  const feature = join(root, 'feature')
  git(main, 'worktree', 'add', '-qb', 'fix/probe', feature)
  const codexHome = join(home, 'codex home')
  const env = { ...process.env, HOME: home, CODEX_HOME: codexHome }
  delete env.DEV_LOOP_NOTES
  const hash = sha(readFileSync(join(main, MANIFEST)))
  return { home, root, main, feature, codexHome, env, hash, kit: join(codexHome, 'dev-loop/kit'), copy: join(codexHome, 'dev-loop/kit', hash),
    marker: join(root, 'marker'), hooks: hooksOf(main) }
}

function hooksOf(repo) {
  return Object.entries(readJson(join(repo, '.codex/hooks.json')).hooks).flatMap(([event, groups]) => groups.flatMap((group) => group.hooks.map(({ command }) => {
    const [, script, args] = /\/hooks\/codex\/([\w.-]+)".*KIT_MAIN=\$m exec sh "\$f"(.*)$/s.exec(command)
    return { event, script, args, command, name: `${event} ${script}${args}` }
  })))
}

function stdin(hook, cwd) {
  const input = { cwd, hook_event_name: hook.event }
  if (WALLS.includes(hook.script)) return { ...input, tool_name: 'Bash', tool_input: { command: 'git status' } }
  if (hook.args === ' post-tool') return { ...input, tool_name: 'Bash', tool_input: { command: 'echo hi' } }
  if (hook.script === 'seal.sh') return { ...input, agent_id: 'probe', agent_type: 'implementer' }
  return input
}

function runHook(f, hook, { cwd = f.feature, input = cwd, shell = '/bin/sh', env = f.env } = {}) {
  const { status, stdout, stderr } = spawnSync(shell, ['-c', hook.command], { cwd, input: JSON.stringify(stdin(hook, input)), env, encoding: 'utf8' })
  return { status, stdout, stderr }
}

const runAll = (f, options) => f.hooks.map((hook) => [hook, runHook(f, hook, options)])

function runAsync(f, hook) {
  return new Promise((resolve) => {
    const child = spawn('/bin/sh', ['-c', hook.command], { cwd: f.feature, env: f.env })
    let stdout = ''
    child.stdout.on('data', (data) => { stdout += data })
    child.on('close', (status) => resolve({ status, stdout }))
    child.stdin.end(JSON.stringify(stdin(hook, f.feature)))
  })
}

function files(dir, relative = '') {
  return readdirSync(join(dir, relative), { withFileTypes: true }).flatMap((entry) => {
    const path = relative ? `${relative}/${entry.name}` : entry.name
    if (entry.isDirectory()) return entry.name === '__pycache__' ? [] : files(dir, path)
    return [path]
  })
}

function plantMarker(repo, path, marker) {
  const file = join(repo, path)
  const text = readFileSync(file, 'utf8')
  if (path.endsWith('.sh')) return writeFileSync(file, `#!/bin/sh\nprintf x >>'${marker}'\n`)
  const line = `open(${JSON.stringify(marker)}, "a").write("x")\n`
  const future = 'from __future__ import annotations\n'
  writeFileSync(file, text.includes(future) ? text.replace(future, future + line) : text.replace(/^(#!.*\n)?/, (shebang) => shebang + line))
}

function assertNoMarker(f) {
  sleep(1500)
  assert.equal(existsSync(f.marker), false, 'a changed kit file ran')
}

const SKILL_LINE = 'This repo runs the dev-loop skill. Invoke it before any other action.'
const message = (source, path) => `Blocked: dev-loop kit at ${source} does not match the trusted hooks: ${path}. Restore the kit files on main (git checkout), or run upgrade, then /hooks.`

function assertBlocked(results, source, path) {
  for (const [hook, result] of results) {
    if (WALLS.includes(hook.script)) {
      assert.equal(result.status, 2, hook.name)
      assert.equal(result.stdout, '', hook.name)
      assert.equal(result.stderr, `${message(source, path)}\n`, hook.name)
    } else {
      assert.equal(result.status, 0, hook.name)
      const output = { systemMessage: message(source, path) }
      if (hook.script === 'session-start.sh') output.hookSpecificOutput = { hookEventName: 'SessionStart', additionalContext: `${SKILL_LINE}\n${message(source, path)}` }
      assert.deepEqual(JSON.parse(result.stdout), output, hook.name)
    }
  }
}

function assertRuns(results) {
  for (const [hook, result] of results) {
    assert.equal(result.status, 0, `${hook.name}: ${result.stderr}`)
    assert.ok(!`${result.stdout}${result.stderr}`.includes('Blocked: dev-loop kit at'), hook.name)
  }
}

// #227, oracle 2: the copy is the MANIFEST and its D6 files, byte-equal to main; the hooks give the results of an unchanged kit.
function checkFill(f, shell) {
  const direct = f.hooks.map((hook) => {
    const { status, stdout } = spawnSync('/bin/sh', [join(f.main, '.dev-loop/hooks/codex', hook.script), ...hook.args.split(' ').filter(Boolean)],
      { cwd: f.feature, input: JSON.stringify(stdin(hook, f.feature)), env: f.env, encoding: 'utf8' })
    return { status, stdout }
  })
  const results = runAll(f, { shell })
  results.forEach(([hook, { status, stdout }], index) => assert.deepEqual({ status, stdout }, direct[index], hook.name))
  const manifest = readJson(join(f.main, MANIFEST))
  const expected = [MANIFEST, ...Object.entries(manifest.files).filter(([path, value]) => value.startsWith('sha256:') && D6.some((prefix) => path.startsWith(prefix))).map(([path]) => path)]
  assert.deepEqual(files(f.copy).sort(), expected.sort())
  for (const path of expected) assert.ok(readFileSync(join(f.copy, path)).equals(readFileSync(join(f.main, path))), path)
  assert.deepEqual(readdirSync(f.kit), [f.hash])
  return results
}

// #227, oracles 3, 7: with no copy, each changed main kit file blocks the walls and warns the rest; nothing runs.
function checkChanged(f, shell) {
  for (const path of CHANGED) {
    const before = readFileSync(join(f.main, path))
    plantMarker(f.main, path, f.marker)
    assertBlocked(runAll(f, { shell }), f.main, path)
    writeFileSync(join(f.main, path), before)
  }
  assertNoMarker(f)
  assert.deepEqual(existsSync(f.kit) ? readdirSync(f.kit) : [], [])
}

test('init writes H of the installed MANIFEST into each Codex kit command', () => {
  const f = fixture()
  assert.equal(f.hooks.length, 11)
  for (const { command } of f.hooks) assert.ok(command.startsWith(`f="\${CODEX_HOME:-$HOME/.codex}/dev-loop/kit/${f.hash}/.dev-loop/hooks/codex/`), command)
})

test('a first hook from the feature worktree fills the copy and runs it', () => {
  checkFill(fixture())
})

test('with no copy a changed main kit file blocks the walls and warns the rest', () => {
  checkChanged(fixture())
})

test('with a copy a changed main kit file changes no result', () => {
  const f = fixture()
  const before = runAll(f)
  for (const path of CHANGED) plantMarker(f.main, path, f.marker)
  const after = runAll(f)
  before.forEach(([hook, result], index) => {
    const { status, stdout } = after[index][1]
    assert.equal(status, result.status, hook.name)
    if (hook.script !== 'session-start.sh') return assert.equal(stdout, result.stdout, hook.name)
    const drift = `dev-loop: the hooks run the trusted kit copy; these files differ from it: ${[...CHANGED].sort().join(', ')}`
    assert.deepEqual(JSON.parse(stdout), { hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: `${result.stdout.replace(/\n$/, '')}\n${drift}` } })
  })
  assertNoMarker(f)
})

test('a MANIFEST that lists the changed hash blocks on the MANIFEST', () => {
  const f = fixture()
  const path = '.dev-loop/hooks/codex/board.sh'
  plantMarker(f.main, path, f.marker)
  const manifest = readJson(join(f.main, MANIFEST))
  manifest.files[path] = `sha256:${sha(readFileSync(join(f.main, path)))}`
  writeFileSync(join(f.main, MANIFEST), `${JSON.stringify(manifest, null, 2)}\n`)
  assertBlocked(runAll(f), f.main, MANIFEST)
  assertNoMarker(f)
  assert.equal(existsSync(f.copy), false)
})

test('planted repos never select the code that runs', () => {
  const f = fixture()
  const sub = join(f.feature, 'sub')
  mkdirSync(sub)
  git(sub, 'init', '-q')
  const planted = join(f.root, 'planted')
  mkdirSync(planted)
  git(planted, 'init', '-q')
  for (const dir of ['.dev-loop', '.agents']) cpSync(join(f.main, dir), join(planted, dir), { recursive: true })
  for (const path of CHANGED) plantMarker(planted, path, f.marker)
  const outer = join(f.root, 'outer')
  mkdirSync(join(outer, 'x'), { recursive: true })
  writeFileSync(join(outer, '.git'), `gitdir: ${join(planted, '.git')}\n`)
  const rows = [[sub, sub, MANIFEST], [join(outer, 'x'), null, null], [planted, planted, CHANGED[1]]]
  for (const [cwd, source, path] of rows) {
    const results = runAll(f, { cwd })
    for (const [hook, result] of results) {
      assert.equal(result.status, WALLS.includes(hook.script) ? 2 : 0, `${cwd} ${hook.name}`)
      const text = WALLS.includes(hook.script) ? result.stderr : JSON.parse(result.stdout).systemMessage
      assert.match(text, /^Blocked: dev-loop kit at .* does not match the trusted hooks: /, `${cwd} ${hook.name}`)
    }
    if (source) assertBlocked(results, source, path)
  }
  assert.equal(existsSync(f.copy), false)
  runAll(f)
  for (const [cwd] of rows) assertRuns(runAll(f, { cwd }))
  assertNoMarker(f)
})

test('Stop and subagent hooks never block when the kit cannot load', () => {
  const f = fixture()
  const quiet = f.hooks.filter((hook) => ['Stop', 'SubagentStart', 'SubagentStop'].includes(hook.event))
  assert.equal(quiet.length, 5)
  const check = () => {
    for (const hook of quiet) {
      const result = runHook(f, hook)
      assert.equal(result.status, 0, hook.name)
      assert.ok(!result.stdout.includes('"decision"'), hook.name)
      assert.ok(JSON.parse(result.stdout).systemMessage.startsWith('Blocked: dev-loop kit at '), hook.name)
    }
  }
  plantMarker(f.main, CHANGED[0], f.marker)
  check()
  rmSync(join(f.main, MANIFEST))
  check()
  assertNoMarker(f)
})

test('a bare first worktree blocks the walls on the MANIFEST and warns the rest', () => {
  const f = fixture()
  const bare = join(f.root, 'bare.git')
  git(f.root, 'clone', '-q', '--bare', f.main, bare)
  const linked = join(f.root, 'linked')
  git(bare, 'worktree', 'add', '-q', linked)
  assertBlocked(runAll(f, { cwd: linked }), '', MANIFEST)
  assert.equal(existsSync(f.copy), false)
})

test('eight hooks at once fill one copy', async () => {
  const f = fixture()
  const guard = f.hooks.find((hook) => hook.script === 'git-guard.sh')
  const results = await Promise.all(Array.from({ length: 8 }, () => runAsync(f, guard)))
  const expected = runHook(f, guard)
  for (const result of results) assert.deepEqual(result, { status: 0, stdout: expected.stdout })
  assert.deepEqual(readdirSync(f.kit), [f.hash])
})

test('the result is the same with or without hooks.state headers', () => {
  const f = fixture()
  const planted = join(f.root, 'planted')
  mkdirSync(planted)
  git(planted, 'init', '-q')
  const header = (path) => `[hooks.state."${path}/.codex/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:0"\n`
  const configs = [null, header(f.main), header(f.root) + header(planted) + header(f.feature)]
  const results = (fill) => configs.map((config) => {
    rmSync(f.codexHome, { recursive: true, force: true })
    if (config !== null) {
      mkdirSync(f.codexHome, { recursive: true })
      writeFileSync(join(f.codexHome, 'config.toml'), config)
    }
    if (fill) runAll(f)
    return runAll(f).filter(([hook]) => hook.script !== 'session-start.sh')
  })
  for (const fill of [false, true]) {
    if (!fill) plantMarker(f.main, CHANGED[0], f.marker)
    else git(f.main, 'checkout', '--', CHANGED[0])
    const [first, ...rest] = results(fill)
    for (const other of rest) assert.deepEqual(other, first)
  }
  assertNoMarker(f)
})

test('init and upgrade add no kit folder to the dev-workspace filesystem table', () => {
  const { home, repo } = setup()
  init(repo, home, ['--yes', '--harness=both'])
  const keys = () => JSON.parse(execFileSync('python3', ['-c', `import json, sys, tomllib
def walk(node):
    for key, value in node.items():
        yield key
        if isinstance(value, dict):
            yield from walk(value)
print(json.dumps(list(walk(tomllib.load(open(sys.argv[1], "rb"))["permissions"]["dev-workspace"]["filesystem"]))))`, join(repo, '.codex/config.toml')], { encoding: 'utf8' }))
  const check = () => {
    const found = keys()
    assert.ok(found.length > 0)
    for (const key of found) assert.doesNotMatch(key, /\.dev-loop|\.agents|\.codex/, key)
  }
  check()
  upgrade(repo, home, [], fakePackage('99.0.0'))
  check()
})

test('upgrade to another MANIFEST rewrites each Codex kit entry with the new H', () => {
  const { home, repo } = setup()
  run(fakePackage('98.0.0'), 'init', repo, home, ['--yes', '--harness=both'])
  const old = sha(readFileSync(join(repo, MANIFEST)))
  const count = hookCommands(join(repo, '.codex/hooks.json')).length
  const output = upgrade(repo, home, [], fakePackage('99.0.0'))
  const next = sha(readFileSync(join(repo, MANIFEST)))
  assert.notEqual(next, old)
  const commands = hookCommands(join(repo, '.codex/hooks.json'))
  assert.equal(commands.length, count)
  for (const command of commands) {
    assert.ok(command.includes(`/dev-loop/kit/${next}/`), command)
    assert.ok(!command.includes(old), command)
  }
  assert.match(output, /run \/hooks in Codex/)
  assert.equal(existsSync(join(home, '.codex/dev-loop')), false)
})

test('the hooks act on the stdin cwd while the copy comes from the process cwd', () => {
  const f = fixture()
  const start = f.hooks.find((hook) => hook.name === 'SubagentStart seal.sh start')
  const result = runHook(f, start, { cwd: f.main, input: f.feature })
  assert.equal(result.status, 0, result.stderr)
  const gitDir = git(f.feature, 'rev-parse', '--absolute-git-dir').trim()
  assert.equal(readFileSync(join(gitDir, 'dev-loop-subagents/probe.head'), 'utf8').trim(), git(f.feature, 'rev-parse', 'HEAD').trim())
  assert.ok(existsSync(f.copy))
})

test('each command gives the same result under zsh', { skip: !hasZsh && 'no zsh' }, () => {
  checkChanged(fixture(), 'zsh')
  const f = fixture()
  checkFill(f, 'zsh')
  plantMarker(f.main, CHANGED[0], f.marker)
  const { stdout } = runHook(f, f.hooks.find((hook) => hook.script === 'session-start.sh'), { shell: 'zsh' })
  assert.match(JSON.parse(stdout).hookSpecificOutput.additionalContext, new RegExp(`these files differ from it: ${CHANGED[0].replaceAll('.', '\\.')}$`))
  assertNoMarker(f)
})
