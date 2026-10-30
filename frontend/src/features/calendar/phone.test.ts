// ACT-SURFACES: web.calendar
// Phone calendars (phone.ts; backend calendar/phonecal.py): the freshness line, the stale mark after
// a day, a busy block read as "Busy", and a block with details read with its title and place, both
// read-only (ACT calendar.phone_list).
import { describe, expect, it } from 'vitest'
import { normalizeEvent } from './data'
import { isStale, phoneFreshness } from './phone'
import type { Collection } from './types'

const NOW = new Date('2026-10-28T15:00:00Z')

function phoneCal(synced_at: string | null, device = 'Pixel 9'): Collection {
  return { id: 'phone:k', url: 'phone:k', connector_id: 1, name: 'Work', read_only: true,
    phone: { key: 'k', device, synced_at, stale: false } }
}

describe('phone calendars', () => {
  it('says which device sent the busy times and when', () => {
    expect(phoneFreshness(phoneCal('2026-10-28T14:57:00Z'), NOW)).toEqual({ text: 'Updated from Pixel 9 3 minutes ago', stale: false })
    expect(phoneFreshness(phoneCal('2026-10-28T14:59:50Z', ''), NOW)?.text).toBe('Updated just now')
    expect(phoneFreshness(phoneCal(null), NOW)).toEqual({ text: 'Not updated yet', stale: true })
    expect(phoneFreshness({ phone: null }, NOW)).toBeNull()
  })

  it('marks a calendar stale after a day without an update', () => {
    expect(isStale({ synced_at: '2026-10-27T15:30:00Z', stale: false }, NOW)).toBe(false)
    expect(isStale({ synced_at: '2026-10-27T14:30:00Z', stale: false }, NOW)).toBe(true)
    expect(phoneFreshness(phoneCal('2026-10-26T15:00:00Z'), NOW)?.stale).toBe(true)
  })

  it('reads a busy block as "Busy", read-only', () => {
    const cal = phoneCal('2026-10-28T14:57:00Z')
    const e = normalizeEvent({ uid: 'phone-k-1@mailr', summary: 'Beschäftigt', start: '2026-10-30T17:00:00Z', end: '2026-10-30T18:00:00Z',
      calendar: 'phone:k', phone_busy: true }, { zone: 'UTC', collections: new Map([['phone:k', cal]]) })
    expect(e?.summary).toBe('Busy')
    expect(e?.readOnly).toBe(true)
  })

  it('keeps the title and place of a block from a calendar that sends details, read-only', () => {
    const cal = phoneCal('2026-10-28T14:57:00Z')
    const e = normalizeEvent({ uid: 'phone-k-1-d@mailr', summary: 'Planning review', location: 'Hall B',
      start: '2026-10-30T17:00:00Z', end: '2026-10-30T18:00:00Z', calendar: 'phone:k', phone_busy: true, phone_details: true },
    { zone: 'UTC', collections: new Map([['phone:k', cal]]) })
    expect(e?.summary).toBe('Planning review')
    expect(e?.location).toBe('Hall B')
    expect(e?.readOnly).toBe(true)
  })
})
