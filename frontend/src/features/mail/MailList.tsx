// ACT mail.star @web.list
import { FOCUS_RESET, focusKey as inboxFocusKey } from './inboxFocus'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ChevronDown, ChevronRight, ChevronUp, Inbox, MailPlus, Search, Tag, Star, AlarmClock, Send, FileText, Clock, Trash2, OctagonAlert, Mails, ChevronLeft, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { conversationsQuery, qk, useActions, useActiveLayout, useConversations, useLabelList, useLayoutCounts } from '../../api/hooks'
import { isSectionedLayout, viewLabelId } from './rowText'
import type { ComposePayload, ConversationDetail, ConversationSummary, InboxLayout, Label, LayoutSection, LayoutTab } from '../../api/types'
import { EmptyState } from '../../components/bits'
import { useCompose } from '../../stores/compose'
import { focusOpen } from '../../lib/live/editors'
import { nextAfterRemove, toastError, useUI } from '../../stores/ui'
import { apiView, baseView, mailPath, viewAccount, viewLabelOf } from '../../lib/views'
import { RowContextMenu, type RowMenuTarget } from './RowContextMenu'
import { useListDrag } from './dragDrop'
import { layoutIcon } from '../../lib/layoutIcons'
import { RowSkeleton, type RowHandlers } from './ConversationRow'
import { ListToolbar, listKey } from './ListToolbar'
import { SectionHead, type SectionTarget } from './SectionHead'
import { SnoozeMenu } from './menus'
import { usePagedList, type PagedList } from './usePagedList'
import { SearchChips } from '../search/SearchChips'
import { SearchGlance, useGlance } from '../rail/SearchGlance'
import { glanceOrder, useSearchResults } from '../search/scope'
import { RecordingsGlance } from '../speakr/MailCard'
import { ScheduledList } from './ScheduledList'
import { OutboxList } from './outbox'
import { InboxTabs } from './InboxTabs'
import { useThemeDef, useThemedSettings } from '../../themes/useUITheme'
import { useTouchList } from '../pwa/useTouchList'
import { logicalSwipe, readSwipes, swipeEffect } from '../../lib/swipe'
import { archiveSlot } from '../../lib/rowArchive'
import { useListPrefs } from '../../themes/listPrefs'
import type { ListEntry, ListPresentation } from '../../themes/types'
import { t as tr, appLocale } from '../../i18n'
import { isRtl } from '../../lib/rtl'
import { useSidebarCounts } from '../layout/sidebarCounts'
import { delayed, FOCUS_DELAY, HOVER_DELAY, usePrefetchConversation } from '../conversation/prefetch'

const EMPTY: Record<string, { icon: ReactNode; title: string; body?: string }> = {
  inbox: { icon: <Inbox />, title: tr('mail.mailList.yourInboxIsEmpty'), body: tr('mail.mailList.newMailWillShowUp') },
  starred: { icon: <Star />, title: tr('mail.mailList.noStarredMessages'), body: tr('mail.mailList.starsLetYouGiveMessages') },
  snoozed: { icon: <AlarmClock />, title: tr('mail.mailList.nothingSnoozed'), body: tr('mail.mailList.snoozedConversationsComeBackTo') },
  important: { icon: <Star />, title: tr('mail.mailList.noImportantConversations') },
  sent: { icon: <Send />, title: tr('mail.mailList.noSentMessages'), body: tr('mail.mailList.sendOneNow') },
  drafts: { icon: <FileText />, title: tr('mail.mailList.youDontHaveAnySaved'), body: tr('mail.mailList.savingADraftAllowsYou') },
  scheduled: { icon: <Clock />, title: tr('mail.mailList.nothingScheduled'), body: tr('mail.mailList.messagesYouScheduleWillBe') },
  trash: { icon: <Trash2 />, title: tr('mail.mailList.noConversationsInTrash'), body: tr('mail.mailList.messagesInTrashAreDeleted') },
  spam: { icon: <OctagonAlert />, title: tr('mail.mailList.hoorayNoSpamHere') },
  all: { icon: <Mails />, title: tr('mail.mailList.noConversations') },
  search: { icon: <Search />, title: tr('mail.mailList.noMessagesMatchedYourSearch'), body: tr('mail.mailList.tryDifferentWordsOrUse') },
}

export function emptyFor(view: string) {
  if (viewLabelOf(view) !== null) return { icon: <Tag />, title: tr('mail.mailList.thereAreNoConversationsWith') }
  if (view.startsWith('category:')) return { icon: <Inbox />, title: tr('mail.mailList.noConversationsInThisCategory') }
  return EMPTY[view] ?? EMPTY.all
}

interface MailListProps {
  view: string
  q: string
  openId: number | null
  narrow: boolean
  /** A reading pane shows the open conversation next to this list. */
  withReader?: boolean
}

export function MailList({ view, q, openId, narrow, withReader = false }: MailListProps) {
  const settings = useThemedSettings()
  const pres = useThemeDef().list
  const prefs = useListPrefs()
  const labels = useLabelList()
  const nav = useNavigate()
  // Office draws its own reader with tabs of open emails; those stay when the list's tab changes.
  const externalReader = !!useThemeDef().externalReader
  const qc = useQueryClient()
  const { run } = useActions()
  const openCompose = useCompose((s) => s.open)
  const selected = useUI((s) => s.selected)
  const setSelected = useUI((s) => s.setSelected)
  const toggleSelected = useUI((s) => s.toggleSelected)
  const focusedId = useUI((s) => s.focusedId)
  const setFocused = useUI((s) => s.setFocused)
  const setVisible = useUI((s) => s.setVisible)
  const [snooze, setSnooze] = useState<{ anchor: HTMLElement; id: number } | null>(null)
  const [scrolled, setScrolled] = useState(false)
  const lastClicked = useRef<number | null>(null)

  const labelsById = useMemo(() => new Map(labels.map((l) => [l.id, l])), [labels])
  // The theme may group and reorder rows (date buckets, bundles, sorted columns). null keeps
  // the server's order with no headings.
  const { sort, quick, collapsed } = prefs
  const bundles = settings.card_bundles ?? 'flat'
  const arrangeWith = useCallback(
    (items: ConversationSummary[], sectioned: boolean): ListEntry[] | null =>
      pres.arrange ? pres.arrange(items, { view, prefs: { sort, quick, collapsed }, labelsById, sectioned, bundles }) : null,
    [pres, view, sort, quick, collapsed, labelsById, bundles],
  )
  // Inside inbox sections the sections already group the mail.
  const arrangeInSection = useCallback((items: ConversationSummary[]) => arrangeWith(items, true), [arrangeWith])
  // The inbox (without a search) is drawn from the active layout: optional tabs, each with one
  // or more sections (core/layouts.py). Every inbox type, built in or custom, is a layout.
  const inboxHome = view === 'inbox' && !q
  const { layout: activeLayout, loading: layoutLoading } = useActiveLayout()
  const lay: InboxLayout | null = inboxHome ? activeLayout : null
  // A list counts as sectioned for the theme's grouping (Cards bundles "when the inbox has no
  // sections") whenever a layout defines it, also a tab of one section. Only plain views (labels,
  // folders, search) and the built-in Default inbox (category tabs) are unsectioned.
  // No mail account and nothing in the inbox (a new member who skipped the setup guide): new mail
  // can never arrive, so the inbox says how to connect an account, with no tabs over nothing.
  const sidebar = useSidebarCounts().data
  const noAccount = inboxHome && !!sidebar && !sidebar.accounts?.length && !sidebar.views?.inbox?.total
  const layoutSectioned = isSectionedLayout(lay)
  const arrange = useCallback((items: ConversationSummary[]) => arrangeWith(items, layoutSectioned), [arrangeWith, layoutSectioned])
  const tabKey = `mailr.inbox.tab.${lay?.id ?? ''}`
  const [tabPick, setTabPick] = useState<Record<string, string>>({})
  const storedTab = (() => {
    try {
      return sessionStorage.getItem(tabKey)
    } catch {
      return null
    }
  })()
  const tabId = tabPick[tabKey] ?? storedTab ?? ''
  const tab: LayoutTab | null = lay ? (lay.tabs.find((t) => t.id === tabId) ?? lay.tabs[0] ?? null) : null
  const setTab = useCallback((t: string) => {
    setTabPick((m) => ({ ...m, [tabKey]: t }))
    try {
      sessionStorage.setItem(tabKey, t)
    } catch {
      /* private mode */
    }
  }, [tabKey])
  const counts = useLayoutCounts(lay)
  // "Show only this section" (a section head's button): the tab then shows that one section as
  // its whole list, paged from the toolbar, until Back or another tab. Kept for the browser
  // session per tab, so opening a conversation and coming back stays in it.
  const focusKey = inboxFocusKey(lay?.id, tab?.id)
  const [focusPick, setFocusPick] = useState<Record<string, string | null>>({})
  useEffect(() => {
    const reset = () => setFocusPick({})
    window.addEventListener(FOCUS_RESET, reset)
    return () => window.removeEventListener(FOCUS_RESET, reset)
  }, [])
  const storedFocus = (() => {
    try {
      return sessionStorage.getItem(focusKey)
    } catch {
      return null
    }
  })()
  const focusId = focusKey in focusPick ? focusPick[focusKey] : storedFocus
  const setFocus = useCallback((secId: string | null) => {
    setFocusPick((m) => ({ ...m, [focusKey]: secId }))
    try {
      if (secId) sessionStorage.setItem(focusKey, secId)
      else sessionStorage.removeItem(focusKey)
    } catch {
      /* private mode */
    }
  }, [focusKey])
  const tabSecs = useMemo(() => tab?.sections ?? [], [tab])
  const focusSec = tabSecs.length > 1 ? (tabSecs.find((x) => x.id === focusId) ?? null) : null
  const secs = useMemo(() => (focusSec ? [focusSec] : tabSecs), [focusSec, tabSecs])
  const single = secs.length === 1
  // The tab's main list (mainIndexOf): paged from its own section head, or from the toolbar when
  // it is the only list on screen.
  const mainIdx = mainIndexOf(secs)
  const mainSec: LayoutSection | null = secs[mainIdx] ?? null
  const main = usePagedList(
    { view: 'inbox', layout: lay?.id, tab: tab?.id, sec: mainSec?.id, limit: settings.page_size },
    !!(lay && tab && mainSec),
    `mailr.pages.layout.${lay?.id}.${tab?.id}${focusSec ? `.${focusSec.id}` : ''}`,
  )
  const plain = usePagedList({ view: apiView(view), q: q || undefined, limit: settings.page_size }, !inboxHome && !((baseView(view) === 'scheduled' || view === 'outbox') && !q),
    `mailr.pages.${view}`)
  // The other tabs' first pages are fetched ahead, so a tab switch shows its rows at once instead of
  // a skeleton (6 Oct 2026). Each tab once, then again only when its own counts change (new mail
  // there), not every tab after every change (14 Oct 2026: one new mail fetched every section of
  // every tab). A tab whose pages went stale otherwise refetches when it opens, over its old rows.
  const pageSize = settings.page_size
  const aheadFor = useRef(new Map<string, string>())
  useEffect(() => {
    if (!lay || lay.tabs.length < 2) return
    for (const t of lay.tabs) {
      if (t.id === tab?.id) continue
      const key = `${lay.id}\u0000${t.id}\u0000${pageSize}`
      const seen = JSON.stringify(counts.data?.[t.id] ?? null)
      if (aheadFor.current.get(key) === seen) continue
      aheadFor.current.set(key, seen)
      const m = mainIndexOf(t.sections)
      t.sections.forEach((sec, i) => {
        const limit = i === m ? pageSize : (sec.limit ?? SECTION_PREVIEW)
        void qc.prefetchQuery({ ...conversationsQuery({ view: 'inbox', layout: lay.id, tab: t.id, sec: sec.id, limit, cursor: null }), staleTime: 30_000 })
      })
    }
  }, [lay, tab?.id, pageSize, counts.data, qc])
  const sectioned = !!lay && !single
  // Some tab has several sections: every tab then shows a section header (see SectionHead).
  const headed = !!lay && lay.tabs.some((t) => t.sections.length > 1)

  // The label this list is about: a label view, or a layout tab whose sections all show one
  // label (e.g. "Later"). Archive in such a list also removes that label.
  const setContextLabel = useUI((s) => s.setContextLabel)
  const contextLabel = (() => {
    if (viewLabelOf(view) !== null && !q) return viewLabelOf(view) || null
    if (!tab) return null
    const bases = new Set(tab.sections.map((x) => x.base))
    const only = bases.size === 1 ? [...bases][0] : ''
    return only.startsWith('label:') ? Number(only.slice(6)) || null : null
  })()
  useEffect(() => setContextLabel(contextLabel), [contextLabel, setContextLabel])

  // Items reported by each section (for keyboard nav + select all)
  const [sectionItems, setSectionItems] = useState<Record<string, ConversationSummary[]>>({})
  const report = useCallback((key: string, items: ConversationSummary[]) => {
    setSectionItems((s) => (s[key] === items ? s : { ...s, [key]: items }))
  }, [])

  // Keep the open conversation where it was opened (see UIState.held).
  const held = useUI((s) => s.held)
  const setHeld = useUI((s) => s.setHeld)
  const listsNow = useRef<Record<string, ConversationSummary[]>>({})
  // Keys name the tab too: two one-section tabs must not share the held row.
  const mainKey = sectioned ? `${tab?.id}:${mainSec?.id}` : lay ? `main:${tab?.id}` : 'plain'
  listsNow.current = sectioned ? sectionItems : { [mainKey]: lay ? main.items : plain.items }
  useEffect(() => {
    const cur = useUI.getState().held
    if (!openId) {
      if (cur) setHeld(null)
      return
    }
    if (cur?.id === openId) return
    for (const [key, items] of Object.entries(listsNow.current)) {
      const index = items.findIndex((c) => c.id === openId)
      if (index >= 0) return setHeld({ id: openId, key, index, c: items[index] })
    }
    setHeld(null)
  }, [openId, setHeld])
  const hold = useCallback(
    (key: string, items: ConversationSummary[]): ConversationSummary[] => {
      if (!held) return items
      const has = items.some((c) => c.id === held.id)
      if (key !== held.key) return has ? items.filter((c) => c.id !== held.id) : items
      if (has) return items
      // held.c follows the optimistic patches (read, star, labels), so the row shows its state.
      const out = [...items]
      out.splice(Math.min(held.index, out.length), 0, held.c)
      return out
    },
    [held],
  )
  const plainHeld = useMemo(() => ({ ...plain, items: hold('plain', plain.items) }), [plain, hold])
  const mainHeld = useMemo(() => ({ ...main, items: hold(mainKey, main.items) }), [main, hold, mainKey])

  const plainEntries = useMemo(() => arrange(plainHeld.items), [arrange, plainHeld.items])
  const mainEntries = useMemo(() => arrange(mainHeld.items), [arrange, mainHeld.items])
  const allItems = useMemo(() => {
    if (!lay) return plainEntries ? rowsOf(plainEntries) : plainHeld.items
    if (!sectioned) return mainEntries ? rowsOf(mainEntries) : mainHeld.items
    const seen = new Set<number>()
    const out: ConversationSummary[] = []
    for (const sec of secs) for (const c of sectionItems[`${tab?.id}:${sec.id}`] ?? []) if (!seen.has(c.id)) { seen.add(c.id); out.push(c) }
    return out
  }, [lay, sectioned, secs, tab?.id, plainHeld.items, mainHeld.items, sectionItems, plainEntries, mainEntries])

  useEffect(() => {
    const meta: Record<number, { unread: boolean; starred: boolean }> = {}
    for (const c of allItems) meta[c.id] = { unread: c.unread_count > 0, starred: c.starred }
    setVisible(allItems.map((c) => c.id), meta)
  }, [allItems, setVisible])

  // A selected conversation that leaves this list without an action of ours (moved on another
  // device, by the assistant, by a filter) leaves the selection too: an action on the selection
  // must never reach a conversation the user can no longer see. Same list and page only, and
  // only when no list on screen still holds it (a folded section keeps its rows).
  const shownBefore = useRef<{ key: string; ids: Set<number> } | null>(null)
  const pageNow = (lay ? main : plain).page
  useEffect(() => {
    const key = JSON.stringify([view, q, tab?.id ?? '', pageNow])
    const ids = new Set(allItems.map((c) => c.id))
    const prev = shownBefore.current
    shownBefore.current = { key, ids }
    const st = useUI.getState()
    if (!prev || prev.key !== key || st.allMatching || !st.selected.size) return
    const vanished = [...prev.ids].filter((id) => !ids.has(id) && st.selected.has(id))
    if (!vanished.length) return
    const onScreen = new Set<number>()
    for (const query of qc.getQueryCache().findAll({ queryKey: qk.conversations, type: 'active' }))
      for (const c of (query.state.data as { items?: ConversationSummary[] } | undefined)?.items ?? []) onScreen.add(c.id)
    st.dropFromSelection(vanished.filter((id) => !onScreen.has(id)))
  }, [allItems, view, q, tab?.id, pageNow, qc])

  // With a reading pane, the pane never keeps showing a conversation that left the list (delete,
  // archive, move… from any button, row action or shortcut): open the next one, else nothing.
  const setPaneVisible = useUI((s) => s.setPaneVisible)
  useEffect(() => {
    setPaneVisible(withReader)
    return () => setPaneVisible(false)
  }, [withReader, setPaneVisible])
  const removed = useUI((s) => s.removed)
  const handledSeq = useRef(removed?.seq ?? 0)
  useEffect(() => {
    if (!removed || removed.seq === handledSeq.current) return
    handledSeq.current = removed.seq
    if (!withReader || !openId || !removed.ids.includes(openId)) return
    const next = nextAfterRemove(removed.order, openId, removed.ids, settings.auto_advance ?? 'list', true)
    nav(mailPath(view, next, q || undefined))
  }, [removed, withReader, openId, settings.auto_advance, nav, view, q])

  // Clear selection when the view changes. Not on mount: coming back from a conversation
  // (u / Back) must keep the keyboard cursor on the conversation that was open.
  // Compare with the last list shown, not a "mounted" flag: React's strict mode runs a new
  // component's effects twice, and the second run cleared the cursor on the way back.
  const listKeyRef = useRef<string | null>(null)
  useEffect(() => {
    const k = JSON.stringify([view, q, tab?.id ?? null])
    const prev = listKeyRef.current
    listKeyRef.current = k
    if (prev === null || prev === k) return
    setSelected(new Set())
    useUI.getState().setAllMatching(null)
    setFocused(null)
  }, [view, q, tab?.id, setSelected, setFocused])

  // While "Select all N" is on, every row on the page shows as selected unless left out
  // (also on later pages).
  const allMatching = useUI((s) => s.allMatching)
  const allMatchingQuery = useUI((s) => s.allMatchingQuery)
  const excluded = useUI((s) => s.excluded)
  // All of one section (its header, or the toolbar's "Select all N" of the main section): only
  // that section's rows show as selected.
  const matchSec = sectioned && allMatchingQuery?.sec && allMatchingQuery.tab === tab?.id ? allMatchingQuery.sec : null
  const matchItems = matchSec ? sectionItems[`${tab?.id}:${matchSec}`] : null
  useEffect(() => {
    if (!allMatching) return
    setSelected(new Set((matchItems ?? allItems).map((c) => c.id).filter((id) => !excluded.has(id))))
  }, [allMatching, excluded, allItems, matchItems, setSelected])
  const matchIds = useRef<Set<number> | null>(null)
  matchIds.current = matchItems ? new Set(matchItems.map((c) => c.id)) : null

  // * x: select all in the section of the focused conversation (or the first section).
  const sectionsRef = useRef({ secs, tabId: tab?.id, layId: lay?.id, sectionItems, main, mainId: mainSec?.id })
  sectionsRef.current = { secs, tabId: tab?.id, layId: lay?.id, sectionItems, main, mainId: mainSec?.id }
  useEffect(() => {
    const onSelectSection = () => {
      const { secs: ss, tabId, layId, sectionItems: si, main: m, mainId } = sectionsRef.current
      if (!layId || !tabId || !ss.length) return
      const fid = useUI.getState().focusedId
      const one = ss.length === 1
      const rowsOf = (id: string) => (one ? m.items : si[`${tabId}:${id}`] ?? [])
      const sec = ss.find((x) => fid != null && rowsOf(x.id).some((c) => c.id === fid)) ?? ss[0]
      const rows = rowsOf(sec.id)
      const total = one || sec.id === mainId ? m.total
        : (qc.getQueryData<{ total: number }>(qk.conversationList({ view: 'inbox', layout: layId, tab: tabId, sec: sec.id, limit: sec.limit ?? SECTION_PREVIEW, cursor: null }))?.total ?? rows.length)
      const lq = { view: 'inbox', q: '', total: Math.max(total, rows.length), layout: layId, tab: tabId, sec: sec.id, name: sec.title }
      useUI.getState().setAllMatching(listKey(lq), lq)
      setSelected(new Set(rows.map((c) => c.id)))
    }
    window.addEventListener('mailr:select-section', onSelectSection)
    return () => window.removeEventListener('mailr:select-section', onSelectSection)
  }, [qc, setSelected])

  const openConversation = useCallback(
    async (c: ConversationSummary) => {
      setFocused(c.id)
      lastClicked.current = c.id
      // A conversation made only of drafts opens in the compose window.
      if (c.draft_count > 0 && c.message_count === 0) {
        try {
          const det = await qc.fetchQuery({ queryKey: qk.conversation(c.id), queryFn: () => api.get<ConversationDetail>(`/conversations/${c.id}`) })
          const draft = det.messages.filter((m) => m.draft).pop()
          if (draft) {
            // One window per draft: one already open comes forward (stores/compose.ts open()).
            if (useCompose.getState().windows.some((w) => w.draftId === draft.id) || focusOpen('draft', draft.id)) {
              openCompose({ draftId: draft.id })
              return
            }
            const d = await api.get<{ payload: ComposePayload; version?: number | null }>(`/drafts/${draft.id}`)
            openCompose({ draftId: draft.id, payload: d.payload, version: d.version ?? null, conversationId: c.id })
            return
          }
        } catch (e) {
          toastError(e, tr('mail.mailList.couldNotOpenDraft'))
          return
        }
      }
      nav(mailPath(view, c.id, q || undefined))
    },
    [nav, view, q, qc, openCompose, setFocused],
  )

  const handlers: RowHandlers = useMemo(
    () => ({
      onOpen: (c) => void openConversation(c),
      onToggleSelect: (id, shift) => {
        const st = useUI.getState()
        const ids = st.visibleIds
        if (shift && lastClicked.current !== null) {
          const a = ids.indexOf(lastClicked.current)
          const b = ids.indexOf(id)
          if (a >= 0 && b >= 0) {
            // As in desktop mail: the clicked row's new state applies to the whole range, so a
            // Shift-click can also clear a range. After "Select all N" the selection is "all but the
            // excluded", so the range changes the exclusions (else the selection is rebuilt at once).
            const [lo, hi] = a < b ? [a, b] : [b, a]
            const isSel = (x: number) => (st.allMatching ? !st.excluded.has(x) : st.selected.has(x))
            const target = !isSel(id)
            const range = ids.slice(lo, hi + 1)
            if (st.allMatching) {
              for (const x of range) if (isSel(x) !== target) st.toggleExcluded(x)
            } else {
              const next = new Set(st.selected)
              for (const x of range) {
                if (target) next.add(x)
                else next.delete(x)
              }
              setSelected(next)
            }
            lastClicked.current = id
            return
          }
        }
        lastClicked.current = id
        // A row outside the section that is all selected: the selection becomes the rows it
        // shows plus this one (all of the section no longer applies).
        if (useUI.getState().allMatching && matchIds.current && !matchIds.current.has(id)) {
          const next = new Set(useUI.getState().selected)
          next.add(id)
          useUI.getState().setAllMatching(null)
          setSelected(next)
          return
        }
        // After "Select all N", unchecking a row leaves it out instead of shrinking the
        // selection to this page.
        if (useUI.getState().allMatching) {
          useUI.getState().toggleExcluded(id)
          return
        }
        toggleSelected(id)
      },
      onAction: (c, action) => {
        const silent = action === 'star' || action === 'unstar' || action === 'important' || action === 'not_important'
        // In a label's list (e.g. the Later tab) Archive also removes that label.
        const from = action === 'archive' ? useUI.getState().contextLabelId : null
        void run({ ids: [c.id], action, ...(from ? { from_label_id: from } : {}) }, { silent })
      },
      onSnooze: (c, anchor) => setSnooze({ anchor, id: c.id }),
    }),
    [openConversation, run, setSelected, toggleSelected],
  )

  // Desktop selection on the rows themselves (every theme): Ctrl/⌘-click toggles a row, Shift-click
  // selects the range from the last clicked or open row. When nothing is selected yet, the open
  // conversation joins the selection, as in desktop mail apps. Buttons inside rows keep their
  // own behaviour.
  const onRowClickCapture = (e: React.MouseEvent) => {
    if (!(e.shiftKey || e.ctrlKey || e.metaKey)) return
    const t = e.target as HTMLElement
    const row = t.closest<HTMLElement>('[data-id]')
    if (!row || t.closest('button, a, input, label')) return
    e.preventDefault()
    e.stopPropagation()
    const id = Number(row.dataset.id)
    const st = useUI.getState()
    if (e.shiftKey) {
      if (lastClicked.current === null && openId) lastClicked.current = openId
      handlers.onToggleSelect(id, true)
      return
    }
    if (st.selected.size === 0 && openId && openId !== id && st.visibleIds.includes(openId)) st.toggleSelected(openId)
    handlers.onToggleSelect(id, false)
  }
  // Prefetch (features/conversation/prefetch.ts): a row the mouse rests on, or the keyboard cursor
  // (j/k) stops at, is fetched with its expanded bodies, so opening it draws at once. Touch and
  // pen never hover-prefetch: a tap opens the row straight away anyway.
  const prefetch = usePrefetchConversation(view)
  const hoverTimer = useMemo(() => delayed((id) => prefetch(id)), [prefetch])
  useEffect(() => () => hoverTimer.cancel(), [hoverTimer])
  const onRowPointerOver = (e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    const row = (e.target as HTMLElement).closest<HTMLElement>('[data-id]')
    const id = row ? Number(row.dataset.id) : NaN
    if (id && id !== openId) hoverTimer.schedule(id, HOVER_DELAY)
    else hoverTimer.cancel()
  }
  const cursorId = focusedId ?? null
  useEffect(() => {
    if (!cursorId || cursorId === openId) return
    const t = setTimeout(() => prefetch(cursorId), FOCUS_DELAY)
    return () => clearTimeout(t)
  }, [cursorId, openId, prefetch])

  // Right-click (or, on a touch screen, press and hold): the app's menu for the row (or for the
  // selection it belongs to).
  const [rowMenu, setRowMenu] = useState<RowMenuTarget | null>(null)
  const openRowMenu = (id: number, x: number, y: number) => {
    const sel = useUI.getState().selected
    const ids = sel.has(id) ? [...sel] : [id]
    const byId = new Map(allItems.map((c) => [c.id, c]))
    const items = ids.map((v) => byId.get(v)).filter((c): c is ConversationSummary => !!c)
    if (items.length) setRowMenu({ x, y, items })
  }
  const onRowContextMenu = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement
    const row = t.closest<HTMLElement>('[data-id]')
    if (!row || e.shiftKey) return // Shift+right-click keeps the browser's own menu
    e.preventDefault()
    openRowMenu(Number(row.dataset.id), e.clientX, e.clientY)
  }

  // Touch screens: swipe a row for its swipe action (Settings → Mail → Reading), hold it for its menu,
  // pull the list down to refresh (features/pwa/useTouchList.ts).
  const scrollRef = useRef<HTMLDivElement>(null)
  const itemsRef = useRef(allItems)
  itemsRef.current = allItems
  const swipeView = q ? 'search' : view
  // The archive swipe follows the row (lib/rowArchive.ts): Move to Inbox on a row not in the Inbox.
  const swipeOf = (c: ConversationSummary, dir: Parameters<typeof logicalSwipe>[0]) =>
    swipeEffect(readSwipes()[logicalSwipe(dir, isRtl())], c, swipeView, archiveSlot([c], swipeView, labelsById, {
      contextLabelId: useUI.getState().contextLabelId, removesCurrent: settings.archive_removes_current_label ?? true,
    }))
  useTouchList(scrollRef, {
    enabled: true,
    swipeFor: (id, dir) => {
      const c = itemsRef.current.find((x) => x.id === id)
      const e = c ? swipeOf(c, dir) : null
      return e?.action ? { label: e.label, tone: e.tone, removes: e.removes } : null
    },
    onSwipe: (id, dir, row) => {
      const c = itemsRef.current.find((x) => x.id === id)
      if (!c) return
      const e = swipeOf(c, dir)
      if (e.action === 'snooze') setSnooze({ anchor: row, id })
      else if (e.action) {
        // ACT mail.archive @web.list, ACT mail.delete @web.list: the swipe runs the same action as the row's buttons.
        const from = e.action === 'archive' ? useUI.getState().contextLabelId : null
        void run({ ids: [id], action: e.action, ...(from ? { from_label_id: from } : {}) }, { silent: e.action === 'star' || e.action === 'unstar' })
      }
    },
    onLongPress: openRowMenu,
    onRefresh: () =>
      Promise.all([
        qc.refetchQueries({ queryKey: qk.conversations, type: 'active' }),
        qc.invalidateQueries({ queryKey: qk.labels }),
        qc.invalidateQueries({ queryKey: qk.layouts }),
      ]),
  })

  // Drag rows (or the selection) onto the nav's folders and labels.
  const drag = useListDrag(view, q, openId, labels)

  const refetch = () => {
    void qc.invalidateQueries({ queryKey: qk.conversations })
    void qc.invalidateQueries({ queryKey: qk.labels })
    void qc.invalidateQueries({ queryKey: qk.layouts })
  }
  // With several sections on screen, the main section pages from its own head (Section `pager`):
  // a toolbar pager there would change rows that may be out of view.
  const pager = lay ? (sectioned ? null : main) : plain
  const refreshing = !pager ? false : pager.query.isFetching && !pager.query.isLoading

  const rowProps = {
    labelsById,
    view,
    markers: settings.importance_markers,
    showChips: settings.density !== 'compact',
    narrow,
    hideLabelId: viewLabelId(view, contextLabel, tab, lay?.tabs.length ?? 0, labels),
    selected,
    focusedId: focusedId ?? openId,
    handlers,
    pres,
    draggable: drag.canDrag,
  }

  // The search's scope: Mail shows mail only; All adds the Events, Tasks and Notes
  // groups, in the order of the app searched from. When only those groups have hits, the mail part
  // says so in one quiet line and the mail-only chips step aside.
  const searchScope = useSearchResults()
  const withGroups = !!q && !(view === 'search' && searchScope.scope === 'mail')
  const glance = useGlance(q || '', withGroups)
  const mailNone = !lay && !plain.loading && !plain.waiting && !plain.error && plain.items.length === 0
  const onlyGroups = withGroups && glance.total > 0 && mailNone
  // A Mail-only search that finds nothing asks the other apps too, and offers their matches: the
  // scope chip is small, and "nothing matched" read as "it doesn't exist".
  const mailScoped = !!q && view === 'search' && searchScope.scope === 'mail'
  const elsewhere = useGlance(q || '', mailScoped && mailNone).total
  const empty: EmptySpec = onlyGroups
    ? { icon: null, title: tr('mail.mailList.noMailMatches'), quiet: true }
    : mailScoped && elsewhere > 0
      ? {
          ...emptyFor('search'),
          body: tr('mail.mailList.otherAppsHaveMatches'),
          action: <button type="button" className="btn tonal" data-testid="search-all-apps" onClick={() => searchScope.set({ scope: 'all' })}>{tr('mail.mailList.showMatchesFromAllApps')}</button>,
        }
      : emptyFor(q ? 'search' : baseView(view))
  const layoutTabs = lay && lay.tabs.length > 1 && !noAccount && (
    <InboxTabs layout={lay} activeId={tab?.id} counts={counts.data} capacity={pres.tabIndicators ?? 'full'} narrow={narrow} onSelect={(t) => {
      // Another tab is another list: the open conversation closes (Office keeps its reader tabs).
      if (t.id !== tab?.id && openId && !externalReader) nav(mailPath('inbox'))
      // The open tab clicked again: back to all its sections.
      if (t.id === tab?.id && focusSec) setFocus(null)
      setTab(t.id)
    }} />
  )

  const listQuery = useMemo(
    () => lay && tab && mainSec
      ? { view: 'inbox', q: '', total: main.total, layout: lay.id, tab: tab.id, sec: mainSec.id,
          name: secs.length > 1 ? `${tab.name}: ${mainSec.title}` : tab.name }
      : lay
        ? null
        : { view: apiView(view), q: q || '', total: plain.total },
    [lay, tab, mainSec, main.total, secs.length, view, q, plain.total],
  )
  // What the assistant takes for "this view" (backend agent/screen.py, view_tools.py): on an inbox tab the
  // whole tab, every section of it (layout and tab, no section), or the one section shown alone; a plain
  // list as the toolbar has it. The tab's total comes from its "all" count when the tab bar has one, else
  // from a one-row list of the whole tab.
  const multi = !!(lay && tab && !focusSec && tabSecs.length > 1)
  const countedAll = tab ? counts.data?.[tab.id]?.all : undefined
  const tabList = useConversations({ view: 'inbox', layout: lay?.id, tab: tab?.id, limit: 1 }, multi && countedAll == null)
  const tabTotal = countedAll ?? tabList.data?.total
  const assistantList = useMemo(
    () => lay && tab
      ? focusSec
        ? { view: 'inbox', q: '', total: main.total, layout: lay.id, tab: tab.id, sec: focusSec.id, name: `${tab.name}: ${focusSec.title}` }
        : multi
          ? { view: 'inbox', q: '', total: tabTotal, layout: lay.id, tab: tab.id, name: `${tab.name} tab (all sections)` } // i18n-ignore: the scope's name for the assistant (backend view_tools.describe_query)
          : { view: 'inbox', q: '', total: main.total, layout: lay.id, tab: tab.id, name: `${tab.name} tab` } // i18n-ignore: the scope's name for the assistant
      : listQuery,
    [lay, tab, focusSec, multi, main.total, tabTotal, listQuery],
  )
  const setListQuery = useUI((st) => st.setListQuery)
  useEffect(() => {
    setListQuery(assistantList)
    return () => setListQuery(null)
  }, [assistantList, setListQuery])

  return (
    <div className={`list-pane${narrow ? ' narrow' : ''}`} data-mail-list>
      {pres.tabsPlacement === 'top' && layoutTabs}
      <ListToolbar
        view={view}
        items={allItems}
        onRefresh={refetch}
        refreshing={refreshing}
        pager={pager}
        pagerLabel={focusSec ? focusSec.title : undefined}
        scrolled={scrolled}
        listQuery={listQuery}
      />
      {q && !onlyGroups && <SearchChips q={q} view={view} />}
      {withGroups && <SearchGlance q={q} order={glanceOrder(view === 'search' ? searchScope.from : 'mail')} />}
      {q && <RecordingsGlance q={q} />}
      <div
        ref={scrollRef}
        className="list-scroll"
        onScroll={(e) => setScrolled((e.target as HTMLElement).scrollTop > 0)}
        onClickCapture={onRowClickCapture}
        onContextMenu={onRowContextMenu}
        onPointerOver={onRowPointerOver}
        onPointerLeave={() => hoverTimer.cancel()}
        {...drag.props}
      >
        {pres.tabsPlacement === 'inside' && layoutTabs}
        {/* Each list inside (a page, or one inbox section) is its own grid of rows (RowGrid);
            section heads, skeletons and empty states sit between them. */}
        <div className="list-grid">
        {pres.ListHeader && <pres.ListHeader view={view} narrow={narrow} />}
        {noAccount ? (
          <NoMailAccount />
        ) : inboxHome && !lay && layoutLoading ? (
          <RowSkeleton count={12} delay={SKELETON_DELAY} />
        ) : lay && tab && single ? (
          <>
          {/* Tabs of one section show its name like the multi-section tabs do, so flicking
              between tabs keeps the same top; there is nothing to collapse. */}
          {headed && <SectionHead title={secs[0].title} total={main.total} target={{ layout: lay.id, tab: tab.id, sec: secs[0].id }}
            items={mainHeld.items} loading={main.loading} capped={main.capped} onBack={focusSec ? () => setFocus(null) : undefined} backLabel={tab.name} />}
          <PlainList list={mainHeld} entries={mainEntries} rowProps={rowProps} label={tab.name} expected={counts.data?.[tab.id]?.all} empty={tab.id === lay.tabs[0]?.id ? empty : { icon: layoutIcon(tab.icon), title: tr('mail.mailList.nothingIn', { tabName: tab.name }) }} />
          </>
        ) : lay && tab ? (
          <>
            {secs.map((sec, i) => {
              const isMain = i === mainIdx
              return (
                <Section
                  key={`${lay.id}:${tab.id}:${sec.id}`}
                  sectionKey={`${tab.id}:${sec.id}`}
                  target={{ layout: lay.id, tab: tab.id, sec: sec.id }}
                  storageKey={`${lay.id}.${tab.id}.${sec.id}`}
                  title={sec.title}
                  allMail={sec.base.startsWith('label:') && !sec.inbox_only}
                  params={{ view: 'inbox', layout: lay.id, tab: tab.id, sec: sec.id, limit: isMain ? settings.page_size : (sec.limit ?? SECTION_PREVIEW) }}
                  list={isMain ? main : undefined}
                  paged={isMain}
                  pageSize={settings.page_size}
                  hold={hold}
                  onFocus={() => setFocus(sec.id)}
                  onItems={report}
                  rowProps={rowProps}
                  arrange={arrangeInSection}
                  emptyText={emptyTextFor(sec, isMain, i)}
                  // ACT layout.hide_empty @web.list: the layout leaves out sections with nothing in them;
                  // the main section stays, so an empty tab still says so.
                  hideEmpty={!!lay.hide_empty && !isMain}
                />
              )
            })}
          </>
        ) : baseView(view) === 'scheduled' && !q ? (
          <ScheduledList account={viewAccount(view)} />
        ) : view === 'outbox' && !q ? (
          <OutboxList />
        ) : (
          <PlainList list={plainHeld} entries={plainEntries} rowProps={rowProps} empty={empty} />
        )}
        </div>
      </div>
      <SnoozeMenu anchor={snooze?.anchor ?? null} open={!!snooze} onClose={() => setSnooze(null)} ids={snooze ? [snooze.id] : []} />
      <RowContextMenu target={rowMenu} view={view} q={q} onClose={() => setRowMenu(null)} />
    </div>
  )
}

/** The inbox of someone with no mail account yet: the way to connect one. */
function NoMailAccount() {
  return (
    <div data-testid="inbox-no-account">
      <EmptyState icon={<MailPlus />} title={tr('mail.mailList.noMailAccountConnectedYet')}>
        <p>{tr('mail.mailList.connectTheEmailAccountYou')}</p>
        <Link className="btn filled" to="/settings/mail/accounts?add=1">{tr('mail.mailList.connectAnEmailAccount')}</Link>
      </EmptyState>
    </div>
  )
}

function emptyTextFor(sec: LayoutSection, isMain: boolean, index: number): string {
  if (isMain && sec.id === 'everything') return tr('mail.mailList.woohooYouveReadAllThe')
  if (isMain && index > 0 && sec.exclude_above) return tr('mail.mailList.nothingElseHere')
  if (isMain && sec.base === 'inbox' && !sec.filter && !sec.q) return tr('mail.mailList.yourInboxIsEmpty2')
  return tr('mail.mailList.noConversations2', { secTitle: sec.title.toLowerCase() })
}

interface RowProps {
  labelsById: Map<number, Label>
  view: string
  markers: boolean
  showChips: boolean
  narrow: boolean
  hideLabelId?: number
  selected: Set<number>
  focusedId: number | null
  handlers: RowHandlers
  pres: ListPresentation
  draggable: boolean
  /** The grid's one tab stop (set per grid by RowGrid's owner). */
  stopId?: number | null
}

function rowsOf(entries: ListEntry[]): ConversationSummary[] {
  const out: ConversationSummary[] = []
  for (const e of entries) if (e.kind === 'row') out.push(e.c)
  return out
}

/** The rows of one list (a page, or one inbox section) as a grid: rows and group headings
 *  only; tabs, section heads, skeletons, empty states and the pager sit outside it. Rows hold
 *  buttons, so this is a grid of rows, not a listbox of options. */
function RowGrid({ label, count, children, style }: { label: string; count: number; children: ReactNode; style?: CSSProperties }) {
  return (
    <div role="grid" aria-label={label} aria-multiselectable="true" aria-rowcount={count} style={style}>
      {children}
    </div>
  )
}

/** The grid's tab stop: the cursor row when this grid has it, else the first row. */
function tabStopOf(ids: number[], focusedId: number | null): number | null {
  if (focusedId != null && ids.includes(focusedId)) return focusedId
  return ids[0] ?? null
}

/** A group heading, or one row drawn by the theme's row component. */
function EntryView({ entry, rowProps, index }: { entry: ListEntry; rowProps: RowProps; index: number }) {
  if (entry.kind === 'group') {
    const Head = rowProps.pres.GroupHead
    // A heading is a row of the grid with one cell; both wrappers draw no box.
    return Head ? (
      <div role="row" aria-rowindex={index + 1} className="grid-contents">
        <div role="gridcell" className="grid-contents">
          <Head group={entry.group} view={rowProps.view} />
        </div>
      </div>
    ) : null
  }
  return <Rows items={[entry.c]} rowProps={rowProps} start={index} />
}

function Rows({ items, rowProps, start = 0 }: { items: ConversationSummary[]; rowProps: RowProps; start?: number }) {
  const Row = rowProps.pres.Row
  return (
    <>
      {items.map((c, i) => (
        <Row
          key={c.id}
          c={c}
          labelsById={rowProps.labelsById}
          selected={rowProps.selected.has(c.id)}
          focused={rowProps.focusedId === c.id}
          tabStop={rowProps.stopId === c.id}
          rowIndex={start + i + 1}
          view={rowProps.view}
          markers={rowProps.markers}
          showChips={rowProps.showChips}
          narrow={rowProps.narrow}
          hideLabelId={rowProps.hideLabelId}
          draggable={rowProps.draggable}
          {...rowProps.handlers}
        />
      ))}
    </>
  )
}

type EmptySpec = { icon: ReactNode; title: string; body?: string; quiet?: boolean; action?: ReactNode }

function PlainList({ list, entries, rowProps, empty, label = tr('mail.mailList.conversations'), expected }: { list: PagedList; entries?: ListEntry[] | null; rowProps: RowProps; empty: EmptySpec; label?: string;
  /** How many conversations the list should hold, when known (a tab's count): the skeleton
   *  draws that many rows (at most 12), so one conversation is not announced by a full page. */
  expected?: number }) {
  const parentRef = useRef<HTMLDivElement>(null)
  const items = list.items
  const scrollEl = () => parentRef.current?.closest('.list-scroll') as HTMLElement | null
  const rowH = rowProps.pres.rowHeight(rowProps.narrow)
  // Grouped lists virtualise headings and rows together.
  const count = entries ? entries.length : items.length
  const virtual = useVirtualizer({
    count,
    getScrollElement: scrollEl,
    estimateSize: (i) => (entries?.[i]?.kind === 'group' ? 40 : rowH),
    overscan: 12,
    getItemKey: (i) => (entries ? entryKey(entries[i], i) : (items[i]?.id ?? i)),
    scrollMargin: parentRef.current?.offsetTop ?? 0,
  })

  // Keep the keyboard-focused row in view.
  const focusedIndex = rowProps.focusedId
    ? entries
      ? entries.findIndex((e) => e.kind === 'row' && e.c.id === rowProps.focusedId)
      : items.findIndex((c) => c.id === rowProps.focusedId)
    : -1
  useEffect(() => {
    if (focusedIndex >= 0) virtual.scrollToIndex(focusedIndex, { align: 'auto' })
  }, [focusedIndex, virtual])

  // Scroll to top on page change.
  useEffect(() => {
    scrollEl()?.scrollTo({ top: 0 })
  }, [list.page])

  if (list.error && !list.items.length)
    return (
      <EmptyState title={tr('mail.mailList.couldntLoadConversations')}>
        <p>{list.error.message || tr('mail.mailList.mailrDidNotAnswerCheck')}</p>
        <button type="button" className="btn tonal" onClick={() => void list.query.refetch()}>
          {tr('common.retry')}
        </button>
      </EmptyState>
    )
  if (list.loading) return <RowSkeleton count={expected != null ? Math.min(12, Math.max(1, expected)) : 12} delay={SKELETON_DELAY} />
  if (list.waiting)
    return (
      <EmptyState icon={<WifiOff />} title={tr('mail.mailList.waitingForConnection')}>
        <p>{tr('mail.mailList.youreOfflineAndThisList')}</p>
      </EmptyState>
    )
  // Another group of the search has hits: one quiet line, not the empty state.
  if (!items.length && empty.quiet) return <p className="muted list-quiet-empty" role="status" data-testid="no-mail-matches">{empty.title}</p>
  if (!items.length || (entries && !entries.length))
    return (
      <EmptyState icon={empty.icon} title={entries && items.length ? tr('mail.mailList.noConversationsMatchTheQuick') : empty.title}>
        {empty.body && !(entries && items.length) && <p>{empty.body}</p>}
        {!(entries && items.length) && empty.action}
      </EmptyState>
    )

  const ids = entries ? rowsOf(entries).map((c) => c.id) : items.map((c) => c.id)
  const gridProps = { ...rowProps, stopId: tabStopOf(ids, rowProps.focusedId) }
  // Small pages render directly (no virtualisation overhead, exact heights).
  if (count <= 60) {
    return (
      <div ref={parentRef}>
        <RowGrid label={label} count={count}>
          {entries ? entries.map((e, i) => <EntryView key={entryKey(e, i)} entry={e} rowProps={gridProps} index={i} />) : <Rows items={items} rowProps={gridProps} />}
        </RowGrid>
        <ListFooter list={list} />
      </div>
    )
  }

  const vItems = virtual.getVirtualItems()
  return (
    <div ref={parentRef}>
      <RowGrid label={label} count={count} style={{ height: virtual.getTotalSize(), position: 'relative' }}>
        {vItems.map((v) => {
          return (
            <div key={v.key} data-index={v.index} ref={virtual.measureElement} className="v-row" style={{ position: 'absolute', top: 0, insetInlineStart: 0, width: '100%', transform: `translateY(${v.start - virtual.options.scrollMargin}px)` }}>
              {entries ? <EntryView entry={entries[v.index]} rowProps={gridProps} index={v.index} /> : <Rows items={[items[v.index]]} rowProps={gridProps} start={v.index} />}
            </div>
          )
        })}
      </RowGrid>
      <ListFooter list={list} />
    </div>
  )
}

function entryKey(e: ListEntry | undefined, i: number): string | number {
  if (!e) return i
  return e.kind === 'group' ? `g:${e.group.id}` : e.c.id
}

function ListFooter({ list }: { list: PagedList }) {
  if (!list.canNext && !list.canPrev) return null
  return (
    <div className="list-footer no-print">
      <span>{list.rangeText}</span>
      <span className="row">
        <button type="button" className="btn text sm" disabled={!list.canPrev} onClick={list.prev}>
          <ChevronLeft />{' '}{tr('common.newer')}
        </button>
        <button type="button" className="btn text sm" disabled={!list.canNext} onClick={list.next}>
          {tr('common.older')}{' '}<ChevronRight />
        </button>
      </span>
    </div>
  )
}

/** Conversations shown per small custom section before "View all". */
const SECTION_PREVIEW = 10
/** A list that loads faster than this shows no skeleton at all. */
const SKELETON_DELAY = 300

/** A tab's main list, paged from the toolbar: the last section without a limit (or the last
 *  section when every section has one). */
function mainIndexOf(secs: LayoutSection[]): number {
  for (let i = secs.length - 1; i >= 0; i--) if (secs[i].limit == null) return i
  return secs.length - 1
}
function Section({
  sectionKey,
  storageKey: storageId,
  title,
  params,
  list: external,
  paged = false,
  pageSize,
  onFocus,
  onItems,
  rowProps,
  arrange,
  emptyText,
  exclude,
  hold,
  target,
  allMail,
  hideEmpty = false,
}: {
  sectionKey: string
  /** A label section that lists archived mail too (SectionHead). */
  allMail?: boolean
  /** The layout hides empty sections (layout.hide_empty): nothing renders once this one has loaded empty. */
  hideEmpty?: boolean
  /** The section as a list the header's actions act on. */
  target?: SectionTarget
  /** Remembers the collapsed state (per layout, tab and section). */
  storageKey: string
  title: string
  exclude?: Set<number>
  params: { view: string; section?: string; sections?: string; q?: string; layout?: string; tab?: string; sec?: string; limit: number }
  /** A list owned by the page (the tab's main list). */
  list?: PagedList
  /** The main list: its pager sits in this section's head. */
  paged?: boolean
  /** Rows a capped section shows once expanded ("Show more"). */
  pageSize: number
  /** "Show only this section". */
  onFocus?: () => void
  onItems: (key: string, items: ConversationSummary[]) => void
  rowProps: RowProps
  arrange: (items: ConversationSummary[]) => ListEntry[] | null
  emptyText: string
  hold?: (key: string, items: ConversationSummary[]) => ConversationSummary[]
}) {
  const storageKey = `mailr.section.${storageId}`
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === '1'
    } catch {
      return false
    }
  })
  // A capped section expands in place ("Show N more") up to a page, and folds back ("Show fewer").
  const [expanded, setExpanded] = useState(false)
  const cap = params.limit
  const own = usePagedList(expanded ? { ...params, limit: Math.max(cap, pageSize) } : params, !external)
  const list = external ?? own
  const ref = useRef<HTMLElement>(null)
  // Paging the main section from its head brings the head to the top of the list once the new
  // page's rows are there, so they are in view (the page loads after the click).
  const toTop = useRef(false)
  const pager = useMemo(() => {
    if (!paged || !external) return undefined
    return {
      rangeText: external.rangeText, canPrev: external.canPrev, canNext: external.canNext,
      prev: () => { toTop.current = true; external.prev() },
      next: () => { toTop.current = true; external.next() },
    }
  }, [paged, external])
  const pageRows = external?.items
  const pageLoading = external?.loading
  useEffect(() => {
    if (!toTop.current || pageLoading || !pageRows?.length) return
    toTop.current = false
    const head = ref.current?.querySelector<HTMLElement>('.section-head')
    const scroller = ref.current?.closest<HTMLElement>('.list-scroll')
    if (!head || !scroller) return
    requestAnimationFrame(() => {
      scroller.scrollTop += head.getBoundingClientRect().top - scroller.getBoundingClientRect().top
    })
  }, [pageRows, pageLoading])
  const excludeKey = exclude ? [...exclude].join(',') : ''
  const items = useMemo(() => {
    const own = hold ? hold(sectionKey, list.items) : list.items
    return excludeKey ? own.filter((c) => !excludeKey.split(',').includes(String(c.id))) : own
  }, [list.items, excludeKey, hold, sectionKey])
  const entries = useMemo(() => (collapsed ? null : arrange(items)), [collapsed, items, arrange])
  const visible = useMemo(() => (collapsed ? [] : entries ? rowsOf(entries) : items), [collapsed, items, entries])
  const gridProps = { ...rowProps, stopId: tabStopOf(visible.map((c) => c.id), rowProps.focusedId) }
  useEffect(() => {
    onItems(sectionKey, visible)
  }, [sectionKey, visible, onItems])

  const toggle = () => {
    const v = !collapsed
    setCollapsed(v)
    try {
      localStorage.setItem(storageKey, v ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  if (hideEmpty && !list.loading && !list.error && list.total === 0 && items.length === 0) return null

  return (
    <section className="section" aria-label={title} ref={ref}>
      <SectionHead title={title} total={list.total} collapsed={collapsed} onToggle={toggle} target={target} items={items}
        loading={list.loading} capped={list.capped} allMail={allMail} pager={pager} onFocus={onFocus} />
      {!collapsed &&
        (list.error && !items.length ? (
          <div className="section-empty" role="alert">
            <span className="section-empty-text">{tr('mail.mailList.couldntLoadThisSection')}</span><button type="button" className="btn text sm" onClick={() => void list.query.refetch()}>{tr('common.retry')}</button>
          </div>
        ) : list.loading ? (
          <RowSkeleton count={3} delay={SKELETON_DELAY} />
        ) : list.waiting ? (
          <div className="section-empty"><span className="section-empty-text">{tr('mail.mailList.waitingForConnection2')}</span></div>
        ) : items.length === 0 ? (
          <div className="section-empty"><span className="section-empty-text">{emptyText}</span></div>
        ) : (
          <>
            <RowGrid label={title} count={entries ? entries.length : items.length}>
              {entries
                ? entries.map((e, i) => <EntryView key={entryKey(e, i)} entry={e} rowProps={gridProps} index={i} />)
                : <Rows items={items} rowProps={gridProps} />}
            </RowGrid>
            {!external && (list.total > items.length || expanded) && (
              <div className="section-more-row">
                {list.total > items.length && !expanded && (
                  // ACT layout.section_more @web.list: the capped section grows in place.
                  <button type="button" className="section-more" data-testid="section-show-more" onClick={() => setExpanded(true)}>
                    {tr('mail.mailList.showMore', { count: Math.min(list.total, Math.max(cap, pageSize)) - items.length })} <ChevronDown size={16} />
                  </button>
                )}
                {list.total > items.length && expanded && onFocus && (
                  <button type="button" className="section-more" data-testid="section-show-all" onClick={onFocus}>
                    {tr('mail.mailList.showAllIn', { total: list.total.toLocaleString(appLocale()) })} <ChevronRight size={16} />
                  </button>
                )}
                {expanded && (
                  <button type="button" className="section-more" data-testid="section-show-fewer" onClick={() => setExpanded(false)}>
                    {tr('mail.mailList.showFewer')} <ChevronUp size={16} />
                  </button>
                )}
              </div>
            )}
          </>
        ))}
    </section>
  )
}
