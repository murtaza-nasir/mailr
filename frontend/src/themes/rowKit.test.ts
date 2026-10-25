/// <reference types="node" />
// The row kit: every theme's list row draws the importance marker, the muted icon,
// the label chips and the signal chips from features/mail/RowParts.tsx, so the settings that
// govern them (Importance markers, the label's "show in message list", the density's attachment
// chips) work in every theme. Before, only Classic read `markers` and `showChips`.
import { describe, expect, it } from 'vitest'

const RAW = import.meta.glob(['../features/mail/ConversationRow.tsx', '../features/mail/RowParts.tsx', './*/*.tsx'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const read = (path: string) => RAW[path.startsWith('themes/') ? `./${path.slice(7)}` : `../${path}`]
const ROWS: Record<string, string> = {
  classic: 'features/mail/ConversationRow.tsx',
  office: 'themes/office/OfficeRow.tsx',
  focus: 'themes/focus/FocusRow.tsx',
  desk: 'themes/desk/DeskTable.tsx',
  cards: 'themes/cards/CardRow.tsx',
}

describe('row kit', () => {
  for (const [theme, path] of Object.entries(ROWS)) {
    const text = read(path)
    it(`${theme} draws the shared parts`, () => {
      for (const part of ['<RowMarker', 'markers={markers}', '<RowMuted', 'rowLabels(', '<RowLabelChips', '<RowSignals'])
        expect(text, `${path} lacks ${part}`).toContain(part)
      // No theme filters labels or draws a marker of its own.
      expect(text).not.toMatch(/show_in_message_list|<ImportanceMarker|<NudgeChip|<SignalChips/)
    })
  }
  it('rows with attachment chips honour the density (showChips)', () => {
    for (const theme of ['classic', 'cards']) {
      const text = read(ROWS[theme])
      expect(text).toMatch(/rowAttachments\(c, showChips,/)
    }
  })
})

describe('attachment chips', () => {
  it('cut long names with an ellipsis on the name, not the flex chip', async () => {
    const { readFileSync } = await import('node:fs')
    const css = readFileSync(new URL('../styles/list.css', import.meta.url), 'utf8')
    expect(css).toMatch(/\.att-chip \.att-name,[^{]*\{[^}]*text-overflow: ellipsis/)
    expect(read('features/mail/RowParts.tsx')).toContain('<span className="att-name">')
  })
})
