import { describe, expect, it } from 'vitest'
import type { ComposePayload } from '../api/types'
import {
  DRAFTS_KEY, MAX_AGE_MS, TAB_STALE_MS, beat, flushAllDrafts, hasLocalDraft, hasUnsavedDrafts, readLocalDrafts,
  registerOpenForm, removeLocalDraft, restorableDrafts, saveLocalDraft, type DraftStorage, type LocalDraft,
} from './draftStore'

function memory(): DraftStorage & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  }
}

const payload = (text: string): ComposePayload => ({ identity_id: 1, to: [{ email: 'a@example.com', name: '' }], cc: [], bcc: [], subject: 'Hi', html: `<p>${text}</p>`, attachments: [] })

function draft(localId: string, over: Partial<LocalDraft> = {}): LocalDraft {
  return { localId, owner: 7, tab: 'tab-a', draftId: null, conversationId: null, payload: payload(localId), updatedAt: 1000, ...over }
}

describe('local draft records', () => {
  it('saves, replaces and removes', () => {
    const s = memory()
    expect(saveLocalDraft(draft('x'), s)).toBe(true)
    expect(saveLocalDraft(draft('x', { payload: payload('newer'), updatedAt: 2000 }), s)).toBe(true)
    expect(readLocalDrafts(s).x.payload.html).toBe('<p>newer</p>')
    expect(hasLocalDraft('x', s)).toBe(true)
    expect(hasLocalDraft(undefined, s)).toBe(false)
    removeLocalDraft('x', s)
    expect(readLocalDrafts(s)).toEqual({})
    expect(s.data.has(DRAFTS_KEY)).toBe(false)
  })

  it('survives unusable storage', () => {
    const broken: DraftStorage = {
      getItem: () => { throw new Error('SecurityError') },
      setItem: () => { throw new Error('QuotaExceededError') },
      removeItem: () => { throw new Error('SecurityError') },
    }
    expect(saveLocalDraft(draft('x'), broken)).toBe(false)
    expect(readLocalDrafts(broken)).toEqual({})
    expect(() => removeLocalDraft('x', broken)).not.toThrow()
    expect(restorableDrafts(7, 'tab-a', 1000, broken)).toEqual([])
    expect(saveLocalDraft(draft('x'), null)).toBe(false)
    const s = memory()
    s.setItem(DRAFTS_KEY, '{not json')
    expect(readLocalDrafts(s)).toEqual({})
    s.setItem(DRAFTS_KEY, '[1,2]')
    expect(readLocalDrafts(s)).toEqual({})
  })
})

describe('restorableDrafts', () => {
  it("restores this tab's own drafts after a reload, oldest first", () => {
    const s = memory()
    saveLocalDraft(draft('b', { updatedAt: 3000 }), s)
    saveLocalDraft(draft('a', { updatedAt: 2000 }), s)
    expect(restorableDrafts(7, 'tab-a', 5000, s).map((d) => d.localId)).toEqual(['a', 'b'])
  })

  it("never shows one user's text to another", () => {
    const s = memory()
    saveLocalDraft(draft('mine'), s)
    saveLocalDraft(draft('theirs', { owner: 8 }), s)
    expect(restorableDrafts(7, 'tab-a', 5000, s).map((d) => d.localId)).toEqual(['mine'])
    expect(restorableDrafts(8, 'tab-a', 5000, s).map((d) => d.localId)).toEqual(['theirs'])
  })

  it("leaves a live tab's drafts alone and takes a dead tab's", () => {
    const s = memory()
    saveLocalDraft(draft('other', { tab: 'tab-b' }), s)
    beat('tab-b', 10_000, s)
    expect(restorableDrafts(7, 'tab-a', 10_000 + TAB_STALE_MS - 1, s)).toEqual([])
    expect(restorableDrafts(7, 'tab-a', 10_000 + TAB_STALE_MS + 1, s).map((d) => d.localId)).toEqual(['other'])
    // A tab that never beat (crashed before its first beat) is dead.
    saveLocalDraft(draft('ghost', { tab: 'tab-c' }), s)
    expect(restorableDrafts(7, 'tab-a', 10_000, s).map((d) => d.localId)).toEqual(['ghost'])
  })

  it('drops expired and malformed records', () => {
    const s = memory()
    saveLocalDraft(draft('old', { updatedAt: 1000 }), s)
    saveLocalDraft(draft('new', { updatedAt: MAX_AGE_MS }), s)
    const all = readLocalDrafts(s)
    ;(all as Record<string, unknown>).junk = { localId: 'junk' }
    s.setItem(DRAFTS_KEY, JSON.stringify(all))
    expect(restorableDrafts(7, 'tab-a', MAX_AGE_MS + 2000, s).map((d) => d.localId)).toEqual(['new'])
    expect(Object.keys(readLocalDrafts(s))).toEqual(['new'])
  })
})

describe('open forms', () => {
  it('reports unsaved text and flushes every form', () => {
    const flushed: [string, boolean][] = []
    let unsaved = false
    const off1 = registerOpenForm('f1', { unsaved: () => unsaved, flush: (all) => void flushed.push(['f1', all]) })
    const off2 = registerOpenForm('f2', { unsaved: () => false, flush: () => { throw new Error('broken form') } })
    expect(hasUnsavedDrafts()).toBe(false)
    unsaved = true
    expect(hasUnsavedDrafts()).toBe(true)
    flushAllDrafts(true)
    expect(flushed).toEqual([['f1', true]])
    off1()
    off2()
    expect(hasUnsavedDrafts()).toBe(false)
  })
})
