// ACT-SURFACES: web.notes web.calendar web.people
// The import dialog's words (note.import_keep, event.import_ics, contacts.import_vcf, import.undo).
import { describe, expect, it } from 'vitest'
import { doneText, importable, previewLines } from './ImportDialog'

describe('import preview', () => {
  it('says what comes in and what stays out of a Keep export', () => {
    const l = previewLines({ kind: 'keep', calendar: null, counts: { notes: 2, checklists: 1, labels: 3, new_labels: 2, archived: 1, trashed: 4, attachments: 1, other_files: 0 } })
    expect(l.in).toEqual(['2 notes', '1 checklist', '3 labels (2 new)', '1 note stays archived'])
    expect(l.out).toEqual(["4 notes from Keep's trash", '1 attachment'])
    expect(importable({ kind: 'keep', counts: { notes: 0, checklists: 0 } })).toBe(0)
  })
  it('names the calendar and the events already there', () => {
    const l = previewLines({ kind: 'ics', calendar: { href: 'x', id: 1, name: 'Family' }, counts: { events: 12, existing: 3 } })
    expect(l.in).toEqual(['12 events into Family'])
    expect(l.out).toEqual(['3 events already in your calendars'])
  })
  it('counts new and known people, and cards without an address', () => {
    const l = previewLines({ kind: 'vcf', calendar: null, counts: { people: 1, existing: 2, no_email: 1, groups: 0 } })
    expect(l.in[0]).toBe('1 new person')
    expect(l.in[1]).toContain('2 people already in People')
    expect(l.out).toEqual(['1 card without an email address'])
    expect(importable({ kind: 'vcf', counts: { people: 0, existing: 2 } })).toBe(2)
  })
  it('toasts the result', () => {
    expect(doneText({ kind: 'keep', counts: { notes: 2, checklists: 1 } })).toBe('Imported 3 notes from Google Keep')
    expect(doneText({ kind: 'vcf', counts: { people: 4, updated: 1 } })).toBe('Imported 4 people, 1 updated')
  })
})
