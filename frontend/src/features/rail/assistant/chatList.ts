// Chats: the logic of the list of earlier Ambrose chats (ChatList.tsx), kept free of React so the
// tests can drive it. The list is GET /agent/sessions, newest first. A delete waits for its Undo
// toast: the row hides at once and DELETE /agent/sessions/{id} (permanent) is sent when the toast
// goes or the list closes. Undo sends nothing. Android does the same (ui/assistant/ChatList.kt).
// Search is GET /agent/sessions?q= (backend agent/chat_search.py): the results carry the text around
// the hit as parts, the hits marked, which the list renders as text in spans. Rename is PATCH
// /agent/sessions/{id} {title}; an empty title gives back the automatic one (the first question).

import { ApiError } from '../../../api/client'

/** The query key of the list; a search adds 'search' and its words. */
export const CHATS_KEY = ['agent', 'sessions'] as const

/** One row of GET /agent/sessions. */
export interface ChatSummary {
  id: string
  title: string
  updated_at: string
  sensitive?: boolean
  /** The start of the latest answer as one plain line (no citations, no Markdown), or null. */
  preview?: string | null
  /** The user gave the title (Rename); false: the title is the first question. */
  renamed?: boolean
  /** Search results only: where the newest hit is, and the text around it with the hits marked. */
  match?: 'title' | 'question' | 'answer'
  snippet?: string | null
  snippet_parts?: TextPart[] | null
  /** Search results only: the title's parts, when the title matched. */
  title_parts?: TextPart[] | null
}

/** A piece of a text: plain, or a search hit (rendered in a span, never as markup). */
export interface TextPart {
  text: string
  hit: boolean
}

/** PATCH /agent/sessions/{id}: the chat's title now, and what it was (for Undo). */
export interface RenameResult {
  id: string
  title: string
  renamed: boolean
  previous: { title: string; renamed: boolean }
}

/** The server's limit for a title the user gives. */
export const TITLE_MAX = 120

/** A title as typed: one line, spaces collapsed, trimmed (the server cleans it the same way). */
export function cleanTitle(s: string): string {
  return s.split(/\s+/).filter(Boolean).join(' ')
}

/** What Rename sends for the text typed, or null when there is nothing to send: unchanged, or longer
 *  than the server takes. Empty sends '' (back to the automatic title), unless the title is that already. */
export function renameBody(typed: string, current: Pick<ChatSummary, 'title' | 'renamed'>): { title: string } | null {
  const t = cleanTitle(typed)
  if (t.length > TITLE_MAX) return null
  if (!t) return current.renamed ? { title: '' } : null
  return t === current.title ? null : { title: t }
}

/** Undo of a rename: the title the chat had, or '' when that was the automatic one. */
export function undoBody(previous: RenameResult['previous']): { title: string } {
  return { title: previous.renamed ? previous.title : '' }
}

/** The list with one chat's title changed (the open list and any search results). */
export function withTitle<T extends ChatSummary>(list: T[] | undefined, id: string, title: string, renamed: boolean): T[] | undefined {
  if (!list) return list
  return list.map((c) => (c.id === id ? { ...c, title, renamed, title_parts: c.title === title ? c.title_parts : null } : c))
}

/** The search to run for what is in the box: '' for none (the whole list). */
export function searchTerm(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ')
}

/** The keys that put the focus in the search box from the list: Ctrl+F or Cmd+F, and `/`. */
export function isFindKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): boolean {
  if (e.altKey) return false
  if (e.key === '/' && !e.ctrlKey && !e.metaKey) return true
  return (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f'
}

export type ChatsProblem = 'offline' | 'failed'

/** Why the list could not load: the browser is offline or the request never reached the server
 *  (offline), or the server refused (failed). */
export function chatsProblem(e: unknown, online: boolean): ChatsProblem {
  if (!online) return 'offline'
  if (e instanceof ApiError) return e.status === 0 ? 'offline' : 'failed'
  // fetch() rejects with a bare TypeError when the server cannot be reached.
  if (e instanceof TypeError) return 'offline'
  return 'failed'
}

/** The rows to show: the server's list without the chats whose delete is waiting or done. */
export function visibleChats(all: ChatSummary[] | undefined, hidden: ReadonlySet<string>): ChatSummary[] {
  return (all ?? []).filter((c) => !hidden.has(c.id))
}

/** The row a key moves to (arrow keys, Home, End), or null for a key the list does not take. */
export function moveIndex(key: string, at: number, count: number): number | null {
  if (count <= 0) return null
  switch (key) {
    case 'ArrowDown':
      return Math.min(count - 1, at + 1)
    case 'ArrowUp':
      return Math.max(0, at - 1)
    case 'Home':
      return 0
    case 'End':
      return count - 1
    default:
      return null
  }
}

export interface DeleteQueueHooks {
  /** The server's delete (DELETE /agent/sessions/{id}). */
  remove: (id: string) => Promise<unknown>
  /** The row's visibility changed: hidden while its delete waits or after it went through. */
  setHidden: (id: string, hidden: boolean) => void
  /** The server deleted the chat. */
  deleted?: (id: string) => void
  /** The server refused: the row is back. */
  failed?: (id: string) => void
}

/**
 * One delete at a time waits for its Undo. Hiding a second chat sends the first one's delete.
 * `commit(id)` sends the waiting delete (only that chat's, when an id is given); `undo(id)` drops it.
 * Each delete is sent at most once.
 */
export function createDeleteQueue(h: DeleteQueueHooks) {
  let pending: string | null = null
  const commit = (only?: string): Promise<void> => {
    const id = pending
    if (!id || (only !== undefined && only !== id)) return Promise.resolve()
    pending = null
    return h.remove(id).then(
      () => h.deleted?.(id),
      () => {
        h.setHidden(id, false)
        h.failed?.(id)
      },
    )
  }
  return {
    get pending() {
      return pending
    },
    hide(id: string): Promise<void> {
      const before = commit()
      pending = id
      h.setHidden(id, true)
      return before
    },
    undo(id: string): boolean {
      if (pending !== id) return false
      pending = null
      h.setHidden(id, false)
      return true
    },
    commit,
  }
}
