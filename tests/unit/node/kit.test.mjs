import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, cpSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'
import { projectCommand, buildKit, codexHookKey, projectFragments } from '../../../lib/kit.mjs'
import { packageRoot, tempDir } from './helpers.mjs'

const CLAUDE = '"${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/.dev-loop/hooks/claude/'
const CODEX_MAIN = `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); if [ -z "$i" ]; then m=$w; elif [ -d "$w/.dev-loop" ] && git -C "$d" worktree list --porcelain | sed -n 's/^worktree //p' | { while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$d" ] && exit 0; done; exit 1; } && sed -n -E 's|^[[]hooks[.]state[.]"(/.*)/[.]codex/hooks[.]json:[^"]*"[]][[:space:]]*$|\\1|p' "\${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | { while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$w" ] && exit 0; done; exit 1; }; then m=$w; fi; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`
const FILL = readFileSync(join(packageRoot, 'lib', 'codex_fill.py'), 'utf8').replace(/\n$/, '')
const HASH = '0'.repeat(64)
const MISSING = '[ -f "$f" ] || { echo "Blocked: dev-loop hook script missing: $f" >&2; exit 2; }; '
const codex = (script, args = '', hash = HASH, { fill = FILL, after = '' } = {}) => {
  const wall = ['git-guard.sh', 'acceptance-wall.sh'].includes(script)
  const [mode, code] = wall ? ['block', 2] : script === 'session-start.sh' ? ['session', 0] : ['warn', 0]
  return `f="\${CODEX_HOME:-$HOME/.codex}/dev-loop/kit/${hash}/.dev-loop/hooks/codex/${script}"; k=\${f%/.dev-loop/*}; m=$(git worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); [ -d "$k" ] || python3 -I -c '${fill}' ${mode} "$k" "$m" || exit ${code}; ${after}${wall ? MISSING : ''}KIT_ROOT=$k KIT_MAIN=$m exec sh "$f"${args}`
}
// Each earlier Codex command prefix (#158, #200, #212, #222).
const OLD_PREFIXES = ['$(git rev-parse --show-toplevel)', "$(git worktree list --porcelain | sed -n '1s/^worktree //p')",
  `$(git worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')`,
  `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); { [ -z "$i" ] || [ -d "$w/.dev-loop" ]; } && m=$w; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`,
  `$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); { [ -z "$i" ] || { [ -d "$w/.dev-loop" ] && grep -qsF "[hooks.state.\\"$w/.codex/hooks.json:" "\${CODEX_HOME:-$HOME/.codex}/config.toml"; }; } && m=$w; i=1; fi; [ "$d" = / ] && break; d=\${d%/*}; d=\${d:-/}; done; printf %s "$m")`,
  CODEX_MAIN]
const OLD_TAIL = '"; [ -x "$f" ] || { echo "Blocked: dev-loop hook script missing: $f" >&2; exit 2; }; exec "$f"'

test('a Claude hook command points at the project hook folder and keeps its arguments', () => {
  assert.equal(
    projectCommand('~/.claude/hooks/deny-path.sh tests/acceptance'),
    `${CLAUDE}deny-path.sh" tests/acceptance`,
  )
  assert.equal(projectCommand('~/.claude/hooks/gate.sh'), `${CLAUDE}gate.sh"`)
})

test('a Codex hook command runs its script from the kit copy named by H', () => {
  const hash = 'a'.repeat(64)
  for (const home of ['{{HOME}}', '/h']) {
    assert.equal(projectCommand(`${home}/.codex/hooks/gate.sh`, hash), codex('gate.sh', '', hash))
    assert.equal(projectCommand(`${home}/.codex/hooks/seal.sh turn`, hash), codex('seal.sh', ' turn', hash))
    assert.equal(projectCommand(`${home}/.codex/hooks/git-guard.sh`, hash), codex('git-guard.sh', '', hash))
  }
  assert.ok(codex('gate.sh', '', hash).includes(' warn "$k" "$m" || exit 0; '))
  assert.ok(codex('git-guard.sh', '', hash).includes(' block "$k" "$m" || exit 2; '))
  assert.equal(projectCommand('/h/.codex/hooks/session-start.sh', hash), codex('session-start.sh', '', hash))
  assert.ok(codex('session-start.sh', '', hash).includes(' session "$k" "$m" || exit 0; '))
})

test('codexHookKey reads the script and arguments of every head', () => {
  const current = codex('seal.sh', ' turn')
  const newTail = current.slice(current.indexOf('seal.sh"') + 'seal.sh'.length)
  const commands = [current, codex('seal.sh', ' turn', 'f'.repeat(64)), codex('seal.sh', ' turn', 'f'.repeat(64), { fill: 'import sys' }),
    codex('seal.sh', ' turn', HASH, { after: MISSING })]
  for (const prefix of OLD_PREFIXES) for (const start of ['"', 'f="']) for (const tail of [`${OLD_TAIL} turn`, newTail, '" turn']) {
    commands.push(`${start}${prefix}/.dev-loop/hooks/codex/seal.sh${tail}`)
  }
  for (const command of commands) assert.equal(codexHookKey(command), 'seal.sh" turn', command)
  assert.equal(codexHookKey(codex('git-guard.sh')), 'git-guard.sh"')
  for (const command of ['echo mine', 'timeout 60 "$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/gate.sh"',
    '"$(git rev-parse --show-toplevel)/sub/.dev-loop/hooks/codex/gate.sh"', codex('seal.sh', ' turn', 'A'.repeat(64)),
    `f="\${CODEX_HOME:-$HOME/.codex}/dev-loop/kit/${HASH}/.dev-loop/hooks/codex/gate.sh"; echo user-step; KIT_ROOT=$k KIT_MAIN=$m exec sh "$f"`,
    codex('gate.sh', '', HASH, { after: 'echo user-step; ' }), `f="\${CODEX_HOME:-$HOME/.codex}/dev-loop/kit/${HASH}/.dev-loop/hooks/codex/gate.sh" turn`,
codex('seal.sh', ' turn', HASH, { fill: "x' ; echo '" })]) {
    assert.equal(codexHookKey(command), null, command)
  }
})

test('a Codex hook command needs the MANIFEST hash', () => {
  assert.throws(() => projectCommand('/h/.codex/hooks/gate.sh'), /MANIFEST hash/)
  assert.throws(() => projectCommand('/h/.codex/hooks/gate.sh', 'abc'), /MANIFEST hash/)
  const hooks = projectFragments({ packageRoot, harnesses: ['codex'] })[0]
  const raw = readFileSync(hooks.source, 'utf8')
  assert.throws(() => hooks.rewrite(raw), /MANIFEST hash/)
  assert.doesNotThrow(() => projectFragments({ packageRoot, harnesses: ['codex'], manifestHash: HASH })[0].rewrite(raw))
})

test('a command with no hook path does not change', () => {
  const fragment = JSON.parse(readFileSync(join(packageRoot, 'claude', 'settings.fragment.json'), 'utf8'))
  const oneLiner = fragment.hooks.SessionStart[0].hooks[0].command
  assert.equal(projectCommand(oneLiner), oneLiner)
})

test('a Claude-only kit has the link and no Codex path', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude'] })
  const paths = kit.files.map((file) => file.path)
  assert.deepEqual(kit.links, ['dev-loop', 'pause', 'continue', 'release-audit', 'garden', 'release', 'release-notes'].map((name) => ({ path: `.claude/skills/${name}`, target: `../../.agents/skills/${name}` })))
  assert.ok(!paths.some((path) => path.startsWith('.codex/') || path.includes('/codex/')))
  assert.ok(paths.includes('.agents/skills/dev-loop/SKILL.md'))
  assert.ok(paths.includes('.agents/skills/dev-loop/scripts/check_setup.py'))
  assert.ok(paths.includes('.claude/agents/implementer.md'))
  assert.ok(paths.includes('.dev-loop/hooks/claude/gate.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/claude/seal.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/claude/git-pin.sh'))
  for (const name of ['deny-ask.sh', 'pause.sh', 'worktree-wall.sh']) {
    assert.equal(kit.files.find((file) => file.path === `.dev-loop/hooks/claude/${name}`)?.executable, true, name)
  }
  assert.equal(kit.files.find((file) => file.path === '.dev-loop/hooks/claude/gate.sh').executable, true)
  assert.equal(kit.files.find((file) => file.path === '.claude/agents/implementer.md').executable, false)
})

test('a Codex-only kit has no link and no Claude path', () => {
  const kit = buildKit({ packageRoot, harnesses: ['codex'] })
  const paths = kit.files.map((file) => file.path)
  assert.deepEqual(kit.links, [])
  for (const path of ['.agents/skills/pause/SKILL.md', '.agents/skills/pause/agents/openai.yaml', '.agents/skills/continue/SKILL.md', '.agents/skills/release-audit/SKILL.md', '.agents/skills/release-audit/agents/openai.yaml', '.agents/skills/garden/SKILL.md', '.agents/skills/garden/agents/openai.yaml', '.agents/skills/release/SKILL.md', '.agents/skills/release/agents/openai.yaml', '.agents/skills/release-notes/SKILL.md', '.agents/skills/release-notes/agents/openai.yaml']) {
    assert.ok(paths.includes(path), path)
  }
  assert.ok(!paths.some((path) => path.startsWith('.claude/') || path.includes('/claude/')))
  assert.ok(paths.includes('.codex/agents/implementer.toml'))
  assert.ok(paths.includes('.codex/agents/planner.toml'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/session-start.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/seal.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/git-pin.sh'))
  assert.ok(!paths.includes('.dev-loop/hooks/codex/kit-main.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/acceptance-wall.sh'))
  assert.ok(paths.includes('.codex/rules/dev-loop.rules'))
  assert.ok(paths.includes('.agents/skills/dev-loop/SKILL.md'))
})

test('the kit holds no Python cache and uses forward slashes', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude', 'codex'] })
  for (const { path } of kit.files) {
    assert.ok(!path.includes('__pycache__'), path)
    assert.ok(!path.endsWith('.pyc'), path)
    assert.ok(!path.includes('\\'), path)
  }
})

test('a .DS_Store under skill/ or skills/<name>/ is not in the kit', () => {
  const root = tempDir('kit-')
  cpSync(join(packageRoot, 'skill'), join(root, 'skill'), { recursive: true })
  cpSync(join(packageRoot, 'claude'), join(root, 'claude'), { recursive: true })
  cpSync(join(packageRoot, 'codex'), join(root, 'codex'), { recursive: true })
  cpSync(join(packageRoot, 'skills'), join(root, 'skills'), { recursive: true })
  writeFileSync(join(root, 'skill', 'references', '.DS_Store'), 'x')
  writeFileSync(join(root, 'skills', 'pause', '.DS_Store'), 'x')
  const kit = buildKit({ packageRoot: root, harnesses: ['claude', 'codex'] })
  assert.ok(!kit.files.some((file) => file.path.endsWith('.DS_Store')))
})

test('the kit has no test_*.py filter: a skill/scripts/test_x.py is included', () => {
  const root = tempDir('kit-')
  cpSync(join(packageRoot, 'skill'), join(root, 'skill'), { recursive: true })
  cpSync(join(packageRoot, 'claude'), join(root, 'claude'), { recursive: true })
  cpSync(join(packageRoot, 'codex'), join(root, 'codex'), { recursive: true })
  cpSync(join(packageRoot, 'skills'), join(root, 'skills'), { recursive: true })
  writeFileSync(join(root, 'skill', 'scripts', 'test_x.py'), 'x = 1\n')
  const kit = buildKit({ packageRoot: root, harnesses: ['claude', 'codex'] })
  const paths = kit.files.map((file) => file.path)
  assert.ok(paths.includes('.agents/skills/dev-loop/scripts/test_x.py'))
  assert.ok(paths.includes('.agents/skills/dev-loop/scripts/devloop.py'))
  assert.ok(paths.includes('.agents/skills/dev-loop/references/tests.md'))
})

test('the Claude agents run the project hooks', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude'] })
  for (const name of ['implementer.md', 'spec-tester.md']) {
    const { content } = kit.files.find((file) => file.path === `.claude/agents/${name}`)
    assert.ok(!content.includes('~/.claude'), name)
    assert.ok(content.includes(`${CLAUDE}deny-path.sh"`), name)
  }
  const { content } = kit.files.find((file) => file.path === '.claude/agents/implementer.md')
  assert.ok(content.includes(`command: '${CLAUDE}deny-path.sh" tests/acceptance'`))
})

test('the fragments merge into the project files with project commands', () => {
  const actions = projectFragments({ packageRoot, harnesses: ['claude', 'codex'], manifestHash: HASH })
  assert.deepEqual(actions.map((action) => [action.kind, action.merge, action.target]), [
    ['merge-json', 'settings', '.claude/settings.json'],
    ['merge-json', 'hooks', '.codex/hooks.json'],
    ['merge-toml', undefined, '.codex/config.toml'],
  ])
  for (const action of actions) assert.ok(action.source.startsWith(packageRoot))

  const settings = actions[0]
  const claude = JSON.parse(settings.rewrite(readFileSync(settings.source, 'utf8')))
  assert.equal(claude.hooks.Stop[0].hooks[0].command, `${CLAUDE}gate.sh"`)
  assert.equal(claude.permissions.deny.length, 10)
  assert.ok(claude.permissions.deny.includes('Agent(caveman:cavecrew-reviewer)'))
  assert.deepEqual(claude.env, { ENABLE_STOP_REVIEW: '0', ENABLE_COMMIT_REVIEW: '0', SG_PUSH_SWEEP: '0', CLAUDE_CODE_ENABLE_TODO_TOOLS: '1' })

  const hooks = actions[1]
  const raw = readFileSync(hooks.source, 'utf8')
  for (const text of [raw, raw.split('{{HOME}}').join('/h')]) {
    const codexHooks = JSON.parse(hooks.rewrite(text))
    assert.equal(codexHooks.hooks.SessionStart[0].hooks[0].command, codex('session-start.sh'))
    assert.equal(codexHooks.hooks.PreToolUse[0].hooks[0].command, codex('git-guard.sh'))
  }

  const toml = actions[2]
  const tomlText = readFileSync(toml.source, 'utf8')
  assert.equal(toml.rewrite(tomlText), tomlText)

  assert.deepEqual(projectFragments({ packageRoot, harnesses: ['claude'] }).map((action) => action.target), ['.claude/settings.json'])
})

function kitSourceCopy(fill) {
  const root = tempDir('kit-')
  for (const folder of ['skill', 'skills', 'claude', 'codex']) cpSync(join(packageRoot, folder), join(root, folder), { recursive: true })
  mkdirSync(join(root, 'lib'))
  for (const name of ['kit.mjs', 'manifest.mjs']) copyFileSync(join(packageRoot, 'lib', name), join(root, 'lib', name))
  if (fill !== undefined) writeFileSync(join(root, 'lib', 'codex_fill.py'), fill)
  return root
}

test('buildKit works from a kit source with no lib/codex_fill.py', async () => {
  const root = kitSourceCopy()
  const copy = await import(pathToFileURL(join(root, 'lib', 'kit.mjs')).href)
  assert.ok(copy.buildKit({ packageRoot: root, harnesses: ['claude', 'codex'] }).files.length > 0)
})

test('a Codex command refuses a fill text that holds a single quote', async () => {
  const root = kitSourceCopy("print('x')\n")
  const copy = await import(pathToFileURL(join(root, 'lib', 'kit.mjs')).href)
  assert.throws(() => copy.projectCommand('{{HOME}}/.codex/hooks/seal.sh', HASH), /no single quote/)
})

test('the kit has no simplifier', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude', 'codex'] })
  assert.ok(!kit.files.some((file) => file.path.includes('simplifier')))
  assert.ok(kit.files.some((file) => file.path === '.codex/agents/final-reviewer.toml'))
})
