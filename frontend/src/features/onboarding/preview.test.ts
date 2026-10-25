import { describe, expect, it } from 'vitest'
import { SAMPLE_ROWS, stylePreview, toRow } from './preview'

describe('inbox style previews', () => {
  it('uses a sample until her own mail arrives', () => {
    const p = stylePreview('simple', [])
    expect(p.own).toBe(false)
    expect(p.groups[0].rows).toEqual(SAMPLE_ROWS.slice(0, 4))
  })
  it('shows her own mail once it is there', () => {
    const own = [toRow({ subject: 'Hello', unread_count: 1, important: true, category: null, participants: [{ email: 'me@x.org', me: true }, { email: 'sam@x.org', name: 'Sam' }] })]
    const p = stylePreview('simple', own)
    expect(p.own).toBe(true)
    expect(p.groups[0].rows[0]).toEqual({ from: 'Sam', subject: 'Hello', unread: true, important: true, category: 'primary' })
  })
  it('puts important mail first, and draws category tabs for Categories', () => {
    const imp = stylePreview('important_first', [])
    expect(imp.groups.map((g) => g.title)).toEqual(['Important', 'Everything else'])
    expect(imp.groups[0].rows.every((r) => r.important)).toBe(true)
    const cats = stylePreview('default', [])
    expect(cats.tabs[0]).toBe('Primary')
    expect(cats.groups[0].rows.every((r) => r.category === 'primary')).toBe(true)
  })
})
