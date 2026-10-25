// ACT-SURFACES: web.panel
// Chats (chatList.ts): which rows show, what a failed load is called, the keys, and the delete that
// waits for its Undo toast. ACT agent.chat_list ACT agent.chat_delete
// Search and rename: the words searched, the keys that reach the box, what Rename and its Undo send,
// and the title changed in the list. ACT agent.chat_search ACT agent.chat_rename
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../../api/client'
import {
  TITLE_MAX, chatsProblem, cleanTitle, createDeleteQueue, isFindKey, moveIndex, renameBody, searchTerm, undoBody,
  visibleChats, withTitle, type ChatSummary,
} from './chatList'

const chat = (id: string): ChatSummary => ({ id, title: `Chat ${id}`, updated_at: '2026-10-07T10:00:00Z', preview: null })

function queue(remove: (id: string) => Promise<unknown> = () => Promise.resolve({ ok: true })) {
  const hidden = new Set<string>()
  const deleted: string[] = []
  const failed: string[] = []
  const sent = vi.fn(remove)
  const q = createDeleteQueue({
    remove: sent,
    setHidden: (id, on) => (on ? hidden.add(id) : hidden.delete(id)),
    deleted: (id) => deleted.push(id),
    failed: (id) => failed.push(id),
  })
  return { q, hidden, deleted, failed, sent }
}

describe('chats list', () => {
  it('shows the server list without hidden chats, in its order', () => {
    expect(visibleChats([chat('a'), chat('b'), chat('c')], new Set(['b'])).map((c) => c.id)).toEqual(['a', 'c'])
    expect(visibleChats(undefined, new Set())).toEqual([])
  })

  it('calls a failed load offline or failed', () => {
    expect(chatsProblem(new ApiError(500, 'boom'), true)).toBe('failed')
    expect(chatsProblem(new ApiError(0, 'network', undefined, 'network'), true)).toBe('offline')
    expect(chatsProblem(new TypeError('Failed to fetch'), true)).toBe('offline')
    expect(chatsProblem(new ApiError(500, 'boom'), false)).toBe('offline')
    expect(chatsProblem(null, false)).toBe('offline')
  })

  it('moves with the arrow keys, Home and End, and stops at the ends', () => {
    expect(moveIndex('ArrowDown', 0, 3)).toBe(1)
    expect(moveIndex('ArrowDown', 2, 3)).toBe(2)
    expect(moveIndex('ArrowUp', 0, 3)).toBe(0)
    expect(moveIndex('ArrowUp', 2, 3)).toBe(1)
    expect(moveIndex('Home', 2, 3)).toBe(0)
    expect(moveIndex('End', 0, 3)).toBe(2)
    expect(moveIndex('Enter', 0, 3)).toBeNull()
    expect(moveIndex('ArrowDown', 0, 0)).toBeNull()
  })
})

describe('delete with Undo', () => {
  it('hides the row at once and sends nothing until the toast goes', async () => {
    const { q, hidden, sent, deleted } = queue()
    void q.hide('a')
    expect(hidden.has('a')).toBe(true)
    expect(sent).not.toHaveBeenCalled()
    await q.commit('a')
    expect(sent).toHaveBeenCalledWith('a')
    expect(deleted).toEqual(['a'])
    expect(hidden.has('a')).toBe(true)
  })

  it('Undo brings the row back and sends nothing, even when the toast goes afterwards', async () => {
    const { q, hidden, sent } = queue()
    void q.hide('a')
    expect(q.undo('a')).toBe(true)
    expect(hidden.has('a')).toBe(false)
    await q.commit('a') // the toast closes after its Undo
    await q.commit() // the list closes
    expect(sent).not.toHaveBeenCalled()
  })

  it('sends each delete once, and a second delete sends the first', async () => {
    const { q, sent } = queue()
    void q.hide('a')
    await q.hide('b')
    expect(sent.mock.calls).toEqual([['a']])
    await q.commit('a') // a's toast closes later: already sent
    expect(sent).toHaveBeenCalledTimes(1)
    expect(q.undo('a')).toBe(false)
    await q.commit() // the list closes
    expect(sent.mock.calls).toEqual([['a'], ['b']])
  })

  it('a refused delete brings the row back and says so', async () => {
    const { q, hidden, failed, deleted } = queue(() => Promise.reject(new ApiError(500, 'no')))
    void q.hide('a')
    await q.commit()
    expect(hidden.has('a')).toBe(false)
    expect(failed).toEqual(['a'])
    expect(deleted).toEqual([])
  })
})

describe('chats search', () => {
  it('searches the words typed, spaces collapsed; only spaces is no search', () => {
    expect(searchTerm('  lumen   kickoff ')).toBe('lumen kickoff')
    expect(searchTerm('   ')).toBe('')
  })

  it('takes Ctrl+F, Cmd+F and / to the box, nothing else', () => {
    const k = (key: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean }> = {}) =>
      isFindKey({ key, ctrlKey: false, metaKey: false, altKey: false, ...mods })
    expect(k('/')).toBe(true)
    expect(k('f', { ctrlKey: true })).toBe(true)
    expect(k('F', { metaKey: true })).toBe(true)
    expect(k('f')).toBe(false)
    expect(k('/', { ctrlKey: true })).toBe(false)
    expect(k('f', { ctrlKey: true, altKey: true })).toBe(false)
  })
})

describe('chats rename', () => {
  const auto = { title: 'What did Dana say?', renamed: false }
  const named = { title: 'Lumen kickoff', renamed: true }

  it('sends the title as one trimmed line', () => {
    expect(cleanTitle('  Lumen\n  kickoff  ')).toBe('Lumen kickoff')
    expect(renameBody('  Lumen\n kickoff ', auto)).toEqual({ title: 'Lumen kickoff' })
  })

  it('sends nothing when the title does not change or is too long', () => {
    expect(renameBody('What did Dana say?', auto)).toBeNull()
    expect(renameBody(' Lumen kickoff ', named)).toBeNull()
    expect(renameBody('x'.repeat(TITLE_MAX + 1), auto)).toBeNull()
    expect(renameBody('x'.repeat(TITLE_MAX), auto)).toEqual({ title: 'x'.repeat(TITLE_MAX) })
  })

  it('an empty title goes back to the first question, once', () => {
    expect(renameBody('   ', named)).toEqual({ title: '' })
    expect(renameBody('', auto)).toBeNull()
  })

  it('Undo sends the title it had, or empty for the automatic one', () => {
    expect(undoBody({ title: 'Lumen kickoff', renamed: true })).toEqual({ title: 'Lumen kickoff' })
    expect(undoBody({ title: 'What did Dana say?', renamed: false })).toEqual({ title: '' })
  })

  it('changes the title of one chat in the list and drops its stale hits', () => {
    const list: ChatSummary[] = [
      { ...chat('a'), title_parts: [{ text: 'Chat a', hit: true }] },
      chat('b'),
    ]
    const out = withTitle(list, 'a', 'Renamed', true)!
    expect(out.map((c) => c.title)).toEqual(['Renamed', 'Chat b'])
    expect(out[0].renamed).toBe(true)
    expect(out[0].title_parts).toBeNull()
    expect(out[1]).toBe(list[1])
    expect(withTitle(undefined, 'a', 'x', true)).toBeUndefined()
  })
})
