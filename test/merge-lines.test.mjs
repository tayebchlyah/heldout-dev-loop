// test/merge-lines.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { mergeRules } from '../lib/merge-lines.mjs'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const defaultRules = readFileSync(`${repoRoot}/codex/rules/default.rules`, 'utf8')

const fragment = [
  'prefix_rule(',
  '    pattern=["ls"],',
  '    decision="allow",',
  '    justification="Listing a directory is always safe.",',
  '    match=["ls -la"],',
  '    not_match=[],',
  ')',
  'command_rule(',
  '    pattern=["pwd"],',
  '    decision="allow",',
  '    justification="Printing the working directory is always safe.",',
  '    match=["pwd"],',
  '    not_match=[],',
  ')',
  '',
].join('\n')

test('a null current text yields the fragment unchanged, every justification added', () => {
  const { text, added } = mergeRules(null, fragment)
  assert.equal(text, fragment)
  assert.deepEqual(added, [
    'Listing a directory is always safe.',
    'Printing the working directory is always safe.',
  ])
})

test('a second merge of the same fragment adds nothing and leaves the text identical', () => {
  const first = mergeRules(null, fragment)
  const second = mergeRules(first.text, fragment)
  assert.deepEqual(second.added, [])
  assert.equal(second.text, first.text)
})

test('an unrelated user rule survives at its original position and the fragment appends after it', () => {
  const userRule = [
    'prefix_rule(',
    '    pattern=["curl"],',
    '    decision="prompt",',
    '    justification="Example: a user\'s own rule must survive a merge untouched.",',
    '    match=["curl example.com"],',
    '    not_match=["curl --help"],',
    ')',
    '',
  ].join('\n')

  const { text, added } = mergeRules(userRule, defaultRules)

  // the user's block is untouched and still first in the file
  assert.ok(text.startsWith(userRule.trimEnd()), "the user's rule must stay at its original position")

  // every dev-loop rule from the real fragment was appended
  assert.deepEqual(added, [
    'Global rule: git push needs explicit permission each time.',
    'Git with a directory override needs review because later arguments can hide a push or destructive write.',
    'Hard reset can destroy uncommitted work.',
    'Recursive deletion of filesystem root is never allowed.',
  ])
  for (const justification of added) {
    assert.ok(text.includes(`justification="${justification}"`))
  }
})

test('merging the real default.rules fragment into itself is a no-op', () => {
  const { text, added } = mergeRules(defaultRules, defaultRules)
  assert.deepEqual(added, [])
  assert.equal(text, defaultRules)
})

test('a fragment block already present, reformatted, is recognized by its justification alone', () => {
  const current = [
    'prefix_rule(',
    '  pattern=["ls"], decision="allow",',
    '  justification="Listing a directory is always safe.",',
    '  match=["ls -la"], not_match=[],',
    ')',
    '',
  ].join('\n')
  const { added } = mergeRules(current, fragment)
  assert.deepEqual(added, ['Printing the working directory is always safe.'])
})
