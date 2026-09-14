const MARKER = '[permissions.dev-workspace]'

export function splitToml(text) {
  const lines = text.split('\n')
  const index = lines.findIndex((line) => line.trimStart().startsWith('['))
  if (index === -1) return { root: lines, tables: [] }
  return { root: lines.slice(0, index), tables: lines.slice(index) }
}

// A substring check on the raw text would also fire on a commented-out
// mention ("# see [permissions.dev-workspace] docs") or on the marker
// spelled out inside a string value, and wrongly report the profile as
// already merged. Only a line that IS the table header, once surrounding
// whitespace is stripped, counts.
function hasMarkerHeader(text) {
  return text.split('\n').some((line) => line.trim() === MARKER)
}

// Splits a run of table lines (as returned by splitToml's `tables`) into one
// entry per header line ("[...]"), each carrying its header and every line
// up to (not including) the next header. A blank line before the first
// header, if any, belongs to no table and is dropped.
function splitTables(lines) {
  const tables = []
  let current = null
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('[')) {
      current = { header: trimmed, lines: [line] }
      tables.push(current)
    } else if (current) {
      current.lines.push(line)
    }
  }
  return tables
}

export function mergeCodexConfig(currentText, fragmentText) {
  if (currentText !== null && hasMarkerHeader(currentText)) {
    return { text: currentText, changed: false }
  }
  if (currentText === null) {
    return { text: fragmentText.endsWith('\n') ? fragmentText : `${fragmentText}\n`, changed: true }
  }

  const current = splitToml(currentText)
  const fragment = splitToml(fragmentText)

  const existing = new Set(current.root.map((line) => line.trim()))
  const rootToAdd = fragment.root.filter((line) => line.trim() !== '' && !existing.has(line.trim()))

  // Never append a table the target already declares under the same header:
  // TOML forbids declaring the same table twice, and this installer only
  // ever adds missing content, it never rewrites a user's own values.
  const existingHeaders = new Set(splitTables(current.tables).map((table) => table.header))
  const tablesToAdd = splitTables(fragment.tables)
    .filter((table) => !existingHeaders.has(table.header))
    .flatMap((table) => table.lines)

  const lines = [
    ...trimTrailingBlank(current.root),
    ...rootToAdd,
    '',
    ...trimTrailingBlank(current.tables),
    '',
    ...trimTrailingBlank(tablesToAdd),
    '',
  ]
  return { text: lines.join('\n'), changed: true }
}

function trimTrailingBlank(lines) {
  const copy = [...lines]
  while (copy.length > 0 && copy[copy.length - 1].trim() === '') copy.pop()
  return copy
}
