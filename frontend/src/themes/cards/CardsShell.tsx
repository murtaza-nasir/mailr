import { LayoutGrid, Menu, Pencil, Plus, Settings, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useModalFocus } from '../../hooks/useModalFocus'
import { NavLink } from 'react-router'
import { WaitingBell } from '../../features/waiting/WaitingBell'
import { ComposeDock } from '../../features/compose/ComposeDock'
import { ComposeFab } from '../../features/compose/ComposeFab'
import { useAppNav, useMailboxes, useMailRoute, type AppNavEntry, type NavEntry } from '../../features/layout/mailboxes'
import { AccountFolders, CreateLabelDialogWithParent, LabelList, NavRow } from '../../features/layout/Sidebar'
import { useFavoriteEntries, useNavTarget } from '../../features/layout/favorites'
import { useDragExpand, useDragging } from '../../features/mail/dragDrop'
import { AccountDot, AccountFoldersHint, AccountGroupMenu, useAccountGroups } from '../../features/layout/accounts'
import { AccountButton, QuickSettingsButton } from '../../features/layout/TopBar'
import { AmbroseButton } from '../../features/layout/AmbroseButton'
import { SidePanel } from '../../features/rail/SideRail'
import { SearchBar } from '../../features/search/SearchBar'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { mailPath } from '../../lib/views'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { GlobalOverlays } from '../shared/frame'
import { CommandPalette } from '../shared/PaletteHost'
import type { ShellProps } from '../types'
import type { MailAccount } from '../../api/types'
import { t as tr } from '../../i18n'

// The rail's app switcher: Mail, Calendar, Tasks and Notes, as in every theme. Mail opens the
// Inbox and carries its unread count; Ambrose is the top bar's button (AmbroseButton).
function AppLinkDest({ a, onClick, active = a.active }: { a: AppNavEntry; onClick?: () => void; active?: boolean }) {
  return (
    <NavLink to={a.to} className={() => `c-dest${active ? ' active' : ''}`} aria-current={a.active ? 'page' : undefined} data-app-nav={a.app} onClick={onClick}>
      <span className="c-ind">{a.icon}</span>
      <span className="c-dest-label">{a.title}</span>
    </NavLink>
  )
}

/** Mail on the rail: it opens the Inbox, shows its unread count, and takes conversation drops and
 *  the favourites menu as the Inbox entry did. */
function MailDest({ a, onClick, badge, active }: { a: AppNavEntry; onClick?: () => void; badge?: number; active: boolean }) {
  const t = useNavTarget('inbox', { title: tr('common.inbox') })
  return (
    <>
      {t.menu}
      <NavLink to={mailPath('inbox')} className={() => `c-dest${active ? ' active' : ''}${t.className}`} aria-current={a.active ? 'page' : undefined} data-app-nav="mail" onClick={onClick} {...t.props}>
        <span className="c-ind">
          {a.icon}
          {!!badge && <span className="c-dest-badge">{badge > 99 ? '99+' : badge}</span>}
        </span>
        <span className="c-dest-label">{a.title}</span>
      </NavLink>
    </>
  )
}

function Dest({ e, active, onClick }: { e: NavEntry; active: boolean; onClick?: () => void }) {
  const t = useNavTarget(e.view, { title: e.title })
  return (
    <>
    {t.menu}
    <NavLink to={mailPath(e.view)} className={`c-dest${active ? ' active' : ''}${t.className}`} aria-current={active ? 'page' : undefined} onClick={onClick} {...t.props}>
      <span className="c-ind">
        {e.icon}
        {!!e.count && e.view === 'inbox' && <span className="c-dest-badge">{e.count > 99 ? '99+' : e.count}</span>}
      </span>
      <span className="c-dest-label">{e.title}</span>
    </NavLink>
    </>
  )
}

/** "More" on the rail: opens the drawer, also when a conversation drag rests on it. */
function MoreDest({ onOpen }: { onOpen: () => void }) {
  const open = useUI((s) => s.mobileNavOpen)
  const expand = useDragExpand(open, onOpen)
  return (
    <button type="button" className="c-dest" onClick={onOpen} {...expand}>
      <span className="c-ind">
        <LayoutGrid />
      </span>
      <span className="c-dest-label">{tr('common.more')}</span>
    </button>
  )
}

/**
 * Expressive and colourful: a navigation rail with a compose FAB, conversations as rounded
 * cards bundled into people, notifications and newsletters, a tonal palette from your accent,
 * and sheets on phones (the shared phone bar, PhoneChrome, replaces the rail there).
 */
export function CardsShell({ children }: ShellProps) {
  const route = useMailRoute()
  const { primary } = useMailboxes()
  const mobile = useMediaQuery('(max-width: 900px)')
  const panel = useUI((s) => s.panel)
  const setPanel = useUI((s) => s.setPanel)
  const windows = useCompose((s) => s.windows)
  const openDrawer = () => useUI.getState().setMobileNav(true)
  // A drawer a drag opened closes again when the drag ends.
  const dragging = useDragging()
  const openedByDrag = useRef(false)
  const openForDrag = () => {
    openedByDrag.current = true
    openDrawer()
  }
  useEffect(() => {
    if (!dragging && openedByDrag.current) {
      openedByDrag.current = false
      useUI.getState().setMobileNav(false)
    }
  }, [dragging])
  const sheet = mobile && windows.some((w) => w.mode === 'normal')
  // While in Mail, the rail also lists the main mail views under the apps; the rest are in the drawer.
  const main = primary.filter((e) => ['starred', 'snoozed', 'sent', 'drafts'].includes(e.view))
  const inboxCount = primary.find((e) => e.view === 'inbox')?.count
  const apps = useAppNav()

  return (
    <div className={`app cards-app${route.isSettings ? ' on-settings' : ''}`}>
      <nav className="c-rail no-print" aria-label={tr('common.apps')}>
        <button type="button" className="c-menu icon-btn" aria-label={tr('themes.cards.cardsShell.allMailboxesAndLabels')} data-tip={tr('themes.cards.cardsShell.allMailboxes')} onClick={openDrawer}>
          <Menu />
        </button>
        <button type="button" className="c-fab" aria-label={tr('common.compose')} data-tip={tr('common.composeC')} onClick={() => useCompose.getState().open()}>
          <Pencil />
        </button>
        {apps.map((a) =>
          a.app === 'mail' ? (
            // In one of the mail views below, that view is the marked one.
            <MailDest key="mail" a={a} badge={inboxCount} onClick={() => setPanel(null)} active={a.active && !main.some((e) => e.view === route.current)} />
          ) : (
            <AppLinkDest key={a.app} a={a} onClick={() => setPanel(null)} />
          ),
        )}
        {route.isMail && (
          <>
            <span className="c-rail-sep" />
            {main.map((e) => (
              <Dest key={e.view} e={e} active={route.current === e.view} />
            ))}
            <MoreDest onOpen={dragging ? openForDrag : openDrawer} />
          </>
        )}
        <span className="spacer" />
        <NavLink to="/settings" className={`c-dest${route.isSettings ? ' active' : ''}`}>
          <span className="c-ind">
            <Settings />
          </span>
          <span className="c-dest-label">{tr('common.settings')}</span>
        </NavLink>
      </nav>

      <header className="c-top no-print">
        <button type="button" className="icon-btn c-top-menu" aria-label={tr('themes.cards.cardsShell.allMailboxesAndLabels')} onClick={openDrawer}>
          <Menu />
        </button>
        <SearchBar />
        <div className="c-top-right">
          <AmbroseButton />
          <WaitingBell />
          <QuickSettingsButton />
          <AccountButton />
        </div>
      </header>

      <main className="main c-main" id="main">
        {children}
      </main>

      <Drawer />
      {panel && <div className="panel-scrim" aria-hidden onClick={() => setPanel(null)} />}
      {/* Renders nothing while no panel is open (and keeps a stepped-aside Ambrose chat, hidden). */}
      <SidePanel />

      {mobile && route.isMail && !route.convId && (
        <ComposeFab />
      )}
      {sheet && (
        <div
          className="c-sheet-scrim"
          aria-hidden
          onClick={() => {
            for (const w of useCompose.getState().windows) if (w.mode === 'normal') useCompose.getState().setMode(w.key, 'minimized')
          }}
        />
      )}
      <ComposeDock />
      <CommandPalette />
      <GlobalOverlays />
    </div>
  )
}

/** Every mailbox, category and label in a modal drawer ("More" on the rail, the menu on phones). */
function Drawer() {
  const { labels, primary, secondary, categories } = useMailboxes()
  const { current } = useMailRoute()
  const open = useUI((s) => s.mobileNavOpen)
  const setOpen = useUI((s) => s.setMobileNav)
  const [creating, setCreating] = useState<string | null>(null)
  const [creatingIn, setCreatingIn] = useState<MailAccount | undefined>()
  const newLabel = (parent: string, account?: MailAccount) => {
    setCreatingIn(account)
    setCreating(parent)
  }
  const favorites = useFavoriteEntries()
  const accountGroups = useAccountGroups()
  const close = () => setOpen(false)
  const ref = useRef<HTMLElement>(null)
  useModalFocus(ref, open, close) // focus in, Tab kept inside, Escape closes, focus back
  return (
    <>
      {open && <div className="c-drawer-scrim" onClick={close} />}
      <nav ref={ref} className={`c-drawer no-print${open ? ' open' : ''}`} aria-label={tr('themes.cards.cardsShell.allMailboxes')} aria-hidden={!open} inert={!open}
        {...(open ? { role: 'dialog', 'aria-modal': true, 'data-modal-drawer': '' } : {})}>
        <div className="c-drawer-head">
          <span className="c-drawer-title">Mailr</span>
          <button type="button" className="icon-btn" aria-label={tr('common.close')} onClick={close}>
            <X />
          </button>
        </div>
        <button
          type="button"
          className="c-drawer-compose"
          onClick={() => {
            close()
            useCompose.getState().open()
          }}
        >
          <Pencil />{' '}{tr('common.compose')}
        </button>
        {favorites.length > 0 && (
          <>
            <div className="c-drawer-sec">{tr('common.favorites')}</div>
            {favorites.map((e) => (
              <NavRow key={`fav-${e.view}`} e={e} active={current === e.view} onNavigate={close} favorite />
            ))}
          </>
        )}
        <div className="c-drawer-sec">{tr('common.mail2')}</div>
        {[...primary, ...secondary].map((e) => (
          <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} />
        ))}
        <AccountFoldersHint className="c-acct-hint" onNavigate={close} />
        {/* Each account's own folders, while "Show each account separately" is on. */}
        {accountGroups.map((g) => (
          <div key={g.account.id} className="acct-group" role="group" aria-label={g.account.address} data-account={g.account.id}>
            <div className="c-drawer-sec acct-sec">
              <AccountDot account={g.account} label={false} /><bdi className="acct-sec-name">{g.account.address}</bdi>
              <AccountGroupMenu account={g.account} onNewFolder={(a) => newLabel('', a)} />
            </div>
            {g.entries.map((e) => (
              <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} />
            ))}
            <AccountFolders g={g} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p, g.account)} />
          </div>
        ))}
        <div className="c-drawer-sec">{tr('common.categories')}</div>
        {categories.map((e) => (
          <NavRow key={e.view} e={e} active={current === e.view} onNavigate={close} />
        ))}
        <div className="c-drawer-sec">
          <span>{tr('common.labels')}</span>
          <button type="button" className="icon-btn sm" aria-label={tr('common.createNewLabel')} data-tip={tr('common.createNewLabel')} onClick={() => newLabel('')}>
            <Plus />
          </button>
        </div>
        <LabelList labels={labels} current={current} onNavigate={close} onCreateChild={(p) => newLabel(p)} />
      </nav>
      <CreateLabelDialogWithParent parent={creating} account={creatingIn} onClose={() => setCreating(null)} />
    </>
  )
}
