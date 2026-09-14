const MARKER = '[permissions.dev-workspace]'

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

// A substring check on the raw text would also fire on a commented-out
// mention ("# see [permissions.dev-workspace] docs") or on the marker
// spelled out inside a string value, and wrongly report the profile as
// already merged. Only a line that normalizes to exactly the marker header
// counts.
function hasMarkerHeader(text) {
  return text.split('\n').some((line) => normalizeHeader(line) === MARKER)
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

export function mergeCodexConfig(currentText, fragmentText) {
  if (currentText !== null && hasMarkerHeader(currentText)) {
    // The marker table is already installed: this is the cheap "already
    // installed" short-circuit, and it makes the per-table filter below
    // dead code for this project's own fragment (which always includes the
    // marker table). The filter still matters on its own for a *partial*
    // install: a target that already has some of the fragment's tables
    // (e.g. [features]) but not yet the marker table.
    return { text: currentText, changed: false, skipped: [] }
  }
  if (currentText === null) {
    return { text: fragmentText.endsWith('\n') ? fragmentText : `${fragmentText}\n`, changed: true, skipped: [] }
  }

  const current = splitToml(currentText)
  const fragment = splitToml(fragmentText)

  const existing = new Set(current.root.map((line) => line.trim()))
  const rootToAdd = fragment.root.filter((line) => line.trim() !== '' && !existing.has(line.trim()))

  // Never append a table the target already declares under the same header:
  // TOML forbids declaring the same table twice, and this installer only
  // ever adds missing content, it never rewrites a user's own values. The
  // headers dropped this way are reported back as `skipped`, so a caller
  // can tell the user their own value wins over dev-loop's for that table.
  const existingHeaders = new Set(splitTables(current.tables).map((table) => table.header))
  const fragmentTables = splitTables(fragment.tables)
  const tablesToAdd = fragmentTables.filter((table) => !existingHeaders.has(table.header))
  const skipped = fragmentTables
    .filter((table) => existingHeaders.has(table.header))
    .map((table) => table.header)

  const lines = [
    ...trimTrailingBlank(current.root),
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
