// ACT-SURFACES: web.note web.notes web.table web.panel
// Notes on the web: the reminder presets and labels of note.remind (Later today,
// Tomorrow, Next week), the datetime-local round trip, the template
// previews of note.from_template, note.save_template, note.delete_template and table.save_template,
// the one undo of a batch on several selected cards (offerUndoAll), and note.from_answer (the
// assistant panel's Save to note: the answer, its sources, and a link per cited record).
import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { answerNote, answerTitle } from './fromAnswer'
import type { Citation } from '../rail/assistant/citations'
import { fromLocalInput, reminderChip, remindLabel, remindPresets, toLocalInput } from './remind'
import { templatePreview } from './templates'

const P = { zone: 'America/Chicago', hour12: true, morningHour: 9 }

describe('note.remind presets', () => {
  it('offers Later today at 18:00 before 17:00, Tomorrow and Next week at the morning hour', () => {
    // Tue 6 Oct 2026, 10:00 in Chicago (15:00 UTC)
    const now = new Date('2026-10-06T15:00:00Z')
    const p = remindPresets(now, P)
    expect(p.map((x) => x.id)).toEqual(['later', 'tomorrow', 'week'])
    expect(p[0].at.toISOString()).toBe('2026-10-06T23:00:00.000Z')
    expect(p[1].at.toISOString()).toBe('2026-10-07T14:00:00.000Z')
    // Next week: Monday 12 Oct, 9:00 in Chicago
    expect(p[2].at.toISOString()).toBe('2026-10-12T14:00:00.000Z')
  })

  it('moves Later today to 20:00 between 17:00 and 19:00 and leaves it out after', () => {
    expect(remindPresets(new Date('2026-10-06T22:30:00Z'), P)[0]).toMatchObject({ id: 'later' })
    expect(remindPresets(new Date('2026-10-06T22:30:00Z'), P)[0].at.toISOString()).toBe('2026-10-07T01:00:00.000Z')
    expect(remindPresets(new Date('2026-10-07T01:30:00Z'), P).map((x) => x.id)).toEqual(['tomorrow', 'week'])
  })

  it('on a Monday, Next week is the Monday after', () => {
    const p = remindPresets(new Date('2026-10-12T15:00:00Z'), P)
    expect(p.find((x) => x.id === 'week')!.at.toISOString()).toBe('2026-10-19T14:00:00.000Z')
  })

  it('labels a time as Today, Tomorrow or the day, with the clock', () => {
    const now = new Date('2026-10-06T15:00:00Z')
    expect(remindLabel('2026-10-06T23:00:00Z', now, P)).toBe('Today, 6:00 PM')
    expect(remindLabel('2026-10-07T14:00:00Z', now, P)).toBe('Tomorrow, 9:00 AM')
    expect(remindLabel('2026-10-12T14:00:00Z', now, P)).toBe('Mon 12 Oct, 9:00 AM')
    expect(reminderChip({ key: 'n|n1|abs|x', at: '2026-10-06T23:00:00+00:00', state: 'snoozed', snoozed_until: '2026-10-07T14:00:00+00:00' }, now, P))
      .toBe('Snoozed until Tomorrow, 9:00 AM')
  })

  it('reads and writes the picker value in the user zone', () => {
    const d = fromLocalInput('2026-10-07T09:30', P.zone)!
    expect(d.toISOString()).toBe('2026-10-07T14:30:00.000Z')
    expect(toLocalInput(d, P.zone)).toBe('2026-10-07T09:30')
    expect(fromLocalInput('nope', P.zone)).toBeNull()
  })
})

describe('templates', () => {
  it('previews a list by its sections, a table by its columns and a note by its lines', () => {
    expect(templatePreview({ kind: 'checklist', content: { sections: [{ name: 'Produce' }, { name: 'Dairy' }], items: [{ text: 'milk' }] } })).toEqual(['Produce', 'Dairy'])
    expect(templatePreview({ kind: 'checklist', content: { sections: [], items: [{ text: 'tent' }, { text: 'pegs' }] } })).toEqual(['tent', 'pegs'])
    expect(templatePreview({ kind: 'table', content: { columns: [{ name: 'Date' }, { name: 'Amount' }] } })).toEqual(['Date', 'Amount'])
    expect(templatePreview({ kind: 'text', content: { body: 'Agenda:\n- \n\nNotes:' } }, 2)).toEqual(['Agenda:', 'Notes:'])
  })
})

describe('one undo for a batch', () => {
  afterEach(() => vi.restoreAllMocks())

  it('runs every write of the batch and offers one Undo that sends their undos newest first', async () => {
    const calls: string[] = []
    vi.doMock('../../api/client', async (orig) => ({
      ...(await orig<typeof import('../../api/client')>()),
      request: async (path: string, o?: { method?: string }) => {
        calls.push(`${o?.method ?? 'GET'} ${path}`)
        return { undo: { method: 'POST', path: `${path}/restore`, body: null, label: 'x' } }
      },
    }))
    vi.resetModules()
    const api = await import('./api')
    const ui = await import('../../stores/ui')
    const qc = new QueryClient()
    await api.batch(qc, ['n1', 'n2'], (id) => api.write(qc, 'DELETE', `/notes/${id}`, undefined, { quiet: true }), '2 items moved to Trash')
    expect(calls).toEqual(['DELETE /notes/n1', 'DELETE /notes/n2'])
    const undo = ui.useUI.getState().lastUndo
    expect(typeof undo).toBe('function')
    expect(api.undoLast(qc)).toBe(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(calls.slice(2)).toEqual(['POST /notes/n2/restore', 'POST /notes/n1/restore'])
    vi.doUnmock('../../api/client')
  })
})

describe('note.from_answer', () => {
  const cite = (p: Partial<Citation>): Citation => ({
    n: 1, kind: 'message', conversation_id: null, message_id: null, subject: null, from: null, date: null, quote: null, verified: true,
    start: null, end: null, url: null, ...p,
  })

  it('titles the note with the first line in plain words', () => {
    expect(answerTitle('## **Budget** for [Q4](https://x.example)\nmore')).toBe('Budget for Q4')
    expect(answerTitle('- Dana asked for the deck [1](mailr-cite:1)')).toBe('Dana asked for the deck')
    expect(answerTitle('Dana is your accountant [1](mailr-cite:1).')).toBe('Dana is your accountant.')
    expect(answerTitle('x'.repeat(100))).toHaveLength(80)
  })

  it('keeps the sources it uses and links each cited email, event, task and recording once', () => {
    const md = 'Dana sent the deck [1](mailr-cite:1) and the review is Tuesday [2](mailr-cite:2). Also [3](mailr-cite:3) [4](mailr-cite:4) [5](mailr-cite:5) [1](mailr-cite:1).'
    const n = answerNote(md, [
      cite({ n: 1, kind: 'message', conversation_id: 42, subject: 'Deck', from: 'Dana' }),
      cite({ n: 2, kind: 'event', uid: 'ev1', occurrence: '2026-10-06T15:00:00+00:00', subject: 'Review' }),
      cite({ n: 3, kind: 'task', uid: 'tk1', subject: 'Send notes' }),
      cite({ n: 4, kind: 'recording', connector_id: 3, recording_id: 9, subject: 'Weekly sync' }),
      cite({ n: 5, kind: 'web', url: 'https://example.org/a', subject: 'An article' }),
      cite({ n: 6, kind: 'task', uid: 'unused' }),
    ])
    expect(n.body).toContain('Sources:\n[1] Deck — Dana')
    expect(n.body).toContain('[5] An article')
    expect(n.links).toEqual([
      { target_kind: 'conversation', target_ref: { conversation_id: 42 } },
      { target_kind: 'event', target_ref: { uid: 'ev1', occurrence: '2026-10-06T15:00:00+00:00' } },
      { target_kind: 'task', target_ref: { uid: 'tk1' } },
      { target_kind: 'recording', target_ref: { connector_id: 3, recording_id: 9 } },
    ])
  })
})
