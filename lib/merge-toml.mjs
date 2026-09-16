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
  const trimmed = line.trim()
  let inQuote = null
  let commentAt = trimmed.length
  for (let i = 0; i < trimmed.length; i++) {
    const char = trimmed[i]
    if (inQuote) {
      if (char === inQuote) inQuote = null
      continue
    }
    if (char === '"' || char === "'") {
      inQuote = char
      continue
    }
    if (char === '#') {
      commentAt = i
      break
    }
  }
  const withoutComment = trimmed.slice(0, commentAt).trim()
  const match = withoutComment.match(/^\[\s*(.*?)\s*\]$/)
  return match ? `[${match[1]}]` : withoutComment
}

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

function isDefinition(line) {
  const trimmed = line.trim()
  return trimmed !== '' && !trimmed.startsWith('#')
}

// Every table name a root dotted key implies: "a.b.c" declares tables a and
// a.b, so a fragment (or target) table with either header would be a second
// declaration of the same table.
function impliedHeaders(key) {
  const parts = key.split('.')
  return parts.slice(0, -1).map((_, index) => `[${parts.slice(0, index + 1).join('.')}]`)
}

export function mergeCodexConfig(currentText, fragmentText) {
  if (currentText === null) {
    return { text: fragmentText.endsWith('\n') ? fragmentText : `${fragmentText}\n`, changed: true, skipped: [] }
  }

  const current = splitToml(currentText)
  const fragment = splitToml(fragmentText)

  const currentRoot = current.root.filter((line) => !isDefinition(line) || rootKey(line) !== 'sandbox_mode')
  const removedLegacySandbox = currentRoot.length !== current.root.length
  const currentRootKeys = currentRoot.filter(isDefinition).map(rootKey)
  const existingKeys = new Set(currentRootKeys)
  const existingHeaders = new Set(splitTables(current.tables).map((table) => table.header))
  // A dotted key at the root declares its parent tables just as a header
  // does, and a header declares a name a dotted key can no longer define.
  // Both directions must count as "already declared", or the merge appends
  // a second declaration and the file stops parsing.
  const declaredByKeys = new Set(currentRootKeys.flatMap(impliedHeaders))

  const fragmentRoot = fragment.root.filter(isDefinition)
  const rootPresent = (line) => {
    const key = rootKey(line)
    return existingKeys.has(key) || impliedHeaders(key).some((header) => existingHeaders.has(header))
  }
  const rootToAdd = fragmentRoot.filter((line) => !rootPresent(line))
  const skippedKeys = fragmentRoot.filter(rootPresent).map(rootKey)

  // Never append a table the target already declares under the same header:
  // TOML forbids declaring the same table twice, and this installer only
  // ever adds missing content, it never rewrites a user's own values. The
  // headers dropped this way are reported back as `skipped`, so a caller
  // can tell the user their own value wins over dev-loop's for that table.
  const fragmentTables = splitTables(fragment.tables)
  const tablePresent = (table) => existingHeaders.has(table.header) || declaredByKeys.has(table.header)
  const tablesToAdd = fragmentTables.filter((table) => !tablePresent(table))
  const skipped = [...skippedKeys, ...fragmentTables.filter(tablePresent).map((table) => table.header)]

  // Nothing missing: leave the file exactly as it is. The kept keys and
  // tables are still reported, so a re-run says again which of the user's
  // own values won over dev-loop's.
  if (!removedLegacySandbox && rootToAdd.length === 0 && tablesToAdd.length === 0) {
    return { text: currentText, changed: false, skipped }
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
  return { text: lines.join('\n'), changed: true, skipped }
}

function trimTrailingBlank(lines) {
  const copy = [...lines]
  while (copy.length > 0 && copy[copy.length - 1].trim() === '') copy.pop()
  return copy
}
