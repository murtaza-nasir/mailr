// ACT-SURFACES: web.settings
// Settings → Calendar & tasks → Calendars and task lists: each calendar under the account or source
// it comes from (calendarGroups.ts), and a sync failure in plain words with the technical text
// under Details (calendar/syncProblem.tsx).
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Collection } from '../calendar/types'
import { problemKind, SYNC_PROBLEM_KINDS, SyncProblem, syncProblemText } from '../calendar/syncProblem'
import { groupCollections, groupLabel, sourceOf } from './calendarGroups'

const col = (over: Partial<Collection> & { id: string; connector_id: number }): Collection => ({ name: over.id, events: true, ...over })

describe('groupCollections', () => {
  const cols: Collection[] = [
    col({ id: 'phone:1', name: 'Work phone', connector_id: 9, phone: { key: '1', device: 'Pixel 9', synced_at: null, stale: false }, source: { kind: 'phone', device: 'Pixel 9' } }),
    col({ id: 'a/default', name: 'Default Calendar', connector_id: 3, source: { kind: 'account', name: 'Studio', host: 'cal.example.org' } }),
    col({ id: 'b/cal', name: 'Calendar', connector_id: 1, source: { kind: 'builtin' } }),
    col({ id: 'b/tasks', name: 'Tasks', connector_id: 1, events: false, tasks: true, hidden: true, source: { kind: 'builtin' } }),
    col({ id: 'g/family', name: 'Family', connector_id: 1, shared: { kind: 'group', group: 'Family', access: 'write' }, source: { kind: 'shared' } }),
    col({ id: 'a/family', name: 'family', connector_id: 3, source: { kind: 'account', name: 'Studio', host: 'cal.example.org' } }),
    col({ id: 'c/cal', name: 'Calendar', connector_id: 4, source: { kind: 'account', name: 'Office', host: 'dav.example.net' } }),
    col({ id: 'webcal:7', name: 'Holidays', connector_id: 7, subscription: { url: 'https://example.org/h.ics', refresh_hours: 12 }, source: { kind: 'link' } }),
  ]

  it('puts each calendar under its source, Mailr calendar first, in the server order inside a group', () => {
    const groups = groupCollections(cols)
    expect(groups.map((g) => [g.kind, g.title, g.sub ?? null, g.items.map((c) => c.name)])).toEqual([
      ['builtin', 'Mailr calendar', null, ['Calendar', 'Tasks']],
      ['account', 'Studio', 'cal.example.org', ['Default Calendar', 'family']],
      ['account', 'Office', 'dav.example.net', ['Calendar']],
      ['shared', 'Shared with you', null, ['Family']],
      ['phone', 'Calendars from your phone', 'Pixel 9', ['Work phone']],
      ['link', 'Calendar links', null, ['Holidays']],
    ])
  })

  it('keeps hidden calendars listed', () => {
    expect(groupCollections(cols)[0].items.find((c) => c.name === 'Tasks')?.hidden).toBe(true)
  })

  it('names a picker group by account and host, so two "Calendar" options stay apart', () => {
    const labels = groupCollections(cols).filter((g) => g.items.some((c) => c.name === 'Calendar')).map(groupLabel)
    expect(labels).toEqual(['Mailr calendar', 'Office · dav.example.net'])
  })

  it('works out the source from the older fields when the server sends none', () => {
    const accounts = [{ id: 1, name: 'Calendar', builtin: true }, { id: 3, name: 'Studio', url: 'https://cal.example.org/dav/' }]
    const old = cols.map(({ source: _s, ...c }) => c as Collection)
    expect(old.map((c) => sourceOf(c, accounts).kind)).toEqual(['phone', 'account', 'builtin', 'builtin', 'shared', 'account', 'account', 'link'])
    expect(sourceOf(old[1], accounts)).toEqual({ kind: 'account', name: 'Studio', host: 'cal.example.org' })
    expect(groupCollections(old, accounts).map((g) => g.key)).toEqual(['builtin', 'account:3', 'account:4', 'shared', 'phone:Pixel 9', 'link'])
  })
})

describe('syncProblemText', () => {
  it('explains the address that no longer has calendars, with what to do', () => {
    expect(syncProblemText('not_found', 'cal.example.org')).toBe(
      'The calendar server at cal.example.org no longer has calendars at this address. It may have moved or been switched off. '
      + 'Check the address, or disconnect this account if you no longer use it.')
  })

  it('has a text naming the host for every kind, and never shows the technical text', () => {
    const texts = SYNC_PROBLEM_KINDS.map((k) => syncProblemText(k, 'cal.example.org'))
    expect(new Set(texts).size).toBe(SYNC_PROBLEM_KINDS.length)
    for (const s of texts) {
      expect(s).toContain('cal.example.org')
      expect(s).not.toMatch(/PROPFIND|HTTP \d|Error\b/)
    }
  })

  it('reads an unknown or missing kind as unknown, and names the account without a host', () => {
    expect(problemKind('something-new')).toBe('unknown')
    expect(problemKind(undefined)).toBe('unknown')
    expect(syncProblemText(null, null, 'Studio')).toBe('Syncing with Studio failed. The technical message is under Details.')
  })

  it('folds the technical text under Details', () => {
    const html = renderToStaticMarkup(createElement(SyncProblem, { kind: 'not_found', host: 'cal.example.org', detail: 'PROPFIND https://cal.example.org/dav/: not found' }))
    expect(html).toMatch(/data-kind="not_found"/)
    expect(html).toMatch(/<details[^>]*><summary[^>]*>Details<\/summary><code[^>]*>PROPFIND https:\/\/cal.example.org\/dav\/: not found<\/code><\/details>/)
    expect(html.indexOf('no longer has calendars')).toBeLessThan(html.indexOf('PROPFIND'))
  })
})
