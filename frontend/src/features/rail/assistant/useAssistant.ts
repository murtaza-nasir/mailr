// State and streaming for the built-in assistant (POST /agent/chat, SSE).
//
// History lives on the server: the client sends only the new turn, the session id, and what is on
// screen (open compose windows, the open conversation, the view). A turn is a timeline of text
// segments and tool steps (timeline.ts). The run does not depend on the connection: when the
// stream drops, the hook re-attaches with GET /agent/chat/{id}/stream?after=<seq>, and if the run
// is gone it reloads the chat, where an unfinished turn shows as interrupted with Retry.

import { msgText } from './present/serverText'
import { CHATS_KEY, withTitle, type ChatSummary } from './chatList'
import { cardToRef } from './cardRef'
import { assistantNameNow } from './useAssistantName'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, getStream, postStream } from '../../../api/client'
import type { ComposePayload } from '../../../api/types'
import { screenContext, useCompose } from '../../../stores/compose'
import { flushOpenEditors } from '../../../lib/live/editors'
import { noteVersion, useLive, type LiveKind } from '../../../lib/live/store'
import { appContext } from '../../assistant-context/appContext'
import { ASSISTANT_SESSION_KEY, askContext } from '../../assistant-context/askAbout'
import { toast, useUI } from '../../../stores/ui'
import { useOfficeTabs } from '../../../themes/office/tabs'
import type { AlsoCovers } from '../../approvals/grant'
import type { Citation } from './citations'
import type { Display, Outcome } from './present/types'
import { fallbackLabel, type Step } from './steps'
import {
  applyEvent, fromReplay, markWaiting, replayModel, type ConfigChange, type DraftEditNote, type ReplayTimelineItem,
  type TimelineTurn, type TurnStatus,
} from './timeline'
import { t as tr } from '../../../i18n'

export interface ApprovalRef {
  id: number
  action: string
  title: string
  target?: string | null
  preview?: ApprovalPreview | null
  status: string
  expires_at?: string | null
  /** The server's display model and outcome (agent/present.py); older servers send neither. */
  display?: Display | null
  outcome?: Outcome | null
  /** How many other waiting requests each grant on this card would approve too (pending cards only). */
  also_covers?: AlsoCovers | null
  /** False when a hard rule held the card: it is decided once, with no grant buttons. */
  grants?: boolean | null
}

export { cardToRef }

/** A card as the server sends it (GET /approvals, approval_required.approval, timeline items). */
export interface ApprovalCardData {
  id: number
  action: string
  target?: string | null
  preview?: ApprovalPreview | null
  status: string
  expires_at?: string | null
  display?: Display | null
  outcome?: Outcome | null
  also_covers?: AlsoCovers | null
  grants?: boolean | null
}


export interface ApprovalPreview {
  title?: string
  lines?: string[]
  kind?: string
  danger?: boolean
  reason?: string
  email?: { from?: string; to?: { email: string; name?: string }[]; cc?: { email: string }[]; subject?: string; text?: string; attachments?: unknown[]; images?: string[]; links?: string[] }
  event?: Record<string, unknown>
  task?: Record<string, unknown>
  message_ids?: number[]
  display?: Display
  /** A bulk review's card (agent/review.py): the plan by move and sender, and what is kept. */
  review?: ReviewPreview
}

export interface ReviewSender {
  from: string; count: number; conversation_ids: number[]; subjects: string[]
  /** The sender's address, and whether the card offers a filter for their future mail. */
  email?: string; suggest_filter?: boolean
  /** Each of the sender's conversations, for the per-conversation move chip (cards from 20 Oct 2026 on). */
  conversations?: { conversation_id: number; subject: string; date: string | null }[]
}
export interface ReviewRow {
  conversation_id: number; from: string; subject: string; reason: string
  email?: string; date?: string | null
  /** On a decided card: kept because the user chose Keep. */
  by_you?: boolean
  /** The move came from the user's past choice for this sender (`reason` says so), not from the model. */
  past?: boolean
}
export interface ReviewPreview {
  review_id: number
  scope: string
  total: number
  groups: { move: 'trash' | 'archive' | 'read' | 'label'; label?: string; label_id?: number | null; count: number; senders: ReviewSender[] }[]
  set_aside: Record<string, number>
  kept: ReviewRow[]
  kept_count: number
  unsure: ReviewRow[]
  unsure_count: number
  /** The labels the review could add (the move chip offers them). */
  labels?: { id: number; name: string }[]
  /** On a decided card: how many conversations the user moved. */
  changed?: number
}

export interface UserTurn {
  role: 'user'
  text: string
  id?: number
  skills?: string[]
}

/** A draft the assistant made or changed in this turn (the "Open draft" card). */
export interface DraftCard {
  payload: ComposePayload
  draftId: number | null
  /** The version this turn wrote (an older card shows "Updated since"). */
  version: number | null
}

/** Another item the turn made or changed (backend core/live.ref): event, task, memory, filter. */
export interface ItemCard {
  kind: LiveKind
  id: string | number
  version: number | null
  title: string
}

export interface AssistantTurn extends TimelineTurn {
  role: 'assistant'
  /** A reply the app wrote itself for a slash command (/copy with nothing to copy, …): not an answer. */
  notice?: boolean
  approvals: ApprovalRef[]
  drafts: DraftCard[]
  refs: ItemCard[]
  configs: ConfigChange[]
  draftEdits: DraftEditNote[]
  error?: string
  stopped?: boolean
  running: boolean
  status: TurnStatus
  resume?: boolean // continuation after an approval decision (no user bubble)
  userMessageId?: number
  /** The notes that went into the prompt (the "Used memories" line). */
  memoryIds?: number[]
  seconds?: number
  conversations?: number
  startedAt: number
  /** Bulk reviews this turn started (review_mail): a progress card each, then its review card. */
  reviews?: number[]
}

export type Turn = UserTurn | AssistantTurn

const SESSION_KEY = ASSISTANT_SESSION_KEY

function loadSession(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}

function saveSession(id: string | null) {
  try {
    if (id) sessionStorage.setItem(SESSION_KEY, id)
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    /* ignore */
  }
}

interface ReplayTurn {
  role: 'user' | 'assistant'
  id?: number
  content: string
  citations: Citation[]
  steps: Step[]
  timeline?: ReplayTimelineItem[]
  status?: TurnStatus | null
  stop_reason?: string | null
  resume?: boolean
  model?: unknown
  unconfirmed?: boolean
  user_message_id?: number | null
  skills?: string[]
  memory_ids?: number[]
}

/** A chat's title, and whether the user gave it (Rename) or it is the first question. */
export interface ChatTitle {
  text: string
  renamed: boolean
}

interface SessionReplay {
  id: string
  turns: ReplayTurn[]
  running?: boolean
  title?: string
  renamed?: boolean
  skills?: string[]
  pending_approvals?: ApprovalCardData[]
  /** The chat's bulk reviews (agent/review.py), newest last. */
  reviews?: { review_id: number }[]
}

function parse(data: string): Record<string, unknown> {
  try {
    return JSON.parse(data) as Record<string, unknown>
  } catch {
    return { text: data }
  }
}

const STEP_OUTCOME: Record<string, string> = {
  approved: 'serverText.approvals.step_outcome.approved', executing: 'serverText.approvals.step_outcome.approved',
  executed: 'serverText.approvals.step_outcome.approved', denied: 'serverText.approvals.step_outcome.denied',
  expired: 'serverText.approvals.step_outcome.expired', failed: 'serverText.approvals.step_outcome.failed',
}

/** Turns with approval `id` updated in place: its status, display and outcome, and the step that asked. */
export function withApproval(list: Turn[], id: number, status: string, card?: ApprovalCardData): Turn[] {
  const stepStatus = status === 'denied' || status === 'failed' || status === 'expired' ? 'failed' : status === 'pending' ? 'waiting' : 'done'
  return list.map((t) =>
    t.role === 'assistant' && t.approvals.some((a) => a.id === id)
      ? {
          ...t,
          approvals: t.approvals.map((a) =>
            a.id === id
              ? { ...a, status, ...(card ? { display: card.display ?? a.display, outcome: card.outcome ?? null } : status !== a.outcome?.state ? { outcome: null } : {}) }
              : a,
          ),
          // The step that asked says what happened to the request ("approved", "denied").
          items: t.items.map((it) => (it.kind === 'steps' ? { ...it, steps: it.steps.map((s) => (s.approvalId === id ? { ...s, status: stepStatus, summary: STEP_OUTCOME[status] ? tr(STEP_OUTCOME[status]) : s.summary } : s)) } : it)),
        }
      : t,
  )
}

export function newAssistantTurn(extra: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    role: 'assistant', items: [], citations: [], approvals: [], drafts: [], refs: [], configs: [], draftEdits: [],
    running: true, status: 'running', startedAt: Date.now(), ...extra,
  }
}

function fromReplayTurns(s: SessionReplay): Turn[] {
  const turns: Turn[] = s.turns.map((t) =>
    t.role === 'user'
      ? { role: 'user', text: t.content, id: t.id, skills: t.skills }
      : newAssistantTurn({
          items: fromReplay(t.timeline, t.content, t.steps ?? []),
          approvals: (t.timeline ?? []).flatMap((i) => (i.type === 'approval' && i.approval ? [cardToRef(i.approval as ApprovalCardData)] : [])),
          citations: t.citations ?? [],
          running: false,
          status: (t.status ?? 'done') as TurnStatus,
          stopReason: t.stop_reason ?? null,
          model: replayModel(t.model),
          unconfirmed: t.unconfirmed === true,
          resume: t.resume,
          userMessageId: t.user_message_id ?? undefined,
          memoryIds: t.memory_ids ?? [],
          error: t.status === 'error' ? tr('rail.assistant.useAssistant.couldNotAnswerThisRetry', { name: assistantNameNow() }) : undefined,
          startedAt: 0,
        }),
  )
  // Pending cards the timeline does not hold (older servers): on the last reply.
  const placed = new Set(turns.flatMap((t) => (t.role === 'assistant' ? t.approvals.map((a) => a.id) : [])))
  const pendingCards = (s.pending_approvals ?? []).filter((a) => !placed.has(a.id)).map(cardToRef)
  const lastA = [...turns].reverse().find((t): t is AssistantTurn => t.role === 'assistant')
  if (lastA && pendingCards.length) lastA.approvals = [...lastA.approvals, ...pendingCards]
  // Reviews this chat started: their progress (or result) on the last reply.
  if (lastA && s.reviews?.length) lastA.reviews = s.reviews.map((r) => r.review_id)
  return turns
}

/** What the page shows, for the assistant: view, search, open conversation, compose windows. */
function uiContext(conversationId: number | null): Record<string, unknown> {
  let view: string | undefined
  let q: string | undefined
  try {
    const m = /^\/mail\/([^/]+)/.exec(window.location.pathname)
    view = m ? decodeURIComponent(m[1]) : window.location.pathname.split('/')[1] || undefined
    q = new URLSearchParams(window.location.search).get('q') ?? undefined
  } catch {
    /* ignore */
  }
  // Everything else the screen shows: Office's conversation tabs, the checked rows, the list's rows.
  // Ids only; the server reads subjects and senders itself.
  const ui = useUI.getState()
  const office = typeof document !== 'undefined' && !!document.querySelector('.office-app')
  const open = office ? useOfficeTabs.getState().pinned.map((t) => t.id).filter((id) => id !== conversationId) : []
  const listShown = typeof document !== 'undefined' && !!document.querySelector('[data-mail-list]')
  return {
    view, q, conversation_id: conversationId, ...screenContext(),
    open_conversation_ids: open.slice(0, 10),
    selected_conversation_ids: [...ui.selected].slice(0, 50),
    selected_all_matching: !!ui.allMatchingQuery,
    list_conversation_ids: listShown ? ui.visibleIds.slice(0, 30) : [],
    // The whole list as a query, with its total ("30 of 143"): the server pages through it (list_view).
    ...(listShown && ui.listQuery ? { list: ui.listQuery } : {}),
    // The Calendar and Tasks apps: focused event, view, selected tasks (assistant-context/appContext.ts).
    ...appContext(),
  }
}

// Notes: the undo of each change the assistant made to a note or a list (backend notes/ops.py).
const ALLOWED_UNDO = /^\/(labels|rules|me\/settings|identities|layouts|assistant|notes)(\/|$)/

export function useAssistant(conversationId: number | null) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [sessionId, setSessionId] = useState<string | null>(loadSession)
  /** The open chat's title and whether the user gave it (the header shows it; Rename changes it). */
  const [title, setTitle] = useState<ChatTitle | null>(null)
  const [skills, setSkills] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const qcRef = useRef(qc)
  const abortRef = useRef<AbortController | null>(null)
  /** The turn in flight (run or follow), settled when its stream is over; open() waits for it. */
  const flightRef = useRef<Promise<unknown> | null>(null)
  const sessionRef = useRef<string | null>(sessionId)
  const turnsRef = useRef<Turn[]>(turns)
  useEffect(() => {
    sessionRef.current = sessionId
    turnsRef.current = turns
  })

  const patchLast = (fn: (t: AssistantTurn) => AssistantTurn) =>
    setTurns((list) => {
      const copy = list.slice()
      const i = copy.length - 1
      const t = copy[i]
      if (t && t.role === 'assistant') copy[i] = fn(t)
      return copy
    })

  /** Apply one server event to the running turn. Returns true when the turn is over. */
  const handle = useCallback((type: string, d: Record<string, unknown>): boolean => {
    switch (type) {
      case 'session': {
        const id = String(d.session_id ?? '')
        if (id) {
          sessionRef.current = id
          setSessionId(id)
          saveSession(id)
        }
        if (Array.isArray(d.skills)) setSkills(d.skills as string[])
        if (typeof d.title === 'string') {
          const text = d.title
          setTitle((t) => ({ text, renamed: t?.renamed ?? false }))
        }
        if (d.continuation) {
          // The turn goes on after an approval: same timeline, a line where it continues.
          patchLast((t) => applyEvent(t, 'resume', { segment: d.segment, text: tr('rail.assistant.useAssistant.afterYourApproval') }))
          return false
        }
        const umid = typeof d.user_message_id === 'number' ? d.user_message_id : undefined
        const memoryIds = Array.isArray(d.memory_ids) ? (d.memory_ids as number[]) : []
        if (memoryIds.length) patchLast((t) => ({ ...t, memoryIds }))
        if (umid) {
          setTurns((list) => {
            const copy = list.slice()
            const a = copy[copy.length - 1]
            const u = copy[copy.length - 2]
            if (a?.role === 'assistant') copy[copy.length - 1] = { ...a, userMessageId: umid }
            if (u?.role === 'user' && (!u.id || u.id === umid)) copy[copy.length - 2] = { ...u, id: umid }
            return copy
          })
        }
        return false
      }
      case 'approval_required': {
        const card = d.approval as ApprovalCardData | null | undefined
        const a: ApprovalRef = card
          ? cardToRef(card)
          : {
              id: Number(d.approval_id), action: String(d.action ?? ''), title: String(d.title ?? d.action ?? ''),
              target: (d.target as string) ?? null, preview: (d.preview as ApprovalPreview) ?? null,
              status: String(d.status ?? 'pending'), expires_at: (d.expires_at as string) ?? null,
              display: (d.preview as ApprovalPreview | null)?.display ?? null,
            }
        patchLast((t) => markWaiting(applyEvent({ ...t, approvals: [...t.approvals, a] }, type, d), a.id))
        return false
      }
      case 'approval_update': {
        // A decided card changes in place ("Approved · moved 6 messages to Trash").
        const card = d.approval as ApprovalCardData | undefined
        if (card) setTurns((list) => withApproval(list, card.id, card.status, card))
        return false
      }
      case 'review': {
        // A bulk review started (review_mail): its progress card, then its review card, in this turn.
        const rid = Number(d.review_id)
        if (rid) patchLast((t) => ({ ...t, reviews: [...(t.reviews ?? []).filter((x) => x !== rid), rid] }))
        return false
      }
      case 'draft': {
        // One card per draft: a second change in the same turn updates it.
        const ref = (d.ref ?? null) as { version?: number | null } | null
        const card: DraftCard = { payload: d.payload as ComposePayload, draftId: (d.draft_id as number) ?? null, version: ref?.version ?? null }
        patchLast((t) => ({ ...t, drafts: [...t.drafts.filter((x) => card.draftId == null || x.draftId !== card.draftId), card] }))
        return false
      }
      case 'ref': {
        const kind = d.kind as LiveKind
        noteVersion(kind, d.id as string | number, (d.version as number | null) ?? null, 'assistant')
        if (kind !== 'draft') {
          const card: ItemCard = { kind, id: d.id as string | number, version: (d.version as number | null) ?? null, title: String(d.title ?? '') }
          patchLast((t) => ({ ...t, refs: [...t.refs.filter((x) => !(x.kind === kind && String(x.id) === String(card.id))), card] }))
        }
        return false
      }
      case 'draft_edit': {
        const win = String(d.window ?? '')
        const changes = (d.changes ?? {}) as Record<string, unknown>
        const applied = useCompose.getState().pushEdit(win, changes)
        const subject = useCompose.getState().live[win]?.subject
        const edits = Array.isArray(changes.edits) ? (changes.edits as { find: string; replace: string }[]) : []
        const proposed = changes.proposed_recipients ? tr('rail.assistant.useAssistant.recipientsProposedAcceptOrReject') : ''
        const what = edits.length
          ? edits.map((e) => `“${e.find}” → “${e.replace}”`).join(', ')
          : changes.body_text != null || changes.subject != null ? tr('rail.assistant.useAssistant.newText') : proposed || tr('rail.assistant.useAssistant.newText')
        patchLast((t) => ({
          ...t,
          draftEdits: [...t.draftEdits, { window: win, draftId: (d.draft_id as number) ?? null, applied, summary: `${subject ? `“${subject}”: ` : ''}${what}` }],
        }))
        return false
      }
      case 'config':
        // A changed brief: the card by the messages reads it again.
        if (d.brief_conversation_id != null) void qcRef.current?.invalidateQueries({ queryKey: ['brief'] })
        patchLast((t) => ({ ...t, configs: [...t.configs, { title: String(d.title ?? tr('rail.assistant.useAssistant.changedASetting')), undo: (d.undo as ConfigChange['undo']) ?? null }] }))
        return false
      case 'title': {
        // The short title made for a new chat (agent/chat_title.py); a name the user gave stays.
        const text = String(d.title ?? '')
        const id = sessionRef.current
        if (text) {
          setTitle((t) => (t?.renamed ? t : { text, renamed: false }))
          if (id) qcRef.current?.setQueriesData<ChatSummary[]>({ queryKey: CHATS_KEY }, (list) => withTitle(list, id, text, false))
        }
        return false
      }
      case 'final':
        patchLast((t) => ({ ...applyEvent(t, type, d), stopped: t.stopped || Boolean(d.stopped) }))
        return false
      case 'done':
        patchLast((t) => ({
          ...applyEvent(t, type, d), running: false, status: d.stopped ? 'stopped' : 'done', stopped: t.stopped || Boolean(d.stopped),
          seconds: typeof d.seconds === 'number' ? d.seconds : (Date.now() - t.startedAt) / 1000,
          conversations: typeof d.conversations === 'number' ? d.conversations : undefined,
          // The notes recall brought back during the turn join the ones in the prompt.
          ...(Array.isArray(d.memory_ids) && d.memory_ids.length ? { memoryIds: d.memory_ids as number[] } : {}),
        }))
        // Whatever the turn changed (archive, labels, tasks…) shows at once, even if the live
        // stream missed it.
        for (const key of [['conversations'], ['conversation'], ['labels'], ['tasks'], ['calendar']]) void qcRef.current?.invalidateQueries({ queryKey: key })
        return true
      case 'error':
        if (d.code === 'command') {
          // The server's own reply to a slash command (agent/commands.py): an answer, not a failure.
          patchLast((t) => ({ ...applyEvent(t, 'final', { content: String(d.detail ?? d.message ?? ''), segment: 0 }), running: false, status: 'done' }))
          return true
        }
        patchLast((t) => ({ ...t, running: false, status: 'error', error: String(d.detail ?? d.message ?? tr('rail.assistant.useAssistant.couldNotAnswer', { name: assistantNameNow() })) }))
        // The access policy turned the assistant off: GET /me again, and the panel shows the message.
        if (d.code === 'no_access') void qcRef.current?.invalidateQueries({ queryKey: ['me'] })
        return true
      case 'tool_start':
        patchLast((t) => applyEvent(t, type, d, msgText(d.label_msg, String(d.label || fallbackLabel(String(d.name ?? ''), d.args)))))
        return false
      default:
        patchLast((t) => applyEvent(t, type, d))
        return false
    }
  }, [])

  const show = useCallback((s: SessionReplay) => {
    setTitle(typeof s.title === 'string' ? { text: s.title, renamed: Boolean(s.renamed) } : null)
    setTurns(fromReplayTurns(s))
    setSkills(s.skills ?? [])
  }, [])

  const replay = useCallback(async (id: string): Promise<SessionReplay | null> => {
    try {
      const s = await api.get<SessionReplay>(`/agent/sessions/${encodeURIComponent(id)}`)
      show(s)
      return s
    } catch {
      saveSession(null)
      setSessionId(null)
      return null
    }
  }, [show])

  /** Read events from a stream; returns [finished, last seq]. */
  const consume = useCallback(
    async (stream: AsyncGenerator<{ event: string; data: string }>, lastSeq: number): Promise<[boolean, number]> => {
      let seq = lastSeq
      for await (const ev of stream) {
        const d = parse(ev.data)
        if (typeof d.seq === 'number') {
          // Already handled before the stream dropped (one server event may be several SSE
          // events with the same seq, so only earlier streams' events are skipped).
          if (d.seq <= lastSeq) continue
          seq = Math.max(seq, d.seq)
        }
        const type = ev.event !== 'message' ? ev.event : String(d.type ?? '')
        if (handle(type, d)) return [true, seq]
      }
      return [false, seq]
    },
    [handle],
  )

  /** The stream dropped before the turn ended: re-attach, or reload the chat. */
  const recover = useCallback(
    async (lastSeq: number, signal: AbortSignal, since?: number) => {
      let seq = lastSeq
      if (!sessionRef.current && since) {
        // The stream broke before the server named the chat: find the one this turn created.
        try {
          const recent = await api.get<{ id: string; updated_at: string }[]>('/agent/sessions', { limit: 3 })
          const hit = recent.find((r) => Date.parse(r.updated_at) >= since - 5000)
          if (hit) {
            sessionRef.current = hit.id
            setSessionId(hit.id)
            saveSession(hit.id)
          }
        } catch {
          /* offline: fall through to "interrupted" */
        }
      }
      for (let attempt = 0; attempt < 4 && sessionRef.current && !signal.aborted; attempt++) {
        try {
          const [done, s] = await consume(getStream(`/agent/chat/${encodeURIComponent(sessionRef.current)}/stream`, { after: seq }, signal), seq)
          seq = s
          if (done) return
        } catch (e) {
          if ((e as Error).name === 'AbortError') return
          // 404: no run in this process, or not registered yet (the chat row is written a moment
          // before the run is): try again once or twice before reloading the chat.
          if (e instanceof ApiError && e.status === 404 && attempt >= 2) break
        }
        await new Promise((r) => setTimeout(r, 800 * (attempt + 1)))
      }
      if (sessionRef.current && !signal.aborted) await replay(sessionRef.current)
    },
    [consume, replay],
  )

  const runTurn = useCallback(
    async (body: Record<string, unknown>, userText: string | null, cut?: number) => {
      const resuming = body.resume_approval_id != null
      const turn = newAssistantTurn({ resume: resuming })
      setTurns((list) => {
        const base = cut != null ? list.slice(0, cut) : list
        const last = base[base.length - 1]
        // A continuation after an approval joins the last reply.
        if (resuming && last?.role === 'assistant') {
          return [...base.slice(0, -1), { ...last, running: true, status: 'running', error: undefined, startedAt: Date.now() }]
        }
        return [...base, ...(userText !== null ? [{ role: 'user', text: userText } as UserTurn] : []), turn]
      })
      setBusy(true)
      const ac = new AbortController()
      abortRef.current = ac
      let finished = false
      let seq = 0
      try {
        ;[finished, seq] = await consume(postStream('/agent/chat', body, ac.signal), 0)
      } catch (e) {
        if ((e as Error).name === 'AbortError') {
          finished = true
          patchLast((t) => ({ ...t, stopped: true, status: 'stopped' }))
        } else if (e instanceof ApiError && e.status === 409 && sessionRef.current) {
          // The previous turn is still running on the server (this panel lost track of it):
          // drop the new message, show that turn again and follow it, so Stop works.
          finished = true
          setTurns((list) => list.slice(0, list.length - (userText !== null ? 2 : 1)))
          toast({ message: tr('rail.assistant.useAssistant.stillAnsweringYourPreviousMessage') })
          await replay(sessionRef.current)
          setTurns((list) => {
            const copy = list.slice()
            const last = copy[copy.length - 1]
            if (last?.role === 'assistant') copy[copy.length - 1] = newAssistantTurn({ userMessageId: last.userMessageId })
            return copy
          })
          await recover(0, ac.signal)
        } else if (e instanceof ApiError && e.status > 0) {
          finished = true
          patchLast((t) => ({ ...t, running: false, status: 'error', error: e.message }))
          // 403: the access policy turned the assistant off. GET /me again, and the panel shows the message.
          if (e.status === 403) void qcRef.current?.invalidateQueries({ queryKey: ['me'] })
        }
      }
      try {
        if (!finished) await recover(seq, ac.signal, userText !== null && !sessionRef.current ? turn.startedAt : undefined)
      } finally {
        patchLast((t) => (t.running ? { ...t, running: false, status: t.status === 'running' ? 'interrupted' : t.status, seconds: t.seconds ?? (Date.now() - t.startedAt) / 1000 } : t))
        setBusy(false)
        abortRef.current = null
      }
    },
    [consume, recover, replay],
  )

  const run = useCallback(
    (body: Record<string, unknown>, userText: string | null, cut?: number) => {
      if (abortRef.current) return
      const p = runTurn(body, userText, cut)
      flightRef.current = p
      return p
    },
    [runTurn],
  )

  /** A chat whose turn still runs on the server: its last reply is followed from the first event. */
  const follow = useCallback(async () => {
    if (abortRef.current) return
    const ac = new AbortController()
    abortRef.current = ac
    setBusy(true)
    setTurns((list) => {
      const copy = list.slice()
      const last = copy[copy.length - 1]
      if (last?.role === 'assistant') copy[copy.length - 1] = newAssistantTurn({ userMessageId: last.userMessageId })
      return copy
    })
    const p = (async () => {
      try {
        await recover(0, ac.signal)
      } finally {
        // A stream dropped for another chat (open) leaves that chat's turns alone.
        if (abortRef.current === ac) {
          setBusy(false)
          abortRef.current = null
        }
      }
    })()
    flightRef.current = p
    await p
  }, [recover])

  // Re-open the previous chat of this tab (server keeps the history); re-attach if it is running.
  useEffect(() => {
    const id = loadSession()
    if (!id) return
    let alive = true
    void (async () => {
      const s = await replay(id)
      if (!alive || !s?.running || abortRef.current) return
      await follow()
    })()
    return () => {
      alive = false
    }
  }, [replay, follow])

  /**
   * ACT agent.chat_open @web.panel: an earlier chat from Chats, replayed from GET /agent/sessions/{id}
   * (turns, sources, used memories, approval cards) and continued from there; a turn that still runs
   * is followed live. The stream of the chat that was open stops here (its run goes on on the
   * server). False when the chat could not be read; the open chat then stays as it was.
   */
  const open = useCallback(
    async (id: string): Promise<boolean> => {
      let s: SessionReplay
      try {
        s = await api.get<SessionReplay>(`/agent/sessions/${encodeURIComponent(id)}`)
      } catch {
        return false
      }
      abortRef.current?.abort()
      await flightRef.current?.catch(() => undefined)
      sessionRef.current = id
      saveSession(id)
      setSessionId(id)
      show(s)
      if (s.running) void follow()
      return true
    },
    [show, follow],
  )

  useEffect(() => () => abortRef.current?.abort(), [])
  // While a turn that waits for the user runs, the editors report typing (lib/live/useLiveItem.ts).
  useEffect(() => {
    useLive.setState({ holding: busy && useLive.getState().waitForTyping })
  }, [busy])

  // "Ask assistant" from an event or tasks (assistant-context/askAbout.ts): the record goes with
  // every turn while it is attached.
  const ctx = useCallback(() => {
    const ui = uiContext(conversationId)
    // "Let me finish typing first": the assistant's writes to an open editor wait for a pause.
    if (useLive.getState().waitForTyping) ui.wait_for_idle = true
    const ask = askContext()
    if (ask.tasks) ui.tasks = { ...((ui.tasks as Record<string, unknown>) ?? {}), ...ask.tasks }
    return { conversation_id: conversationId, ui, ...ask.top }
  }, [conversationId])

  // Flush-before-ask: every open editor saves first, so the assistant
  // works on what is saved; the question then carries what the editors show (ctx()).
  const asking = useRef(false)
  const afterFlush = useCallback(async (go: () => void) => {
    if (asking.current) return
    asking.current = true
    try {
      await flushOpenEditors()
    } finally {
      asking.current = false
    }
    go()
  }, [])

  // `/local` (agent/commands.py): this chat stays on the local model from the next message on.
  const [localOnly, setLocalOnlyState] = useState(false)
  const localOnlyRef = useRef(false)
  const setLocalOnly = useCallback((on: boolean) => {
    localOnlyRef.current = on
    setLocalOnlyState(on)
  }, [])

  const send = useCallback(
    (text: string) => {
      const t = text.trim()
      if (!t) return
      const local = localOnlyRef.current
      // The context is read once the editors are saved: it says what they show then.
      void afterFlush(() => void run({ message: t, session_id: sessionRef.current, context: ctx(), ...(local ? { local_only: true } : {}) }, t))
    },
    [run, ctx, afterFlush],
  )

  /** A reply the app gives itself (`/help`, a command that needs a chat): shown, not saved. */
  const notice = useCallback((userText: string, text: string) => {
    setTurns((list) => {
      const answer = applyEvent(newAssistantTurn({ running: false, status: 'done' }), 'final', { content: text, segment: 0 })
      const next: Turn[] = [...list, { role: 'user', text: userText }, { ...answer, running: false, status: 'done', notice: true }]
      turnsRef.current = next
      return next
    })
  }, [])

  /** `/retitle`: a new short title from the first question (POST /agent/sessions/{id}/retitle). */
  const retitle = useCallback(async () => {
    const id = sessionRef.current
    if (!id) return null
    const r = await api.post<{ id: string; title: string; renamed: boolean }>(`/agent/sessions/${encodeURIComponent(id)}/retitle`)
    setTitle({ text: r.title, renamed: false })
    qcRef.current?.setQueriesData<ChatSummary[]>({ queryKey: CHATS_KEY }, (list) => withTitle(list, id, r.title, false))
    return r.title
  }, [])

  /** Continue the chat after the user decided an approval it created. */
  const resume = useCallback(
    (approvalId: number) => {
      if (!sessionRef.current) return
      void run({ session_id: sessionRef.current, resume_approval_id: approvalId, context: ctx() }, null)
    },
    [run, ctx],
  )

  /** Regenerate the reply to a user message (replaces it and everything after it). */
  const rerun = useCallback(
    (userMessageId: number) => {
      if (!sessionRef.current || abortRef.current) return
      const i = turnsRef.current.findIndex((t) => t.role === 'user' && t.id === userMessageId)
      if (i < 0) return
      void afterFlush(() => void run({ session_id: sessionRef.current, rerun_from: userMessageId, context: ctx() }, null, i + 1))
    },
    [run, ctx, afterFlush],
  )

  /** Replace a user message and run from there. */
  const editResend = useCallback(
    (userMessageId: number, text: string) => {
      const t = text.trim()
      if (!t || !sessionRef.current || abortRef.current) return
      const i = turnsRef.current.findIndex((x) => x.role === 'user' && x.id === userMessageId)
      if (i < 0) return
      void afterFlush(() => void run({ session_id: sessionRef.current, edit_from: userMessageId, message: t, context: ctx() }, t, i))
    },
    [run, ctx, afterFlush],
  )

  /** Rerun the last reply (the Shift+R shortcut). */
  const rerunLast = useCallback(() => {
    const last = [...turnsRef.current].reverse().find((t): t is UserTurn => t.role === 'user' && t.id != null)
    if (last?.id) rerun(last.id)
  }, [rerun])

  const stop = useCallback(() => {
    const id = sessionRef.current
    if (id) void api.post(`/agent/chat/${encodeURIComponent(id)}/stop`, {}).catch(() => undefined)
    // The server ends the turn with what it has; drop the stream only if it does not.
    setTimeout(() => abortRef.current?.abort(), 8000)
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    // A send right after a reset (the ask capture) must start a new chat, before the next render.
    sessionRef.current = null
    turnsRef.current = []
    saveSession(null)
    setSessionId(null)
    setTitle(null)
    setSkills([])
    setTurns([])
    localOnlyRef.current = false
    setLocalOnlyState(false)
  }, [])

  /** Show a stored chat (a memory's "From a chat" link) as it was, ready to continue. False when it
   *  could not be loaded (deleted, or offline). */
  /** A memory's "From a chat" (ACT memory.open_chat @web.settings): that chat, through the same open
   *  as Chats; nothing to do when it is already the open one. */
  const openSession = useCallback(async (id: string): Promise<boolean> => {
    if (sessionRef.current === id && turnsRef.current.length) return true
    return open(id)
  }, [open])

  const updateApproval = useCallback((id: number, status: string, card?: ApprovalCardData) => {
    setTurns((list) => withApproval(list, id, status, card))
  }, [])

  /** A finished review's card, under the review's progress (once: a reload may already hold it). */
  const addReviewCard = useCallback((reviewId: number, card: ApprovalCardData) => {
    setTurns((list) => {
      if (list.some((t) => t.role === 'assistant' && t.approvals.some((a) => a.id === card.id))) {
        return withApproval(list, card.id, card.status, card)
      }
      return list.map((t) => (t.role === 'assistant' && t.reviews?.includes(reviewId) ? { ...t, approvals: [...t.approvals, cardToRef(card)] } : t))
    })
  }, [])

  /** Undo a configuration change the assistant made (the server sent the request to make). */
  const undoConfig = useCallback(async (turnIndex: number, configIndex: number) => {
    const t = turnsRef.current[turnIndex]
    if (!t || t.role !== 'assistant') return false
    const u = t.configs[configIndex]?.undo
    if (!u || !ALLOWED_UNDO.test(u.path)) return false
    const method = u.method.toUpperCase()
    const body = (u.body ?? undefined) as Record<string, unknown> | undefined
    if (method === 'DELETE') await api.del(u.path)
    else if (method === 'PATCH') await api.patch(u.path, body)
    else if (method === 'PUT') await api.put(u.path, body)
    else await api.post(u.path, body)
    setTurns((list) => list.map((x, i) => (i === turnIndex && x.role === 'assistant' ? { ...x, configs: x.configs.map((c, j) => (j === configIndex ? { ...c, undone: true } : c)) } : x)))
    return true
  }, [])

  return { turns, busy, sessionId, title, setTitle, skills, send, resume, stop, reset, open, openSession, updateApproval, addReviewCard, rerun, rerunLast, editResend, undoConfig, localOnly, setLocalOnly, notice, retitle }
}
