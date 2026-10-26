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
  '037c09c9fae5d155', '0d660d1fcb539d95', '102ca67ea08effac', '111feeed4ff1b594', '1366fac71146793a',
  '1aaf3025033c8e43', '1b4c9133da73a711', '1c69272ab4ce6ce0', '1f33bc3e6845b911', '211aff1a15450ed5',
  '235a48d24966e5d3', '24404b72dfb0a8e4', '27a4c151e2a7f598', '2993f8453f06da87', '33094b2abc7c0bc4',
  '35bce2b6d46de0fe', '365761c7ff66af00', '3e13e313a65387eb', '4a0310b339e5bb82', '4a4b52cc9761464e',
  '4e629e9dbcc153d7', '4f55dfd0f7a4282f', '5339564675dffe49', '578d179a488febf4', '57c15f8c94ae515a',
  '5981b3c991eca505', '5d77db2a2906aed3', '5daecbb9e2cd435c', '608116505c211577', '61d452998bdcae13',
  '68db42aa1f81ccd6', '69db2fdfc57c2a56', '78944f78d5ae1080', '79e97b5f3319badc', '7c0fd69d75480e1d',
  '7fb8b10bd498cb18', '804dd3baf1e160c7', '8139e763e2cd1cef', '83ca6d000a92893b', '87e94f5c862d6bda',
  '8892b6087899695d', '8c0b0fb70efd5854', '8eb370ba6b769652', '90adcb99a4f6db7c', '91aeaac1d32cecbd',
  '93b8b8f269be3e5b', '9a06248f2bc65afc', '9dab06aab29e3b99', 'a259d1d02e04b9a5', 'a4ea87f6b93701fa',
  'af9b6fe9bdaa154b', 'b3ec9ddca0dd1604', 'b5df80799343ab23', 'b5f5468e942891b6', 'b922b7e0ba4cfdb9',
  'ba2436bd25a09dd5', 'c1dc6761d5f76f3b', 'c3aa9a8cc69dc3bf', 'cb87b7885c9470db', 'cbc62794911ff31b',
  'd35d653712d1b04f', 'd48e1093a46fac35', 'dcdb1abf8c9a1797', 'e1aa78bd1c17f27b', 'ead0667368a4a273',
  'eef90af3a9197404', 'f2ed4886e20f1a03', 'f4d0cc7c68ecc2a0', 'f7070d57bbe5496e', 'ff3f6413ad1bc39a',
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
