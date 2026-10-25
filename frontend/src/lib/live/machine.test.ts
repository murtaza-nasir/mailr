// The live editor state machine (lib/live/machine.ts) and the change signal (lib/live/store.ts).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiveDoc, changedUnits } from './machine'
import { EDITING_TIMEOUT_MS, editingText, byName, noteVersion, onEditingEvent, onItemEvent, resetLive, subscribeItem, useLive } from './store'

const P = (...xs: string[]) => xs.map((x) => `<p>${x}</p>`).join('')
type Draft = { subject: string; html: string; to: string[] }
const draft = (html: string, subject = 'Plan', to = ['a@x.org']): Draft => ({ subject, html, to })

describe('LiveDoc: a change from elsewhere', () => {
  it('replaces a clean editor and keeps what it showed for Undo', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 3, content: draft(P('Hi', 'Old')) })
    const d = doc.onRemote({ version: 4, content: draft(P('Hi', 'New')), by: 'assistant' }, draft(P('Hi', 'Old')), false)
    expect(d).toEqual({ type: 'replace', content: draft(P('Hi', 'New')), before: draft(P('Hi', 'Old')) })
    expect(doc.version).toBe(4)
  })

  it('ignores a version it has already', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 5, content: draft(P('A')) })
    expect(doc.onRemote({ version: 5, content: draft(P('B')) }, draft(P('A')), false).type).toBe('ignore')
    expect(doc.onRemote({ version: 4, content: draft(P('B')) }, draft(P('A')), true).type).toBe('ignore')
  })

  it('merges into a dirty editor when the changes do not overlap', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('Hi Dana,', 'Thanks.', 'Best,')) })
    const mine = draft(P('Hi Dana,', 'Thanks a lot.', 'Best,'))
    const d = doc.onRemote({ version: 2, content: draft(P('Hi Dana,', 'Thanks.', 'Best regards,'), 'Plan v2'), by: 'assistant' }, mine, true)
    expect(d.type).toBe('merge')
    if (d.type === 'merge') expect(d.content).toEqual(draft(P('Hi Dana,', 'Thanks a lot.', 'Best regards,'), 'Plan v2'))
    expect(doc.version).toBe(2)
    expect(doc.conflict).toBeNull()
  })

  it('stops at a conflict when both changed the same paragraph, then "Use mine" bases on theirs', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('See you Thursday.')) })
    const mine = draft(P('See you Thursday at 3.'))
    const d = doc.onRemote({ version: 2, content: draft(P('See you Friday.')), by: 'assistant' }, mine, true)
    expect(d.type).toBe('conflict')
    if (d.type === 'conflict') {
      expect(d.conflict.fields).toEqual(['html'])
      expect(d.conflict.texts.html).toEqual([{ base: '<p>See you Thursday.</p>', mine: '<p>See you Thursday at 3.</p>', theirs: '<p>See you Friday.</p>' }])
    }
    expect(doc.version).toBe(1)
    // A newer copy while the bar is up replaces "theirs".
    const again = doc.onRemote({ version: 3, content: draft(P('See you Monday.')), by: 'assistant' }, mine, true)
    expect(again.type === 'conflict' && again.conflict.theirs.version).toBe(3)
    expect(doc.useMine()).toEqual(mine)
    expect(doc.version).toBe(3)
    expect(doc.conflict).toBeNull()
  })

  it('"Use theirs" gives the stored copy and bases on it', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('A')) })
    doc.onRemote({ version: 2, content: draft(P('C')) }, draft(P('B')), true)
    expect(doc.useTheirs()).toEqual(draft(P('C')))
    expect(doc.version).toBe(2)
  })

  it('takes a remote change into an editor that has no base yet only by merging', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' })
    const d = doc.onRemote({ version: 7, content: draft(P('Theirs')) }, draft(P('Mine')), true)
    expect(d.type).toBe('conflict')
  })
})

describe('LiveDoc: its own saves', () => {
  it('a plain save moves the base', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('A')) })
    expect(doc.onSaved(draft(P('B')), 2, null, draft(P('B')))).toBeNull()
    expect(doc.base).toEqual({ version: 2, content: draft(P('B')) })
  })

  it('a merged save rebases what was typed while it was on its way', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('One', 'Two', 'Three')) })
    const sent = draft(P('One!', 'Two', 'Three'))
    const stored = draft(P('One!', 'Two', 'Three (assistant)'))
    const typedSince = draft(P('One!', 'Two?', 'Three'))
    const fix = doc.onSaved(sent, 3, stored, typedSince)
    expect(fix).toEqual({ content: draft(P('One!', 'Two?', 'Three (assistant)')), conflict: false })
    expect(doc.base).toEqual({ version: 3, content: stored })
  })

  it('a merged save with nothing typed since shows the stored copy', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('A', 'B')) })
    const sent = draft(P('A!', 'B'))
    const stored = draft(P('A!', 'B!'))
    expect(doc.onSaved(sent, 3, stored, sent)).toEqual({ content: stored, conflict: false })
  })

  it('a refused save (409) opens the conflict with the server copy', () => {
    const doc = new LiveDoc<Draft>({ html: 'html' }, { version: 1, content: draft(P('A')) })
    const c = doc.onRejected({ base: { version: 1, content: draft(P('A')) }, theirs: { version: 2, content: draft(P('T')), by: 'agent:mcp' }, fields: ['html'] }, draft(P('M')))
    expect(c.theirs).toEqual({ version: 2, content: draft(P('T')), by: 'agent:mcp' })
    expect(doc.useMine()).toEqual(draft(P('M')))
    expect(doc.version).toBe(2)
  })
})

describe('changed units', () => {
  it('names the units that are new', () => {
    expect(changedUnits(['a', 'b', 'c'], ['a', 'B', 'c', 'd'])).toEqual([1, 3])
    expect(changedUnits(['x', 'x'], ['x', 'x', 'x'])).toEqual([2])
  })
})

describe('the change signal', () => {
  afterEach(() => resetLive())

  it('passes item events to subscribers and keeps the latest version', () => {
    const got: number[] = []
    const off = subscribeItem('draft', 12, (e) => got.push(e.version ?? -1))
    onItemEvent({ kind: 'draft', id: 12, version: 2, by: 'assistant' })
    onItemEvent({ kind: 'draft', id: 12, version: 1, by: 'user' }) // older: dropped
    onItemEvent({ kind: 'draft', id: 13, version: 9, by: 'user' })
    off()
    onItemEvent({ kind: 'draft', id: 12, version: 3, by: 'user' })
    expect(got).toEqual([2])
    expect(useLive.getState().versions['draft:12'].version).toBe(3)
    noteVersion('draft', 12, 2)
    expect(useLive.getState().versions['draft:12'].version).toBe(3)
  })

  it('shows "editing" until the end, or until it times out', () => {
    vi.useFakeTimers()
    try {
      onEditingEvent({ kind: 'draft', id: 5, by: 'assistant', state: 'start' })
      expect(useLive.getState().editing['draft:5'].by).toBe('assistant')
      onEditingEvent({ kind: 'draft', id: 5, by: 'assistant', state: 'end' })
      expect(useLive.getState().editing['draft:5']).toBeUndefined()
      onEditingEvent({ kind: 'task', id: 'u1', by: 'agent:Claude', state: 'start' })
      vi.advanceTimersByTime(EDITING_TIMEOUT_MS + 1)
      expect(useLive.getState().editing['task:u1']).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('says who', () => {
    expect(editingText('draft', 'assistant')).toBe('Ambrose is editing this draft…')
    expect(editingText('task', 'agent:Claude Desktop')).toBe('Claude Desktop is editing this task…')
    expect(byName('device')).toBe('another device')
    expect(byName('agent:ci')).toBe('the agent ci')
  })
})
