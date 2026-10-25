import { describe, expect, it } from 'vitest'
import type { Display } from '../rail/assistant/present/types'
import { historyLine } from './approvals'

const event: Display = {
  v: 1, icon: 'event', title: 'Create event “Review with Alice”', summary: null, danger: false, reason: null, text: '',
  fields: [
    { label: 'When', value: { t: 'when', text: 'Wed 30 Sep, 2:00–3:00 PM', start: null, end: null, all_day: false } },
    { label: 'Where', value: { t: 'text', text: 'Room 204' } },
    { label: 'Calendar', value: { t: 'text', text: 'Work' } },
    { label: 'Notes', value: { t: 'quote', text: 'Bring the slides' } },
  ],
}

describe('historyLine', () => {
  it('uses the card’s wording: when, where and the calendar by name', () => {
    expect(historyLine(event)).toBe('When: Wed 30 Sep, 2:00–3:00 PM · Where: Room 204 · Calendar: Work')
  })
  it('prefers the summary, skips long values and shortens long text', () => {
    expect(historyLine({ ...event, summary: 'Mail from 6 senders gets the label Finance.' })).toBe('Mail from 6 senders gets the label Finance.')
    const long = 'x'.repeat(80)
    expect(historyLine({ ...event, fields: [event.fields[3], { label: '', value: { t: 'text', text: long } }] })).toBe(`${'x'.repeat(59)}…`)
  })
  it('has nothing to say without a display', () => {
    expect(historyLine(null)).toBeNull()
    expect(historyLine({ ...event, fields: [] })).toBeNull()
  })
})
