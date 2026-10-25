// Live updates: SSE /events with a /changes cursor to catch up after reconnects.

import { assistantNameNow } from '../features/rail/assistant/useAssistantName'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, buildUrl } from './client'
import type { Changes, Connector } from './types'
import { liveCounts } from './liveState'
import { qk } from './hooks'
import { CHANGE_KEYS } from './queryKeys'
import { convSlug } from '../lib/pubid'
import { toast } from '../stores/ui'
import { mailPath } from '../lib/views'
import { safeText } from '../lib/safeText'
import { applyServerCounts, type SidebarCounts } from '../features/layout/sidebarCounts'
import { appNavigate } from '../lib/appNav'
import { CLIENT_ID } from '../lib/live/clientId'
import { LIVE_RESYNC, onEditingEvent, onItemEvent, type EditingEvent, type ItemEvent } from '../lib/live/store'
import { notePath, tableRoutePath } from '../lib/routes'
import { itemTouched, noteUnshared, noteWriter, onPresence, type PresenceEvent } from '../features/notes/presence'
import { HELLO_EVENT } from '../features/notes/presenceBeacon'
import { outboxEvent, plainSendError } from '../features/mail/outbox'
import { t } from '../i18n'
import { approvalTag, closeApprovalNotifications } from '../lib/serviceWorker'

type Entity = 'conversation' | 'message' | 'label' | 'outbox' | 'connector' | 'rule' | 'identity' | 'task' | 'event' | 'calendar' | 'reminder' | string

export interface InvalidateOptions {
  /** The conversations that changed, when the server named them all ('all' or absent: any). Only
   *  their open views refetch, not every conversation this tab has looked at. */
  conversations?: Iterable<number> | 'all'
  /** The server follows these changes with a `counts` event (the live stream is open): the sidebar
   *  numbers and the labels' unread counts arrive with it, so they are not refetched here. */
  countsFollow?: boolean
}

/** Mark stale what the changed entities show. Unknown entities are ignored (newer servers send more).
 *  Only the queries on screen refetch (React Query's default); the rest refetch when next shown. */
export function invalidateFor(qc: QueryClient, entities: Set<Entity>, opts: InvalidateOptions = {}) {
  if (entities.has('conversation') || entities.has('message')) {
    qc.invalidateQueries({ queryKey: qk.conversations })
    const ids = opts.conversations
    if (ids === undefined || ids === 'all' || entities.has('message')) qc.invalidateQueries({ queryKey: ['conversation'] })
    else for (const id of ids) qc.invalidateQueries({ queryKey: qk.conversation(id) })
    if (!opts.countsFollow) qc.invalidateQueries({ queryKey: qk.labels })
    // Calendar → Today lists mail waiting on a reply (GET /today).
    qc.invalidateQueries({ queryKey: ['today'] })
  }
  // A label's name, colour or place: the list itself (its numbers come with the counts event).
  if (entities.has('label')) qc.invalidateQueries({ queryKey: qk.labels, exact: !!opts.countsFollow })
  if (entities.has('outbox')) qc.invalidateQueries({ queryKey: ['outbox'] })
  if (entities.has('connector')) qc.invalidateQueries({ queryKey: ['connectors'] })
  if (entities.has('rule')) qc.invalidateQueries({ queryKey: ['rules'] })
  if (entities.has('identity')) qc.invalidateQueries({ queryKey: qk.me })
  // Calendar, tasks and reminders (api/queryKeys.ts CHANGE_KEYS): the ids are mirror row ids the
  // client cannot map to its keys, so each entity marks its whole family stale, once per burst.
  const stale = new Map<string, readonly string[]>()
  for (const e of entities) for (const key of CHANGE_KEYS[e] ?? []) stale.set(key.join('\u0000'), key)
  for (const key of stale.values()) qc.invalidateQueries({ queryKey: [...key] })
  if (entities.has('decision') || entities.has('suggestion')) qc.invalidateQueries({ queryKey: ['suggestions'] })
  if (entities.has('settings')) qc.invalidateQueries({ queryKey: qk.me })
  if (entities.has('layout')) {
    qc.invalidateQueries({ queryKey: ['layouts'] })
    qc.invalidateQueries({ queryKey: qk.me })
  }
  if (['skill', 'memory', 'entity', 'assistant_profile'].some((e) => entities.has(e))) qc.invalidateQueries({ queryKey: ['assistant'] })
  // Notes and tables written elsewhere (live events): once per burst, not once per tick of a
  // shared checklist.
  if (entities.has('notes')) qc.invalidateQueries({ queryKey: ['notes'] })
  if (entities.has('tables')) qc.invalidateQueries({ queryKey: ['tables'] })
}

/** Whether a `connector` event says something the cached account list does not show. */
export function connectorNews(qc: QueryClient, ev: ServerEvent): boolean {
  const list = qc.getQueryData<Connector[]>(['connectors'])
  const c = Array.isArray(list) ? list.find((x) => x.id === ev.id) : undefined
  if (!c) return true
  return c.status !== ev.status || (c.status_detail ?? '').slice(0, 300) !== String(ev.detail ?? '')
}

export interface ServerEvent {
  type: string
  entity?: Entity
  title?: string
  body?: string
  kind?: string
  /** New mail from a VIP sender (Settings → Mail → Inbox). */
  vip?: boolean
  conversation_id?: number
  status?: string
  chat_session_id?: string | null
  /** Reminder notifications: the reminder key; other fields as the server sends them. */
  key?: string
  [field: string]: unknown
}

// The approval notifications this tab showed, by tag, so an approval decided anywhere closes its
// notification here too (a page's own notifications are not in the worker's getNotifications()).
const shownApprovals = new Map<string, Notification>()

/** ACT approval.sync @web.notification: approvals that left pending (decided here or elsewhere,
 *  expired or withdrawn) lose their notifications, this tab's and the worker's. */
export function closeApprovals(ids: (number | string)[]) {
  for (const id of ids) {
    const tag = approvalTag(id)
    shownApprovals.get(tag)?.close()
    shownApprovals.delete(tag)
  }
  void closeApprovalNotifications(ids)
}

function notificationTag(ev: ServerEvent): string | undefined {
  if (ev.conversation_id) return `c${ev.conversation_id}`
  if (ev.kind === 'approval' && ev.pending_id) return approvalTag(String(ev.pending_id))
  return undefined
}

function desktopNotify(ev: ServerEvent) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  if (document.visibilityState === 'visible') return
  try {
    // The server writes the title ("New mail from …"); sender text is cleaned of bidi controls.
    const tag = notificationTag(ev)
    const n = new Notification(safeText(ev.title) || 'Mailr', { body: safeText(ev.body), tag, icon: '/icon-192.png' })
    if (tag?.startsWith('a')) {
      shownApprovals.set(tag, n)
      n.onclose = () => {
        if (shownApprovals.get(tag) === n) shownApprovals.delete(tag)
      }
    }
    n.onclick = () => {
      window.focus()
      if (ev.conversation_id) appNavigate(mailPath('inbox', Number(ev.conversation_id)))
      else if (ev.pending_id) appNavigate(`/approvals/${Number(ev.pending_id)}`)
      else if (ev.kind === 'account') appNavigate('/settings/mail/accounts')
    }
  } catch {
    /* ignore */
  }
}

/** Changes within this many ms are refetched together. */
export const FLUSH_MS = 400
const FLUSH_MAX_MS = 3000

/** How many refetches in a row came close together (a steady stream of changes: an import, a
 *  backfill): each widens the next window, up to FLUSH_MAX_MS, so an open list is not refetched
 *  every 400 ms for an hour. A quiet spell resets it. */
export function nextStreak(streak: number, sinceLastFlush: number): number {
  // Close together: the first change came within a second of the last refetch.
  return sinceLastFlush < flushDelay(streak) + 1000 ? Math.min(streak + 1, 3) : 0
}

export function flushDelay(streak: number): number {
  return Math.min(FLUSH_MS * 2 ** streak, FLUSH_MAX_MS)
}

/** Mount once inside the authenticated app. `assistantDesktop`: the account's "Assistant
 *  messages" switch (Settings → Notifications). Mail, approvals and account notices arrive only
 *  when the account wants them (backend core/notifyprefs.py), so they need no check here. */
export function useLiveUpdates(enabled: boolean, assistantDesktop = true) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return
    let es: EventSource | null = null
    let cursor: number | null = null
    let closed = false
    let retry = 1000
    let pending = new Set<Entity>()
    // The conversations the pending changes named; 'all' once one change did not say.
    let pendingConvs: Set<number> | 'all' = new Set()
    let timer: ReturnType<typeof setTimeout> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let opened = false
    // Watchdog: the server sends something at least every 25 s. Silence means the stream is dead
    // behind a proxy; reconnect. While it is down, poll the change cursor so lists still update.
    let lastSeen = Date.now()
    let lastPoll = 0

    let streak = 0
    let lastFlush = 0

    const flush = () => {
      timer = null
      const now = Date.now()
      streak = nextStreak(streak, now - lastFlush)
      lastFlush = now
      const ents = pending
      const conversations = pendingConvs
      pending = new Set()
      pendingConvs = new Set()
      invalidateFor(qc, ents, { conversations, countsFollow: liveCounts.open })
    }
    /** `ids`: the conversations a change names (a `changes` event's conversation_ids, a new-mail
     *  notification's conversation_id); a conversation change without them may touch any. */
    const queue = (e: Entity, ids?: unknown) => {
      pending.add(e)
      if (e === 'conversation' && pendingConvs !== 'all') {
        const list = Array.isArray(ids) ? ids : ids != null ? [ids] : null
        if (list && list.every((x) => typeof x === 'number')) for (const x of list as number[]) pendingConvs.add(x)
        else pendingConvs = 'all'
      }
      if (!timer) timer = setTimeout(flush, flushDelay(streak))
    }

    async function catchUp() {
      if (!cursor) {
        try {
          const c = await api.get<Changes>('/changes', { since: '' })
          cursor = c.cursor
        } catch {
          /* changes endpoint optional */
        }
        return
      }
      try {
        let more = true
        let guard = 0
        const ents = new Set<Entity>()
        while (more && guard++ < 10) {
          const c: Changes = await api.get<Changes>('/changes', { since: cursor })
          cursor = c.cursor
          c.changes.forEach((x) => ents.add(x.entity))
          more = c.more
        }
        if (ents.size) invalidateFor(qc, ents)
      } catch {
        // Cursor expired or endpoint unavailable: refresh everything.
        invalidateFor(qc, new Set(['conversation', 'label', 'outbox', 'calendar']))
        cursor = null
      }
    }

    function connect() {
      if (closed) return
      es = new EventSource(buildUrl('/events'), { withCredentials: true })
      es.onopen = () => {
        retry = 1000
        lastSeen = Date.now()
        liveCounts.set(true)
        void catchUp()
        // Live events sent while the stream was down are lost: open editors check their item again.
        // The first open has nothing to catch up on.
        if (opened) window.dispatchEvent(new Event(LIVE_RESYNC))
        opened = true
      }
      es.onmessage = (m) => {
        lastSeen = Date.now()
        let ev: ServerEvent
        try {
          ev = JSON.parse(m.data) as ServerEvent
        } catch {
          return
        }
        if (ev.type === 'changes' && ev.entity) queue(ev.entity, ev.conversation_ids)
        // The server missed events for this tab (its queue overflowed, or its database listener
        // reconnected): catch up from the change cursor, as after a reconnect.
        else if (ev.type === 'resync') {
          void catchUp()
          window.dispatchEvent(new Event(LIVE_RESYNC))
        }
        // Notes and checklists: a note, an item or the viewer's
        // state changed; refetch the notes views, except for this tab's own echoes.
        else if ((ev.type === 'item' && (ev.kind === 'note' || ev.kind === 'checkitem')) || ev.type === 'notes') {
          if (ev.origin !== CLIENT_ID) queue('notes')
          // A shared note (notes/sharing.py): "Dana is editing" from the author of another person's write.
          if (ev.type === 'item' && ev.origin !== CLIENT_ID) noteWriter(String(ev.note ?? ev.id ?? ''), ev.author as { id?: number; name?: string })
          // Another person's tick or change marks the row with their name for a moment.
          if (ev.type === 'item' && ev.kind === 'checkitem' && ev.origin !== CLIENT_ID) itemTouched(String(ev.id ?? ''), ev.author as { id?: number; name?: string })
          if (ev.type === 'notes' && (ev.what === 'shares' || ev.what === 'unshared')) {
            // Who sees a note or a table changed: the tables lists and the notices follow too.
            queue('tables')
            if (ev.what === 'unshared') {
              noteUnshared(String(ev.id ?? ''), String(ev.by_name ?? t('api.events.theOwner')))
              toast({ message: t('api.events.stoppedSharingWithYou', { string: String(ev.by_name ?? t('api.events.theOwner')), string2: String(ev.name ?? '') }), duration: 6000 })
            }
          }
        }
        // Tables: a row or a table changed; an open grid
        // reloads its rows in place and keeps the focused cell. The Notes grid's cards follow.
        else if (ev.type === 'item' && (ev.kind === 'table' || ev.kind === 'row')) {
          if (ev.origin !== CLIENT_ID) {
            noteWriter(String(ev.table ?? ev.id ?? ''), ev.author as { id?: number; name?: string })
            queue('tables')
            if (ev.kind === 'table') queue('notes')
          }
        }
        // Who has a shared list, note or table open (notes/presence.py): "Cleo is in this list".
        else if (ev.type === 'presence') {
          if (ev.origin !== CLIENT_ID && onPresence(ev as unknown as PresenceEvent))
            window.dispatchEvent(new CustomEvent(HELLO_EVENT, { detail: { id: String(ev.id ?? '') } }))
        }
        // Live editing (lib/live/store.ts): a new version of an item, and agents writing one.
        else if (ev.type === 'item' && ev.kind) onItemEvent(ev as unknown as ItemEvent)
        else if (ev.type === 'editing' && ev.kind) onEditingEvent(ev as unknown as EditingEvent)
        else if (ev.type === 'counts' && ev.views) {
          // The sidebar numbers after a change (core/sidebar.py): set, not refetched.
          applyServerCounts(qc, ev as unknown as SidebarCounts)
        } else if (ev.type === 'outbox') {
          queue('outbox')
          if (ev.status === 'awaiting_approval') {
            toast({ message: ev.title ?? t('api.events.anAgentMessageIsWaiting'), actionLabel: t('common.review'), onAction: () => appNavigate('/settings/agents#approvals') })
          } else {
            // A send the composer follows settles its own toast (features/mail/outbox.tsx).
            const composerSpeaks = outboxEvent(ev)
            // An uncertain send speaks once nothing showed that it left (its event then has a title).
            if ((ev.status === 'failed' || (ev.status === 'uncertain' && ev.title)) && !composerSpeaks) {
              // Plain words; the raw error stays behind the Outbox's Details.
              const subject = typeof ev.subject === 'string' && ev.subject ? t('api.events.wasNotSent', { subject: ev.subject }) : t('api.events.aMessageWasNotSent')
              toast({ message: subject + plainSendError(String(ev.error ?? '')), error: true, actionLabel: t('api.events.openOutbox'), onAction: () => appNavigate('/mail/outbox') })
            }
          }
        } else if (ev.type === 'notification' && ev.kind === 'assistant') {
          // The assistant's own messages: always shown in the app, and as a
          // desktop notification unless desktop notifications are off.
          toast({
            message: `${ev.title ?? assistantNameNow()}: ${(ev.body ?? '').split('\n')[0].slice(0, 140)}`,
            duration: 10_000,
            ...(ev.conversation_id ? { actionLabel: t('common.open'), onAction: () => appNavigate(`/mail/inbox/${convSlug(Number(ev.conversation_id))}`) } : {}),
          })
          if (assistantDesktop) desktopNotify(ev)
          void qc.invalidateQueries({ queryKey: ['assistant', 'runs'] })
        } else if (ev.type === 'notification' && ev.kind === 'reminder') {
          // A reminder fired (server-planned). The tray and the web notification
          // listen for this event; here only the reminder lists go stale.
          queue('reminder')
          window.dispatchEvent(new CustomEvent('mailr:reminder', { detail: ev }))
        } else if (ev.type === 'notification' && ev.kind === 'calendar_share') {
          // Someone shared a calendar with this user (calendar/sharing.py): a quiet toast, never a
          // desktop notification. The calendar list and its notice line refetch.
          toast({ message: ev.title ?? t('api.events.aCalendarWasSharedWith'), duration: 8_000, actionLabel: t('common.open'), onAction: () => appNavigate('/calendar') })
          void qc.invalidateQueries({ queryKey: ['calendar'] })
        } else if (ev.type === 'notification' && ev.kind === 'notes_share') {
          // Someone shared a note or a table with this user (notes/sharing.py): a quiet toast, never
          // a desktop notification. The notes lists and the notice line refetch.
          const id = String(ev.id ?? '')
          toast({ message: ev.title ?? t('api.events.aNoteWasSharedWith'), duration: 8_000, actionLabel: t('common.open'), onAction: () => appNavigate(id.startsWith('t') ? tableRoutePath(id) : notePath(id)) })
          void qc.invalidateQueries({ queryKey: ['notes'] })
          void qc.invalidateQueries({ queryKey: ['tables'] })
        } else if (ev.type === 'notification' && ev.kind === 'account') {
          // A mail account needs its password again (backend core/accounthealth.py): once per failure.
          toast({ message: ev.title ?? t('api.events.aMailAccountNeedsIts'), duration: 12_000, actionLabel: t('common.open'), onAction: () => appNavigate('/settings/mail/accounts') })
          desktopNotify(ev)
          queue('connector')
        } else if (ev.type === 'notification') {
          // New mail, approvals and failed sends: the server sends only what the account wants.
          desktopNotify(ev)
          queue('conversation', ev.conversation_id)
        } else if (ev.type === 'assistant_brief' && ev.conversation_id) {
          void qc.invalidateQueries({ queryKey: ['brief', Number(ev.conversation_id)] })
          void qc.invalidateQueries({ queryKey: ['assistant', 'runs'] })
        } else if (ev.type === 'summary' && ev.conversation_id) {
          void qc.invalidateQueries({ queryKey: qk.summary(ev.conversation_id) })
        } else if (ev.type === 'memory_suggestions') {
          queue('memory')
        } else if (ev.type === 'connector') {
          // Every sync pass reports "Up to date" again: refetch only for news (another status or
          // detail, or an account this tab has not listed).
          if (connectorNews(qc, ev)) queue('connector')
        } else if (ev.type === 'approval.decided') {
          // Approvals that left pending, by any path (one event for a batch): their notifications go.
          closeApprovals(Array.isArray(ev.ids) ? (ev.ids as (number | string)[]) : [])
          void qc.invalidateQueries({ queryKey: ['approvals'] })
        } else if (ev.type === 'approval') {
          void qc.invalidateQueries({ queryKey: ['approvals'] })
          void qc.invalidateQueries({ queryKey: ['outbox'] })
          // An approved action has run: what it changed (mail, labels, tasks, events) is stale.
          if (ev.status === 'executed' || ev.status === 'failed') {
            invalidateFor(qc, new Set<Entity>(['conversation', 'label', 'task', 'event', 'calendar', 'rule', 'outbox']))
          }
          if (ev.status === 'pending' && !ev.chat_session_id) {
            // Assistant approvals appear in the chat; token approvals get a toast (the top-bar
            // badge opens the drawer).
            toast({
              message: ev.title ?? t('api.events.anAgentIsWaitingFor'),
              duration: 10_000,
              ...(ev.id ? { actionLabel: t('common.review'), onAction: () => appNavigate(`/approvals/${Number(ev.id)}`) } : {}),
            })
          } else if (ev.status === 'failed') {
            toast({ message: t('api.events.anApprovedAgentActionFailed'), error: true })
          }
        }
      }
      es.onerror = () => {
        liveCounts.set(false)
        es?.close()
        es = null
        if (closed) return
        // With jitter: after a server restart every tab would otherwise come back at the same moment.
        reconnectTimer = setTimeout(connect, Math.round(retry * (0.5 + Math.random())))
        retry = Math.min(retry * 2, 30_000)
      }
    }

    connect()
    const watchdog = setInterval(() => {
      const now = Date.now()
      const open = es?.readyState === EventSource.OPEN
      if (open && now - lastSeen > 70_000) {
        es?.close()
        es = null
        if (reconnectTimer) clearTimeout(reconnectTimer)
        connect()
      } else if (!open && now - lastPoll > 20_000 && document.visibilityState === 'visible') {
        lastPoll = now
        void catchUp()
      }
    }, 10_000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void catchUp()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      closed = true
      liveCounts.set(false)
      clearInterval(watchdog)
      es?.close()
      if (timer) clearTimeout(timer)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [enabled, assistantDesktop, qc])
}
