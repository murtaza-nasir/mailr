import { ChevronDown, Command, Pencil } from 'lucide-react'
import { NavLink } from 'react-router'
import { WaitingBell } from '../../features/waiting/WaitingBell'
import { ComposeDock } from '../../features/compose/ComposeDock'
import { ComposeFab } from '../../features/compose/ComposeFab'
import { useAppNav, useMailboxes, useMailRoute } from '../../features/layout/mailboxes'
import { useMailAccounts } from '../../features/layout/accounts'
import { AmbroseButton } from '../../features/layout/AmbroseButton'
import { AccountButton, QuickSettingsButton } from '../../features/layout/TopBar'
import { SidePanel } from '../../features/rail/SideRail'
import { SearchBar } from '../../features/search/SearchBar'
import { viewTitle } from '../../lib/views'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { CommandPalette, usePalette } from '../shared/PaletteHost'
import { GlobalOverlays } from '../shared/frame'
import type { ShellProps } from '../types'
import { DropShelf } from './DropShelf'
import { KeyHints } from './KeyHints'
import { t } from '../../i18n'

/**
 * Keyboard-first and quiet: one centred column, no sidebar. Views, labels and actions live in
 * the command palette (Ctrl/⌘K); the keys that matter show at the bottom.
 */
export function FocusShell({ children }: ShellProps) {
  const route = useMailRoute()
  const apps = useAppNav()
  const { labels } = useMailboxes()
  const { accounts } = useMailAccounts()
  const panel = useUI((s) => s.panel)
  const setPanel = useUI((s) => s.setPanel)
  const openPalette = () => usePalette.getState().setOpen(true)
  const title = route.isSettings
    ? t('common.settings')
    : route.app === 'calendar'
      ? t('common.calendar')
      : route.app === 'tasks'
        ? t('common.tasks')
        : route.app === 'recordings'
          ? t('common.recordings')
        : route.app === 'notes'
          ? t('common.notes')
        : route.q
          ? t('common.search')
          : viewTitle(route.view, labels, accounts).split('/').pop()

  return (
    <div className="app focus-app">
      <header className="f-top no-print">
        <button type="button" className="f-where" onClick={openPalette} aria-label={t('themes.focus.focusShell.openTheCommandPaletteTo', { title })} data-tip={t('themes.focus.focusShell.goToCtrlK')}>
          <span className="f-brand">Mailr</span>
          <span className="f-slash" aria-hidden>
            /
          </span>
          <span className="f-view">{title}</span>
          <ChevronDown aria-hidden />
        </button>
        <SearchBar />
        <div className="f-top-right">
          <button type="button" className="f-cmd" onClick={openPalette} aria-label={t('themes.focus.focusShell.commandPalette')} data-tip={t('themes.focus.focusShell.commands')}>
            <Command />
            <kbd>{t('themes.focus.focusShell.ctrlK')}</kbd>
          </button>
          {/* The app switcher: Mail, Calendar, Tasks, Notes, the same apps in every theme. */}
          <nav className="f-apps" aria-label={t('common.apps')}>
            {apps.map((a) => (
              <NavLink key={a.app} to={a.to} className={`icon-btn sm${a.active ? ' active' : ''}`} aria-label={a.title} aria-current={a.active ? 'page' : undefined}
                data-tip={`${a.title} (${a.keys})`} data-app-nav={a.app} onClick={() => setPanel(null)}>
                {a.icon}
              </NavLink>
            ))}
          </nav>
          <AmbroseButton />
          <WaitingBell className="icon-btn sm" />
          <QuickSettingsButton className="icon-btn sm" />
          <button type="button" className="f-compose" onClick={() => useCompose.getState().open()} data-tip={t('common.composeC')}>
            <Pencil />
            <span>{t('common.compose')}</span>
          </button>
          <AccountButton />
        </div>
      </header>
      <DropShelf />
      <main className="main f-main" id="main">
        {children}
      </main>
      {panel && <div className="f-scrim" aria-hidden onClick={() => setPanel(null)} />}
      {/* Renders nothing while no panel is open (and keeps a stepped-aside Ambrose chat, hidden). */}
      <SidePanel />
      {route.isMail && (
        <ComposeFab />
      )}
      <KeyHints />
      <ComposeDock />
      <CommandPalette />
      <GlobalOverlays />
    </div>
  )
}
