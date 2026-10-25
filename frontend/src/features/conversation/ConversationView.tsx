// ACT mail.untrash, ACT mail.delete_forever: the reader's Move to Inbox and Delete forever in Trash and Spam.
// ACT mail.archive @web.reader, ACT mail.delete @web.reader, ACT mail.spam @web.reader, ACT mail.not_spam @web.reader, ACT mail.unread @web.reader, ACT mail.snooze @web.reader, ACT mail.label @web.reader,
// ACT mail.move @web.reader, ACT mail.mute @web.reader, ACT mail.important @web.reader (the reader toolbar); ACT mail.read @web.reader (opening it);
// ACT mail.show_in_tab @web.reader, ACT mail.category @web.reader (the ⋮ menu's Show in tab and Category submenus, as in the row's menu); Create from email is in CreateFrom.tsx
import { ConversationSkeleton } from './LazyConversationView'
import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { ActionChip } from '../mail/ActionChip'
import { DeadlineChip } from '../mail/DeadlineChip'
import { shownSignals } from '../mail/signalGuard'
import { SMART_NAMES, decisionSource } from './decisionSource'
import {
  AlarmClock, Archive, BellRing, Bot, ArchiveRestore, ArrowLeft, ChevronLeft, ChevronRight, EllipsisVertical, FolderInput, Info, Mail, OctagonAlert,
  AlarmClockOff, Printer, ShieldCheck, Sparkles, Tag, Trash2, VolumeX, Volume2, ExternalLink, Minimize2, ChevronDown, ChevronUp, ChevronsDownUp, ChevronsUpDown, Trash, Filter, LayoutList, Search, Shapes,
  SquarePlus,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { convFromSlug } from '../../lib/pubid'
import { BriefCard, useBrief, useBriefSide } from './BriefCard'
import { startsOpen, summaryShown, type AiCardsInput } from './aiCards'
import { openAsk, useAskTarget } from '../ask/askStore'
import { ConversationLinks } from './ConversationLinks'
import { LinkedChips, LinkPickerPopover, useLinked } from '../notes/LinkedPanel'
import { CreateFromChoices } from './CreateFrom'
import { ScheduleMeetingPicker } from './ScheduleMeeting'
import { useNavigate, useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, asList } from '../../api/client'
import { useToolbarFit } from '../../hooks/useToolbarFit'
import { useChipFit } from '../../hooks/useChipFit'
import { qk, useActions, useConversation, useLabelList, useSettings } from '../../api/hooks'
import type { ComposePayload, Decision, Label, Message, Summary } from '../../api/types'
import { Markdown } from '../rail/assistant/LazyMarkdown'
import type { Citation } from '../rail/assistant/citations'
import { ImportanceMarker, LabelChip, Skeleton, Spinner } from '../../components/bits'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuHeader, MenuItem, MenuSep, Popover, SubMenu, useAnchor } from '../../components/Popover'
import { emptyPayload, useCompose } from '../../stores/compose'
import { focusOpen } from '../../lib/live/editors'
import { toast, toastError, useUI } from '../../stores/ui'
import { inOutbox, sendStateFor, SendStatusLine, useActiveOutbox, useOutboxActions } from '../mail/outbox'
import { mailPath, baseView } from '../../lib/views'
import { findChoices } from '../../lib/convMenus'
import { readerShowsImages } from './MessageBody'
import { initiallyExpanded, NEIGHBOUR_DELAY, readerMessages, usePrefetchConversation } from './prefetch'
import { usePhoneChrome } from '../pwa/PhoneChrome'
import { CategoryChoices, FilterChoices, FindChoices, ShowInTabChoices, useOffersShowInTab } from '../mail/convMenus'
import { FollowUpChoices, FollowUpDialog, FollowUpMenu, LabelMenu, MOVE_TO_FOLDER_HINT, MoveMenu, SnoozeMenu, useFollowUp } from '../mail/menus'
import { FromMessageDialog } from './CalendarBits'
import { formatDateTime } from '../../lib/format'
import { escapeHtml } from '../../lib/html'
import { ComposeForm } from '../compose/LazyComposeForm'
import { ConversationUnavailable } from './ConversationUnavailable'
import { MessageCard, type ReplyMode } from './MessageCard'
import { scrollBehavior } from '../../lib/motion'
import { t as tr } from '../../i18n'
import { useAssistantName } from '../rail/assistant/useAssistantName'

interface Props {
  id: number
  view: string
  q: string
  /** Rendered inside a reading pane (no back button). */
  pane?: boolean
}

interface InlineReply {
  key: number
  payload: ComposePayload
  draftId: number | null
  /** The draft's live version when the payload was read (lib/live). */
  version?: number | null
  /** Waiting for the server ("Opening draft…"): no form yet, so nothing can be typed and lost. */
  loading?: string
  /** The request this slot waits for (openInline). */
  token?: number
}

/** The reader toolbar's fold steps (useToolbarFit): what leaves the bar first for ⋮. */
const FOLD = { first: 1, pager: 1, ask: 2, create: 3, followUp: 4, unread: 5, spam: 6 }

export function ConversationView({ id, view, q, pane }: Props) {
  const assistantName = useAssistantName()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [params] = useSearchParams()
  // "Open in full view" from a reading pane: the same conversation, back beside the list.
  const fullView = params.get('full') === '1'
  const exitFull = useCallback(() => nav(mailPath(view, id, q || undefined)), [nav, view, id, q])
  useEffect(() => {
    if (!fullView) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.key !== 'Escape' || e.defaultPrevented || t?.closest('input, textarea, [contenteditable="true"], [role="dialog"], .popover')) return
      exitFull()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullView, exitFull])
  const focusMsg = Number(params.get('m')) || null
  const hl = params.get('hl')
  const { data, isLoading, error, refetch, fetchStatus } = useConversation(id)
  const labels = useLabelList()
  const labelsById = useMemo(() => new Map(labels.map((l) => [l.id, l])), [labels])
  const { run } = useActions()
  const openCompose = useCompose((s) => s.open)
  const contextLabelId = useUI((s) => s.contextLabelId)
  const settingsNow = useSettings()
  const [briefSide] = useBriefSide()
  const briefQ = useBrief(id)
  const briefBeside = briefSide && !!briefQ.data?.brief?.output
  const visibleIds = useUI((s) => s.visibleIds)
  const setFocused = useUI((s) => s.setFocused)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [showOlder, setShowOlder] = useState(false)
  const [reply, setReply] = useState<InlineReply | null>(null)
  const replyNow = useRef(reply)
  replyNow.current = reply
  /** The key of the inline form the user has changed (typing, recipients): never replaced. */
  const touchedKey = useRef<number | null>(null)
  const loadSeq = useRef(0)
  const convNow = useRef(id)
  convNow.current = id
  const [confirmForever, setConfirmForever] = useState(false)
  const labelMenu = useAnchor()
  const moveMenu = useAnchor()
  const snoozeMenu = useAnchor()
  const followMenu = useAnchor()
  const moreMenu = useAnchor()
  // Create from email: the toolbar's menu, the "Link a note or table" search and the meeting step.
  const createMenu = useAnchor()
  const notesPick = useAnchor()
  const [meetingAt, setMeetingAt] = useState<HTMLElement | null>(null)
  const moreBtn = useRef<HTMLButtonElement>(null)
  // The toolbar folds to fit the pane (useToolbarFit): the pager, then Ask, Create from email,
  // Follow up, Mark as unread and Report spam move into ⋮, in that order (FOLD).
  const headRef = useRef<HTMLDivElement>(null)
  const folded = useToolbarFit(headRef)
  const inMore = (step: number) => `narrow-only${folded >= step ? ' on' : ''}`
  const createBtn = useRef<HTMLButtonElement>(null)
  const linked = useLinked('conversation', { conversation_id: id }, data?.conversation.subject ?? '')
  // Dialogs the ⋮ menu's submenus open (they outlast the menu).
  const [fromMsg, setFromMsg] = useState<{ m: Message; kind: 'task' | 'event' } | null>(null)
  const [followCustom, setFollowCustom] = useState(false)
  const followUp = useFollowUp([id])
  const showInTab = useOffersShowInTab()
  const markedRead = useRef<number | null>(null)
  const labelBtn = useRef<HTMLButtonElement>(null)
  const moveBtn = useRef<HTMLButtonElement>(null)
  const snoozeBtn = useRef<HTMLButtonElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const replyRef = useRef<HTMLDivElement>(null)

  const conv = data?.conversation
  // The subject line's chips: an event's or a task's status gives way before its title is cut.
  const titleRunRef = useRef<HTMLDivElement>(null)
  useChipFit(titleRunRef)
  // Shift+A and the palette's "Ask about this…" mean the open conversation.
  useAskTarget(conv ? { kind: 'conversation', id: conv.id, title: conv.subject } : null)
  const inTrash = baseView(view) === 'trash'
  // Outside Trash/Spam, messages the user deleted stay folded away (Gmail: "N deleted messages").
  const [showDeleted, setShowDeleted] = useState(false)
  const { messages, deletedCount } = useMemo(
    () => readerMessages(data?.messages ?? [], view, labelsById, showDeleted),
    [data, view, showDeleted, labelsById],
  )
  const lastMine = [...messages].reverse().find((m) => m.sent_by_me && !m.draft) ?? null
  // What has not left yet: the send state of each message, and sends from this thread with
  // no draft in it (a reply sent straight from a new composer).
  const outboxRows = useActiveOutbox().data ?? []
  const outboxActs = useOutboxActions()
  const loose = outboxRows.filter((o) => o.conversation_id === id && inOutbox(o) && !messages.some((m) => m.id === o.draft_id))
  const inSpam = baseView(view) === 'spam' || (conv?.label_ids ?? []).some((l) => labelsById.get(l)?.role === 'junk')

  // Expand the last message and every unread one (Gmail behaviour), once per conversation.
  const [initFor, setInitFor] = useState<number | null>(null)
  if (data && initFor !== id) {
    setInitFor(id)
    // The same messages the list prefetched bodies for (prefetch.ts).
    const ex = initiallyExpanded(messages)
    if (focusMsg && messages.some((m) => m.id === focusMsg)) ex.add(focusMsg)
    setExpanded(ex)
    setShowOlder(false)
    setReply(null)
  }

  // Opened from a citation (?m=<message>&hl=<quote>): expand that message and bring it into view.
  // The highlighted passage itself is scrolled to by MessageBody once its body has loaded.
  // Runs once per citation click: later refetches (new mail, sync events) must not pull the
  // view back while the user scrolls.
  const focusedKey = useRef<string | null>(null)
  useEffect(() => {
    if (!focusMsg || !messages.some((m) => m.id === focusMsg)) return
    // A new quote in the message already in view: the body moves the mark and scrolls to it
    // (MessageBody); scrolling to the message's top here would cancel that.
    const sameMessage = focusedKey.current?.split('|')[0] === String(focusMsg)
    const key = `${focusMsg}|${hl ?? ''}`
    if (focusedKey.current === key) return
    focusedKey.current = key
    if (sameMessage && hl) return
    setShowOlder(true)
    setExpanded((prev) => (prev.has(focusMsg) ? prev : new Set(prev).add(focusMsg)))
    const t = setTimeout(() => {
      const el = scrollRef.current?.querySelector<HTMLElement>(`[data-message-id="${focusMsg}"]`)
      el?.scrollIntoView({ block: 'start', behavior: scrollBehavior() })
    }, 60)
    return () => clearTimeout(t)
  }, [focusMsg, hl, messages])

  // ACT mail.read: mark as read once per opening. The first render of a conversation decides; later updates
  // (e.g. the user choosing "Mark as unread" while it is open) must not re-mark it read.
  useEffect(() => {
    if (!conv || markedRead.current === conv.id) return
    markedRead.current = conv.id
    if (conv.unread_count > 0) void run({ ids: [conv.id], action: 'read' }, { silent: true })
  }, [conv, run])

  useEffect(() => {
    setFocused(id)
  }, [id, setFocused])

  // Position within the current list for "N of M" and prev/next.
  const pos = visibleIds.indexOf(id)
  const newerId = pos > 0 ? visibleIds[pos - 1] : null
  const olderId = pos >= 0 && pos < visibleIds.length - 1 ? visibleIds[pos + 1] : null
  // The neighbours in the list are the likeliest next opens (j/k, the pager, auto-advance): prefetch
  // them once this conversation is in. On a phone only the next one down, to spare mobile data.
  const prefetch = usePrefetchConversation(view)
  const phone = usePhoneChrome()
  const loaded = !!data
  useEffect(() => {
    if (!loaded) return
    const t = setTimeout(() => {
      prefetch(olderId)
      if (!phone) prefetch(newerId)
    }, NEIGHBOUR_DELAY)
    return () => clearTimeout(t)
  }, [loaded, olderId, newerId, phone, prefetch])
  const back = () => nav(mailPath(view, null, q || undefined))
  // The conversation no longer exists (its one message was a draft, now sent or deleted): the data
  // from before would keep the deleted draft on screen, so the reader goes back to the list.
  const gone = !!data && error instanceof ApiError && error.status === 404
  useEffect(() => {
    if (gone) back()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the conversation is gone
  }, [gone])
  const go = (cid: number | null) => cid && nav(mailPath(view, cid, q || undefined))
  const autoAdvance = useSettings().auto_advance ?? 'list'
  const afterRemove = () => {
    // In a reading pane the list moves the pane on (MailList, when the action removed it). If
    // nothing did (it was in no loaded list), close it rather than keep showing it.
    if (pane) {
      if (useUI.getState().focusedId === id && window.location.pathname.split('/').some((seg) => convFromSlug(seg) === id)) back()
      return
    }
    // Full page: Gmail's auto-advance: back to the list (default), or on to newer/older.
    const next = autoAdvance === 'older' ? olderId : autoAdvance === 'newer' ? newerId : null
    if (next) go(next)
    else back()
  }

  // Reply keyboard shortcuts dispatch window events handled here.
  useEffect(() => {
    const onReplyEvt = (e: Event) => {
      // A mode, or a mode with the text to start from (the row menu's meeting step: "Reply on the thread").
      const d = (e as CustomEvent<ReplyMode | { mode: ReplyMode; text: string }>).detail
      const mode = typeof d === 'string' ? d : d.mode
      const last = [...messages].reverse().find((m) => !m.draft)
      if (last) void startReply(mode, last, typeof d === 'string' ? undefined : d.text)
    }
    const onMenu = (e: Event) => {
      const which = (e as CustomEvent<string>).detail
      if (which === 'label' && labelBtn.current) labelMenu.show(labelBtn.current)
      if (which === 'move' && moveBtn.current) moveMenu.show(moveBtn.current)
      if (which === 'snooze' && snoozeBtn.current) snoozeMenu.show(snoozeBtn.current)
      if (which === 'create') {
        // At phone width the toolbar's button is hidden: the menu hangs from ⋮.
        const b = createBtn.current
        const at = b && b.getBoundingClientRect().width > 0 ? b : moreBtn.current
        if (at) createMenu.show(at)
      }
    }
    const onMsgNav = (e: Event) => {
      const dir = (e as CustomEvent<string>).detail === 'nextMessage' ? 1 : -1
      const cards = Array.from(scrollRef.current?.querySelectorAll<HTMLElement>('.msg') ?? [])
      if (!cards.length) return
      const active = document.activeElement?.closest('.msg') as HTMLElement | null
      const i = active ? cards.indexOf(active) : dir > 0 ? -1 : cards.length
      const next = cards[Math.max(0, Math.min(cards.length - 1, i + dir))]
      next.setAttribute('tabindex', '-1')
      next.focus()
      next.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() })
    }
    window.addEventListener('mailr:reply', onReplyEvt)
    window.addEventListener('mailr:conv-menu', onMenu)
    window.addEventListener('mailr:message-nav', onMsgNav)
    return () => {
      window.removeEventListener('mailr:reply', onReplyEvt)
      window.removeEventListener('mailr:conv-menu', onMenu)
      window.removeEventListener('mailr:message-nav', onMsgNav)
    }
  })

  /** Opens what `load` answers in the inline slot. A slow answer never replaces a form the user is
   *  using: the slot shows `label` meanwhile (no editor, so nothing typed can be lost), and an answer
   *  that finds the slot taken (the user opened another reply and typed in it) opens in a compose
   *  window instead. An answer for a conversation the user has left is dropped. */
  const openInline = async (label: string, draftId: number | null, load: () => Promise<Omit<InlineReply, 'key'>>, failed: string) => {
    const token = ++loadSeq.current
    const forId = id
    const cur = replyNow.current
    const inUse = !!cur && !cur.loading && touchedKey.current === cur.key
    if (!inUse) setReply({ key: -token, payload: emptyPayload(), draftId, loading: label, token })
    try {
      const got = await load()
      if (convNow.current !== forId) return
      if (!inUse && replyNow.current?.token === token) {
        setReply({ ...got, key: Date.now() })
        setTimeout(() => replyRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' }), 50)
      } else {
        openCompose({ payload: got.payload, draftId: got.draftId, version: got.version ?? null, conversationId: conv?.id ?? null })
      }
    } catch (e) {
      if (replyNow.current?.token === token) setReply(null)
      toastError(e, failed)
    }
  }

  /** Reply, Reply all and Forward: when the thread already has a saved draft (yours, or one
   *  Ambrose wrote, even one waiting for your approval), that draft opens; the toast offers a new
   *  reply instead. Opening a draft that waits for approval withdraws Ambrose's request to send it
   *  (you send it yourself), so the thread never ends with two answers. `fresh`: a new reply. */
  // ACT draft.resume @web.reader
  const startReply = (mode: ReplyMode, m: Message, text?: string, fresh = false) => {
    const saved = fresh ? null : [...messages].reverse().find((x) => x.draft) ?? null
    if (saved) {
      const ambrose = outboxRows.find((o) => o.draft_id === saved.id && o.status === 'awaiting_approval')
      void editDraft(saved)
      toast({
        message: ambrose ? tr('conversation.conversationView.openedTheDraftWroteIt', { name: assistantName }) : tr('conversation.conversationView.openedTheDraftYouAlready'),
        actionLabel: tr('conversation.conversationView.startANewReply'), onAction: () => void startReply(mode, m, text, true), duration: 8000,
      })
      return Promise.resolve()
    }
    return newReply(mode, m, text)
  }

  /** Opens the inline reply; `text` (a reply suggestion) goes first, above the quote. */
  const newReply = (mode: ReplyMode, m: Message, text?: string) =>
    openInline(tr('conversation.conversationView.openingReply'), null, async () => {
      const p = await api.get<{ payload: ComposePayload } | ComposePayload>('/compose/reply', { message_id: m.id, mode, images: readerShowsImages(m.id) ? 'allowed' : 'blocked' })
      let payload = 'payload' in p ? p.payload : p
      if (text) payload = { ...payload, html: `<p>${escapeHtml(text)}</p>${payload.html ?? ''}` }
      return { payload, draftId: null }
    }, tr('conversation.conversationView.couldNotStartReply'))

  const editDraft = async (m: Message) => {
    // A draft Ambrose asked to send, opened to edit: that request is withdrawn first, so an
    // approval later can never send the older text (you send the draft yourself).
    const asked = outboxRows.find((o) => o.draft_id === m.id && o.status === 'awaiting_approval')
    if (asked) await outboxActs.discardQuiet(asked)
    // One window per draft: a window or reply that has it open comes forward instead.
    if (useCompose.getState().windows.some((w) => w.draftId === m.id)) {
      useCompose.getState().open({ draftId: m.id })
      return
    }
    if (focusOpen('draft', m.id)) return
    // Already opening it (a second click while the first waits): the first answer opens it.
    if (replyNow.current?.loading && replyNow.current.draftId === m.id) return
    await openInline(tr('conversation.conversationView.openingDraft'), m.id, async () => {
      const d = await api.get<{ payload: ComposePayload; version?: number | null }>(`/drafts/${m.id}`)
      return { payload: d.payload, draftId: m.id, version: d.version ?? null }
    }, tr('conversation.conversationView.couldNotOpenDraft'))
  }

  if (isLoading) return <ConversationSkeleton />
  // Data already on screen stays there when a refetch fails (offline, a 5xx).
  if (!conv)
    return (
      <div className="conv-view">
        <div className="conv-view-head">
          {!pane && (
            <button type="button" className="icon-btn" aria-label={tr('common.back')} onClick={back}>
              <ArrowLeft />
            </button>
          )}
        </div>
        <ConversationUnavailable id={id} error={error} paused={fetchStatus === 'paused'} onRetry={() => void refetch()} />
      </div>
    )

  const userLabels = conv.label_ids.map((l) => labelsById.get(l)).filter((l): l is Label => !!l && !l.role)
  const deadline = shownSignals(conv, labels).find((x) => x.name === 'deadline') // none in Spam or Trash
  const inInbox = conv.label_ids.some((l) => labelsById.get(l)?.role === 'inbox')

  // Collapse the middle of long threads behind "N older messages".
  const HEAD = 1
  const TAIL = 2
  const collapsedMiddle = !showOlder && messages.length > HEAD + TAIL + 1
  const hiddenCount = collapsedMiddle ? messages.length - HEAD - TAIL : 0
  const shown: (Message | 'older')[] = collapsedMiddle ? [...messages.slice(0, HEAD), 'older', ...messages.slice(-TAIL)] : messages
  const allExpanded = messages.every((m) => expanded.has(m.id))

  // Opened from a label's list (e.g. the Later tab) and still carrying that label: Archive
  // means "done", removing the label as well as Inbox.
  const removesCurrent = settingsNow.archive_removes_current_label ?? true
  const ctxLabel = removesCurrent && contextLabelId && conv.label_ids.includes(contextLabelId) ? labelsById.get(contextLabelId) : undefined
  const act = (action: Parameters<typeof run>[0]['action'], removes = false) =>
    void run({ ids: [conv.id], action, ...(action === 'archive' && ctxLabel ? { from_label_id: ctxLabel.id } : {}) },
      { onDone: removes ? afterRemove : undefined })

  // What decides whether the brief and the summary start folded (aiCards.ts).
  const aiInput: AiCardsInput = {
    messageCount: conv.message_count, hasBrief: !!briefQ.data?.brief?.output,
    newest: [...messages].reverse().find((m) => !m.draft),
  }
  const briefEl = (
    <BriefCard id={conv.id} input={aiInput} onOpenCitation={(c) => {
              const params = new URLSearchParams()
              if (c.message_id) params.set('m', String(c.message_id))
              if (c.quote && c.verified) params.set('hl', c.quote)
              nav(`${mailPath(view, c.conversation_id ?? conv.id, q || undefined)}${q ? '&' : '?'}${params.toString()}`)
            }} />
  )

  return (
    <div className="conv-view">
      <div ref={headRef} className="conv-view-head no-print" role="toolbar" aria-label={tr('common.conversationActions')}>
        {!pane && (
          <button type="button" className="icon-btn" aria-label={tr('conversation.conversationView.backToList')} data-tip={tr('conversation.conversationView.backTo', { value: baseView(view) === 'search' ? tr('common.searchResults') : 'list' })} onClick={back}>
            <ArrowLeft />
          </button>
        )}
        {inTrash ? (
          <>
            <button type="button" className="icon-btn" aria-label={tr('common.moveToInbox')} data-tip={tr('common.moveToInbox')} onClick={() => act('untrash', true)}>
              <ArchiveRestore />
            </button>
            <button type="button" className="icon-btn" aria-label={tr('common.deleteForever')} data-tip={tr('common.deleteForever')} onClick={() => setConfirmForever(true)}>
              <Trash />
            </button>
          </>
        ) : (
          <>
            {inInbox || ctxLabel ? (
              <button type="button" className="icon-btn" aria-label={tr('common.archive')} data-tip={ctxLabel ? tr('conversation.conversationView.archiveRemoveFrom', { ctxLabelName: ctxLabel.name }) : tr('common.archive')} onClick={() => act('archive', baseView(view) === 'inbox' || !!ctxLabel)}>
                <Archive />
              </button>
            ) : null}
            {inInbox ? null : (
              <button type="button" className="icon-btn" aria-label={tr('common.moveToInbox')} data-tip={tr('common.moveToInbox')} onClick={() => act('inbox')}>
                <ArchiveRestore />
              </button>
            )}
            {inSpam ? (
              <button type="button" className="btn text sm" onClick={() => act('not_spam', true)}>
                <ShieldCheck />{' '}{tr('common.notSpam')}
              </button>
            ) : (
              <button type="button" className="icon-btn wide-only" aria-label={tr('common.reportSpam')} data-tip={tr('common.reportSpam')} onClick={() => act('spam', true)} data-fold-hide={FOLD.spam}>
                <OctagonAlert />
              </button>
            )}
            <button type="button" className="icon-btn" aria-label={tr('common.delete2')} data-tip={tr('common.delete2')} onClick={() => act('trash', true)}>
              <Trash2 />
            </button>
          </>
        )}
        <span className="vsep" />
        <button type="button" className="icon-btn wide-only" aria-label={tr('common.markAsUnread')} data-tip={tr('common.markAsUnread')} onClick={() => void run({ ids: [conv.id], action: 'unread' }, { onDone: pane ? undefined : back })} data-fold-hide={FOLD.unread}>
          <Mail />
        </button>
        <button ref={snoozeBtn} type="button" className="icon-btn" aria-label={tr('common.snooze')} data-tip={tr('common.snooze')} onClick={(e) => snoozeMenu.toggle(e.currentTarget)}>
          <AlarmClock />
        </button>
        {conv.snoozed_until && (
          <button type="button" className="icon-btn" aria-label={tr('conversation.conversationView.unsnooze')} data-tip={tr('conversation.conversationView.unsnooze')} onClick={() => act('unsnooze', baseView(view) === 'snoozed')}>
            <AlarmClockOff />
          </button>
        )}
        {/* ACT mail.follow_up @web.reader: offered once the user has written in the conversation. */}
        {lastMine && (
          <button type="button" className={`icon-btn wide-only${conv.follow_up_at ? ' active' : ''}`} aria-label={tr('conversation.conversationView.remindMeIfNoReply')}
            aria-pressed={!!conv.follow_up_at} data-tip={conv.follow_up_at ? tr('conversation.conversationView.followUpReminderSet') : tr('conversation.conversationView.remindMeIfNoReply')}
            onClick={(e) => followMenu.toggle(e.currentTarget)} data-fold-hide={FOLD.followUp}>
            <BellRing />
          </button>
        )}
        <span className="vsep wide-only" data-fold-hide={FOLD.unread} />
        <button ref={moveBtn} type="button" className="icon-btn" aria-label={tr('common.moveToFolder')} data-tip={tr('conversation.conversationView.moveToFolder', { moveToFolderHint: MOVE_TO_FOLDER_HINT })} onClick={(e) => moveMenu.toggle(e.currentTarget)}>
          <FolderInput />
        </button>
        <button ref={labelBtn} type="button" className="icon-btn" aria-label={tr('common.labels')} data-tip={tr('common.labels')} onClick={(e) => labelMenu.toggle(e.currentTarget)}>
          <Tag />
        </button>
        {/* Create from email: Task, Event, Meeting with these people, Note, Checklist, Link to a note or
            table; at phone width the same menu is in ⋮. Shift+T opens it. */}
        <button ref={createBtn} type="button" className={`icon-btn wide-only${createMenu.open || notesPick.open || meetingAt ? ' active' : ''}`} aria-label={tr('conversation.conversationView.createFromEmail')} aria-haspopup="menu"
          aria-expanded={createMenu.open} data-tip={tr('conversation.conversationView.createFromEmailShiftT')} data-testid="reader-create" onClick={(e) => createMenu.toggle(e.currentTarget)} data-fold-hide={FOLD.create}>
          <SquarePlus />
        </button>
        <Popover anchor={createMenu.anchor} open={createMenu.open} onClose={createMenu.close} label={tr('conversation.conversationView.createFromEmail')} minWidth={240}>
          <CreateFromChoices convId={conv.id} online={linked.online} onClose={createMenu.close}
            onFromMessage={(kind, m) => setFromMsg({ m, kind })}
            onMeeting={() => setMeetingAt(createBtn.current && createBtn.current.getBoundingClientRect().width > 0 ? createBtn.current : moreBtn.current)}
            onLink={() => { const at = createBtn.current && createBtn.current.getBoundingClientRect().width > 0 ? createBtn.current : moreBtn.current; if (at) notesPick.show(at) }} />
        </Popover>
        <LinkPickerPopover linked={linked} anchor={notesPick.anchor} open={notesPick.open} onClose={notesPick.close} />
        {/* ACT agent.ask_about @web.reader: the ask capture about this conversation. */}
        <button type="button" className="icon-btn wide-only" aria-label={tr('conversation.conversationView.ask', { name: assistantNameNow() })} data-tip={tr('conversation.conversationView.askShiftA', { name: assistantNameNow() })} data-testid="reader-ask"
          onClick={(e) => openAsk({ kind: 'conversation', id: conv.id, title: conv.subject }, e.currentTarget)} data-fold-hide={FOLD.ask}>
          <Bot />
        </button>
        <button ref={moreBtn} type="button" className="icon-btn" aria-label={tr('common.more')} data-tip={tr('common.more')} onClick={(e) => moreMenu.toggle(e.currentTarget)}>
          <EllipsisVertical />
        </button>
        <Popover anchor={moreMenu.anchor} open={moreMenu.open} onClose={moreMenu.close}>
          {/* Phone width: the toolbar keeps Back, Archive, Delete, Snooze, Move, Labels and ⋮; the rest is here,
              in groups (the same grouping as the Android sheet): the common
              actions first, then Create from email, Organise and More. Menus are bottom sheets there. */}
          {/* A narrow reading pane folds the same buttons in here, one at a time (inMore). */}
          <div className={inMore(FOLD.ask)}>
            <MenuItem icon={<Bot />} onSelect={() => { moreMenu.close(); openAsk({ kind: 'conversation', id: conv.id, title: conv.subject }, moreBtn.current) }}>
              {tr('conversation.conversationView.ask', { name: assistantNameNow() })}
            </MenuItem>
          </div>
          <div className={inMore(FOLD.unread)}>
            <MenuItem icon={<Mail />} onSelect={() => { moreMenu.close(); void run({ ids: [conv.id], action: 'unread' }, { onDone: pane ? undefined : back }) }}>
              {tr('common.markAsUnread')}
            </MenuItem>
          </div>
          {lastMine && (
            <div className={inMore(FOLD.followUp)}>
              <SubMenu icon={<BellRing />} label={tr('conversation.conversationView.remindMeIfNoReply')} minWidth={280}>
                <FollowUpChoices ids={[conv.id]} current={conv.follow_up_at} onClose={moreMenu.close} onCustom={() => { moreMenu.close(); setFollowCustom(true) }} />
              </SubMenu>
            </div>
          )}
          {pos >= 0 && (
            <div className={inMore(FOLD.pager)}>
              <MenuItem icon={<ChevronLeft />} disabled={!newerId} onSelect={() => { moreMenu.close(); go(newerId) }}>
                {tr('conversation.conversationView.newerConversation')}
              </MenuItem>
              <MenuItem icon={<ChevronRight />} disabled={!olderId} onSelect={() => { moreMenu.close(); go(olderId) }}>
                {tr('conversation.conversationView.olderConversation')}
              </MenuItem>
            </div>
          )}
          <div className={inMore(FOLD.create)}>
            <MenuSep />
            <SubMenu icon={<SquarePlus />} label={tr('conversation.conversationView.createFromEmail')} minWidth={260}>
              <CreateFromChoices convId={conv.id} online={linked.online} onClose={moreMenu.close}
                onFromMessage={(kind, m) => setFromMsg({ m, kind })}
                onMeeting={() => setMeetingAt(moreBtn.current)}
                onLink={() => moreBtn.current && notesPick.show(moreBtn.current)} />
            </SubMenu>
          </div>
          <div className={inMore(FOLD.first)}>
            <MenuSep />
            <MenuHeader>{tr('common.organize')}</MenuHeader>
          </div>
          {showInTab && (
            <SubMenu icon={<LayoutList />} label={tr('common.showInTab')} minWidth={220}>
              <ShowInTabChoices items={[conv]} onClose={moreMenu.close} />
            </SubMenu>
          )}
          <SubMenu icon={<Shapes />} label={tr('common.category')} minWidth={200}>
            <CategoryChoices items={[conv]} onClose={moreMenu.close} />
          </SubMenu>
          <div className={inMore(FOLD.first)}>
            <MenuSep />
            <MenuHeader>{tr('common.more')}</MenuHeader>
          </div>
          <MenuItem icon={conv.muted ? <Volume2 /> : <VolumeX />} onSelect={() => { moreMenu.close(); act(conv.muted ? 'unmute' : 'mute', !conv.muted && baseView(view) === 'inbox') }}>
            {conv.muted ? tr('conversation.conversationView.unmute') : tr('common.mute')}
          </MenuItem>
          {!inTrash && !inSpam && (
            <div className={inMore(FOLD.spam)}>
              <MenuItem icon={<OctagonAlert />} onSelect={() => { moreMenu.close(); act('spam', true) }}>
                {tr('common.reportSpam')}
              </MenuItem>
            </div>
          )}
          <div className="narrow-only">
            <MenuItem icon={<Printer />} onSelect={() => { moreMenu.close(); window.print() }}>
              {tr('conversation.conversationView.printAll')}
            </MenuItem>
          </div>
          {findChoices(conv.participants, conv.subject).length > 0 && (
            <>
              <MenuSep />
              <SubMenu icon={<Search />} label={tr('conversation.conversationView.findMail')} minWidth={240}>
                <FindChoices participants={conv.participants} subject={conv.subject} onClose={moreMenu.close} />
              </SubMenu>
              <SubMenu icon={<Filter />} label={tr('conversation.conversationView.filterMailLikeThis')} minWidth={240}>
                <FilterChoices participants={conv.participants} subject={conv.subject} convId={conv.id} onClose={moreMenu.close} />
              </SubMenu>
              <MenuSep />
            </>
          )}
          <MenuItem icon={allExpanded ? <ChevronsDownUp /> : <ChevronsUpDown />} onSelect={() => { moreMenu.close(); setShowOlder(true); setExpanded(allExpanded ? new Set([messages[messages.length - 1].id]) : new Set(messages.map((m) => m.id))) }}>
            {allExpanded ? tr('conversation.conversationView.collapseAll') : tr('conversation.conversationView.expandAll')}
          </MenuItem>
        </Popover>
        <LabelMenu anchor={labelMenu.anchor} open={labelMenu.open} onClose={labelMenu.close} ids={[conv.id]} items={[conv]} />
        <MoveMenu anchor={moveMenu.anchor} open={moveMenu.open} onClose={moveMenu.close} ids={[conv.id]} currentView={view} onDone={afterRemove} />
        <FollowUpMenu anchor={followMenu.anchor} open={followMenu.open} onClose={followMenu.close} ids={[conv.id]} current={conv.follow_up_at} />
        <SnoozeMenu anchor={snoozeMenu.anchor} open={snoozeMenu.open} onClose={snoozeMenu.close} ids={[conv.id]} onDone={afterRemove} />
        <FollowUpDialog open={followCustom} onClose={() => setFollowCustom(false)} onPick={(d) => { setFollowCustom(false); followUp(d) }} />
        {fromMsg && <FromMessageDialog m={fromMsg.m} kind={fromMsg.kind} onClose={() => setFromMsg(null)} />}
        <ScheduleMeetingPicker conv={conv} messages={messages} anchor={meetingAt} onClose={() => setMeetingAt(null)} onReply={(text) => {
          const last = [...messages].reverse().find((x) => !x.draft)
          if (last) void startReply('reply_all', last, text)
        }} />
        <span className="spacer" />
        {pos >= 0 && (
          <span className="conv-pager wide-only" data-fold-hide={FOLD.pager}>
            <span className="range" style={{ fontSize: 12, color: 'var(--text-2)', padding: '0 8px' }}>
              {tr('conversation.conversationView.of', { value: pos + 1, visibleIdsLength: visibleIds.length })}
            </span>
            <button type="button" className="icon-btn" aria-label={tr('common.newer')} data-tip={tr('common.newer')} disabled={!newerId} onClick={() => go(newerId)}>
              <ChevronLeft />
            </button>
            <button type="button" className="icon-btn" aria-label={tr('common.older')} data-tip={tr('common.older')} disabled={!olderId} onClick={() => go(olderId)}>
              <ChevronRight />
            </button>
          </span>
        )}
      </div>

      <div className="conv-scroll" ref={scrollRef}>
        <div className={`conv-inner${briefBeside ? ' with-side' : ''}`}>
          {/* Gmail's header: subject, importance marker and label chips flow as one line of text
              (the subject may wrap); the actions sit on the right, centred on the first line. */}
          <div className="conv-subject">
            <div className="conv-subject-main">
            {/* One run of text: the subject, the importance marker and the labels, then the chips: the
                email's suggested action, the linked events and tasks, and the linked notes and tables. It
                wraps like a sentence, so a short subject keeps everything on one line. */}
            <div className="conv-title-run" ref={titleRunRef}>
            <h1 className="title">
              <span className="subject-text" dir="auto">{conv.subject || tr('common.noSubject')}</span>
              <ImportanceMarker on={conv.important} onToggle={() => void run({ ids: [conv.id], action: conv.important ? 'not_important' : 'important' }, { silent: true })} />
              {userLabels.map((l) => (
                <LabelChip key={l.id} label={l} large onRemove={() => void run({ ids: [conv.id], action: 'remove_labels', label_ids: [l.id] })} />
              ))}
              {inInbox && <span className="label-chip lg">{tr('common.inbox')}</span>}
              {/* ACT mail.dismiss_deadline @web.reader: the Due chip with its "Not a deadline" menu, after the labels on the subject line. */}
              {deadline && <span className="conv-signals no-print"><DeadlineChip cid={conv.id} sig={deadline} /></span>}
            </h1>
            <span className="conv-chips no-print">
              {/* ACT mail.primary_action @web.reader: the email's main action on the subject line, after the labels. */}
              {conv.primary_action && <span className="conv-primary-action"><ActionChip action={conv.primary_action} /></span>}
              <ConversationLinks cid={conv.id} inline />
              {/* ACT note.unlink @web.reader, ACT table.unlink @web.reader: the linked notes and tables as chips, × unlinks with undo. */}
              <LinkedChips linked={linked} as="span" testId="linked-panel" />
            </span>
            </div>
            </div>
            <div className="actions no-print">
              {messages.length > 1 && (
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={allExpanded ? tr('conversation.conversationView.collapseAll') : tr('conversation.conversationView.expandAll')}
                  data-tip={allExpanded ? tr('conversation.conversationView.collapseAll') : tr('conversation.conversationView.expandAll')}
                  onClick={() => {
                    setShowOlder(true)
                    setExpanded(allExpanded ? new Set([messages[messages.length - 1].id]) : new Set(messages.map((m) => m.id)))
                  }}
                >
                  {allExpanded ? <ChevronsDownUp /> : <ChevronsUpDown />}
                </button>
              )}
              <button type="button" className="icon-btn wide-only" aria-label={tr('conversation.conversationView.printAll')} data-tip={tr('conversation.conversationView.printAll')} onClick={() => window.print()}>
                <Printer />
              </button>
              {pane && (
                <button type="button" className="icon-btn" aria-label={tr('conversation.conversationView.openInFullView')} data-tip={tr('conversation.conversationView.openInFullView')} onClick={() => nav(mailPath(view, conv.id, q || undefined) + (q ? '&' : '?') + 'full=1')}>
                  <ExternalLink />
                </button>
              )}
              {fullView && (
                <button type="button" className="icon-btn" aria-label={tr('conversation.conversationView.exitFullView')} data-tip={tr('conversation.conversationView.exitFullViewEsc')} onClick={exitFull}>
                  <Minimize2 />
                </button>
              )}
              <WhyButton messages={messages} labelsById={labelsById} />
            </div>
          </div>

          <div className={briefBeside ? 'conv-split' : 'conv-flow'}>
          <div className="conv-main">
          <div className="ai-cards">
          {!briefBeside && briefEl}
          <SummaryCard id={conv.id} input={aiInput} onOpenCitation={(c) => {
            // Same conversation: stay in this view and jump to the cited sentence.
            const params = new URLSearchParams()
            if (c.message_id) params.set('m', String(c.message_id))
            if (c.quote && c.verified) params.set('hl', c.quote)
            nav(`${mailPath(view, c.conversation_id ?? conv.id, q || undefined)}${q ? '&' : '?'}${params.toString()}`)
          }} />
          </div>

          {conv.nudged_at && lastMine && (
            <div className="nudge-banner" role="status">
              <BellRing size={18} />
              <span>{tr('conversation.conversationView.noReplySinceYouWrote', { dateTime: formatDateTime(lastMine.date) })}</span>
              <button type="button" className="btn tonal sm" onClick={() => void startReply('reply_all', lastMine)}>{tr('conversation.conversationView.writeAFollowUp')}</button>
              <button type="button" className="btn text sm" onClick={() => void run({ ids: [conv.id], action: 'cancel_follow_up' }, { silent: true })}>{tr('common.dismiss')}</button>
            </div>
          )}

          {deletedCount > 0 && (
            <div className="deleted-note">
              {tr('conversation.conversationView.inThisConversation', { value: tr('conversation.conversationView.deletedMessages', { count: deletedCount }) })}{' '}
              <button type="button" className="btn text sm" onClick={() => setShowDeleted(true)}>
                {tr('conversation.conversationView.viewMessages', { count: deletedCount })}
              </button>
            </div>
          )}

          {shown.map((m) =>
            m === 'older' ? (
              <div key="older" className="older-pill" role="button" tabIndex={0} aria-label={tr('conversation.conversationView.showOlderMessages', { count: hiddenCount })} onClick={() => setShowOlder(true)} onKeyDown={(e) => e.key === 'Enter' && setShowOlder(true)}>
                <span>{hiddenCount}</span>
              </div>
            ) : (
              <MessageCard
                key={m.id}
                m={m}
                // A message that has not left (Sending, Not sent, Waiting for approval) stays open, so
                // its Retry or Approve is in view.
                expanded={expanded.has(m.id) || (m.draft && !!sendStateFor(m, outboxRows))}
                onToggle={() => {
                  const next = new Set(expanded)
                  if (next.has(m.id)) next.delete(m.id)
                  else next.add(m.id)
                  setExpanded(next)
                }}
                onReply={(mode, msg, text) => void startReply(mode, msg, text)}
                onEditDraft={(msg) => void editDraft(msg)}
                sendState={sendStateFor(m, outboxRows)}
                labelsById={labelsById}
                inSpam={inSpam}
                isLast={!reply && m === messages[messages.length - 1] && !m.draft}
                highlight={m.id === focusMsg ? hl : null}
              />
            ),
          )}

          {loose.map((o) => (
            <div key={`ob-${o.id}`} className="send-loose" data-testid="send-loose">
              <span className="send-loose-what">{tr('conversation.conversationView.to', { recipients: o.recipients.slice(0, 2).join(', '), value: o.recipients.length > 2 ? ` +${o.recipients.length - 2}` : '', subject: o.subject || tr('common.noSubject') })}</span>
              <SendStatusLine state={{ kind: o.status === 'failed' ? 'not_sent' : 'sending', row: o }} />
            </div>
          ))}

          {reply && (
            <div className="inline-compose" ref={replyRef} data-inline-reply>
              <span style={{ width: 40 }} />
              <div className="box">
                {reply.loading ? (
                  <div className="compose-loading" role="status" aria-live="polite">
                    <Spinner /> {reply.loading}
                  </div>
                ) : (
                <ComposeForm
                  key={reply.key}
                  initial={reply.payload}
                  draftId={reply.draftId}
                  initialVersion={reply.version}
                  conversationId={conv.id}
                  variant="inline"
                  liveKey={`inline-${conv.id}`}
                  autoFocusBody
                  onDraftId={(d) => setReply((r) => (r ? { ...r, draftId: d } : r))}
                  onClose={() => {
                    setReply(null)
                    void qc.invalidateQueries({ queryKey: qk.conversation(conv.id) })
                  }}
                  onPopOut={(payload, draftId, localId) => {
                    setReply(null)
                    openCompose({ payload, draftId, conversationId: conv.id, localId, takeOver: true })
                  }}
                  onTouched={() => {
                    touchedKey.current = reply.key
                  }}
                />
                )}
              </div>
            </div>
          )}
          </div>
          {briefBeside && <aside className="conv-side">{briefEl}</aside>}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmForever}
        title={tr('conversation.conversationView.deleteForever')}
        body={tr('conversation.conversationView.thisConversationWillBePermanently')}
        confirmLabel={tr('common.deleteForever')}
        danger
        onClose={() => setConfirmForever(false)}
        onConfirm={() => {
          setConfirmForever(false)
          void run({ ids: [conv.id], action: 'delete_forever' }, { onDone: afterRemove })
        }}
      />
    </div>
  )
}

function SummaryCard({ id, input, onOpenCitation }: { id: number; input: AiCardsInput; onOpenCitation: (c: Citation) => void }) {
  const [open, setOpen] = useState(() => {
    let stored: boolean | null = null
    try {
      const v = localStorage.getItem('mailr.summaryOpen')
      stored = v === '1' ? true : v === '0' ? false : null
    } catch {
      /* private window: the default */
    }
    return startsOpen(stored, input, 'summary')
  })
  const eligible = summaryShown(input)
  const q = useQuery<Summary>({
    queryKey: qk.summary(id),
    queryFn: () => api.get<Summary>(`/conversations/${id}/summary`),
    enabled: eligible && open,
    staleTime: 5 * 60_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
  })
  if (!eligible) return null
  if (q.isError && (q.error as { status?: number }).status === 404) return null
  const status = q.data?.status
  if (status === 'unavailable') return null
  const toggle = () => {
    setOpen(!open)
    try {
      localStorage.setItem('mailr.summaryOpen', open ? '0' : '1')
    } catch {
      /* ignore */
    }
  }
  return (
    <div className={`summary-card no-print${open ? ' open' : ''}`}>
      <div className="head" role="button" tabIndex={0} aria-expanded={open} onClick={toggle} onKeyDown={(e) => e.key === 'Enter' && toggle()}>
        <Sparkles />
        <span style={{ flex: 1 }}>{tr('common.summary')}</span>
        {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
      </div>
      {open && (
        <div className="body">
          {q.isLoading || status === 'pending' ? (
            <>
              <Skeleton width="90%" />
              <Skeleton width="75%" style={{ marginTop: 8 }} />
            </>
          ) : q.isError ? (
            <span className="muted">{tr('conversation.conversationView.summaryUnavailableRightNow')}</span>
          ) : (
            <SummaryBody s={q.data} onOpenCitation={onOpenCitation} />
          )}
        </div>
      )}
    </div>
  )
}

function SummaryBody({ s, onOpenCitation }: { s: Summary | undefined; onOpenCitation: (c: Citation) => void }) {
  if (!s) return null
  const bullets = Array.isArray(s.summary) ? s.summary : s.summary ? [s.summary] : []
  const cites = s.citations ?? []
  const line = (text: string) => <Markdown text={text} citations={cites} onOpenCitation={onOpenCitation} />
  return (
    <div className="summary-body">
      {bullets.length > 0 && (
        <ul>
          {bullets.map((b, i) => (
            <li key={i}>{line(b)}</li>
          ))}
        </ul>
      )}
      {s.action_items && s.action_items.length > 0 && (
        <>
          <div className="summary-sub">{tr('conversation.conversationView.forYou')}</div>
          <ul>
            {s.action_items.map((b, i) => (
              <li key={i}>{line(b)}</li>
            ))}
          </ul>
        </>
      )}
      {s.dates && s.dates.length > 0 && (
        <>
          <div className="summary-sub">{tr('conversation.conversationView.dates')}</div>
          <ul>
            {s.dates.map((d, i) => (
              <li key={i}>
                <b>{d.when}</b> · {d.what}
              </li>
            ))}
          </ul>
        </>
      )}
      {s.model && <div className="summary-meta">{tr('conversation.conversationView.summarisedBy', { model: s.model, value: s.local_only ? ' (local)' : '' })}</div>}
    </div>
  )
}

interface ExplainTerm {
  term: string
  matches: boolean
  why?: string
}
interface Explain {
  sent_by_me: boolean
  note: string | null
  filters: { id: number; name: string; query: string; enabled: boolean; matches: boolean; terms: ExplainTerm[] }[]
  orders: { id: number; name: string; query: string; enabled: boolean; matches: boolean; terms: ExplainTerm[]
    run: { status: string; answer?: boolean | null } | null }[]
}

/** A decision's state in words. */
function statusWord(status: string): string {
  if (status === 'suggested') return tr('conversation.conversationView.suggested')
  if (status === 'rejected') return tr('conversation.conversationView.dismissed')
  if (status === 'applied') return tr('conversation.conversationView.applied')
  return status
}

/** One search term of a filter in words ("from:(a@x OR b@y)" → "From a@x or b@y"); the raw term
 *  where the words would guess. */
export function termWords(term: string): string {
  const neg = term.startsWith('-')
  const body = neg ? term.slice(1) : term
  const m = /^([a-z]+):(.+)$/i.exec(body)
  const value = (v: string) => v.replace(/^\((.*)\)$/, '$1').replace(/^"(.*)"$/, '$1').replace(/\s+OR\s+/g, tr('conversation.conversationView.or'))
  let out: string
  if (!m) out = tr('conversation.conversationView.termMentions', { value: value(body) })
  else {
    const [, op, v] = m
    const words: Record<string, string> = {
      from: tr('conversation.conversationView.termFrom', { value: value(v) }),
      to: tr('conversation.conversationView.termTo', { value: value(v) }),
      subject: tr('conversation.conversationView.termSubject', { value: value(v) }),
      label: tr('conversation.conversationView.termLabel', { value: value(v) }),
      list: tr('conversation.conversationView.termList', { value: value(v) }),
    }
    out = op.toLowerCase() === 'has' && v === 'attachment' ? tr('conversation.conversationView.termAttachment') : (words[op.toLowerCase()] ?? term)
    if (out === term) return term
  }
  return neg ? tr('conversation.conversationView.termNot', { value: out }) : out
}

/** A standing order's run on this email, in words. */
function runWords(run: { status: string; answer?: boolean | null } | null): string {
  if (!run) return ''
  if (run.status === 'done') return run.answer == null ? tr('conversation.conversationView.runDone') : run.answer ? tr('conversation.conversationView.runYes') : tr('conversation.conversationView.runNo')
  if (run.status === 'failed') return tr('conversation.conversationView.runFailed')
  if (run.status === 'skipped') return tr('conversation.conversationView.runSkipped')
  return tr('conversation.conversationView.runWaiting')
}

function WhyButton({ messages, labelsById }: { messages: Message[]; labelsById: Map<number, Label> }) {
  const pop = useAnchor()
  const last = [...messages].reverse().find((m) => !m.draft && !m.sent_by_me) ?? messages[messages.length - 1]
  const q = useQuery<Decision[]>({
    queryKey: qk.decisions(last?.id ?? 0),
    queryFn: async () => asList<Decision>(await api.get<unknown>(`/messages/${last!.id}/decisions`)),
    enabled: pop.open && !!last,
  })
  // Filters and standing orders judged against this message now, term by term (/messages/{id}/explain).
  const explain = useQuery<Explain>({
    queryKey: ['explain', last?.id ?? 0],
    queryFn: () => api.get<Explain>(`/messages/${last!.id}/explain`),
    enabled: pop.open && !!last,
  })
  const qc = useQueryClient()
  const name = (d: Decision) => {
    if (d.kind === 'label') return labelsById.get(Number(d.target))?.name ?? d.target
    if (d.kind === 'category') return tr('conversation.conversationView.category', { target: d.target })
    if (d.kind === 'importance') return d.value ? tr('common.important') : tr('conversation.conversationView.notImportant')
    if (d.kind === 'smart') return SMART_NAMES[d.target] ?? d.target
    if (d.kind === 'triage') return tr('conversation.conversationView.triage')
    return d.value ? tr('common.spam') : tr('common.notSpam')
  }
  const decide = (d: Decision, verdict: 'accept' | 'reject') =>
    api
      .post(`/decisions/${d.id}/${verdict}`)
      .then(() => {
        void qc.invalidateQueries({ queryKey: qk.decisions(last!.id) })
        void qc.invalidateQueries({ queryKey: qk.conversations })
      })
      .catch((e) => toastError(e))
  if (!last) return null
  return (
    <>
      <button type="button" className="icon-btn" aria-label={tr('conversation.conversationView.whyTheseLabels')} data-tip={tr('conversation.conversationView.whyTheseLabels')} onClick={(e) => pop.toggle(e.currentTarget)}>
        <Info />
      </button>
      <Popover anchor={pop.anchor} open={pop.open} onClose={pop.close} role="dialog" label={tr('conversation.conversationView.whyTheseLabels2')} placement="bottom-end">
        <div className="decisions">
          <h4>{tr('conversation.conversationView.whyThisConversationIsLabelled')}</h4>
          {q.isLoading && <Skeleton width="80%" />}
          {q.data && q.data.length === 0 && <div className="muted">{tr('conversation.conversationView.noAutomaticDecisionsWereMade')}</div>}
          {q.data?.map((d) => (
            <div key={d.id} className="decision">
              <b>{name(d)}</b>
              <span className={`pill ${d.status === 'suggested' ? 'info' : d.status === 'rejected' ? 'err' : 'ok'}`}>{statusWord(d.status)}</span>
              <span className="why">
                {decisionSource(d)}
                {d.confidence !== null ? tr('conversation.conversationView.confident', { round: Math.round(d.confidence * 100) }) : ''}
                {d.reason ? ` · ${d.reason}` : ''}
              </span>
              {d.status === 'suggested' && (
                <span className="row" style={{ gridColumn: '1 / -1' }}>
                  <button type="button" className="btn tonal sm" onClick={() => void decide(d, 'accept')}>
                    {tr('common.apply')}
                  </button>
                  <button type="button" className="btn text sm" onClick={() => void decide(d, 'reject')}>
                    {tr('common.dismiss')}
                  </button>
                </span>
              )}
              {d.status === 'applied' && d.source !== 'user' && d.kind !== 'triage' && (
                <span className="row" style={{ gridColumn: '1 / -1' }}>
                  <button type="button" className="btn text sm" onClick={() => void decide(d, 'reject')}>
                    {tr('conversation.conversationView.thisIsWrong')}
                  </button>
                </span>
              )}
            </div>
          ))}
          {explain.data && (explain.data.filters.length > 0 || explain.data.orders.length > 0) && (() => {
            // What matched, in words; every check with its search terms only behind "Show all checks".
            const checks = [
              ...explain.data.filters.map((f) => ({ ...f, kind: tr('conversation.conversationView.filter'), run: null as Explain['orders'][number]['run'] })),
              ...explain.data.orders.map((o) => ({ ...o, kind: tr('conversation.conversationView.standingOrder') })),
            ]
            const hits = checks.filter((c) => c.enabled && c.matches)
            return (
              <>
                <h4 style={{ marginTop: 12 }}>{tr('conversation.conversationView.filtersAndStandingOrders')}</h4>
                {explain.data.note && <div className="muted why-note">{explain.data.note}</div>}
                {hits.length === 0 && <div className="muted">{tr('conversation.conversationView.nothingMatched')}</div>}
                {hits.map((f) => (
                  <div key={`${f.kind}-${f.id}`} className="decision" data-testid="why-match">
                    <b>{f.kind}: {f.name}</b>
                    <span className="why">
                      {f.terms.filter((t) => t.matches).map((t) => t.why || termWords(t.term)).join(' · ') || tr('conversation.conversationView.everyEmail')}
                      {runWords(f.run)}
                    </span>
                  </div>
                ))}
                {checks.length > hits.length && (
                  <details className="why-all">
                    <summary>{tr('conversation.conversationView.showAllChecks', { count: checks.length })}</summary>
                    {checks.map((f) => (
                      <div key={`${f.kind}-${f.id}`} className="decision">
                        <b>{f.kind}: {f.name}</b>
                        <span className={`pill ${f.matches ? 'ok' : 'info'}`}>{!f.enabled ? tr('common.paused') : f.matches ? tr('conversation.conversationView.matched') : tr('conversation.conversationView.noMatch')}</span>
                        <span className="why">
                          {f.terms.length > 0
                            ? f.terms.map((t, i) => (
                                <span key={i} className={t.matches ? 'term-ok' : 'term-no'} title={t.why}>
                                  {t.matches ? '✓' : '✗'} <code>{t.term}</code>{t.why ? ` (${t.why})` : ''}{' '}
                                </span>
                              ))
                            : <code>{f.query || tr('conversation.conversationView.everyEmail')}</code>}
                        </span>
                      </div>
                    ))}
                  </details>
                )}
              </>
            )
          })()}
        </div>
      </Popover>
    </>
  )
}
