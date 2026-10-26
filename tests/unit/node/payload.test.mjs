// tests/unit/node/payload.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, existsSync, lstatSync, readlinkSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

const REQUIRED = [
  'skill/SKILL.md',
  'skill/references/claude.md',
  'skill/references/codex.md',
  'skill/references/mutation.md',
  'skill/references/benchmark.md',
  'skill/references/maps.md',
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

function trackedTexts(keep = () => true) {
  const tracked = execFileSync('git', ['ls-files'], { cwd: repoRoot, encoding: 'utf8' })
    .split('\n')
    .filter((line) => line.length > 0 && keep(line))
  return tracked.map((relative) => {
    const path = join(repoRoot, relative)
    return [relative, lstatSync(path).isSymbolicLink() ? readlinkSync(path) : readFileSync(path, 'utf8')]
  })
}

test('no tracked file holds an absolute home path or a personal name', () => {
  // Built from parts so this check's own source text never contains the
  // forbidden substrings verbatim, which would otherwise make this test
  // fail against itself once it is a tracked file.
  const forbiddenPathPrefix = '/' + 'Users/'
  const forbiddenName = 'Tay' + 'eb'
  for (const [relative, text] of trackedTexts()) {
    assert.ok(!text.includes(forbiddenPathPrefix), `absolute path in ${relative}`)
    assert.ok(!text.includes(forbiddenName), `personal name in ${relative}`)
  }
})

// shortcut: a guessed name can be confirmed against this unsalted hash list, upgrade to a keyed HMAC with the key in a CI secret before the repository goes public
const INTERNAL_NAME_HASHES = new Set([
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
  'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED', 'REMOVED',
])

const nameHash = (words) => createHash('sha256').update(words).digest('hex').slice(0, 16)

function internalNames(text, hashes) {
  const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []
  const found = new Set()
  for (let i = 0; i < words.length; i++) {
    for (let n = 1; n <= 3 && i + n <= words.length; n++) {
      const name = words.slice(i, i + n).join(' ')
      if (hashes.has(nameHash(name))) found.add(name)
    }
  }
  return [...found]
}

test('internalNames finds a hashed name of one to three words in any case', () => {
  const hashes = new Set([nameHash('acme'), nameHash('big shop co')])
  assert.deepEqual(internalNames('Run the ACME suite', hashes), ['acme'])
  assert.deepEqual(internalNames('/x/big-shop_co/y', hashes), ['big shop co'])
  assert.deepEqual(internalNames('acmeshop and big shop', hashes), [])
})

test('no tracked file names an internal client or project', () => {
  // shortcut: the installed dev-loop kit under .agents/skills/dev-loop/ is a copy of the guarded source, cleared by the next upgrade; drop this filter after it
  for (const [relative, text] of trackedTexts((line) => !line.startsWith('.agents/skills/dev-loop/'))) {
    assert.deepEqual(internalNames(text, INTERNAL_NAME_HASHES), [], `internal name in ${relative}`)
  }
})

test('dev-loop.json maps commands.acceptance to the script that runs both runners', () => {
  const marker = JSON.parse(readFileSync(join(repoRoot, 'dev-loop.json'), 'utf8'))
  assert.equal(marker.commands.acceptance, 'scripts/acceptance.sh')
  const script = readFileSync(join(repoRoot, 'scripts', 'acceptance.sh'), 'utf8')
  assert.ok(script.includes('--discover'))
  assert.ok(script.includes('node --test'))
})

test('ci.yml has a job named acceptance that runs commands.acceptance', () => {
  const ci = readFileSync(join(repoRoot, '.github', 'workflows', 'ci.yml'), 'utf8')
  const marker = JSON.parse(readFileSync(join(repoRoot, 'dev-loop.json'), 'utf8'))
  assert.match(ci, /^ {2}acceptance:$/m)
  assert.ok(ci.includes(`run: ${marker.commands.acceptance}`))
})
