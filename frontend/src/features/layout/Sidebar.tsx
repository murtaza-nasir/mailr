import {
  ChevronDown, ChevronRight, ChevronUp, EllipsisVertical, Pencil, Plus, Tag, Trash2, Settings2, EyeOff, Eye, Palette, FolderPlus, ListTree, Folders, Pin,
} from 'lucide-react'
import { resetInboxFocus } from '../mail/inboxFocus'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router'
import { useLabelList, useLabelMutations } from '../../api/hooks'
import type { Label, MailAccount } from '../../api/types'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover, SubMenu, useAnchor } from '../../components/Popover'
import { dotStyle } from '../../lib/labels'
import { labelLeaf } from '../../lib/format'
import { CLASSIC_FAVORITES_SKIP_SHOWN, favoritesToShow } from '../../lib/favorites'
import { accountLabelView, labelTree, mailPath, viewAccount, viewLabelOf } from '../../lib/views'
import { splitAccountFolders } from '../../lib/accountFolders'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { ColorPicker, CreateLabelDialog } from '../mail/menus'
import { useDragExpand, useDragging, useDropTarget } from '../mail/dragDrop'
import { FavoriteMenuItems, useFavoriteEntries, useNavTarget } from './favorites'
import { useAppNav, useMailboxes, useMailRoute, type NavEntry } from './mailboxes'
import { AccountDot, AccountFoldersHint, AccountGroupNav, useAccountGroups, useMailAccounts, type AccountGroup } from './accounts'
import { PinMenuItems, useAccountPins, useFolderDrag, useFolderRowPins, usePinDropZone, type PinPlace } from './folderPins'
import { t as tr, appLocale } from '../../i18n'

export function useExpanded(key: string, initial: boolean): [boolean, () => void] {
  const [v, setV] = useState(() => {
    try {
      const s = localStorage.getItem(key)
      return s === null ? initial : s === '1'
    } catch {
      return initial
    }
  })
  return [
    v,
    () => {
      setV(!v)
      try {
        localStorage.setItem(key, v ? '0' : '1')
      } catch {
        /* ignore */
      }
    },
  ]
}

/** Pages with their own navigation, where Classic's sidebar is only the apps (Sidebar). */
export function appsOnlyPath(pathname: string): boolean {
  return /^\/(calendar|tasks|notes|tables|recordings)(\/|$)/.test(pathname)
}

export function Sidebar() {
  const { labels, primary, secondary, categories } = useMailboxes()
  const accountGroups = useAccountGroups()
  const collapsed = useUI((s) => s.sidebarCollapsed)
  const mobileOpen = useUI((s) => s.mobileNavOpen)
  const setMobileNav = useUI((s) => s.setMobileNav)
  const openCompose = useCompose((s) => s.open)
  const [more, toggleMore] = useExpanded('mailr.nav.more', false)
  const [cats, toggleCats] = useExpanded('mailr.nav.cats', false)
  const [favOpen, toggleFav] = useExpanded('mailr.nav.fav', true)
  const allFavorites = useFavoriteEntries()
  // Classic always shows Inbox, Starred, Snoozed, Sent and Drafts right below Favorites, so the
  // group leaves them out and holds only what the user added (CLASSIC_FAVORITES_SKIP_SHOWN in
  // lib/favorites). With the default list (Inbox, Sent, Drafts) it hides.
  const shownViews = new Set(primary.map((e) => e.view))
  const favorites = favoritesToShow(allFavorites, shownViews, CLASSIC_FAVORITES_SKIP_SHOWN)
  const favoritesShown = favorites.map((e) => e.view)
  const showFavorites = favorites.length > 0
  const dragging = useDragging()
  const moreExpand = useDragExpand(more, toggleMore)
  const catsExpand = useDragExpand(cats, toggleCats)
  const favExpand = useDragExpand(favOpen, toggleFav)
  const [creating, setCreating] = useState<string | null>(null)
  const [creatingIn, setCreatingIn] = useState<MailAccount | undefined>()
  const newLabel = (parent: string, account?: MailAccount) => {
    setCreatingIn(account)
    setCreating(parent)
  }

  const { current } = useMailRoute()
  const apps = useAppNav()
  const close = () => setMobileNav(false)
  // Calendar, Tasks, Notes and Recordings have their own navigation: the mail folders and labels
  // stay out of them, and the sidebar is a rail of the apps (the 240 px mail nav
  // squeezed the week grid). The phone drawer keeps everything.
  const { pathname } = useLocation()
  const appsOnly = !mobileOpen && appsOnlyPath(pathname)

  // Collapsed: resting the pointer on the rail for a moment opens it as an overlay (the
  // page does not move); leaving closes it again.
  const [peek, setPeek] = useState(false)
  const peekTimer = useRef<number | null>(null)
  const clearPeekTimer = () => {
    if (peekTimer.current !== null) window.clearTimeout(peekTimer.current)
    peekTimer.current = null
  }
  const onEnter = () => {
    if (!collapsed || mobileOpen) return
    clearPeekTimer()
    peekTimer.current = window.setTimeout(() => setPeek(true), 250)
  }
  const onLeave = () => {
    clearPeekTimer()
    peekTimer.current = window.setTimeout(() => setPeek(false), 150)
  }
  useEffect(() => () => clearPeekTimer(), [])
  useEffect(() => {
    if (!collapsed) setPeek(false)
  }, [collapsed])
  // A conversation drag over the collapsed rail opens it like a resting pointer does.
  useEffect(() => {
    if (!dragging) setPeek(false)
  }, [dragging])

  return (
    <>
      {mobileOpen && <div className="mobile-scrim" onClick={close} />}
      {appsOnly ? (
        <nav className="sidebar collapsed apps-only no-print" aria-label={tr('common.apps')}>
          <div className="nav">
            <div className="nav-apps" role="group" aria-label={tr('common.apps')}>
              {apps.map((a) => (
                <NavLink key={a.app} to={a.to} className={() => `nav-item${a.active ? ' active' : ''}`}
                  data-tip={a.title} aria-current={a.active ? 'page' : undefined} data-app-nav={a.app}>
                  {a.icon}
                  <span className="name">{a.title}</span>
                </NavLink>
              ))}
            </div>
          </div>
        </nav>
      ) : (
      <nav
        className={`sidebar no-print${collapsed ? ' collapsed' : ''}${collapsed && peek ? ' peek' : ''}${mobileOpen ? ' mobile-open' : ''}`}
        aria-label={tr('layout.sidebar.mailboxes')}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onDragEnter={dragging && collapsed && !peek ? () => setPeek(true) : undefined}
      >
        <button
          type="button"
          className="compose-btn"
          onClick={() => {
            close()
            openCompose()
          }}
          data-tip={collapsed && !peek ? tr('common.compose') : undefined}
        >
          <Pencil />
          {(!collapsed || peek) && <span>{tr('common.compose')}</span>}
        </button>
        <div className="nav">
          {showFavorites && (
            <div className="nav-favorites" role="group" aria-label={tr('common.favorites')}>
              <button type="button" className="nav-item nav-group-toggle" onClick={toggleFav} aria-expanded={favOpen} {...favExpand}>
                <span className="caret">{favOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                <span className="name">{tr('common.favorites')}</span>
              </button>
              {favOpen && favorites.map((e) => <NavRow key={`fav-${e.view}`} e={e} active={current === e.view} onNavigate={close} favorite favoritesShown={favoritesShown} />)}
            </div>
          )}
          {primary.map((e) => (
            <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} favoritesShown={favoritesShown} />
          ))}
          {/* Each account's own folders, only while "Show each account separately" is on (the
              unified entries above stay; features/layout/accounts.tsx). */}
          {accountGroups.length > 0 && (
            <div className="nav-accounts" role="group" aria-label={tr('layout.accounts.accounts')} data-testid="nav-accounts">
              <div className="nav-section-head">
                <span className="spacer">{tr('layout.accounts.accounts')}</span>
              </div>
              {accountGroups.map((g) => (
                <AccountGroupNav key={g.account.id} g={g} storage="mailr.nav.acct"
                  row={(e) => <NavRow e={e} active={current === e.view} onNavigate={close} indent={1} />}
                  onNewFolder={(a) => newLabel('', a)}
                  folders={(g) => <AccountFolders g={g} depth={1} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p, g.account)} />} />
              ))}
            </div>
          )}
          {/* With two or more accounts and the groups off: one line offering them, until dismissed. */}
          {!collapsed && <AccountFoldersHint onNavigate={close} />}
          <button type="button" className="nav-item" onClick={toggleMore} aria-expanded={more} {...moreExpand}>
            {more ? <ChevronUp /> : <ChevronDown />}
            <span className="name">{more ? tr('common.less') : tr('common.more')}</span>
          </button>
          {more && (
            <>
              {secondary.map((e) => (
                <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} />
              ))}
              <button type="button" className="nav-item" onClick={toggleCats} aria-expanded={cats} {...catsExpand}>
                <span className="caret">{cats ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                <Tag />
                <span className="name">{tr('common.categories')}</span>
              </button>
              {cats && categories.map((e) => <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} indent={1} />)}
              <NavLink to="/settings/mail/labels" className="nav-item" onClick={close}>
                <Settings2 />
                <span className="name">{tr('layout.sidebar.manageLabels')}</span>
              </NavLink>
              <button type="button" className="nav-item" onClick={() => setCreating('')}>
                <Plus />
                <span className="name">{tr('common.createNewLabel')}</span>
              </button>
            </>
          )}
          {/* The app switcher, the same apps in the same order as every theme: Mail, Calendar,
              Tasks, Notes. Ambrose is the top bar's button (AmbroseButton). On phones the bottom
              bar holds the apps, so the drawer leaves them out (phone.css). */}
          <div className="nav-apps" role="group" aria-label={tr('common.apps')}>
            <div className="nav-section-head">
              <span className="spacer">{tr('common.apps')}</span>
            </div>
            {apps.map((a) => (
              <NavLink
                key={a.app}
                to={a.to}
                // In Mail the folder above is already marked; the Mail entry only says where you are.
                className={() => `nav-item${a.active ? (a.app === 'mail' ? ' app-current' : ' active') : ''}`}
                onClick={close}
                data-tip={a.title}
                aria-current={a.active ? 'page' : undefined}
                data-app-nav={a.app}
              >
                {a.icon}
                <span className="name">{a.title}</span>
              </NavLink>
            ))}
          </div>
          <div className="nav-section-head">
            <span className="spacer">{tr('common.labels')}</span>
            <button type="button" className="icon-btn sm" aria-label={tr('common.createNewLabel')} data-tip={tr('common.createNewLabel')} onClick={() => setCreating('')}>
              <Plus size={20} />
            </button>
          </div>
          <LabelList labels={labels} current={current} onNavigate={close} onCreateChild={(parent) => newLabel(parent)} />
        </div>
      </nav>
      )}
      <CreateLabelDialogWithParent parent={creating} account={creatingIn} onClose={() => setCreating(null)} />
    </>
  )
}

/** The New label dialog under a parent; opened from an account's group (`account`), it makes a
 *  folder of that account (the label's home) and opens it there. */
export function CreateLabelDialogWithParent({ parent, account, onClose }: { parent: string | null; account?: MailAccount; onClose: () => void }) {
  const nav = useNavigate()
  if (parent === null) return null
  return (
    <CreateLabelDialog
      open
      key={`${account?.id ?? ''}:${parent}`}
      initialName=""
      initialParent={parent}
      account={account}
      onClose={onClose}
      onCreated={(l) => nav(mailPath(account ? accountLabelView(account.id, l.id) : `label:${l.id}`))}
    />
  )
}

/** A folder (or a favorite) in the sidebar-style navs: Classic, Office's folder pane and the
 *  Cards drawer. Right-click for Favorites; a drop target for dragged conversations. */
export function NavRow({ e, active, onNavigate, indent = 0, favorite = false, favoritesShown }: { e: NavEntry; active: boolean; onNavigate: () => void; indent?: number; favorite?: boolean; favoritesShown?: readonly string[] }) {
  const t = useNavTarget(e.view, { favorite, title: e.title, among: favoritesShown })
  return (
    <>
      <NavLink
        to={mailPath(e.view)}
        className={`nav-item${active ? ' active' : ''}${e.bold ? ' unread' : ''}${t.className}`}
        style={indent ? ({ '--depth': indent } as CSSProperties) : undefined}
        onClick={() => {
          // The Inbox from the navigation: every tab's sections again (a section shown alone ends).
          if (e.view === 'inbox') resetInboxFocus()
          onNavigate()
        }}
        data-tip={e.tip ?? e.title}
        aria-current={active ? 'page' : undefined}
        {...t.props}
      >
        {e.icon}
        <span className="name">{e.title}</span>
        {!!e.count && <span className="count">{e.count.toLocaleString(appLocale())}</span>}
      </NavLink>
      {t.menu}
    </>
  )
}

/**
 * The label tree of the sidebar-style navs. With `account` (an account's group, while "Show each
 * account separately" is on) it lists that account's folders: each label opens
 * `acct:<id>:label:<label id>` with the account's own numbers, and nothing shows when there are
 * none. Without it, the unified Labels list: a label made in an account carries that account's dot
 * (two or more accounts). `depth` indents the whole tree (a group's rows sit one step in).
 */
// ACT mail.account_label_view @web.nav
export function LabelList({ labels, current, onNavigate, onCreateChild, account, depth: base = 0, pinOrder }: { labels: Label[]; current: string; onNavigate: () => void; onCreateChild: (parent: string) => void; account?: AccountGroup; depth?: number; pinOrder?: readonly number[] }) {
  const tree = useMemo(() => labelTree(labels), [labels])
  const { byId, multi } = useMailAccounts()
  const storeKey = account ? `mailr.nav.collapsedLabels.acct.${account.account.id}` : 'mailr.nav.collapsedLabels'
  const [collapsedSet, setCollapsed] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(storeKey) ?? '[]') as string[])
    } catch {
      return new Set()
    }
  })
  const toggle = (name: string) => {
    const next = new Set(collapsedSet)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setCollapsed(next)
    try {
      localStorage.setItem(storeKey, JSON.stringify([...next]))
    } catch {
      /* ignore */
    }
  }
  const viewOf = (l: Label) => (account ? accountLabelView(account.account.id, l.id) : `label:${l.id}`)
  const unreadOf = (l: Label) => (account ? account.views[viewOf(l)]?.unread ?? 0 : l.unread)
  const hiddenByAncestor = (name: string) => {
    const parts = name.split('/')
    for (let i = 1; i < parts.length; i++) if (collapsedSet.has(parts.slice(0, i).join('/'))) return true
    return false
  }
  // A home folder shows in its account even while it holds none of the account's mail; the
  // "show if unread" and "hide" choices still apply, with the account's own numbers.
  const visible = tree.filter(({ label }) => {
    if (hiddenByAncestor(label.name)) return false
    if (label.show_in_list === 'hide') return current === viewOf(label)
    if (label.show_in_list === 'unread') return unreadOf(label) > 0 || current === viewOf(label)
    return true
  })
  if (!visible.length) return account ? null : <div className="nav-item muted" style={{ cursor: 'default' }}>{tr('layout.sidebar.noLabelsYet')}</div>
  return (
    <>
      {visible.map(({ label, depth, hasChildren }) => {
        const home = !account && multi && label.home_account_id ? byId.get(label.home_account_id) : undefined
        return (
          <LabelRow key={label.id} label={label} view={viewOf(label)} unread={unreadOf(label)} home={home} inAccount={!!account}
            depth={base + depth} hasChildren={hasChildren} collapsed={collapsedSet.has(label.name)} onToggle={() => toggle(label.name)}
            active={current === viewOf(label)} onNavigate={onNavigate} onCreateChild={onCreateChild}
            place={account ? { accountId: account.account.id, address: account.account.address, pinned: false, order: pinOrder ?? [] } : undefined}
            fullName={!!account && depth === 0 && label.name.includes('/')} />
        )
      })}
    </>
  )
}

/**
 * An account's folders in its group (features/layout/folderPins.tsx): the
 * pinned ones in the user's order, the unpinned ones with unread mail while the option is on (and
 * the open folder while the rest are closed), then one row "More folders (n)" with the unread sum
 * of the folders inside it, which opens to the rest as a tree. Open or closed is a setting, so it
 * follows the user. Every sidebar-style theme draws this (Classic, Office, Desk, Cards, phones).
 */
export function AccountFolders({ g, current, onNavigate, onCreateChild, depth = 0 }: { g: AccountGroup; current: string; onNavigate: () => void; onCreateChild: (parent: string) => void; depth?: number }) {
  const p = useAccountPins()
  const all = useLabelList()
  const id = g.account.id
  const address = g.account.address
  const open = p.moreOpen(id)
  const pinIds = p.pinsOf(id)
  const curLabel = viewAccount(current) === id ? viewLabelOf(current) : null
  const split = useMemo(
    () => splitAccountFolders(g.labels, all, pinIds, (l) => g.views[accountLabelView(id, l.id)]?.unread ?? 0, { showUnread: p.showUnread, moreOpen: open, current: curLabel }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pinIds is a fresh array of the same setting
    [g.labels, g.views, all, JSON.stringify(pinIds), p.showUnread, open, curLabel, id],
  )
  const order = split.pinned.map((l) => l.id)
  const dragFrom = useFolderDrag((s) => s.drag)
  // "Pin here" shows while a folder is dragged from this group's More folders or from the unified
  // Labels list (the rows below it move down by one row only during that drag).
  const pinZone = usePinDropZone(id, address, 'pin')
  const showZone = pinZone.active && (dragFrom?.accountId === id || dragFrom?.accountId === null)
  const moreZone = usePinDropZone(id, address, 'unpin')
  const toggle = () => p.setMoreOpen(id, !open)
  const expand = useDragExpand(open, toggle)
  const row = (l: Label, pinned: boolean) => {
    const view = accountLabelView(id, l.id)
    return (
      <LabelRow key={`${pinned ? 'p' : 's'}${l.id}`} label={l} view={view} unread={g.views[view]?.unread ?? 0} inAccount depth={depth}
        hasChildren={false} collapsed={false} onToggle={() => {}} active={current === view} onNavigate={onNavigate} onCreateChild={onCreateChild}
        place={{ accountId: id, address, pinned, order }} fullName />
    )
  }
  return (
    <>
      {split.pinned.map((l) => row(l, true))}
      {showZone && (
        <div className={`nav-item pin-zone${pinZone.over ? ' drop-over' : ''}`} style={{ '--depth': depth } as CSSProperties} data-testid={`acct-pin-zone-${id}`} {...pinZone.props}>
          <Pin />
          <span className="name">{tr('layout.folderPins.pinHere')}</span>
        </div>
      )}
      {split.surfaced.map((l) => row(l, false))}
      {split.rest.length > 0 && (
        <button type="button" className={`nav-item acct-more-folders${!open && split.restUnread > 0 ? ' unread' : ''}${moreZone.over ? ' drop-over' : ''}`}
          style={{ '--depth': depth } as CSSProperties} aria-expanded={open} onClick={toggle} data-testid={`acct-more-${id}`}
          data-tip={tr('layout.folderPins.moreFoldersTip', { count: split.rest.length, address })} {...moreZone.props} {...expand}>
          <Folders />
          <span className="name">{tr('layout.folderPins.moreFolders')}</span>
          {/* Closed, the row carries the unread mail of the folders inside it; open, they show their own. */}
          {!open && split.restUnread > 0 && <span className="count">{split.restUnread.toLocaleString(appLocale())}</span>}
          {/* The disclosure chevron at the end: every theme places it alike (the label tree's carets
              hang at the start, where Cards has no room). */}
          <span className="more-chevron" aria-hidden>{open ? <ChevronUp /> : <ChevronDown />}</span>
        </button>
      )}
      {open && split.rest.length > 0 && (
        <LabelList labels={split.rest} account={g} depth={depth + 1} current={current} onNavigate={onNavigate} onCreateChild={onCreateChild} pinOrder={order} />
      )}
    </>
  )
}

function LabelRow({ label, view, unread, home, inAccount, depth, hasChildren, collapsed, onToggle, active, onNavigate, onCreateChild, place, fullName = false }: { label: Label; view: string; unread: number; home?: MailAccount; inAccount: boolean; depth: number; hasChildren: boolean; collapsed: boolean; onToggle: () => void; active: boolean; onNavigate: () => void; onCreateChild: (p: string) => void
  /** In an account's group: pinned or not, for the menu, keys and drag (features/layout/folderPins.tsx). */
  place?: PinPlace
  /** The whole path as the name (a pinned folder, or one whose parent is not beside it). */
  fullName?: boolean }) {
  const menu = useAnchor()
  const { update, remove } = useLabelMutations()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(label.name)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const drop = useDropTarget(view)
  // A collapsed label opens when a conversation drag rests on it, so its sublabels can take the drop.
  const expand = useDragExpand(!hasChildren || !collapsed, onToggle)
  const pins = useFolderRowPins(label.id, place)
  return (
    <>
      <NavLink
        to={mailPath(view)}
        className={`nav-item${active ? ' active' : ''}${unread > 0 ? ' unread' : ''}${drop.over ? ' drop-over' : ''}${pins.className}`}
        style={{ '--depth': depth } as CSSProperties}
        onClick={onNavigate}
        data-tip={label.name}
        data-drop-view={view}
        data-folder={label.id}
        onMouseDown={(e) => {
          // No focus on a right press: the menu would close on the scroll it can cause.
          if (e.button === 2) e.preventDefault()
        }}
        onContextMenu={(e) => {
          if (e.shiftKey) return
          e.preventDefault()
          const at = new DOMRect(e.clientX, e.clientY, 0, 0)
          // After any scroll already under way (the menu would close on it at once).
          requestAnimationFrame(() => menu.show(at))
        }}
        {...drop.props}
        {...pins.props}
        {...(expand.onDragEnter ? { onDragEnter: expand.onDragEnter, onDragLeave: expand.onDragLeave } : {})}
      >
        {hasChildren && (
          <span
            className="caret"
            role="button"
            aria-label={collapsed ? tr('layout.sidebar.expand') : tr('layout.sidebar.collapse')}
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onToggle()
            }}
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          </span>
        )}
        <span className="label-dot">
          <span style={dotStyle(label.color)} />
        </span>
        <span className="name">{fullName ? label.name : labelLeaf(label.name)}</span>
        {home && <AccountDot account={home} className="label-home" />}
        {unread > 0 && <span className="count hide-on-hover">{unread.toLocaleString(appLocale())}</span>}
        <button
          type="button"
          className="icon-btn xs more-btn"
          aria-label={tr('layout.sidebar.optionsFor', { labelName: label.name })}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            menu.toggle(e.currentTarget)
          }}
        >
          <EllipsisVertical size={16} />
        </button>
      </NavLink>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="right-start" minWidth={240}>
        <SubMenu icon={<ListTree />} label={tr('layout.sidebar.inLabelList')} minWidth={200}>
          {(['show', 'unread', 'hide'] as const).map((v) => (
            <MenuItem key={v} selected={label.show_in_list === v} onSelect={() => { menu.close(); update.mutate({ id: label.id, show_in_list: v }) }}>
              {v === 'show' ? tr('common.show') : v === 'unread' ? tr('layout.sidebar.showIfUnread') : tr('common.hide')}
            </MenuItem>
          ))}
        </SubMenu>
        <MenuItem icon={label.show_in_message_list ? <EyeOff /> : <Eye />} onSelect={() => { menu.close(); update.mutate({ id: label.id, show_in_message_list: !label.show_in_message_list }) }}>
          {label.show_in_message_list ? tr('layout.sidebar.hideInMessageList') : tr('layout.sidebar.showInMessageList')}
        </MenuItem>
        <SubMenu icon={<Palette />} label={tr('layout.sidebar.labelColour')} panelRole="dialog" minWidth={0}>
          <ColorPicker
            value={label.color}
            name={label.name}
            onPick={(col) => {
              menu.close()
              update.mutate({ id: label.id, color: col })
            }}
          />
        </SubMenu>
        <MenuSep />
        <MenuItem icon={<Pencil />} onSelect={() => { menu.close(); setName(label.name); setEditing(true) }}>
          {tr('common.edit2')}
        </MenuItem>
        <MenuItem icon={<Trash2 />} onSelect={() => { menu.close(); setConfirmDelete(true) }}>
          {tr('common.removeLabel')}
        </MenuItem>
        <MenuItem icon={<FolderPlus />} onSelect={() => { menu.close(); onCreateChild(label.name) }}>
          {inAccount ? tr('layout.accounts.newFolderInside') : tr('layout.sidebar.addSublabel')}
        </MenuItem>
        {!inAccount && <MenuSep />}
        {!inAccount && <FavoriteMenuItems view={view} close={menu.close} />}
        {place && <MenuSep />}
        {place && <PinMenuItems place={place} labelId={label.id} close={menu.close} />}
      </Popover>
      <Dialog
        open={editing}
        onClose={() => setEditing(false)}
        title={tr('layout.sidebar.editLabel')}
        footer={
          <>
            <button type="button" className="btn text" onClick={() => setEditing(false)}>
              {tr('common.cancel')}
            </button>
            <button
              type="button"
              className="btn filled"
              disabled={!name.trim() || name.trim() === label.name}
              onClick={() => {
                update.mutate({ id: label.id, name: name.trim() })
                setEditing(false)
              }}
            >
              {tr('common.save')}
            </button>
          </>
        }
      >
        <div className="field">
          <label htmlFor={`edit-label-${label.id}`}>{tr('layout.sidebar.labelNameUseToNest')}</label>
          <input id={`edit-label-${label.id}`} className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
      </Dialog>
      <ConfirmDialog
        open={confirmDelete}
        title={tr('common.removeLabel')}
        body={tr('layout.sidebar.deleteTheLabelConversationsKeep', { labelName: label.name })}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false)
          remove.mutate(label.id)
        }}
      />
    </>
  )
}
