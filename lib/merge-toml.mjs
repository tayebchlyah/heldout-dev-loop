export function splitToml(text) {
  const lines = text.split('\n')
  const index = lines.findIndex((line) => line.trimStart().startsWith('['))
  if (index === -1) return { root: lines, tables: [] }
  return { root: lines.slice(0, index), tables: lines.slice(index) }
}

// Reduces a table header line to a canonical form so two spellings of the
// same table ("[features]", "[ features ]", "[features]  # comment") compare
// equal. Strips a trailing "# ..." comment, but only a '#' that sits outside
// a quoted table name: a quoted key (e.g. ":workspace_roots") could in
// principle contain '#', and that must never be mistaken for a comment.
// Then trims the whitespace just inside the brackets.
function normalizeHeader(line) {
  const withoutComment = stripComment(line)
  const match = withoutComment.match(/^\[\s*(.*?)\s*\]$/)
  if (!match) return withoutComment
  if (match[1].startsWith('[')) return `[${match[1]}]`
  return canonHeader(splitOutsideQuotes(match[1], '.').map(normalizeKey))
}

const canonSegment = (segment) => (/^[A-Za-z0-9_-]+$/.test(segment) || /^["']/.test(segment) ? segment : `"${segment}"`)
const canonHeader = (segments) => `[${segments.map(canonSegment).join('.')}]`

function stripComment(text) {
  return splitOutsideQuotes(text.trim(), '#')[0].trim()
}

// #136: "github.com", 'github.com' and github.com name the same key.
function normalizeKey(key) {
  const trimmed = key.trim()
  const match = trimmed.match(/^(["'])([^"'\\]*)\1$/)
  return match ? match[2] : trimmed
}

function splitOutsideQuotes(text, separator) {
  const parts = []
  let inQuote = null
  let start = 0
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (inQuote) {
      if (char === inQuote) inQuote = null
    } else if (char === '"' || char === "'") {
      inQuote = char
    } else if (char === separator) {
      parts.push(text.slice(start, i))
      start = i + 1
    }
  }
  parts.push(text.slice(start))
  return parts
}

const rawLineKey = (line) => splitOutsideQuotes(line, '=')[0].trim()
const keySegments = (key) => splitOutsideQuotes(key, '.').map(normalizeKey)
const lineKey = (line) => JSON.stringify(keySegments(rawLineKey(line)))
const canonValue = (text) => {
  const value = stripComment(text)
  const match = value.match(/^"([^"\\]*)"$|^'([^'\\]*)'$/)
  return match ? `"${match[1] ?? match[2]}"` : value
}
const lineValue = (line) => canonValue(splitOutsideQuotes(line, '=').slice(1).join('='))
const headerSegments = (header) => splitOutsideQuotes(header.slice(1, -1), '.').map(normalizeKey)

// Splits a run of table lines (as returned by splitToml's `tables`) into one
// entry per header line ("[...]"), each carrying its normalized header and
// every original line up to (not including) the next header, so the lines
// that get appended keep the fragment's own formatting. A blank line before
// the first header, if any, belongs to no table and is dropped.
function splitTables(lines) {
  const tables = []
  let current = null
  for (const line of lines) {
    if (line.trim().startsWith('[')) {
      current = { header: normalizeHeader(line), lines: [line] }
      tables.push(current)
    } else if (current) {
      current.lines.push(line)
    }
  }
  return tables
}

// The key a root line defines: the text before the first '=' ("model",
// "features.hooks"). Comparing whole lines instead would let a target that
// sets its own value for one of the fragment's keys receive the fragment's
// line too, and TOML refuses a key defined twice.
function rootKey(line) {
  const trimmed = line.trim()
  const equals = trimmed.indexOf('=')
  return equals === -1 ? trimmed : trimmed.slice(0, equals).trim()
}

// #263: root keys compare by lineKey: "model", 'model' and model name the same key.
const SANDBOX_MODE = JSON.stringify(['sandbox_mode'])

function isDefinition(line) {
  const trimmed = line.trim()
  return trimmed !== '' && !trimmed.startsWith('#')
}

// Every table name a root dotted key implies: "a.b.c" declares tables a and
// a.b, so a fragment (or target) table with either header would be a second
// declaration of the same table.
function impliedHeaders(key, includeSelf = false) {
  const parts = keySegments(key)
  const count = includeSelf ? parts.length : parts.length - 1
  return parts.slice(0, count).map((_, index) => canonHeader(parts.slice(0, index + 1)))
}

function keyLines(lines) {
  const keys = []
  let open = null
  let depth = 0
  for (const line of lines) {
    if (open === null && depth === 0 && isDefinition(line)) keys.push(line)
    for (let i = 0; i < line.length; i++) {
      const char = line[i]
      if (open) {
        if (open[0] === '"' && char === '\\') i++
        else if (line.startsWith(open, i)) { open = null; i += 2 }
      } else if (char === '#') break
      else if (line.startsWith('"""', i) || line.startsWith("'''", i)) { open = line.slice(i, i + 3); i += 2 }
      else if (char === '"' || char === "'") {
        let end = i + 1
        while (end < line.length && line[end] !== char) end += char === '"' && line[end] === '\\' ? 2 : 1
        i = end
      } else if (char === '[' || char === '{') depth++
      else if (char === ']' || char === '}') depth--
    }
  }
  return keys
}

// The per-agent permission profiles 2.1.1 wrote and Codex never applied, by
// header, with the body (definition lines, trimmed, joined by newline) 2.1.1
// wrote. A table still equal to it is dev-loop's own and is removed.
export const LEGACY_PROFILE_TABLES = new Map([
  ['[permissions.dev-implementer]', 'description = "Implementation access without held-out acceptance tests."\nextends = "dev-workspace"'],
  ['[permissions.dev-implementer.filesystem.":workspace_roots"]', '"tests/acceptance" = "deny"\n"tests/acceptance/**" = "deny"'],
  ['[permissions.dev-spec-tester]', 'description = "Acceptance-test access without implementation sources."\nextends = "dev-workspace"'],
  ['[permissions.dev-spec-tester.filesystem.":workspace_roots"]', ['src', 'app', 'backend', 'frontend'].flatMap((dir) => [`"${dir}" = "deny"`, `"${dir}/**" = "deny"`]).join('\n')],
  ['[permissions.dev-read-only]', 'description = "Read-only review with environment files unreadable."\nextends = ":read-only"'],
  ['[permissions.dev-read-only.filesystem.":workspace_roots"]', '".env" = "deny"\n".env.*" = "deny"\n"**/.env" = "deny"\n"**/.env.*" = "deny"'],
])

export const KEY_MERGED_TABLES = new Set(['[features]', '[permissions.dev-workspace.network]', '[permissions.dev-workspace.network.domains]'])

const tableBody = (table) => table.lines.slice(1).filter(isDefinition).map((line) => line.trim()).join('\n')

export function mergeCodexConfig(currentText, fragmentText) {
  if (currentText === null) {
    return { text: fragmentText.endsWith('\n') ? fragmentText : `${fragmentText}\n`, changed: true, skipped: [], removed: [] }
  }

  const split = splitToml(currentText)
  const isLegacy = (table) => LEGACY_PROFILE_TABLES.get(table.header) === tableBody(table)
  const currentTables = splitTables(split.tables)
  const removed = currentTables.filter(isLegacy).map((table) => table.header)
  const keptTables = currentTables.filter((table) => !isLegacy(table)).map((table) => ({ ...table, lines: [...table.lines] }))
  const fragment = splitToml(fragmentText)

  const currentRoot = split.root.filter((line) => !isDefinition(line) || lineKey(line) !== SANDBOX_MODE)
  const removedLegacySandbox = currentRoot.length !== split.root.length
  const currentRootKeys = currentRoot.filter(isDefinition).map(rootKey)
  const existingKeys = new Set(currentRoot.filter(isDefinition).map(lineKey))
  const existingHeaders = new Set(keptTables.map((table) => table.header))
  // A dotted key at the root declares its parent tables just as a header
  // does, and a header declares a name a dotted key can no longer define.
  // Both directions must count as "already declared", or the merge appends
  // a second declaration and the file stops parsing.
  const inlineRootLines = currentRoot.filter(isDefinition).filter((line) => lineValue(line).startsWith('{'))
  const inlineDeclared = new Set(inlineRootLines.flatMap((line) => impliedHeaders(rootKey(line), true)))
  const declaredByKeys = new Set([...currentRootKeys.flatMap((key) => impliedHeaders(key)), ...inlineDeclared])

  const fragmentRoot = fragment.root.filter(isDefinition)
  const rootPresent = (line) => existingKeys.has(lineKey(line)) || impliedHeaders(rootKey(line)).some((header) => existingHeaders.has(header))
  const rootToAdd = fragmentRoot.filter((line) => !rootPresent(line))
  // A kept key or table equal to dev-loop's own (a re-run, an upgrade) is not
  // the user's value winning, so it is not reported.
  const rootValue = (line) => `${lineKey(line)}=${lineValue(line)}`
  const currentLines = new Set(currentRoot.filter(isDefinition).map(rootValue))
  const skippedKeys = fragmentRoot.filter((line) => rootPresent(line) && !currentLines.has(rootValue(line))).map(rootKey)

  // Never append a table the target already declares under the same header:
  // TOML forbids declaring the same table twice, and this installer only
  // ever adds missing content, it never rewrites a user's own values. The
  // headers dropped this way are reported back as `skipped`, so a caller
  // can tell the user their own value wins over dev-loop's for that table.
  const fragmentTables = splitTables(fragment.tables)
  // #136: a table is also declared when a table above it defines its name as a key
  // (`network = { ... }`, `network.enabled = false`); a header under it would not parse.
  const bySegments = new Map(keptTables.map((table) => [JSON.stringify(headerSegments(table.header)), table]))
  const definesKey = (table, name) => keyLines(table.lines.slice(1)).some((line) => keySegments(rawLineKey(line))[0] === name)
  const declaredInParent = (header) => {
    const segments = headerSegments(header)
    return segments.some((name, index) => {
      const parent = index > 0 && bySegments.get(JSON.stringify(segments.slice(0, index)))
      return Boolean(parent) && definesKey(parent, name)
    })
  }
  const underInline = (header) => headerSegments(header).some((_, index, segments) => inlineDeclared.has(canonHeader(segments.slice(0, index + 1))))
  const tablePresent = (table) => existingHeaders.has(table.header) || declaredByKeys.has(table.header) || underInline(table.header) || declaredInParent(table.header)
  const tablesToAdd = fragmentTables.filter((table) => !tablePresent(table))
  const body = tableBody
  const currentBodies = new Map(keptTables.map((table) => [table.header, body(table)]))
  const differs = (table) => currentBodies.get(table.header) !== body(table)
  // #136: a key-merged table the user declares gets each missing kit key; a user value stays and is reported.
  const keyMerged = (table) => KEY_MERGED_TABLES.has(table.header) && existingHeaders.has(table.header) && !declaredInParent(table.header)
  const skippedItems = []
  let addedKeys = 0
  for (const table of fragmentTables.filter(keyMerged)) {
    const target = keptTables.find((candidate) => candidate.header === table.header)
    const values = new Map(keyLines(target.lines.slice(1)).map((line) => [lineKey(line), lineValue(line)]))
    const missing = []
    for (const line of keyLines(table.lines.slice(1))) {
      if (!values.has(lineKey(line))) missing.push(line)
      else if (values.get(lineKey(line)) !== lineValue(line)) skippedItems.push(`${table.header} ${rawLineKey(line)}`)
    }
    const last = target.lines.findLastIndex((line, index) => index === 0 || isDefinition(line))
    target.lines.splice(last + 1, 0, ...missing)
    addedKeys += missing.length
  }
  const skipped = [
    ...skippedKeys,
    ...fragmentTables.filter((table) => !keyMerged(table) && tablePresent(table) && differs(table)).map((table) => table.header),
    ...skippedItems,
  ]
  const current = { tables: keptTables.flatMap((table) => table.lines) }

  // Nothing missing: leave the file exactly as it is. The kept keys and
  // tables that differ from dev-loop's are still reported, so a re-run says
  // again which of the user's own values won over dev-loop's.
  if (!removedLegacySandbox && removed.length === 0 && rootToAdd.length === 0 && tablesToAdd.length === 0 && addedKeys === 0) {
    return { text: currentText, changed: false, skipped, removed }
  }

  const lines = [
    ...trimTrailingBlank(currentRoot),
    ...rootToAdd,
    '',
    ...trimTrailingBlank(current.tables),
    '',
    ...trimTrailingBlank(tablesToAdd.flatMap((table) => table.lines)),
    '',
  ]
  return { text: lines.join('\n'), changed: true, skipped, removed }
}

function trimTrailingBlank(lines) {
  const copy = [...lines]
  while (copy.length > 0 && copy[copy.length - 1].trim() === '') copy.pop()
  return copy
}
