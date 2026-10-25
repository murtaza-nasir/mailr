import { useIsFetching } from '@tanstack/react-query'
import { Bookmark, ChevronDown, ChevronRight, Clock3, Inbox, Menu, Plus, Tag } from 'lucide-react'
import { useRef, useState } from 'react'
import { OverflowMore, type OverflowCommand } from '../../components/OverflowMore'
import { useToolbarFit } from '../../hooks/useToolbarFit'
import { NavLink } from 'react-router'
import { qk } from '../../api/hooks'
import { WaitingBell } from '../../features/waiting/WaitingBell'
import { ComposeDock } from '../../features/compose/ComposeDock'
import { useMailCommands, type MailCommand } from '../../features/mail/commands'
import { useAppNav, useMailboxes, useMailRoute, type NavEntry } from '../../features/layout/mailboxes'
import { AccountFolders, CreateLabelDialogWithParent, LabelList, useExpanded } from '../../features/layout/Sidebar'
import { useFavoriteEntries, useNavTarget } from '../../features/layout/favorites'
import { useDragExpand } from '../../features/mail/dragDrop'
import { AccountFoldersHint, AccountGroupNav, useAccountGroups, useMailAccounts, useMailboxHeading } from '../../features/layout/accounts'
import { AccountButton, HelpButton, QuickSettingsButton } from '../../features/layout/TopBar'
import { AmbroseButton } from '../../features/layout/AmbroseButton'
import { SidePanel } from '../../features/rail/SideRail'
import { SearchBar } from '../../features/search/SearchBar'
import { mailPath, viewAccount, viewLabelOf, viewTitle } from '../../lib/views'
import { useUI } from '../../stores/ui'
import { GlobalOverlays } from '../shared/frame'
import { CommandPalette } from '../shared/PaletteHost'
import type { ShellProps } from '../types'
import type { MailAccount } from '../../api/types'
import { useSidebarCounts } from '../../features/layout/sidebarCounts'
import { t as tr, appLocale } from '../../i18n'

/** The toolbar's groups. Out of room (useToolbarFit), groups drop their labels in `fold` order, then
 *  Get Mail and Compose (their own steps); past that, groups leave for More in `hide` order. */
const TOOL_GROUPS: { ids: string[]; fold?: number; hide?: number }[] = [
  { ids: ['refresh', 'compose'] },
  { ids: ['reply', 'replyAll', 'forward'], fold: 3, hide: 7 },
  { ids: ['archive', 'restore', 'spam', 'notSpam', 'delete'], fold: 2 },
  { ids: ['label', 'move', 'star', 'read'], fold: 1, hide: 6 },
]
const BUTTON_FOLD: Record<string, number> = { refresh: 4, compose: 5 }

function ToolButton({ cmd }: { cmd: MailCommand }) {
  const label = cmd.id === 'refresh' ? tr('themes.desk.deskShell.getMail') : cmd.id === 'compose' ? tr('common.compose') : (cmd.short ?? cmd.label)
  return (
    <button type="button" className={`d-tool d-tool-${cmd.id}`} disabled={!cmd.enabled} onClick={(e) => cmd.run(e.currentTarget)} data-tip={`${cmd.label}${cmd.shortcut ? ` (${cmd.shortcut})` : ''}`} aria-label={cmd.label}
      data-fold={BUTTON_FOLD[cmd.id]}>
      {cmd.icon}
      <span className="lbl">{label}</span>
    </button>
  )
}

/**
 * A desktop mail client: compact toolbar, folder pane with unread and total counts, a sortable
 * message table with the preview below it, and a status bar. Dense, for people with a lot of mail.
 */
export function DeskShell({ children }: ShellProps) {
  const route = useMailRoute()
  const panel = useUI((s) => s.panel)
  const setPanel = useUI((s) => s.setPanel)
  const { byId, menus } = useMailCommands()
  // Calendar, Tasks and Recordings draw their own left column and commands: the mail tools and folders step aside.
  const onApp = route.app === 'calendar' || route.app === 'tasks' || route.app === 'recordings' || route.app === 'notes'
  const bar = useRef<HTMLDivElement>(null)
  const level = useToolbarFit(bar)
  const groups = TOOL_GROUPS.map((g) => ({ ...g, cmds: g.ids.map(byId).filter((c): c is MailCommand => !!c) }))
  const gone: OverflowCommand[] = onApp ? [] : groups.filter((g) => g.hide && level >= g.hide).flatMap((g) => g.cmds)

  return (
    <div className={`app desk-app${route.isSettings ? ' on-settings' : ''}${onApp ? ' on-app' : ''}`}>
      <div ref={bar} className="d-toolbar no-print" role="toolbar" aria-label={onApp ? tr('themes.desk.deskShell.toolbar') : tr('themes.desk.deskShell.mailToolbar')}>
        <button type="button" className="icon-btn d-menu" aria-label={tr('common.folders')} data-tip={tr('common.folders')} onClick={() => useUI.getState().setMobileNav(!useUI.getState().mobileNavOpen)}>
          <Menu />
        </button>
        {!onApp && groups.map((g, i) => (
          <div key={i} className="d-tools" data-fold={g.fold} data-fold-hide={g.hide}>
            {g.cmds.map((c) => (
              <ToolButton key={c.id} cmd={c} />
            ))}
          </div>
        ))}
        <OverflowMore commands={gone} className="d-tool d-tool-more" />
        {menus}
        <span className="spacer" />
        <SearchBar />
        <AmbroseButton />
        <WaitingBell className="icon-btn sm" />
        <HelpButton className="icon-btn sm hide-mobile" />
        <QuickSettingsButton className="icon-btn sm" />
        <AccountButton />
      </div>
      <SpacesBar />
      <FolderPane />
      <main className="main d-main" id="main">
        {children}
      </main>
      {panel && <div className="panel-scrim" aria-hidden onClick={() => setPanel(null)} />}
      {/* Renders nothing while no panel is open (and keeps a stepped-aside Ambrose chat, hidden). */}
      <SidePanel />
      <StatusBar />
      <ComposeDock />
      <CommandPalette />
      <GlobalOverlays />
    </div>
  )
}

function FolderRow({ e, active, depth = 0, onNavigate, favorite = false }: { e: NavEntry; active: boolean; depth?: number; onNavigate: () => void; favorite?: boolean }) {
  const unread = e.unread
  const t = useNavTarget(e.view, { favorite, title: e.title })
  return (
    <>
      <NavLink
        to={mailPath(e.view)}
        className={`d-folder${active ? ' active' : ''}${unread ? ' unread' : ''}${t.className}`}
        style={depth ? { paddingInlineStart: 10 + depth * 16 } : undefined}
        aria-current={active ? 'page' : undefined}
        onClick={onNavigate}
        title={e.tip ?? e.title}
        {...t.props}
      >
        {e.icon}
        <span className="name">{e.title}</span>
        <span className="n unread-n">{unread ? unread.toLocaleString(appLocale()) : ''}</span>
        <span className="n total-n">
          {e.waiting ? <span className="waiting-n" aria-label={tr('themes.desk.deskShell.waitingForAReply', { waiting: e.waiting })}><Clock3 size={11} aria-hidden />{e.waiting.toLocaleString(appLocale())}</span>
            : e.total ? e.total.toLocaleString(appLocale()) : ''}
        </span>
      </NavLink>
      {t.menu}
    </>
  )
}

function FolderPane() {
  const { labels, primary, secondary, categories } = useMailboxes()
  // The unified mailbox's heading: "All accounts" with several mail accounts, else the one address.
  const heading = useMailboxHeading(tr('themes.desk.deskShell.mailbox'))
  const accountGroups = useAccountGroups()
  const { current } = useMailRoute()
  const mobileOpen = useUI((s) => s.mobileNavOpen)
  const setMobileNav = useUI((s) => s.setMobileNav)
  const [cats, toggleCats] = useExpanded('mailr.desk.cats', false)
  const [labelsOpen, toggleLabels] = useExpanded('mailr.desk.labels', true)
  const [favOpen, toggleFav] = useExpanded('mailr.desk.fav', true)
  const favorites = useFavoriteEntries()
  const catsExpand = useDragExpand(cats, toggleCats)
  const labelsExpand = useDragExpand(labelsOpen, toggleLabels)
  const favExpand = useDragExpand(favOpen, toggleFav)
  const [creating, setCreating] = useState<string | null>(null)
  const [creatingIn, setCreatingIn] = useState<MailAccount | undefined>()
  const newLabel = (parent: string, account?: MailAccount) => {
    setCreatingIn(account)
    setCreating(parent)
  }
  const close = () => setMobileNav(false)
  const all = [...primary, ...secondary]
  const order = ['inbox', 'drafts', 'outbox', 'sent', 'scheduled', 'starred', 'snoozed', 'important', 'all', 'spam', 'trash']
  const folders = order.map((v) => all.find((e) => e.view === v)!).filter(Boolean)
  return (
    <>
      {mobileOpen && <div className="mobile-scrim" onClick={close} />}
      <nav className={`d-folders no-print${mobileOpen ? ' mobile-open' : ''}`} aria-label={tr('common.folders')}>
        <div className="d-folders-head">
          <span>{tr('common.folders')}</span>
          <span className="n" title={tr('common.unread')}>{tr('common.unread')}</span>
          <span className="n" title={tr('themes.desk.deskShell.total')}>{tr('themes.desk.deskShell.total')}</span>
        </div>
        {favorites.length > 0 && (
          <>
            <button type="button" className="d-folder d-group d-fav-head" aria-expanded={favOpen} onClick={toggleFav} {...favExpand}>
              {favOpen ? <ChevronDown /> : <ChevronRight />}
              <Bookmark />
              <span className="name">{tr('common.favorites')}</span>
            </button>
            {favOpen && favorites.map((e) => <FolderRow key={`fav-${e.view}`} e={e} active={current === e.view} depth={1} onNavigate={close} favorite />)}
          </>
        )}
        <div className="d-account">
          <Inbox />
          <span className="name" dir="auto">{heading}</span>
        </div>
        {folders.map((e) => (
          <FolderRow key={e.view} e={e} active={current === e.view} depth={1} onNavigate={close} />
        ))}
        <AccountFoldersHint className="d-acct-hint" onNavigate={close} />
        {/* Each account's own folders, while "Show each account separately" is on. */}
        {accountGroups.map((g) => (
          <AccountGroupNav key={g.account.id} g={g} storage="mailr.desk.acct" toggleClassName="d-folder d-group" toggleStyle={{ paddingInlineStart: 26 }}
            row={(e) => <FolderRow e={e} active={current === e.view} depth={2} onNavigate={close} />}
            onNewFolder={(a) => newLabel('', a)}
            folders={(g) => (
              <div className="d-labels-tree d-acct-labels">
                <AccountFolders g={g} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p, g.account)} />
              </div>
            )} />
        ))}
        <button type="button" className="d-folder d-group" aria-expanded={cats} onClick={toggleCats} style={{ paddingInlineStart: 26 }} {...catsExpand}>
          {cats ? <ChevronDown /> : <ChevronRight />}
          <span className="name">{tr('common.categories')}</span>
        </button>
        {cats && categories.map((e) => <FolderRow key={e.view} e={e} active={current === e.view} depth={2} onNavigate={close} />)}
        <div className="d-folder d-group" style={{ paddingInlineStart: 26 }}>
          <button type="button" className="d-group-toggle" aria-expanded={labelsOpen} onClick={toggleLabels} {...labelsExpand}>
            {labelsOpen ? <ChevronDown /> : <ChevronRight />}
            <Tag />
            <span className="name">{tr('common.labels')}</span>
          </button>
          <button type="button" className="icon-btn xs" aria-label={tr('common.createNewLabel')} data-tip={tr('common.createNewLabel')} onClick={() => newLabel('')}>
            <Plus />
          </button>
        </div>
        {labelsOpen && (
          <div className="d-labels-tree">
            <LabelList labels={labels} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p)} />
          </div>
        )}
      </nav>
      <CreateLabelDialogWithParent parent={creating} account={creatingIn} onClose={() => setCreating(null)} />
    </>
  )
}

function StatusBar() {
  const { view, q, isMail, convId, app } = useMailRoute()
  const { labels, primary, secondary } = useMailboxes()
  const { accounts } = useMailAccounts()
  const groups = useAccountGroups()
  const counts = useSidebarCounts().data?.views
  const selected = useUI((s) => s.selected.size)
  const shown = useUI((s) => s.visibleIds.length)
  const fetching = useIsFetching({ queryKey: qk.conversations }) > 0
  const entry = [...primary, ...secondary, ...groups.flatMap((g) => g.entries)].find((e) => e.view === view)
  const label = view.startsWith('label:') ? labels.find((l) => l.id === Number(view.slice(6))) : undefined
  // A label seen through an account: that account's numbers (none listed: nothing of it yet).
  const acctLabel = viewAccount(view) !== null && viewLabelOf(view) !== null ? (counts?.[view] ?? { unread: 0, total: 0 }) : undefined
  const unread = entry?.unread ?? acctLabel?.unread ?? label?.unread
  const total = entry?.total ?? acctLabel?.total ?? label?.total
  return (
    <footer className="d-status no-print" aria-live="polite">
      {app === 'calendar' || app === 'tasks' || app === 'recordings' || app === 'notes' ? (
        <span className="d-status-where">{app === 'calendar' ? tr('common.calendar') : app === 'tasks' ? tr('common.tasks') : app === 'notes' ? tr('common.notes') : tr('common.recordings')}</span>
      ) : isMail ? (
        <>
          <span className="d-status-where">{q ? tr('common.searchResults') : viewTitle(view, labels, accounts)}</span>
          {unread !== undefined && <span>{tr('themes.desk.deskShell.unread', { unread: unread.toLocaleString(appLocale()) })}</span>}
          {total !== undefined && <span>{tr('themes.desk.deskShell.total2', { total: total.toLocaleString(appLocale()) })}</span>}
          <span>{tr('themes.desk.deskShell.shown', { shown: shown.toLocaleString(appLocale()) })}</span>
          {selected > 0 && <span className="d-status-sel">{tr('themes.desk.deskShell.selected', { selected })}</span>}
          {convId && <span className="hide-narrow">{tr('themes.desk.deskShell.previewing1Conversation')}</span>}
        </>
      ) : (
        <span>{tr('common.settings')}</span>
      )}
      <span className="spacer" />
      <span className={`d-sync${fetching ? ' busy' : ''}`}>{fetching ? tr('themes.desk.deskShell.checkingForNewMail') : tr('common.upToDate')}</span>
    </footer>
  )
}

/** The spaces bar, Desk's app switcher: Mail, Calendar, Tasks and Notes on the far left. */
function SpacesBar() {
  const apps = useAppNav()
  return (
    <nav className="d-spaces no-print" aria-label={tr('common.apps')}>
      {apps.map((a) => (
        <NavLink key={a.app} to={a.to} className={`d-space${a.active ? ' active' : ''}`} aria-label={a.title} aria-current={a.active ? 'page' : undefined} data-tip={`${a.title} (${a.keys})`} data-space={a.app} data-app-nav={a.app}>
          {a.icon}
        </NavLink>
      ))}
    </nav>
  )
}
