import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildPlan, assertLinkSource } from '../lib/plan.mjs'

const repoRoot = '/repo'
const home = '/home/x'

test('the Claude plan covers the skill, the links, the agents, the hooks and the settings', () => {
  const actions = buildPlan({ repoRoot, home, harnesses: ['claude'], mode: 'copy' })
  const targets = actions.map((action) => action.target)
  assert.ok(targets.includes('/home/x/.agents/skills/dev-loop'))
  assert.ok(targets.includes('/home/x/.claude/skills/dev-loop'))
  assert.ok(targets.includes('/home/x/.claude/agents/implementer.md'))
  assert.ok(targets.includes('/home/x/.claude/hooks/gate.sh'))
  assert.ok(targets.includes('/home/x/.claude/settings.json'))
  assert.ok(!targets.some((target) => target.includes('/.codex/')))
})

test('the Codex plan covers the agents, the hooks, the rules and the two configuration files', () => {
  const actions = buildPlan({ repoRoot, home, harnesses: ['codex'], mode: 'copy' })
  const targets = actions.map((action) => action.target)
  assert.ok(targets.includes('/home/x/.codex/agents/implementer.toml'))
  assert.ok(targets.includes('/home/x/.codex/hooks/gate.sh'))
  assert.ok(targets.includes('/home/x/.codex/rules/default.rules'))
  assert.ok(targets.includes('/home/x/.codex/hooks.json'))
  assert.ok(targets.includes('/home/x/.codex/config.toml'))
})

test('a Codex hook follows the install mode and carries no template token', () => {
  const linked = buildPlan({ repoRoot, home, harnesses: ['codex'], mode: 'link' })
    .find((action) => action.target === '/home/x/.codex/hooks/git-guard.sh')
  assert.equal(linked.mode, 'link')
  assert.equal(linked.template, undefined)

  const copied = buildPlan({ repoRoot, home, harnesses: ['codex'], mode: 'copy' })
    .find((action) => action.target === '/home/x/.codex/hooks/git-guard.sh')
  assert.equal(copied.mode, 'copy')
})

test('a merge action keeps its mode in link mode', () => {
  const actions = buildPlan({ repoRoot, home, harnesses: ['claude'], mode: 'link' })
  const settings = actions.find((action) => action.target === '/home/x/.claude/settings.json')
  assert.equal(settings.kind, 'merge-json')
})

test('assertLinkSource rejects a folder that is not a repository clone', () => {
  const root = mkdtempSync(join(tmpdir(), 'dev-loop-link-'))
  assert.throws(() => assertLinkSource(root), /--link needs a Git clone of dev-loop/)
})

test('assertLinkSource accepts a clone that holds the skill', () => {
  const root = mkdtempSync(join(tmpdir(), 'dev-loop-link-ok-'))
  mkdirSync(join(root, '.git'), { recursive: true })
  mkdirSync(join(root, 'skill'), { recursive: true })
  writeFileSync(join(root, 'skill', 'SKILL.md'), 'name: dev-loop\n')
  assert.doesNotThrow(() => assertLinkSource(root))
})

test('the rules file is never a copy or link target, in copy mode or link mode', () => {
  for (const mode of ['copy', 'link']) {
    const actions = buildPlan({ repoRoot, home, harnesses: ['codex'], mode })
    const rules = actions.find((action) => action.target === '/home/x/.codex/rules/default.rules')
    assert.equal(rules.kind, 'merge-lines')
  }
})

test('the Codex config merge reports the tables it skips', () => {
  const actions = buildPlan({ repoRoot, home, harnesses: ['codex'], mode: 'copy' })
  const config = actions.find((action) => action.target === '/home/x/.codex/config.toml')
  assert.equal(config.kind, 'merge-toml')
})
