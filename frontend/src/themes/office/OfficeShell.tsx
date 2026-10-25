import { ChevronDown, ChevronRight, PanelLeft, Pencil, Plus, Settings } from 'lucide-react'
import { useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { Logo } from '../../components/bits'
import { WaitingBell } from '../../features/waiting/WaitingBell'
import { ComposeDock } from '../../features/compose/ComposeDock'
import { ComposeFab } from '../../features/compose/ComposeFab'
import { AccountButton, HelpButton, QuickSettingsButton } from '../../features/layout/TopBar'
import { AmbroseButton } from '../../features/layout/AmbroseButton'
import { AccountFolders, CreateLabelDialogWithParent, LabelList, NavRow, useExpanded } from '../../features/layout/Sidebar'
import { useFavoriteEntries } from '../../features/layout/favorites'
import { useDragExpand } from '../../features/mail/dragDrop'
import { AccountDot, AccountFoldersHint, AccountGroupMenu, useAccountGroups, useMailAccounts, useMailboxHeading } from '../../features/layout/accounts'
import { useAppNav, useMailboxes, useMailRoute, type AppNavEntry, type NavEntry } from '../../features/layout/mailboxes'
import { SidePanel } from '../../features/rail/SideRail'
import { SplitHandle, useSplitSize } from '../../features/mail/SplitHandle'
import { SearchBar } from '../../features/search/SearchBar'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { viewTitle } from '../../lib/views'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { GlobalOverlays } from '../shared/frame'
import { CommandPalette } from '../shared/PaletteHost'
import type { ShellProps } from '../types'
import type { MailAccount } from '../../api/types'
import { OfficeReader } from './OfficeReader'
import { AppRibbon, Ribbon } from './Ribbon'
import { t } from '../../i18n'

const appName = (a: AppNavEntry) => a.title

/**
 * Workspace: a deep ocean title bar with centred search, an app bar, a folder tree, a ribbon of
 * labelled commands, a two-line message list with date groups, and a tabbed reading pane.
 */
export function OfficeShell({ children }: ShellProps) {
  const route = useMailRoute()
  const mobile = useMediaQuery('(max-width: 900px)')
  const panel = useUI((s) => s.panel)
  const setPanel = useUI((s) => s.setPanel)
  const collapsed = useUI((s) => s.sidebarCollapsed)
  const mobileOpen = useUI((s) => s.mobileNavOpen)
  const windows = useCompose((s) => s.windows)
  const labels = useMailboxes().labels
  const { accounts } = useMailAccounts()
  const nav = useNavigate()
  // The reading area holds compose tabs, so it stays mounted; off the mail pages it shows
  // only while a message is being written.
  const readerShown = route.isMail || windows.length > 0
  const splitRef = useRef<HTMLDivElement>(null)
  const split = useSplitSize('office', 'right', splitRef)
  const resizable = readerShown && !mobile && !route.isSettings
  const appNav = useAppNav()
  // Calendar and Tasks draw their own left column; the folder pane is then a phone drawer only.
  const onApp = route.app === 'calendar' || route.app === 'tasks' || route.app === 'recordings' || route.app === 'notes'

  return (
    <div className={`app office-app${collapsed ? ' folders-collapsed' : ''}${route.isSettings ? ' on-settings' : ''}${onApp ? ' on-app' : ''}${onApp && !mobile && route.app !== 'recordings' ? ' has-app-ribbon' : ''}`}>
      <header className="o-titlebar no-print">
        <button
          type="button"
          className="icon-btn"
          aria-label={mobile ? t('common.folders') : collapsed ? t('themes.office.officeShell.showFolderPane') : t('themes.office.officeShell.hideFolderPane')}
          data-tip={mobile ? t('common.folders') : t('themes.office.officeShell.folderPane')}
          onClick={() => (mobile ? useUI.getState().setMobileNav(!mobileOpen) : useUI.getState().toggleSidebar())}
        >
          <PanelLeft />
        </button>
        <a className="o-brand" href="/mail/inbox" onClick={(e) => { e.preventDefault(); nav('/mail/inbox') }} aria-label={t('themes.office.officeShell.mailrInbox')}>
          <Logo />
          <span>Mailr</span>
        </a>
        <SearchBar />
        <div className="o-title-right">
          <AmbroseButton />
          <WaitingBell />
          <HelpButton className="icon-btn hide-mobile" />
          <QuickSettingsButton />
          <AccountButton />
        </div>
      </header>

      <nav className="o-appbar no-print" aria-label={t('common.apps')}>
        {appNav.map((a) => (
          <Link key={a.app} to={a.to} className={`o-app${a.active ? ' active' : ''}`} aria-label={appName(a)} aria-current={a.active ? 'page' : undefined} data-tip={`${appName(a)} (${a.keys})`} data-app-nav={a.app} onClick={() => setPanel(null)}>
            {a.icon}
          </Link>
        ))}
        <span className="spacer" />
        <Link to="/settings" className={`o-app${route.isSettings ? ' active' : ''}`} aria-label={t('common.settings')} data-tip={t('common.settings')}>
          <Settings />
        </Link>
      </nav>

      <FolderPane />

      <div className="o-work">
        {route.isMail && !mobile && <Ribbon />}
        {onApp && !mobile && route.app !== 'recordings' && <AppRibbon app={route.app as 'calendar' | 'tasks' | 'notes'} />}
        <div
          ref={splitRef}
          className={`o-split${readerShown && !mobile ? ' with-reader' : ''}`}
          style={split.size && resizable ? ({ '--o-list-w': `${split.size}px` } as CSSProperties) : undefined}
        >
          <main className="main o-main" id="main">
            {route.isMail && !(mobile && route.convId) && <ListTitle view={route.view} q={route.q} labels={labels} accounts={accounts} />}
            {children}
          </main>
          {resizable && <SplitHandle pane="right" splitRef={splitRef} size={split.size} onSize={split.set} />}
          {!mobile && <OfficeReader hidden={!readerShown} />}
        </div>
      </div>

      {panel && <div className="panel-scrim" aria-hidden onClick={() => setPanel(null)} />}
      {/* Renders nothing while no panel is open (and keeps a stepped-aside Ambrose chat, hidden). */}
      <SidePanel />
      {mobile && route.isMail && (
        <ComposeFab />
      )}
      {mobile && <ComposeDock />}
      <CommandPalette />
      <GlobalOverlays />
    </div>
  )
}

function ListTitle({ view, q, labels, accounts }: { view: string; q: string; labels: ReturnType<typeof useMailboxes>['labels']; accounts: ReturnType<typeof useMailAccounts>['accounts'] }) {
  return (
    <div className="o-list-title">
      <h1>{q ? t('common.searchResults') : viewTitle(view, labels, accounts).split('/').pop()}</h1>
    </div>
  )
}

function Section({ id, title, children, action, lead }: { id: string; title: string; children: ReactNode; action?: ReactNode; lead?: ReactNode }) {
  const [open, toggle] = useExpanded(`mailr.office.folders.${id}`, true)
  const expand = useDragExpand(open, toggle)
  return (
    <div className="o-section" data-section={id}>
      <div className="o-section-head">
        <button type="button" className="o-section-toggle" aria-expanded={open} onClick={toggle} {...expand}>
          {open ? <ChevronDown /> : <ChevronRight />}
          {lead}
          <bdi className="o-section-title">{title}</bdi>
        </button>
        {action}
      </div>
      {open && <div className="o-section-body">{children}</div>}
    </div>
  )
}

function FolderPane() {
  const { labels, primary, secondary, categories } = useMailboxes()
  // The unified mailbox's node: "All accounts" with several mail accounts, else the one address.
  const heading = useMailboxHeading(t('themes.office.officeShell.mailbox'))
  const accountGroups = useAccountGroups()
  const { current } = useMailRoute()
  const mobileOpen = useUI((s) => s.mobileNavOpen)
  const setMobileNav = useUI((s) => s.setMobileNav)
  const [creating, setCreating] = useState<string | null>(null)
  const [creatingIn, setCreatingIn] = useState<MailAccount | undefined>()
  const newLabel = (parent: string, account?: MailAccount) => {
    setCreatingIn(account)
    setCreating(parent)
  }
  const close = () => setMobileNav(false)
  const all = [...primary, ...secondary]
  // Favorites: the user's own list (settings.nav_favorites).
  const fav = useFavoriteEntries()
  const row = (e: NavEntry, key = e.view, favorite = false) => <NavRow key={key} e={e} active={current === e.view} onNavigate={close} favorite={favorite} />
  const openCompose = () => {
    close()
    useCompose.getState().open()
  }

  return (
    <>
      {mobileOpen && <div className="mobile-scrim" onClick={close} />}
      <nav className={`o-folders no-print${mobileOpen ? ' mobile-open' : ''}`} aria-label={t('common.folders')}>
        <button type="button" className="btn filled o-new-mail" onClick={openCompose}>
          <Pencil />{' '}{t('common.compose')}
        </button>
        <Section id="fav" title={t('common.favorites')}>
          {fav.length ? fav.map((e) => row(e, `fav-${e.view}`, true)) : <div className="o-fav-empty">{t('themes.office.officeShell.rightClickAFolderOr')}</div>}
        </Section>
        <Section id="account" title={heading}>
          {all.map((e) => row(e))}
          <Section id="categories" title={t('common.categories')}>
            {categories.map((e) => row(e))}
          </Section>
        </Section>
        <AccountFoldersHint className="o-acct-hint" onNavigate={close} />
        {/* One node per account, as in Outlook, while "Show each account separately" is on. */}
        {accountGroups.map((g) => (
          <Section key={g.account.id} id={`acct.${g.account.id}`} title={g.account.address} lead={<AccountDot account={g.account} label={false} />}
            action={<AccountGroupMenu account={g.account} onNewFolder={(a) => newLabel('', a)} />}>
            {g.entries.map((e) => row(e))}
            {/* The account's folders: the labels that hold its mail and those made in it. */}
            <AccountFolders g={g} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p, g.account)} />
          </Section>
        ))}
        <Section
          id="labels"
          title={t('common.labels')}
          action={
            <button type="button" className="icon-btn xs" aria-label={t('common.createNewLabel')} data-tip={t('common.createNewLabel')} onClick={() => newLabel('')}>
              <Plus />
            </button>
          }
        >
          <LabelList labels={labels} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p)} />
        </Section>
      </nav>
      <CreateLabelDialogWithParent parent={creating} account={creatingIn} onClose={() => setCreating(null)} />
    </>
  )
}
