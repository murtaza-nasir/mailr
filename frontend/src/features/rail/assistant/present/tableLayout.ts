// How a Markdown table in an assistant reply is laid out at a given width.
// The Android renderer has the same rule (ui/present/TableLayout.kt);
// both run the vectors in spec/fixtures/agent-markdown-fixtures.json.

export type TableLayout = 'table' | 'scroll' | 'cards'

const MAX_CHARS = 40
const CHAR_PX = 7
const PAD_PX = 16

/** The visible length of a cell: Markdown marks, citation links and link targets left out. */
export function cellChars(cell: string): number {
  return cell
    .replace(/\[(\d+)\]\(mailr-cite:\d+\)/g, '[$1]')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\*\*|__|`/g, '')
    .trim().length
}

/** 'table' when the columns fit `width` (px or dp), else 'cards' for 3+ columns, else 'scroll'. */
export function tableLayout(header: string[], rows: string[][], width: number): TableLayout {
  let est = 0
  header.forEach((h, i) => {
    const longest = Math.max(cellChars(h), ...rows.map((r) => cellChars(r[i] ?? '')))
    est += Math.min(MAX_CHARS, longest) * CHAR_PX + PAD_PX
  })
  if (est <= width) return 'table'
  return header.length >= 3 ? 'cards' : 'scroll'
}
