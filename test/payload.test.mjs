// test/payload.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const REQUIRED = [
  'skill/SKILL.md',
  'skill/references/claude.md',
  'skill/references/codex.md',
  'skill/scripts/check_setup.py',
  'skill/scripts/test_dev_loop.py',
  'claude/agents/implementer.md',
  'claude/agents/spec-tester.md',
  'claude/hooks/gate.sh',
  'claude/hooks/deny-path.sh',
  'claude/hooks/deny-acceptance-to-subagents.sh',
  'claude/hooks/git-guard.sh',
  'codex/hooks/gate.sh',
  'codex/hooks/deny-path.sh',
  'codex/hooks/git-guard.sh',
  'codex/hooks/session-start.sh',
  'codex/rules/default.rules',
]

const REQUIRED_CODEX_AGENTS = [
  'implementer.toml',
  'spec-tester.toml',
  'task-reviewer.toml',
  'simplifier.toml',
  'final-reviewer.toml',
  'security-reviewer.toml',
]

test('every payload file is present', () => {
  for (const relative of REQUIRED) {
    assert.ok(existsSync(join(repoRoot, relative)), `missing ${relative}`)
  }
})

test('every Codex agent is present', () => {
  const found = readdirSync(join(repoRoot, 'codex/agents'))
  for (const name of REQUIRED_CODEX_AGENTS) {
    assert.ok(found.includes(name), `missing codex/agents/${name}`)
  }
})

test('no tracked file holds an absolute home path or a personal name', () => {
  // Built from parts so this check's own source text never contains the
  // forbidden substrings verbatim, which would otherwise make this test
  // fail against itself once it is a tracked file.
  const forbiddenPathPrefix = '/' + 'Users/'
  const forbiddenName = 'Tay' + 'eb'
  const tracked = execFileSync('git', ['ls-files'], { cwd: repoRoot, encoding: 'utf8' })
    .split('\n')
    .filter((line) => line.length > 0)
  for (const relative of tracked) {
    const text = readFileSync(join(repoRoot, relative), 'utf8')
    assert.ok(!text.includes(forbiddenPathPrefix), `absolute path in ${relative}`)
    assert.ok(!text.includes(forbiddenName), `personal name in ${relative}`)
  }
})
