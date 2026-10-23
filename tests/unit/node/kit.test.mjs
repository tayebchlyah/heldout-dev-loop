import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { projectCommand, buildKit, projectFragments } from '../../../lib/kit.mjs'

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const CLAUDE = '"${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/.dev-loop/hooks/claude/'
const CODEX = '"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/'

test('a Claude hook command points at the project hook folder and keeps its arguments', () => {
  assert.equal(
    projectCommand('~/.claude/hooks/deny-path.sh tests/acceptance'),
    `${CLAUDE}deny-path.sh" tests/acceptance`,
  )
  assert.equal(projectCommand('~/.claude/hooks/gate.sh'), `${CLAUDE}gate.sh"`)
})

test('a Codex hook command points at the project hook folder, rendered or raw', () => {
  assert.equal(projectCommand('/h/.codex/hooks/gate.sh'), `${CODEX}gate.sh"`)
  assert.equal(projectCommand('{{HOME}}/.codex/hooks/gate.sh'), `${CODEX}gate.sh"`)
})

test('a command with no hook path does not change', () => {
  const fragment = JSON.parse(readFileSync(join(packageRoot, 'claude', 'settings.fragment.json'), 'utf8'))
  const oneLiner = fragment.hooks.SessionStart[0].hooks[0].command
  assert.equal(projectCommand(oneLiner), oneLiner)
})

test('a Claude-only kit has the link and no Codex path', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude'] })
  const paths = kit.files.map((file) => file.path)
  assert.deepEqual(kit.link, { path: '.claude/skills/dev-loop', target: '../../.agents/skills/dev-loop' })
  assert.ok(!paths.some((path) => path.startsWith('.codex/') || path.includes('/codex/')))
  assert.ok(paths.includes('.agents/skills/dev-loop/SKILL.md'))
  assert.ok(paths.includes('.agents/skills/dev-loop/scripts/check_setup.py'))
  assert.ok(paths.includes('.claude/agents/implementer.md'))
  assert.ok(paths.includes('.dev-loop/hooks/claude/gate.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/claude/seal.sh'))
  assert.equal(kit.files.find((file) => file.path === '.dev-loop/hooks/claude/gate.sh').executable, true)
  assert.equal(kit.files.find((file) => file.path === '.claude/agents/implementer.md').executable, false)
})

test('a Codex-only kit has no link and no Claude path', () => {
  const kit = buildKit({ packageRoot, harnesses: ['codex'] })
  const paths = kit.files.map((file) => file.path)
  assert.equal(kit.link, null)
  assert.ok(!paths.some((path) => path.startsWith('.claude/') || path.includes('/claude/')))
  assert.ok(paths.includes('.codex/agents/implementer.toml'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/session-start.sh'))
  assert.ok(paths.includes('.dev-loop/hooks/codex/seal.sh'))
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

test('the kit holds no test file of the skill suite', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude', 'codex'] })
  const paths = kit.files.map((file) => file.path)
  assert.deepEqual(paths.filter((path) => /(^|\/)test_[^/]*\.py$/.test(path)), [])
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
  const actions = projectFragments({ packageRoot, harnesses: ['claude', 'codex'] })
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
    const codex = JSON.parse(hooks.rewrite(text))
    assert.equal(codex.hooks.SessionStart[0].hooks[0].command, `${CODEX}session-start.sh"`)
    assert.equal(codex.hooks.PreToolUse[0].hooks[0].command, `${CODEX}git-guard.sh"`)
  }

  const toml = actions[2]
  const tomlText = readFileSync(toml.source, 'utf8')
  assert.equal(toml.rewrite(tomlText), tomlText)

  assert.deepEqual(projectFragments({ packageRoot, harnesses: ['claude'] }).map((action) => action.target), ['.claude/settings.json'])
})

test('the kit has no simplifier', () => {
  const kit = buildKit({ packageRoot, harnesses: ['claude', 'codex'] })
  assert.ok(!kit.files.some((file) => file.path.includes('simplifier')))
  assert.ok(kit.files.some((file) => file.path === '.codex/agents/final-reviewer.toml'))
})
