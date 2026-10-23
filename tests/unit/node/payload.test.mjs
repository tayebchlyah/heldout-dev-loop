// tests/unit/node/payload.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, existsSync, lstatSync, readlinkSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

const REQUIRED = [
  'skill/SKILL.md',
  'skill/references/claude.md',
  'skill/references/codex.md',
  'skill/references/mutation.md',
  'skill/scripts/check_setup.py',
  'skill/scripts/select_mutants.py',
  'skill/scripts/notes_path.py',
  'skill/scripts/devloop.py',
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

test('codex default.rules ships only dev-loop git/rm safety rules', () => {
  const rulesPath = join(repoRoot, 'codex/rules/default.rules')
  const text = readFileSync(rulesPath, 'utf8')

  for (const token of ['wacli', 'whatsapp', '@g.us']) {
    assert.ok(!text.toLowerCase().includes(token), `default.rules must not contain "${token}"`)
  }

  // Every rule call starts a fresh block at "prefix_rule(" or "command_rule(";
  // split on that boundary and require each block to be about git or rm, per
  // controller ruling R6a (no unrelated personal allowlist entries).
  const blocks = text
    .split(/\n(?=(?:prefix_rule|command_rule)\()/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0)

  assert.equal(blocks.length, 4, 'default.rules must contain exactly four rule blocks')

  // Extract the justification string from each block
  const justifications = blocks.map((block) => {
    const match = block.match(/justification="([^"]+)"/)
    return match ? match[1] : null
  })

  // Assert the exact set of dev-loop justifications
  const expectedJustifications = [
    'Global rule: git push needs explicit permission each time.',
    'Git with a directory override needs review because later arguments can hide a push or destructive write.',
    'Hard reset can destroy uncommitted work.',
    'Recursive deletion of filesystem root is never allowed.',
  ]
  assert.deepEqual(
    new Set(justifications),
    new Set(expectedJustifications),
    'default.rules must contain exactly the four dev-loop git/rm safety justifications',
  )
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
    // A tracked symlink's content is its target text, not the file it points to.
    const path = join(repoRoot, relative)
    const text = lstatSync(path).isSymbolicLink() ? readlinkSync(path) : readFileSync(path, 'utf8')
    assert.ok(!text.includes(forbiddenPathPrefix), `absolute path in ${relative}`)
    assert.ok(!text.includes(forbiddenName), `personal name in ${relative}`)
  }
})
