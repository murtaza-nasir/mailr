// ACT mail.untrash @web.list, ACT mail.delete_forever @web.list (Trash and Spam), ACT mail.star @web.list
// ACT mail.archive @web.list, ACT mail.delete @web.list, ACT mail.spam @web.list, ACT mail.not_spam @web.list, ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.snooze @web.list,
// ACT mail.label @web.list, ACT mail.move @web.list, ACT mail.mute @web.list, ACT mail.important @web.list (the selection toolbar);
// ACT mail.show_in_tab @web.list, ACT mail.category @web.list (its ⋯ menu's Show in tab and Category submenus)
import {
  AlarmClock, Archive, ArchiveRestore, ChevronDown, ChevronLeft, ChevronRight, EllipsisVertical, Filter, FolderInput, Mail,
  MailOpen, OctagonAlert, RefreshCw, ShieldCheck, Star, StarOff, Tag, Trash2, VolumeX, ArrowUpRight, ArrowDownRight, Trash, LayoutList, Shapes,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { runOnQuery, useActions, useLabelList } from '../../api/hooks'
import type { ActionName, ConversationSummary } from '../../api/types'
import { toastError } from '../../stores/ui'
import { viewTitle, baseView } from '../../lib/views'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuHeader, MenuItem, MenuSep, Popover, SubMenu, useAnchor } from '../../components/Popover'
import { useUI } from '../../stores/ui'
import { LabelMenu, MOVE_TO_FOLDER_HINT, MoveMenu, SnoozeMenu } from './menus'
import { sectionConfirmText } from './sectionConfirm'
import { CategoryChoices, ShowInTabChoices, useOffersShowInTab } from './convMenus'
import { useThemeDef } from '../../themes/useUITheme'
import { t as tr, tx, appLocale } from '../../i18n'

interface Props {
  view: string
  items: ConversationSummary[]
  onRefresh: () => void
  refreshing: boolean
  pager?: { rangeText: string; canPrev: boolean; canNext: boolean; prev: () => void; next: () => void } | null
  /** The section the pager pages, in a tab with several sections ("Everything else 1–21 of
   *  21", not a number that reads as the whole inbox). */
  pagerLabel?: string
  scrolled?: boolean
  /** The list shown (plain lists only): lets "Select all N conversations" act on all of it. */
  listQuery?: ListQuery | null
}

/** The list a "select all N" action covers: a view and search, or a layout section. */
export interface ListQuery {
  view: string
  q: string
  total: number
  layout?: string
  tab?: string
  sec?: string
  /** What the banner calls the list ("Later", "Inbox: Everything else"). */
  name?: string
}

/** Identity of a list for "select all N" (which list the selection belongs to). */
export function listKey(lq: ListQuery | null | undefined): string | null {
  return lq ? JSON.stringify([lq.view, lq.q, lq.layout ?? '', lq.tab ?? '', lq.sec ?? '']) : null
}

export function ListToolbar({ view, items, onRefresh, refreshing, pager, pagerLabel, scrolled, listQuery }: Props) {
  const qc = useQueryClient()
  const labels = useLabelList()
  const focusedId = useUI((s) => s.focusedId)
  // "Select all N": kept in the store (it survives paging); unchecking a row then excludes it.
  const key = listKey(listQuery)
  const allMatching = useUI((s) => !!key && s.allMatching === key)
  // "Select all in this section" from a section header (another list than the toolbar's).
  const otherAll = useUI((s) => (s.allMatching && s.allMatching !== key ? s.allMatchingQuery : null))
  const excluded = useUI((s) => s.excluded)
  const setAllMatchingKey = useUI((s) => s.setAllMatching)
  const setAllMatching = (on: boolean) => setAllMatchingKey(on ? key : null, on ? listQuery ?? null : null)
  const selected = useUI((s) => s.selected)
  const setSelected = useUI((s) => s.setSelected)
  const shortcutMenu = useUI((s) => s.shortcutMenu)
  const setShortcutMenu = useUI((s) => s.setShortcutMenu)
  const { run } = useActions()
  const nav = useNavigate()
  const selMenu = useAnchor()
  const moreMenu = useAnchor()
  const labelMenu = useAnchor()
  const moveMenu = useAnchor()
  const snoozeMenu = useAnchor()
  const labelBtn = useRef<HTMLButtonElement>(null)
  const moveBtn = useRef<HTMLButtonElement>(null)
  const snoozeBtn = useRef<HTMLButtonElement>(null)
  const [confirmForever, setConfirmForever] = useState(false)
  // Mark all as read (⋯, nothing selected) acts on the whole page: it asks first, like a section's.
  const [confirmRead, setConfirmRead] = useState(false)
  // Themes with their own command bar keep only selection, refresh and paging here.
  const minimal = useThemeDef().list.toolbar === 'minimal'
  const showInTab = useOffersShowInTab()

  const ids = items.filter((c) => selected.has(c.id)).map((c) => c.id)
  // Selections may include ids from other sections (priority inbox); act on all selected.
  const allIds = Array.from(selected)
  const selItems = items.filter((c) => selected.has(c.id))
  const any = allIds.length > 0
  const allChecked = items.length > 0 && ids.length === items.length
  const someChecked = any && !allChecked

  const clear = () => {
    setSelected(new Set())
    setAllMatching(false)
  }
  /** After an action: drop only the rows it acted on. Rows selected while it ran stay selected
   * (clearing everything on completion lost them, and the next action ran on nothing). */
  const deselect = (acted: number[]) => {
    const st = useUI.getState()
    const next = new Set(st.selected)
    for (const id of acted) next.delete(id)
    setSelected(next)
    setAllMatching(false)
  }
  const contextLabelId = useUI((s) => s.contextLabelId)
  const act = (action: ActionName) => {
    const fromLabel = action === 'archive' ? contextLabelId : null
    if (allMatching && listQuery) {
      const lq = listQuery
      clear()
      const ex = [...excluded]
      const { view: v, q, layout, tab, sec } = lq
      const query = { ...(layout ? { view: v, q, layout, tab, sec } : { view: v, q }), ...(ex.length ? { exclude_ids: ex } : {}) }
      void runOnQuery(qc, { ids: [], query, action, ...(action === 'archive' && fromLabel ? { from_label_id: fromLabel } : {}) },
        lq.total - ex.length).catch((e) => toastError(e, tr('common.actionFailed')))
      return
    }
    const acted = allIds
    void run({ ids: acted, action, ...(fromLabel ? { from_label_id: fromLabel } : {}) }).then(() => deselect(acted))
  }

  // Keyboard shortcuts l / v / b open these menus for the selection, or for the focused
  // conversation when nothing is checked (as in Gmail).
  const kbIds = any ? allIds : focusedId && items.some((c) => c.id === focusedId) ? [focusedId] : []
  const kbMenu = kbIds.length ? shortcutMenu : null
  const closeKb = () => setShortcutMenu(null)
  const kbItems = any ? selItems : items.filter((c) => c.id === focusedId)
  const toolbarRef = useRef<HTMLDivElement>(null)
  const kbAnchor = (btn: HTMLButtonElement | null) => btn ?? toolbarRef.current
  const listName = listQuery?.q ? tr('mail.listToolbar.thisSearch') : listQuery?.name || viewTitle(view, labels)

  const selectBy = (fn: (c: ConversationSummary) => boolean) => {
    setSelected(new Set(items.filter(fn).map((c) => c.id)))
    selMenu.close()
  }

  const anyUnread = selItems.some((c) => c.unread_count > 0)
  const anyUnstarred = selItems.some((c) => !c.starred)
  const anyUnimportant = selItems.some((c) => !c.important)
  const inTrash = baseView(view) === 'trash'
  const inSpam = baseView(view) === 'spam'
  const inInbox = (baseView(view) === 'inbox' && !contextLabelId) || view.startsWith('category:')

  return (
    <>
    <div ref={toolbarRef} className={`toolbar${scrolled ? ' scrolled' : ''}`} role="toolbar" aria-label={tr('common.conversationActions')}>
      <div className="sel-group">
        <input
          type="checkbox"
          className="checkbox"
          aria-label={allChecked || allMatching ? tr('mail.listToolbar.deselectAll') : tr('common.selectAll')}
          checked={allMatching ? excluded.size === 0 : allChecked}
          ref={(el) => {
            if (el) el.indeterminate = allMatching ? excluded.size > 0 : someChecked
          }}
          onChange={() => (any ? clear() : setSelected(new Set(items.map((c) => c.id))))}
        />
        <button type="button" className="sel-caret" aria-label={tr('mail.listToolbar.selectOptions')} onClick={(e) => selMenu.toggle(e.currentTarget)}>
          <ChevronDown size={16} />
        </button>
      </div>
      <Popover anchor={selMenu.anchor} open={selMenu.open} onClose={selMenu.close} label={tr('common.select')}>
        <MenuItem onSelect={() => selectBy(() => true)}>{tr('common.all')}</MenuItem>
        <MenuItem onSelect={() => selectBy(() => false)}>{tr('common.none')}</MenuItem>
        <MenuItem onSelect={() => selectBy((c) => c.unread_count === 0)}>{tr('common.read')}</MenuItem>
        <MenuItem onSelect={() => selectBy((c) => c.unread_count > 0)}>{tr('common.unread')}</MenuItem>
        <MenuItem onSelect={() => selectBy((c) => c.starred)}>{tr('common.starred')}</MenuItem>
        <MenuItem onSelect={() => selectBy((c) => !c.starred)}>{tr('mail.listToolbar.unstarred')}</MenuItem>
      </Popover>

      {!any || minimal ? (
        <>
          <button type="button" className="icon-btn" aria-label={tr('common.refresh')} data-tip={tr('common.refresh')} onClick={onRefresh} disabled={refreshing}>
            <RefreshCw className={refreshing ? 'spin' : undefined} style={refreshing ? { animation: 'spin 0.9s linear infinite' } : undefined} />
          </button>
          {minimal && any && <span className="sel-count">{tr('mail.listToolbar.selected', { allIdsLength: allIds.length })}</span>}
          <button type="button" className="icon-btn" aria-label={tr('common.more')} data-tip={tr('common.more')} onClick={(e) => moreMenu.toggle(e.currentTarget)}>
            <EllipsisVertical />
          </button>
          <Popover anchor={moreMenu.anchor} open={moreMenu.open} onClose={moreMenu.close}>
            <MenuItem
              icon={<MailOpen />}
              disabled={!items.some((c) => c.unread_count > 0)}
              onSelect={() => {
                moreMenu.close()
                setConfirmRead(true)
              }}
            >
              {tr('mail.listToolbar.markAllAsRead')}
            </MenuItem>
            {inTrash && (
              <MenuItem icon={<Trash />} onSelect={() => { moreMenu.close(); setConfirmForever(true) }} disabled={!items.length}>
                {tr('mail.listToolbar.emptyTrashNowThisPage')}
              </MenuItem>
            )}
            {/* Themes with their own command bar: the selection's tab and category are here. */}
            {minimal && selItems.length > 0 && (
              <>
                <MenuSep />
                {showInTab && (
                  <SubMenu icon={<LayoutList />} label={tr('common.showInTab')} minWidth={220}>
                    <ShowInTabChoices items={selItems} onClose={moreMenu.close} onDone={() => deselect(selItems.map((c) => c.id))} />
                  </SubMenu>
                )}
                <SubMenu icon={<Shapes />} label={tr('common.category')} minWidth={200}>
                  <CategoryChoices items={selItems} onClose={moreMenu.close} onDone={() => deselect(selItems.map((c) => c.id))} />
                </SubMenu>
              </>
            )}
          </Popover>
        </>
      ) : (
        <>
          {inTrash ? (
            <>
              <button type="button" className="icon-btn" aria-label={tr('common.moveToInbox')} data-tip={tr('common.moveToInbox')} onClick={() => act('untrash')}>
                <ArchiveRestore />
              </button>
              <button type="button" className="icon-btn" aria-label={tr('common.deleteForever')} data-tip={tr('common.deleteForever')} onClick={() => setConfirmForever(true)}>
                <Trash />
              </button>
            </>
          ) : inSpam ? (
            <>
              <button type="button" className="btn text sm" onClick={() => act('not_spam')}>
                <ShieldCheck />{' '}{tr('common.notSpam')}
              </button>
              <button type="button" className="icon-btn" aria-label={tr('common.deleteForever')} data-tip={tr('common.deleteForever')} onClick={() => setConfirmForever(true)}>
                <Trash />
              </button>
            </>
          ) : (
            <>
              {inInbox || baseView(view) === 'all' || baseView(view).startsWith('label:') || baseView(view) === 'starred' || baseView(view) === 'important' || baseView(view) === 'snoozed' || baseView(view) === 'search' ? (
                <button type="button" className="icon-btn" aria-label={tr('common.archive')} data-tip={tr('common.archive')} onClick={() => act('archive')}>
                  <Archive />
                </button>
              ) : null}
              {baseView(view) === 'snoozed' && (
                <button type="button" className="btn text sm" onClick={() => act('unsnooze')}>
                  <AlarmClock />{' '}{tr('mail.listToolbar.unsnooze')}
                </button>
              )}
              {!inInbox && baseView(view) !== 'sent' && baseView(view) !== 'drafts' && (
                <button type="button" className="icon-btn" aria-label={tr('common.moveToInbox')} data-tip={tr('common.moveToInbox')} onClick={() => act('inbox')}>
                  <ArchiveRestore />
                </button>
              )}
              <button type="button" className="icon-btn" aria-label={tr('common.reportSpam')} data-tip={tr('common.reportSpam')} onClick={() => act('spam')}>
                <OctagonAlert />
              </button>
              <button type="button" className="icon-btn" aria-label={tr('common.delete2')} data-tip={tr('common.delete2')} onClick={() => act('trash')}>
                <Trash2 />
              </button>
            </>
          )}
          <span className="sep" />
          <button
            type="button"
            className="icon-btn"
            aria-label={anyUnread ? tr('common.markAsRead') : tr('common.markAsUnread')}
            data-tip={anyUnread ? tr('common.markAsRead') : tr('common.markAsUnread')}
            onClick={() => act(anyUnread ? 'read' : 'unread')}
          >
            {anyUnread ? <MailOpen /> : <Mail />}
          </button>
          {!inTrash && !inSpam && (
            <button ref={snoozeBtn} type="button" className="icon-btn" aria-label={tr('common.snooze')} data-tip={tr('common.snooze')} onClick={(e) => snoozeMenu.toggle(e.currentTarget)}>
              <AlarmClock />
            </button>
          )}
          <span className="sep" />
          <button ref={moveBtn} type="button" className="icon-btn" aria-label={tr('common.moveToFolder')} data-tip={tr('mail.listToolbar.moveToFolder', { moveToFolderHint: MOVE_TO_FOLDER_HINT })} onClick={(e) => moveMenu.toggle(e.currentTarget)}>
            <FolderInput />
          </button>
          <button ref={labelBtn} type="button" className="icon-btn" aria-label={tr('common.labels')} data-tip={tr('common.labels')} onClick={(e) => labelMenu.toggle(e.currentTarget)}>
            <Tag />
          </button>
          <button type="button" className="icon-btn" aria-label={tr('common.more')} data-tip={tr('common.more')} onClick={(e) => moreMenu.toggle(e.currentTarget)}>
            <EllipsisVertical />
          </button>
          <Popover anchor={moreMenu.anchor} open={moreMenu.open} onClose={moreMenu.close}>
            <MenuItem icon={anyUnread ? <Mail /> : <MailOpen />} onSelect={() => { moreMenu.close(); act(anyUnread ? 'unread' : 'read') }}>
              {anyUnread ? tr('common.markAsUnread') : tr('common.markAsRead')}
            </MenuItem>
            <MenuItem icon={<ArrowUpRight />} onSelect={() => { moreMenu.close(); act(anyUnimportant ? 'important' : 'not_important') }}>
              {anyUnimportant ? tr('common.markAsImportant') : tr('mail.listToolbar.markAsNotImportant')}
            </MenuItem>
            <MenuItem icon={anyUnstarred ? <Star /> : <StarOff />} onSelect={() => { moreMenu.close(); act(anyUnstarred ? 'star' : 'unstar') }}>
              {anyUnstarred ? tr('mail.listToolbar.addStar') : tr('common.removeStar')}
            </MenuItem>
            <MenuSep />
            {/* On a phone the groups have headings, as in the reader and the Android sheet. */}
            <div className="narrow-only">
              <MenuHeader>{tr('common.organize')}</MenuHeader>
            </div>
            {showInTab && selItems.length > 0 && (
              <SubMenu icon={<LayoutList />} label={tr('common.showInTab')} minWidth={220}>
                <ShowInTabChoices items={selItems} onClose={moreMenu.close} onDone={() => deselect(selItems.map((c) => c.id))} />
              </SubMenu>
            )}
            {selItems.length > 0 && (
              <SubMenu icon={<Shapes />} label={tr('common.category')} minWidth={200}>
                <CategoryChoices items={selItems} onClose={moreMenu.close} onDone={() => deselect(selItems.map((c) => c.id))} />
              </SubMenu>
            )}
            {!inInbox && (
              <MenuItem icon={<ArrowDownRight />} onSelect={() => { moreMenu.close(); act('inbox') }}>
                {tr('common.moveToInbox')}
              </MenuItem>
            )}
            <MenuSep />
            <div className="narrow-only">
              <MenuHeader>{tr('common.more')}</MenuHeader>
            </div>
            <MenuItem icon={<VolumeX />} onSelect={() => { moreMenu.close(); act('mute') }}>
              {tr('common.mute')}
            </MenuItem>
            <MenuItem
              icon={<Filter />}
              disabled={selItems.length === 0}
              onSelect={() => {
                moreMenu.close()
                const senders = Array.from(new Set(selItems.flatMap((c) => c.participants.filter((p) => !p.me).map((p) => p.email)))).slice(0, 5)
                const q = senders.length ? `from:(${senders.join(' OR ')})` : ''
                nav(`/settings/mail/filters?new=${encodeURIComponent(q)}`)
              }}
            >
              {tr('mail.listToolbar.filterMessagesLikeThese')}
            </MenuItem>
          </Popover>
        </>
      )}

      <LabelMenu anchor={labelMenu.anchor ?? (kbMenu === 'label' ? kbAnchor(labelBtn.current) : null)} open={labelMenu.open || kbMenu === 'label'} onClose={() => { labelMenu.close(); closeKb() }} ids={labelMenu.open ? allIds : kbIds} items={labelMenu.open ? selItems : kbItems} onDone={((acted) => () => deselect(acted))(labelMenu.open ? allIds : kbIds)} />
      <MoveMenu anchor={moveMenu.anchor ?? (kbMenu === 'move' ? kbAnchor(moveBtn.current) : null)} open={moveMenu.open || kbMenu === 'move'} onClose={() => { moveMenu.close(); closeKb() }} ids={moveMenu.open ? allIds : kbIds} onDone={((acted) => () => deselect(acted))(moveMenu.open ? allIds : kbIds)} currentView={view} />
      <SnoozeMenu anchor={snoozeMenu.anchor ?? (kbMenu === 'snooze' ? kbAnchor(snoozeBtn.current) : null)} open={snoozeMenu.open || kbMenu === 'snooze'} onClose={() => { snoozeMenu.close(); closeKb() }} ids={snoozeMenu.open ? allIds : kbIds} onDone={((acted) => () => deselect(acted))(snoozeMenu.open ? allIds : kbIds)} />

      <div className="spacer" />
      {pager && (
        <>
          <span className="range" aria-live="polite" title={pagerLabel ? `${pagerLabel} ${pager.rangeText}` : undefined} data-testid="list-range">
            {pagerLabel && (
              <>
                <span className="range-of">{pagerLabel}</span>
                <span className="range-sep" aria-hidden>
                  ·
                </span>
              </>
            )}
            {pager.rangeText}
          </span>
          <button type="button" className="icon-btn" aria-label={tr('common.newer')} data-tip={tr('common.newer')} disabled={!pager.canPrev} onClick={pager.prev}>
            <ChevronLeft />
          </button>
          <button type="button" className="icon-btn" aria-label={tr('common.older')} data-tip={tr('common.older')} disabled={!pager.canNext} onClick={pager.next}>
            <ChevronRight />
          </button>
        </>
      )}

      <ConfirmDialog
        open={confirmForever}
        title={tr('mail.listToolbar.deleteForever')}
        body={tr('mail.listToolbar.thisPermanentlyDeletesConversationsFrom', { count: (any ? allIds.length : items.length) })}
        confirmLabel={tr('common.deleteForever')}
        danger
        onClose={() => setConfirmForever(false)}
        onConfirm={() => {
          setConfirmForever(false)
          void run({ ids: any ? allIds : items.map((c) => c.id), action: 'delete_forever' }).then(clear)
        }}
      />
      <PageReadConfirm open={confirmRead} items={items} where={listName} onClose={() => setConfirmRead(false)}
        onConfirm={() => {
          setConfirmRead(false)
          void run({ ids: items.filter((c) => c.unread_count > 0).map((c) => c.id), action: 'read' })
        }} />
    </div>
    {otherAll && (
      <div className="select-all-banner" role="status">
        <span>
          {excluded.size === 0 ? (
            <>{tx('mail.listToolbar.allConversationsInAreSelected', { total: otherAll.total.toLocaleString(appLocale()), otherAllName: otherAll.name || tr('mail.listToolbar.thisSection') }, { b: (c) => <b>{c}</b> })}</>
          ) : (
            <>
              {tx('mail.listToolbar.ofConversationsInAreSelected', { value: (otherAll.total - excluded.size).toLocaleString(appLocale()), count: otherAll.total, otherAllName: otherAll.name || tr('mail.listToolbar.thisSection'), excludedSize: excluded.size.toLocaleString(appLocale()) }, { b: (c) => <b>{c}</b> })}
            </>
          )}
        </span>
        <button type="button" className="btn text sm" onClick={() => { setSelected(new Set()); setAllMatchingKey(null) }}>
          {tr('mail.listToolbar.clearSelection')}
        </button>
      </div>
    )}
    {!otherAll && (allChecked || allMatching) && listQuery && listQuery.total > items.length && (
      <div className="select-all-banner" role="status">
        {allMatching ? (
          <>
            <span>
              {excluded.size === 0 ? (
                <>{tx('mail.listToolbar.allConversationsInAreSelected2', { total: listQuery.total.toLocaleString(appLocale()), listName }, { b: (c) => <b>{c}</b> })}</>
              ) : (
                <>
                  {tx('mail.listToolbar.ofConversationsInAreSelected2', { value: (listQuery.total - excluded.size).toLocaleString(appLocale()), count: listQuery.total, listName, excludedSize: excluded.size.toLocaleString(appLocale()) }, { b: (c) => <b>{c}</b> })}
                </>
              )}
            </span>
            <button type="button" className="btn text sm" onClick={clear}>
              {tr('mail.listToolbar.clearSelection')}
            </button>
          </>
        ) : (
          <>
            <span>
              {tx('mail.listToolbar.allConversationsOnThisPage', { itemsLength: items.length }, { b: (c) => <b>{c}</b> })}
            </span>
            <button type="button" className="btn text sm" onClick={() => setAllMatching(true)}>
              {tr('mail.listToolbar.selectAllConversationsIn', { count: listQuery.total, listName })}
            </button>
          </>
        )}
      </div>
    )}
    </>
  )
}

/** The confirmation of Mark all as read in the toolbar's ⋯: the unread rows loaded on screen. */
function PageReadConfirm({ open, items, where, onClose, onConfirm }: { open: boolean; items: ConversationSummary[]; where: string; onClose: () => void; onConfirm: () => void }) {
  const n = items.filter((c) => c.unread_count > 0).length
  const t = sectionConfirmText({ action: 'read', count: n, where, loaded: n, scope: 'page' })
  return (
    <ConfirmDialog open={open} title={t.title} body={t.body} confirmLabel={t.confirmLabel} focusConfirm onClose={onClose}
      onConfirm={() => (n ? onConfirm() : onClose())} />
  )
}
