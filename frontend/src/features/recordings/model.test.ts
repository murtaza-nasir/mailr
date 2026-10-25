// ACT-SURFACES: web.recordings
// The Recordings app's list logic (web.recordings: recording.search, recording.view): filters,
// counts, day groups and the highlighted summary line.
import { describe, expect, it } from 'vitest'
import { applyFilter, counts, dayTitle, filterTitle, groupByDay, highlight, identities, nameKey, statusKind, statusLabel, type RecItem } from './model'
import { parseRecordingsPath, recordingsPath } from '../../lib/routes'

const NOW = new Date(2026, 9, 1, 15, 0) // Thu 1 Oct 2026, 15:00 local

function rec(id: number, daysAgo: number | null, extra: Partial<RecItem> = {}): RecItem {
  const d = daysAgo === null ? null : new Date(2026, 9, 1 - daysAgo, 10, 30)
  return {
    connector_id: 3, recording_id: id, title: `R${id}`, meeting_date: d ? d.toISOString() : null, created_at: null,
    duration_s: 1800, participants: [], tags: [], folder: null, status: 'completed', has_transcript: true,
    has_summary: true, audio_available: true, is_archived: false, local_only: true, url: '', event_link: null, people: [],
    ...extra,
  }
}

const link = { id: 1, connector_id: 3, recording_id: 1, kind: 'event' as const, target: 'ev', occurrence: null, state: 'confirmed' as const, score: 1, source: 'user' }

const ITEMS = [
  rec(1, 0, { folder: 'Advising', tags: ['Work', 'Students'], participants: ['Dana Smith', 'Evan'], event_link: link, people: [{ name: 'Dana Smith', email: 'dana@x.example' }, { name: 'Evan', email: null }] }),
  rec(2, 1, { folder: 'Meetings', tags: ['Work'], participants: ['Evan'] }),
  rec(3, 6, { folder: 'Advising', participants: ['Mara'], event_link: { ...link, recording_id: 3, state: 'suggested' } }),
  rec(4, 7, { tags: ['Personal'], participants: ['evan '] }),
  rec(5, 40, { status: 'processing' }),
  rec(6, null, { status: 'failed' }),
]

describe('recording.search filters', () => {
  it('keeps what each left-column choice names', () => {
    const ids = (f: Parameters<typeof applyFilter>[1], who: string[] = []) => applyFilter(ITEMS, f, NOW, who).map((r) => r.recording_id)
    expect(ids({ kind: 'all' })).toEqual([1, 2, 3, 4, 5, 6])
    // Seven days: today and the six days before it.
    expect(ids({ kind: 'recent' })).toEqual([1, 2, 3])
    // A suggestion counts as linked until it is dismissed.
    expect(ids({ kind: 'linked' })).toEqual([1, 3])
    expect(ids({ kind: 'unlinked' })).toEqual([2, 4, 5, 6])
    expect(ids({ kind: 'folder', id: 'advising' })).toEqual([1, 3])
    expect(ids({ kind: 'tag', id: 'Work' })).toEqual([1, 2])
    // Participants (identity keys): every chosen person must be there, whatever the case and spaces.
    expect(ids({ kind: 'all' }, ['name:evan'])).toEqual([1, 2, 4])
    expect(ids({ kind: 'all' }, ['name:evan', 'name:dana smith'])).toEqual([1])
    expect(ids({ kind: 'recent' }, [nameKey('Evan ')])).toEqual([1, 2])
  })

  it('"Last, First" and "First Last" are one participant, shown as "First Last"', () => {
    const items = [rec(1, 0, { participants: ['Turner, Ross'] }), rec(2, 1, { participants: ['Ross Turner', 'Evan'] })]
    expect(counts(items, NOW).people[0]).toMatchObject({ key: 'name:ross turner', label: 'Ross Turner', email: null, count: 2 })
    expect(applyFilter(items, { kind: 'all' }, NOW, ['name:ross turner']).map((r) => r.recording_id)).toEqual([1, 2])
  })

  it('counts for the left column', () => {
    const c = counts(ITEMS, NOW)
    expect([c.all, c.recent, c.linked, c.unlinked]).toEqual([6, 3, 2, 4])
    expect(Object.fromEntries(c.folders)).toEqual({ Advising: 2, Meetings: 1 })
    expect(Object.fromEntries(c.tags)).toEqual({ Work: 2, Students: 1, Personal: 1 })
    expect(c.people[0]).toMatchObject({ key: 'name:evan', label: 'Evan', email: null, count: 3 })
    expect(c.people.find((p) => p.label === 'Dana Smith')).toMatchObject({ email: 'dana@x.example', count: 1 })
  })
})

// ACT participant.merge: the strings Speakr kept for one person count as that person.
describe('participant.merge grouping', () => {
  const me = (name: string, how: string) => ({ name, email: 'me@x.example', key: 'me', label: 'You', me: true, how })
  const ross = (name: string, how: string) => ({ name, email: null, key: 'name:ross turner', label: 'Ross Turner', me: false, how })
  const items = [
    rec(1, 0, { participants: ['Rowan hale', 'Ross Turner'], people: [me('Rowan hale', 'me'), ross('Ross Turner', 'raw')] }),
    rec(2, 1, { participants: ['Rowan', 'Ross'], people: [me('Rowan', 'me_first'), ross('Ross', 'alias')] }),
    // Both strings in one recording: one person, counted once.
    rec(3, 2, { participants: ['Rowan', 'Rowan hale', 'Dana'], people: [me('Rowan', 'me_first'), me('Rowan hale', 'me'), { name: 'Dana', email: null, key: 'name:dana', label: 'Dana', how: 'raw' }] }),
  ]

  it('groups by person, You first, with the names each person merges', () => {
    const c = counts(items, NOW)
    expect(c.people.map((p) => [p.label, p.count])).toEqual([['You', 3], ['Ross Turner', 2], ['Dana', 1]])
    expect(c.people[0].names).toEqual([{ name: 'Rowan hale', how: 'me' }, { name: 'Rowan', how: 'me_first' }])
    expect(c.people[1].names.map((n) => n.how)).toEqual(['raw', 'alias'])
  })

  it('a row shows each person once; the filter by a person finds every string of theirs', () => {
    expect(identities(items[2]).map((p) => p.label)).toEqual(['You', 'Dana'])
    expect(applyFilter(items, { kind: 'all' }, NOW, ['me']).map((r) => r.recording_id)).toEqual([1, 2, 3])
    expect(applyFilter(items, { kind: 'all' }, NOW, ['name:ross turner']).map((r) => r.recording_id)).toEqual([1, 2])
  })
})

describe('recording.view list', () => {
  it('groups by local day, newest first, undated last', () => {
    const groups = groupByDay([...ITEMS].reverse(), NOW)
    expect(groups.map((g) => g.title.split(',')[0])).toEqual(['Today', 'Yesterday', dayTitle(new Date(2026, 8, 25), NOW).split(',')[0], 'Thu', 'Sat', 'No date'])
    expect(groups.map((g) => g.items.map((r) => r.recording_id))).toEqual([[1], [2], [3], [4], [5], [6]])
    expect(dayTitle(new Date(2026, 8, 25, 9), NOW)).toBe(new Date(2026, 8, 25).toLocaleDateString(undefined, { weekday: 'long' }))
    expect(dayTitle(new Date(2025, 11, 30), NOW)).toContain('2025')
  })

  it('two recordings on one day share a group, the later one first', () => {
    const a = rec(10, 2, { meeting_date: new Date(2026, 8, 29, 9).toISOString() })
    const b = rec(11, 2, { meeting_date: new Date(2026, 8, 29, 16).toISOString() })
    const g = groupByDay([a, b], NOW)
    expect(g).toHaveLength(1)
    expect(g[0].items.map((r) => r.recording_id)).toEqual([11, 10])
  })

  it('status kinds and labels', () => {
    expect(statusKind('completed')).toBe('done')
    expect(statusKind('COMPLETED')).toBe('done')
    expect(statusKind('processing')).toBe('working')
    expect(statusKind('summarizing')).toBe('working')
    expect(statusKind('failed')).toBe('failed')
    expect(statusLabel('pending')).toBe('Waiting')
    expect(statusLabel('summarizing')).toBe('Summarising')
  })

  it('highlights the words of the search in the summary line, by word prefix', () => {
    expect(highlight('Ship the budget on Friday', 'budg fri')).toEqual([
      { text: 'Ship the ', hit: false }, { text: 'budg', hit: true }, { text: 'et on ', hit: false }, { text: 'Fri', hit: true }, { text: 'day', hit: false },
    ])
    expect(highlight('Rebudget', 'budget')).toEqual([{ text: 'Rebudget', hit: false }])
    expect(highlight('anything', '')).toEqual([{ text: 'anything', hit: false }])
  })
})

describe('recordings routes', () => {
  it('parses and builds the app paths', () => {
    expect(parseRecordingsPath('/recordings')).toEqual({ filter: { kind: 'all' }, open: null, q: '' })
    expect(parseRecordingsPath('/recordings/recent', '?q=budget')).toEqual({ filter: { kind: 'recent' }, open: null, q: 'budget' })
    expect(parseRecordingsPath('/recordings/f/Student%20advising')?.filter).toEqual({ kind: 'folder', id: 'Student advising' })
    expect(parseRecordingsPath('/recordings/3/12', '?t=754')?.open).toEqual({ cid: 3, rid: 12, t: 754 })
    expect(parseRecordingsPath('/recordings/3/12')?.open).toEqual({ cid: 3, rid: 12, t: null })
    expect(parseRecordingsPath('/calendar')).toBeNull()
    expect(recordingsPath({ kind: 'tag', id: 'Work & life' })).toBe('/recordings/t/Work%20%26%20life')
    expect(recordingsPath({ kind: 'linked' }, 'a b')).toBe('/recordings/linked?q=a%20b')
    expect(recordingsPath()).toBe('/recordings')
    expect(filterTitle({ kind: 'unlinked' })).toBe('Not linked')
  })
})
