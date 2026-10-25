// ACT mail.read @web.section, ACT mail.unread @web.section, ACT mail.archive @web.section, ACT mail.delete @web.section, ACT mail.snooze @web.section, ACT mail.move @web.section,
// ACT mail.label @web.section, ACT mail.important @web.section, ACT mail.mute @web.section (web.section: a whole inbox section from its header)
import {
  AlarmClock, Archive, ArrowDownRight, ArrowLeft, ArrowUpRight, ChevronDown, ChevronLeft, ChevronRight, EllipsisVertical, FolderInput,
  Mail, MailOpen, Maximize2, Tag, Trash2, VolumeX,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { idsForList, qk, runOnQuery, useConversations, useLabelList, type ListTarget } from '../../api/hooks'
import type { ActionName, ActionRequest, ConversationSummary, Page } from '../../api/types'
import { ConfirmDialog } from '../../components/Dialog'
import { formatOptionTime } from '../../lib/snooze'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { toastError, useUI } from '../../stores/ui'
import { LabelMenu, MoveMenu, SnoozeMenu } from './menus'
import { listKey } from './ListToolbar'
import { sectionConfirmText } from './sectionConfirm'
import { t as tr, appLocale } from '../../i18n'
/** Jobs over more conversations than this keep no Undo (core/tasks.py BULK_UNDO_MAX). */
export const BULK_UNDO_MAX = 10_000

/** Below this header width Delete all goes into ⋯ (labels show from 600px, list.css). */
const NARROW_HEAD = 400

/** A layout section as a list the server acts on ({"query": {"layout", "tab", "sec"}}). */
export interface SectionTarget {
  layout: string
  tab: string
  sec: string
}

interface Props {
  title: string
  total: number
  collapsed?: boolean
  onToggle?: () => void
  /** The section the header acts on; without it the header shows only its name and count. */
  target?: SectionTarget
  /** The section's rows loaded on screen (selection and the read/unread choice). */
  items?: ConversationSummary[]
  /** Nothing is known about the section yet: no action runs. */
  loading?: boolean
  /** The total stopped at the server's cap (the section holds more). */
  capped?: boolean
  /** A label section of an Inbox tab that lists archived mail too ("Only mail in the Inbox" off):
   *  the head says so, so a row archived here and still listed is not a surprise. */
  allMail?: boolean
  /** The tab's main list pages here, next to its rows ("1–50 of 91 ‹ ›"). */
  pager?: { rangeText: string; canPrev: boolean; canNext: boolean; prev: () => void; next: () => void }
  /** "Show only this section": the tab shows this section alone (MailList focus). */
  onFocus?: () => void
  /** The section shown alone: Back to all of the tab's sections (`backLabel` is the tab's name). */
  onBack?: () => void
  backLabel?: string
}

type Verb = 'read' | 'unread' | 'archive' | 'trash' | 'important' | 'not_important' | 'mute'

const DONE: Record<Verb, string> = {
  read: tr('mail.sectionHead.markedAsRead'), unread: tr('mail.sectionHead.markedAsUnread'), archive: 'archived', trash: tr('mail.sectionHead.movedToTrash'),
  important: tr('mail.sectionHead.markedAsImportant'), not_important: tr('mail.sectionHead.markedAsNotImportant'), mute: 'muted',
}

/** A section's header: fold, select all in the section, its name and count, and actions on every
 * conversation the section holds (not only the rows loaded). With `onToggle` the section folds;
 * without, a same-width blank keeps the name where the other tabs have it. */
export function SectionHead({ title, total, collapsed = false, onToggle, target, items = [], loading = false, capped = false, allMail = false,
  pager, onFocus, onBack, backLabel }: Props) {
  const actionable = !!target && !loading && total > 0
  return (
    <div className={`section-head${onToggle ? '' : ' fixed'}${target ? ' with-actions' : ''}`} data-section-head={target?.sec}>
      {/* The checkbox first, in the same column as the rows' checkboxes. */}
      {target && <SectionCheckbox title={title} total={total} target={target} items={items} disabled={!actionable || collapsed} />}
      {onBack ? (
        // ACT layout.section_focus @web.list: back from one section to all of the tab's. The arrow
        // and the name are one button: shown alone, the section has nothing to fold.
        <button type="button" className="section-back" aria-label={tr('mail.sectionHead.backFrom', { title, name: backLabel ?? '' })}
          data-tip={tr('mail.sectionHead.backTo', { name: backLabel ?? '' })} data-testid="section-back" onClick={onBack}>
          <ArrowLeft size={18} aria-hidden />
          <span className="section-back-title">{title}</span>
        </button>
      ) : onToggle ? (
        <button type="button" className="icon-btn xs" aria-label={collapsed ? tr('mail.sectionHead.expand', { title }) : tr('mail.sectionHead.collapse', { title })} aria-expanded={!collapsed} onClick={onToggle}>
          {collapsed ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
        </button>
      ) : (
        <span className="icon-btn xs section-chev-space" aria-hidden />
      )}
      {!onBack && <span className="title" onClick={onToggle}>{title}</span>}
      {allMail && <span className="section-scope" data-tip={tr('mail.sectionHead.allMailTip')} data-testid="section-all-mail">{tr('mail.sectionHead.allMail')}</span>}
      {total > 0 && (
        <span className="count" aria-label={tr('mail.sectionHead.conversations', { count: total, value: capped ? '+' : '' })}>
          {total.toLocaleString(appLocale())}{capped ? '+' : ''}
        </span>
      )}
      {target && actionable && <SectionActions title={title} total={total} target={target} items={items} capped={capped} onFocus={onFocus} />}
      {(pager || onFocus) && (
        <span className="section-tools">
          {pager && (
            <>
              <span className="section-range" aria-live="polite" data-testid="section-range">{pager.rangeText}</span>
              <button type="button" className="icon-btn xs" aria-label={tr('common.newer')} data-tip={tr('common.newer')} disabled={!pager.canPrev} onClick={pager.prev}>
                <ChevronLeft size={18} />
              </button>
              <button type="button" className="icon-btn xs" aria-label={tr('common.older')} data-tip={tr('common.older')} disabled={!pager.canNext} onClick={pager.next}>
                <ChevronRight size={18} />
              </button>
            </>
          )}
          {onFocus && (
            // ACT layout.section_focus @web.list: the tab shows this section alone, paged from the toolbar.
            <button type="button" className="icon-btn xs section-focus" aria-label={tr('mail.sectionHead.showOnly', { title })}
              data-tip={tr('mail.sectionHead.showOnlyTip')} data-testid="section-focus" onClick={onFocus}>
              <Maximize2 size={16} />
            </button>
          )}
        </span>
      )}
    </div>
  )
}

function sectionQuery(t: SectionTarget, total: number, title: string) {
  return { view: 'inbox', q: '', total, layout: t.layout, tab: t.tab, sec: t.sec, name: title }
}

/** "Select all in this section": the whole section (every page), as the toolbar's "Select all N"
 * does for a list. Tri-state while only some of its rows are selected. */
function SectionCheckbox({ title, total, target, items, disabled }: { title: string; total: number; target: SectionTarget; items: ConversationSummary[]; disabled: boolean }) {
  const lq = sectionQuery(target, total, title)
  const key = listKey(lq)
  const all = useUI((s) => s.allMatching === key)
  const excluded = useUI((s) => s.excluded)
  const selected = useUI((s) => s.selected)
  const some = items.some((c) => selected.has(c.id))
  const checked = all && excluded.size === 0
  const mixed = (all && excluded.size > 0) || (!all && some)
  const toggle = () => {
    const ui = useUI.getState()
    if (checked) {
      ui.setAllMatching(null)
      ui.setSelected(new Set())
      return
    }
    ui.setAllMatching(key, lq)
    ui.setSelected(new Set(items.map((c) => c.id)))
  }
  // The label gives the 18px box a 32px target without moving it. An empty section keeps the
  // box's place (names line up) but offers nothing to select.
  return (
    <label className="section-check-hit" style={total === 0 ? { visibility: 'hidden' } : undefined}>
      <input
        type="checkbox"
        className="checkbox section-check"
        aria-label={checked ? tr('mail.sectionHead.deselectAllIn', { title }) : tr('mail.sectionHead.selectAllIn', { title })}
        data-tip={checked ? tr('mail.sectionHead.deselectAllInThisSection') : tr('mail.sectionHead.selectAllInThisSection')}
        checked={checked}
        disabled={disabled}
        ref={(el) => {
          if (el) el.indeterminate = mixed
        }}
        onChange={toggle}
      />
    </label>
  )
}

/** A confirmation waiting for the user: what it would run, on how many, and how it ends. */
interface Pending {
  action: ActionName
  count: number
  capped: boolean
  loaded: number
  detail?: string
  /** A ⋯ submenu (Snooze, Move, Label) waits on the answer; otherwise `exec` runs on confirm. */
  resolve?: (ok: boolean) => void
}

function SectionActions({ title, total, target, items, capped, onFocus }: { title: string; total: number; target: SectionTarget; items: ConversationSummary[]; capped: boolean; onFocus?: () => void }) {
  const qc = useQueryClient()
  const more = useAnchor()
  const moreBtn = useRef<HTMLButtonElement>(null)
  // Every action on the whole section asks first.
  const [confirm, setConfirm] = useState<Pending | null>(null)
  const labels = useLabelList()
  const [sub, setSub] = useState<{ kind: 'snooze' | 'move' | 'label'; ids: number[] } | null>(null)
  const contextLabel = useUI((s) => s.contextLabelId)
  // On a narrow header (phone, slim list pane) Delete all moves into ⋯.
  const box = useRef<HTMLDivElement>(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const head = box.current?.parentElement
    if (!head || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setNarrow(head.clientWidth < NARROW_HEAD))
    ro.observe(head)
    return () => ro.disconnect()
  }, [])

  // Mark all as read while anything in the section is unread; Mark all as unread once it is all
  // read. Rows on screen decide when they can; otherwise one count asks the server.
  const loadedUnread = items.some((c) => c.unread_count > 0)
  const allLoaded = items.length >= total
  const probe = useConversations(
    { view: 'inbox', layout: target.layout, tab: target.tab, sec: target.sec, q: 'is:unread', limit: 1 },
    !loadedUnread && !allLoaded,
  )
  const hasUnread = loadedUnread || (!allLoaded && (probe.data ? probe.data.total > 0 : true))

  const query = (q = '') => ({ view: 'inbox', q, layout: target.layout, tab: target.tab, sec: target.sec })
  const doneText = (verb: string) => ({ done: (n: number) => tr('mail.sectionHead.conversationIn', { n: n.toLocaleString(appLocale()), count: n, title, verb }) })
  const leaveSelection = () => {
    const ui = useUI.getState()
    if (ui.allMatching === listKey(sectionQuery(target, total, title))) {
      ui.setAllMatching(null)
      ui.setSelected(new Set())
    }
  }

  const exec = (action: Verb) => {
    leaveSelection()
    // Read and unread act only on what would change, so their Undo restores exactly that.
    const q = action === 'read' ? 'is:unread' : action === 'unread' ? 'is:read' : ''
    const req: ActionRequest = {
      ids: [], action: action as ActionName, query: query(q),
      ...(action === 'archive' && contextLabel ? { from_label_id: contextLabel } : {}),
    }
    void runOnQuery(qc, req, total, doneText(DONE[action])).catch((e) => toastError(e, tr('common.actionFailed')))
  }
  /** Read and unread act on the unread (or read) ones only: their count comes from the rows on
   * screen when the whole section is loaded, and from the server otherwise. */
  const countFor = async (action: Verb): Promise<{ count: number; capped: boolean; loaded: number }> => {
    if (action !== 'read' && action !== 'unread') return { count: total, capped, loaded: items.length }
    const match = (c: ConversationSummary) => (action === 'read' ? c.unread_count > 0 : c.unread_count === 0)
    const loaded = items.filter(match).length
    if (allLoaded) return { count: loaded, capped: false, loaded }
    const p = { view: 'inbox', layout: target.layout, tab: target.tab, sec: target.sec, q: action === 'read' ? 'is:unread' : 'is:read', limit: 1 }
    const page = await qc.fetchQuery({
      queryKey: qk.conversationList(p),
      queryFn: ({ signal }) => api.get<Page<ConversationSummary>>('/conversations', p, signal),
      staleTime: 5_000,
    })
    return { count: page.total, capped: !!page.total_capped, loaded }
  }
  const ask = (action: Verb) => {
    void countFor(action).then((c) => setConfirm({ action, ...c })).catch((e) => toastError(e, tr('mail.sectionHead.couldNotCountTheConversations')))
  }
  /** The ⋯ submenus run through useActions; the list target asks here before it runs. Label all
   * may send two requests (add, then remove): one answer covers both. */
  const confirmed = useRef(new WeakSet<number[]>())
  const confirmSub = (req: ActionRequest) => {
    if (confirmed.current.has(req.ids)) return Promise.resolve(true)
    const name = (id?: number) => labels.find((l) => l.id === id)?.name
    const detail = req.action === 'snooze' && req.until ? `on ${formatOptionTime(new Date(req.until))}`
      : req.action === 'move' ? name(req.label_id)
        : req.action === 'add_labels' || req.action === 'remove_labels' ? (req.label_ids ?? []).map((id) => name(id)).filter(Boolean).join(', ') || undefined
          : undefined
    return new Promise<boolean>((resolve) => setConfirm({
      action: req.action, count: total, capped, loaded: items.length, detail,
      resolve: (ok) => {
        if (ok) confirmed.current.add(req.ids)
        resolve(ok)
      },
    }))
  }
  const openSub = (kind: 'snooze' | 'move' | 'label') => {
    more.close()
    leaveSelection()
    const t: ListTarget = { query: query(), total, text: doneText('updated'), confirm: confirmSub }
    setSub({ kind, ids: idsForList(t, items.map((c) => c.id)) })
  }
  const pick = (fn: () => void) => () => {
    more.close()
    fn()
  }

  const count = `${total.toLocaleString(appLocale())}${capped ? '+' : ''}`
  const readBtn = hasUnread ? { a: 'read' as const, label: tr('mail.sectionHead.markAllAsRead'), short: tr('common.read'), icon: <MailOpen /> }
    : { a: 'unread' as const, label: tr('mail.sectionHead.markAllAsUnread'), short: tr('common.unread'), icon: <Mail /> }
  const other = hasUnread ? { a: 'unread' as const, label: tr('mail.sectionHead.markAllAsUnread'), icon: <Mail /> }
    : { a: 'read' as const, label: tr('mail.sectionHead.markAllAsRead'), icon: <MailOpen /> }
  const subAnchor = sub ? moreBtn.current : null
  const moveView = contextLabel ? `label:${contextLabel}` : 'inbox'

  return (
    <div ref={box} className={`section-actions${more.open || sub || confirm ? ' open' : ''}`} role="toolbar" aria-label={tr('mail.sectionHead.actionsForAllIn', { title })}>
      <ActBtn label={readBtn.label} short={readBtn.short} icon={readBtn.icon} onClick={() => ask(readBtn.a)} />
      {/* On a phone Archive all sits in ⋮: beside Mark all as read it was an easy mis-tap on a whole section. */}
      {!narrow && <ActBtn label={tr('mail.sectionHead.archiveAll')} short={tr('common.archive')} icon={<Archive />} onClick={() => ask('archive')} />}
      {!narrow && <ActBtn label={tr('mail.sectionHead.deleteAll')} short={tr('common.delete2')} icon={<Trash2 />} onClick={() => ask('trash')} />}
      <button
        ref={moreBtn}
        type="button"
        className="icon-btn sm"
        aria-label={tr('mail.sectionHead.moreActionsForAllIn', { title })}
        data-tip={tr('mail.sectionHead.moreForThisSection')}
        aria-haspopup="menu"
        aria-expanded={more.open}
        onClick={(e) => more.toggle(e.currentTarget)}
      >
        <EllipsisVertical />
      </button>
      <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" label={tr('mail.sectionHead.allIn', { title })} minWidth={230}>
        <div className="menu-header">{tr('mail.sectionHead.allIn2', { count, title })}</div>
        {/* Also in ⋯: a narrow head has no room for the button (list.css .section-focus). */}
        {onFocus && <MenuItem icon={<Maximize2 />} onSelect={() => { more.close(); onFocus() }}>{tr('mail.sectionHead.showOnlyTip')}</MenuItem>}
        <MenuItem icon={other.icon} onSelect={pick(() => ask(other.a))}>{other.label}</MenuItem>
        {narrow && <MenuItem icon={<Archive />} onSelect={pick(() => ask('archive'))}>{tr('mail.sectionHead.archiveAll')}</MenuItem>}
        {narrow && <MenuItem icon={<Trash2 />} onSelect={pick(() => ask('trash'))}>{tr('mail.sectionHead.deleteAll')}</MenuItem>}
        <MenuSep />
        <MenuItem icon={<AlarmClock />} onSelect={() => openSub('snooze')} shortcut="▸">{tr('mail.sectionHead.snoozeAll')}</MenuItem>
        <MenuItem icon={<FolderInput />} onSelect={() => openSub('move')} shortcut="▸">{tr('mail.sectionHead.moveAllTo')}</MenuItem>
        <MenuItem icon={<Tag />} onSelect={() => openSub('label')} shortcut="▸">{tr('mail.sectionHead.labelAll')}</MenuItem>
        <MenuSep />
        <MenuItem icon={<ArrowUpRight />} onSelect={pick(() => ask('important'))}>{tr('mail.sectionHead.markAllAsImportant')}</MenuItem>
        <MenuItem icon={<ArrowDownRight />} onSelect={pick(() => ask('not_important'))}>{tr('mail.sectionHead.markAllAsNotImportant')}</MenuItem>
        <MenuItem icon={<VolumeX />} onSelect={pick(() => ask('mute'))}>{tr('mail.sectionHead.muteAll')}</MenuItem>
      </Popover>
      <SnoozeMenu anchor={sub?.kind === 'snooze' ? subAnchor : null} open={sub?.kind === 'snooze'} onClose={() => setSub(null)} ids={sub?.ids ?? []} />
      <MoveMenu anchor={sub?.kind === 'move' ? subAnchor : null} open={sub?.kind === 'move'} onClose={() => setSub(null)} ids={sub?.ids ?? []} currentView={moveView} />
      <LabelMenu anchor={sub?.kind === 'label' ? subAnchor : null} open={sub?.kind === 'label'} onClose={() => setSub(null)} ids={sub?.ids ?? []} />
      <SectionConfirm pending={confirm} where={title} onAnswer={(ok) => {
        const c = confirm
        setConfirm(null)
        if (!c) return
        if (c.resolve) c.resolve(ok)
        else if (ok) exec(c.action as Verb)
      }} />
    </div>
  )
}

function SectionConfirm({ pending, where, onAnswer }: { pending: Pending | null; where: string; onAnswer: (ok: boolean) => void }) {
  const t = pending ? sectionConfirmText({ ...pending, where, undoMax: BULK_UNDO_MAX }) : null
  return (
    <ConfirmDialog
      open={!!pending}
      title={t?.title ?? ''}
      body={t?.body ?? ''}
      confirmLabel={t?.confirmLabel}
      danger={t?.danger}
      focusConfirm
      onClose={() => onAnswer(false)}
      // Nothing left to change (read elsewhere meanwhile): OK only closes.
      onConfirm={() => onAnswer(pending?.count !== 0)}
    />
  )
}

function ActBtn({ label, short, icon, onClick }: { label: string; short: string; icon: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className="section-act" aria-label={label} data-tip={label} onClick={onClick}>
      {icon}
      <span className="lbl">{short}</span>
    </button>
  )
}
