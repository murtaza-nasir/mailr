// ACT-SURFACES: web.panel
// Chats: the list of earlier Ambrose chats in the Ambrose panel (from the History button in its
// header). It takes the whole panel, with Back to the chat; on a phone that is the whole screen. New
// chat stays in the panel's header beside History.
// Each row: the title (the first question), the last activity, the start of the latest answer, and
// "Open now" on the chat that is open. Opening a row replays that chat in the panel (useAssistant
// open). Each row's menu has Rename (a text field in the row; Enter saves, Esc cancels; Undo in the
// toast) and Delete (with Undo); the logic is in chatList.ts. The search box at the top searches the
// titles, questions and answers on the server; a result shows the text around the hit, the hits in
// spans. Ctrl+F, Cmd+F or / in the list goes to the box; Esc there clears it. The wording is
// Android's (strings_chats.xml).

import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CloudOff, MoreHorizontal, Pencil, Search, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { api } from '../../../api/client'
import { EmptyState, Skeleton } from '../../../components/bits'
import { MenuItem, Popover, useAnchor } from '../../../components/Popover'
import { useOnline } from '../../../hooks/useOnline'
import { formatListDate } from '../../../lib/format'
import { toast, useUI } from '../../../stores/ui'
import { t as tr } from '../../../i18n'
import { assistantNameNow } from './useAssistantName'
import {
  CHATS_KEY, TITLE_MAX, chatsProblem, createDeleteQueue, isFindKey, moveIndex, searchTerm, visibleChats,
  type ChatSummary, type TextPart,
} from './chatList'
import { useRenameChat } from './useRenameChat'

export { CHATS_KEY }

const SKELETON_DELAY = 300
const SEARCH_DELAY = 200

function useDebounced(value: string, ms: number): string {
  const [v, setV] = useState(value)
  useEffect(() => {
    // Clearing the box shows the whole list at once.
    if (!value) {
      setV('')
      return
    }
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Text with the search hits in spans (plain text throughout; React escapes it). */
export function Highlighted({ parts }: { parts: TextPart[] }) {
  return (
    <>
      {parts.map((p, i) => (p.hit ? <span key={i} className="chats-hit">{p.text}</span> : <span key={i}>{p.text}</span>))}
    </>
  )
}

function useDelayed(on: boolean): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!on) {
      setShown(false)
      return
    }
    const t = setTimeout(() => setShown(true), SKELETON_DELAY)
    return () => clearTimeout(t)
  }, [on])
  return shown
}

interface Props {
  /** The chat open in the panel (marked "Open now"). */
  current: string | null
  /** Open a chat; false when it could not be read. */
  onOpen: (id: string) => Promise<boolean>
  /** Back to the chat. */
  onClose: () => void
  /** The server deleted a chat (the open one then makes way for a new chat). */
  onDeleted: (id: string) => void
  /** A chat's title changed here (the header shows the open chat's). */
  onRenamed?: (id: string, title: string, renamed: boolean) => void
  /** Words to search for when the list opens (`/chats words`). */
  initialQuery?: string
}

export function ChatList({ current, onOpen, onClose, onDeleted, onRenamed, initialQuery = '' }: Props) {
  const qc = useQueryClient()
  const online = useOnline()
  const [typed, setTyped] = useState(initialQuery)
  const term = useDebounced(searchTerm(typed), SEARCH_DELAY)
  // ACT agent.chat_list @web.panel: read again each time Chats opens.
  // ACT agent.chat_search @web.panel: the same list, searched on the server; the rows shown stay until the results come.
  const q = useQuery({
    queryKey: term ? [...CHATS_KEY, 'search', term] : CHATS_KEY,
    queryFn: ({ signal }) => api.get<ChatSummary[]>('/agent/sessions', term ? { limit: 50, q: term } : { limit: 50 }, signal),
    staleTime: 0,
    refetchOnMount: 'always',
    placeholderData: keepPreviousData,
  })
  const searching = Boolean(term)
  const rename = useRenameChat(onRenamed)
  const [editing, setEditing] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const onDeletedRef = useRef(onDeleted)
  useEffect(() => {
    onDeletedRef.current = onDeleted
  })
  const toastRef = useRef<number | null>(null)

  // ACT agent.chat_delete @web.panel: the row hides now; the DELETE goes when the toast closes or the list does.
  const queue = useMemo(
    () =>
      createDeleteQueue({
        remove: (id) => api.del(`/agent/sessions/${encodeURIComponent(id)}`),
        setHidden: (id, on) =>
          setHidden((s) => {
            const n = new Set(s)
            if (on) n.add(id)
            else n.delete(id)
            return n
          }),
        deleted: (id) => {
          qc.setQueriesData<ChatSummary[]>({ queryKey: CHATS_KEY }, (list) => list?.filter((c) => c.id !== id))
          onDeletedRef.current(id)
        },
        failed: () => toast({ message: tr('rail.assistant.chatList.deleteFailed'), error: true }),
      }),
    [qc],
  )
  // The list goes (Back, a chat opened, the panel closed): the waiting delete is sent, and its Undo goes too.
  useEffect(
    () => () => {
      void queue.commit()
      if (toastRef.current != null) useUI.getState().dismissToast(toastRef.current)
    },
    [queue],
  )

  const remove = useCallback(
    (c: ChatSummary) => {
      void queue.hide(c.id)
      toastRef.current = toast({
        message: tr('rail.assistant.chatList.deleted'),
        actionLabel: tr('common.undo'),
        onAction: () => queue.undo(c.id),
        onGone: () => void queue.commit(c.id),
      })
    },
    [queue],
  )

  const [opening, setOpening] = useState<string | null>(null)
  const open = useCallback(
    async (c: ChatSummary) => {
      if (opening) return
      if (c.id === current) {
        onClose()
        return
      }
      setOpening(c.id)
      const ok = await onOpen(c.id)
      setOpening(null)
      if (!ok) toast({ message: tr('rail.assistant.chatList.openFailed'), error: true })
    },
    [current, onClose, onOpen, opening],
  )

  const chats = visibleChats(q.data, hidden)
  const listRef = useRef<HTMLUListElement>(null)
  const rows = () => Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[data-chat-row]') ?? [])

  // The focus comes into the list at once (Back), so Esc works in every state; once the rows are
  // there, it moves to the open chat (or the newest).
  const backRef = useRef<HTMLButtonElement>(null)
  useEffect(() => backRef.current?.focus(), [])
  const focused = useRef(false)
  useEffect(() => {
    if (focused.current || !chats.length || searching) return
    focused.current = true
    const all = rows()
    const at = Math.max(0, chats.findIndex((c) => c.id === current))
    all[at]?.focus({ preventScroll: false })
  }, [chats, current, searching])

  // Esc goes back to the chat, wherever the focus is in the panel (a Retry that went leaves it on the page).
  const closeRef = useRef(onClose)
  useEffect(() => {
    closeRef.current = onClose
  })
  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      const el = document.activeElement
      if (el && el !== document.body && !el.closest('.side-panel')) return
      // The search box with words in it, and a title being renamed, take their own Esc.
      if (el?.closest('[data-own-esc]')) return
      if (document.querySelector('.scrim, .popover, [data-modal-drawer]')) return
      e.preventDefault()
      e.stopPropagation()
      closeRef.current()
    }
    document.addEventListener('keydown', on, true)
    return () => document.removeEventListener('keydown', on, true)
  }, [])

  const toSearch = () => {
    searchRef.current?.focus()
    searchRef.current?.select()
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const inField = (e.target as HTMLElement).closest('input, textarea')
    if (!inField && isFindKey(e)) {
      e.preventDefault()
      e.stopPropagation()
      toSearch()
      return
    }
    if (inField) return
    const all = rows()
    const at = all.findIndex((b) => b === document.activeElement || b.parentElement?.contains(document.activeElement))
    if (at < 0) return
    if (e.key === 'F2' && chats[at]) {
      e.preventDefault()
      setEditing(chats[at].id)
      return
    }
    if (e.key === 'ArrowUp' && at === 0) {
      e.preventDefault()
      toSearch()
      return
    }
    if (e.key === 'Delete' && chats[at]) {
      e.preventDefault()
      remove(chats[at])
      // The next row (or the one before) takes the focus.
      requestAnimationFrame(() => {
        const left = rows()
        left[Math.min(at, left.length - 1)]?.focus()
      })
      return
    }
    const to = moveIndex(e.key, at, all.length)
    if (to == null) return
    e.preventDefault()
    all[to]?.focus()
  }

  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && typed) {
      e.preventDefault()
      e.stopPropagation()
      setTyped('')
      return
    }
    if (e.key === 'ArrowDown' || (e.key === 'Enter' && !e.nativeEvent.isComposing)) {
      const first = rows()[0]
      if (!first) return
      e.preventDefault()
      first.focus()
    }
  }

  const endRename = useCallback((c: ChatSummary, text: string | null) => {
    setEditing(null)
    if (text != null) rename(c, text)
    // The focus goes back to the row.
    requestAnimationFrame(() => listRef.current?.querySelector<HTMLButtonElement>(`[data-chat-row="${CSS.escape(c.id)}"]`)?.focus())
  }, [rename])

  const loading = q.data === undefined && !q.isError && online
  const skeleton = useDelayed(loading)
  // A failed or offline load with rows from before keeps them (as Android does); with none, it says why.
  const problem = chats.length === 0 && (q.isError || !online) ? chatsProblem(q.error, online) : null
  const name = assistantNameNow()

  return (
    <div className="chats" data-testid="chats" onKeyDown={onKeyDown}>
      <div className="chats-head">
        <button type="button" className="icon-btn sm" aria-label={tr('rail.assistant.chatList.backToChat')} data-tip={tr('common.back')} onClick={onClose} data-testid="chats-back" ref={backRef}>
          <ArrowLeft size={18} />
        </button>
        <h3 id="chats-title">{tr('rail.assistant.chatList.chats')}</h3>
      </div>
      <div className="chats-search" role="search">
        <Search size={16} aria-hidden className="chats-search-icon" />
        <input ref={searchRef} type="search" className="chats-search-input" value={typed} maxLength={200}
          placeholder={tr('rail.assistant.chatList.searchPlaceholder')} aria-label={tr('rail.assistant.chatList.search')}
          aria-controls="chats-results" aria-keyshortcuts="Control+F Meta+F /" data-testid="chats-search"
          data-own-esc={typed ? '' : undefined} enterKeyHint="search" autoComplete="off" spellCheck={false}
          onChange={(e) => setTyped(e.target.value)} onKeyDown={onSearchKey} />
        {typed && (
          <button type="button" className="icon-btn xs chats-search-clear" aria-label={tr('rail.assistant.chatList.clearSearch')}
            data-tip={tr('rail.assistant.chatList.clearSearch')} onClick={() => {
              setTyped('')
              searchRef.current?.focus()
            }}>
            <X size={14} />
          </button>
        )}
      </div>
      {/* The number of results, for screen readers. */}
      <p className="sr-only" aria-live="polite" data-testid="chats-found">
        {searching && q.data && !q.isPlaceholderData
          ? chats.length ? tr('rail.assistant.chatList.found', { count: chats.length }) : tr('rail.assistant.chatList.noMatch', { query: term })
          : ''}
      </p>
      <div className="chats-body" id="chats-results" aria-busy={(searching && q.isPlaceholderData) || undefined}>
        {loading ? (
          // Room for a few rows from the start; the skeleton itself waits a moment.
          <div className="chats-loading" aria-busy="true" aria-label={tr('common.loading')} data-testid="chats-loading">
            {skeleton &&
              [0, 1, 2].map((i) => (
                <div key={i} className="chats-skel">
                  <Skeleton width="60%" />
                  <Skeleton width="90%" height={12} />
                </div>
              ))}
          </div>
        ) : problem === 'offline' ? (
          <div className="chats-problem" data-testid="chats-offline">
            <CloudOff size={24} aria-hidden />
            <h4>{tr('rail.assistant.chatList.offlineTitle')}</h4>
            <p className="muted">{tr('rail.assistant.chatList.offlineHint')}</p>
            <button type="button" className="btn text sm" disabled={q.isFetching} onClick={() => void q.refetch()}>{tr('common.retry')}</button>
          </div>
        ) : problem === 'failed' ? (
          <div className="chats-problem" role="alert" data-testid="chats-failed">
            <span className="chats-error">{tr('rail.assistant.chatList.loadFailed')}</span>
            <button type="button" className="btn text sm" disabled={q.isFetching} onClick={() => void q.refetch()}>{tr('common.retry')}</button>
          </div>
        ) : chats.length === 0 && searching ? (
          <div className="chats-problem" data-testid="chats-no-match">
            <p>{q.isPlaceholderData ? '' : tr('rail.assistant.chatList.noMatch', { query: term })}</p>
          </div>
        ) : chats.length === 0 ? (
          <div data-testid="chats-empty">
            <EmptyState title={tr('rail.assistant.chatList.emptyTitle')}>
              <p className="muted">{tr('rail.assistant.chatList.emptyHint', { name })}</p>
            </EmptyState>
          </div>
        ) : (
          <ul className={`chats-list${searching ? ' results' : ''}`} ref={listRef} aria-labelledby="chats-title">
            {chats.map((c) =>
              editing === c.id ? (
                <RenameRow key={c.id} chat={c} onDone={(text) => endRename(c, text)} />
              ) : (
                <ChatRow key={c.id} chat={c} current={c.id === current} busy={opening === c.id} searching={searching}
                  onOpen={() => void open(c)} onRename={() => setEditing(c.id)} onDelete={() => remove(c)} />
              ),
            )}
          </ul>
        )}
      </div>
    </div>
  )
}

interface RowProps {
  chat: ChatSummary
  current: boolean
  busy: boolean
  searching: boolean
  onOpen: () => void
  onRename: () => void
  onDelete: () => void
}

function ChatRow({ chat: c, current: isCurrent, busy, searching, onOpen, onRename, onDelete }: RowProps) {
  const title = c.title.trim() || tr('rail.assistant.chatList.untitled')
  const more = useAnchor<HTMLButtonElement>()
  // A search result shows the text around its hit; the list shows the start of the latest answer.
  const snippet = searching && c.snippet_parts?.length ? c.snippet_parts : null
  return (
    <li className={`chats-item${isCurrent ? ' current' : ''}`}>
      <button type="button" className="chats-row" data-chat-row={c.id} aria-current={isCurrent ? 'true' : undefined}
        aria-busy={busy || undefined} aria-keyshortcuts="F2 Delete" onClick={onOpen}>
        <span className="chats-top">
          <span className="chats-title ellipsis" dir="auto">
            {searching && c.title_parts?.length && c.title.trim() ? <Highlighted parts={c.title_parts} /> : title}
          </span>
          <span className="chats-when">{formatListDate(c.updated_at)}</span>
        </span>
        {isCurrent && <span className="chats-now">{tr('rail.assistant.chatList.openNow')}</span>}
        {snippet ? (
          <span className="chats-preview chats-snippet" dir="auto" data-testid="chat-snippet"><Highlighted parts={snippet} /></span>
        ) : (
          c.preview && <span className="chats-preview" dir="auto">{c.preview}</span>
        )}
      </button>
      <button type="button" className={`icon-btn sm chats-more${more.open ? ' active' : ''}`} aria-label={tr('rail.assistant.chatList.more', { title })}
        aria-haspopup="menu" aria-expanded={more.open} data-tip={tr('rail.assistant.chatList.moreTip')} data-testid="chat-more"
        onClick={(e) => more.toggle(e.currentTarget)}>
        <MoreHorizontal size={16} />
      </button>
      <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" label={title} minWidth={180}>
        {/* ACT agent.chat_rename @web.panel: an inline field in the row; F2 on a row does the same. */}
        <MenuItem icon={<Pencil size={16} />} shortcut="F2" onSelect={() => {
          more.close()
          onRename()
        }}>{tr('rail.assistant.chatList.rename')}</MenuItem>
        <MenuItem icon={<Trash2 size={16} />} shortcut="Del" danger onSelect={() => {
          more.close()
          onDelete()
        }}>{tr('rail.assistant.chatList.delete')}</MenuItem>
      </Popover>
    </li>
  )
}

/** A row while its title is renamed: Enter saves, Esc cancels, leaving the field saves. */
function RenameRow({ chat: c, onDone }: { chat: ChatSummary; onDone: (text: string | null) => void }) {
  const [text, setText] = useState(c.title)
  const done = useRef(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (value: string | null) => {
    if (done.current) return
    done.current = true
    onDone(value)
  }
  return (
    <li className="chats-item editing">
      <div className="chats-rename">
        <input ref={ref} className="chats-rename-input" value={text} maxLength={TITLE_MAX} dir="auto" data-own-esc=""
          aria-label={tr('rail.assistant.chatList.renameLabel')} aria-describedby="chats-rename-hint" data-testid="chat-rename-input"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => finish(text)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              finish(text)
            } else if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              finish(null)
            }
          }} />
        <span className="chats-rename-hint" id="chats-rename-hint">{tr('rail.assistant.chatList.renameHint')}</span>
      </div>
    </li>
  )
}
