import { BookOpen, Bot, CircleHelp, CloudUpload, Download, ExternalLink, LogOut, Menu, Settings, Keyboard, Monitor, UserRound } from 'lucide-react'
import { drain, usePending } from '../../lib/offlineQueue'
import { forgetDeviceData } from '../../lib/serviceWorker'
import { unsubscribePush } from '../reminders/notify'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useLayouts, useMe, useSettings, useUpdateSettings } from '../../api/hooks'
import type { InboxType, Settings as S } from '../../api/types'
import { Avatar, Logo } from '../../components/bits'
import { ownPictureUrl } from '../../lib/avatars'
import { MenuItem, MenuSep, Popover, useAnchor, type Placement } from '../../components/Popover'
import { useUI } from '../../stores/ui'
import { SearchBar } from '../search/SearchBar'
import { WaitingBell } from '../waiting/WaitingBell'
import { ThemePicker } from '../../themes/ThemePicker'
import { AmbroseButton } from './AmbroseButton'
import { useAppNav } from './mailboxes'
import { useInstall } from '../../lib/pwa'
import { useStandalone } from '../pwa/InstallGuide'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useThemeDef } from '../../themes/useUITheme'
import { t } from '../../i18n'

export function TopBar() {
  const nav = useNavigate()
  const toggleSidebar = useUI((s) => s.toggleSidebar)
  const setMobileNav = useUI((s) => s.setMobileNav)

  return (
    <header className="topbar no-print">
      <button
        type="button"
        className="icon-btn"
        aria-label={t('layout.topBar.mainMenu')}
        data-tip={t('layout.topBar.mainMenu')}
        onClick={() => {
          if (window.matchMedia('(max-width: 900px)').matches) setMobileNav(!useUI.getState().mobileNavOpen)
          else toggleSidebar()
        }}
      >
        <Menu />
      </button>
      <a className="brand" href="/mail/inbox" onClick={(e) => { e.preventDefault(); nav('/mail/inbox') }} aria-label={t('layout.topBar.mailrInbox')}>
        <Logo />
        <span>
          <b>Mail</b>r
        </span>
      </a>
      <SearchBar />
      <div className="topbar-right">
        <AmbroseButton />
        <WaitingBell />
        <HelpButton className="icon-btn hide-mobile" />
        <QuickSettingsButton />
        <AccountButton />
      </div>
    </header>
  )
}

/** The user guide, served by mailr itself at /help/ to signed-in people (backend core/helpdocs.py).
 *  A new tab: the help pages are the server's, not the web app's. */
export function openHelp() {
  window.open('/help/', '_blank', 'noopener')
}

/** The public source repository, opened from the version line of the support menu. */
const SOURCE_URL = 'https://github.com/murtaza-nasir/mailr'

/** The support menu: the user guide, keyboard shortcuts and the API documentation (or, for people
 *  who cannot make API tokens, the guide to connecting an agent), then the server's
 *  version (GET /me `version`), which a bug report asks for; it links to the source code. */
export function HelpButton({ className = 'icon-btn' }: { className?: string }) {
  const setHelp = useUI((s) => s.setHelp)
  const help = useAnchor()
  const me = useMe().data
  const version = me?.version
  const mayUseApi = me?.user.role === 'admin' || !!me?.access?.features.api_tokens
  return (
    <>
      <button type="button" className={className} aria-label={t('layout.topBar.support')} data-tip={t('layout.topBar.support')} onClick={(e) => help.toggle(e.currentTarget)}>
        <CircleHelp />
      </button>
      <Popover anchor={help.anchor} open={help.open} onClose={help.close} placement="bottom-end">
        <MenuItem icon={<BookOpen />} onSelect={() => { help.close(); openHelp() }}>
          {t('layout.topBar.help')}
        </MenuItem>
        <MenuItem icon={<Keyboard />} onSelect={() => { help.close(); setHelp(true) }} shortcut="?">
          {t('common.keyboardShortcuts')}
        </MenuItem>
        {/* The API reference is served only to people who may make API tokens (backend
            api/apidocs_routes.py); everyone else gets the guide to connecting an agent. */}
        {mayUseApi ? (
          <MenuItem icon={<Monitor />} onSelect={() => { help.close(); window.open('/api/v1/docs', '_blank', 'noopener') }}>
            {t('layout.topBar.apiDocumentation')}
          </MenuItem>
        ) : (
          <MenuItem icon={<Bot />} onSelect={() => { help.close(); window.open('/help/agents/', '_blank', 'noopener') }}>
            {t('layout.topBar.connectAnAgent')}
          </MenuItem>
        )}
        {version && (
          <>
            <MenuSep />
            <MenuItem icon={<ExternalLink />} tip={t('layout.topBar.sourceCode')}
              onSelect={() => { help.close(); window.open(SOURCE_URL, '_blank', 'noopener') }}>
              {t('layout.topBar.version', { version })}
            </MenuItem>
          </>
        )}
      </Popover>
    </>
  )
}

/** The gear: a popover with quick settings (theme, density, colour scheme, inbox type…). */
export function QuickSettingsButton({ className = 'icon-btn', placement = 'bottom-end' }: { className?: string; placement?: Placement }) {
  const nav = useNavigate()
  const quick = useAnchor()
  return (
    <>
      <button type="button" className={className} aria-label={t('common.settings')} data-tip={t('common.settings')} onClick={(e) => quick.toggle(e.currentTarget)}>
        <Settings />
      </button>
      <Popover anchor={quick.anchor} open={quick.open} onClose={quick.close} placement={placement} role="dialog" label={t('layout.topBar.quickSettings')} minWidth={320}>
        <QuickSettings onAll={() => { quick.close(); nav('/settings') }} />
      </Popover>
    </>
  )
}

/** Your avatar: the account menu. It also holds what has no place in the phone's five-app bar:
 *  Settings, Recordings, Pending changes and Install app. */
export function AccountButton({ placement = 'bottom-end' }: { placement?: Placement }) {
  const me = useMe().data
  const nav = useNavigate()
  const qc = useQueryClient()
  const account = useAnchor()
  // Your picture: your default sending address's own, else your account picture (Settings → Mail → Mail accounts).
  const myPic = ownPictureUrl(me)
  const phone = useMediaQuery('(max-width: 900px)')
  const pending = usePending((s) => s.ops.length)
  const standalone = useStandalone()
  const recordings = useAppNav().find((a) => a.app === 'recordings')

  const signOut = async () => {
    try {
      // Changes made offline go first while the session still works.
      if (navigator.onLine && usePending.getState().ops.some((o) => o.state === 'queued')) {
        await drain(async (op) => {
          await api.post('/conversations/actions', op.req as unknown as Record<string, unknown>, { idempotencyKey: op.id })
        }).catch(() => undefined)
      }
      // This browser's Web Push subscription ends with the session: the next person on it gets
      // none of these notifications.
      await unsubscribePush().catch(() => undefined)
      await api.post('/auth/logout')
    } finally {
      // This device forgets the mail it kept for offline reading.
      forgetDeviceData()
      qc.clear()
      // UI state that belongs to this user must not greet the next one on this browser.
      try {
        for (const k of ['mailr.panel', 'mailr.inbox.tab']) localStorage.removeItem(k)
        sessionStorage.clear()
      } catch {
        /* ignore */
      }
      window.location.assign('/login')
    }
  }

  return (
    <>
      <button type="button" className="account-btn" aria-label={t('layout.topBar.account', { email: me?.user.email ?? '', value: pending ? t('layout.topBar.pendingChanges', { count: pending }) : '' })}
        aria-haspopup="menu" aria-expanded={account.open} data-testid="account-btn" onClick={(e) => account.toggle(e.currentTarget)}>
        <Avatar who={me ? { email: me.user.email, name: me.user.name } : null} size="sm" src={myPic} />
        {phone && pending > 0 && <span className="account-dot" aria-hidden />}
      </button>
      <Popover anchor={account.anchor} open={account.open} onClose={account.close} placement={placement} minWidth={280}>
        <div style={{ padding: '12px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
          <Avatar who={me ? { email: me.user.email, name: me.user.name } : null} src={myPic} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 500 }}>{me?.user.name || me?.user.email}</div>
            <div className="muted ellipsis" style={{ fontSize: 13 }}>
              {me?.user.email}
            </div>
          </div>
        </div>
        <MenuSep />
        <MenuItem icon={<Settings />} onSelect={() => { account.close(); nav('/settings') }}>
          {t('common.settings')}
        </MenuItem>
        <MenuItem icon={<UserRound />} onSelect={() => { account.close(); nav('/settings/mail/accounts') }}>
          {t('layout.topBar.accountsIdentities')}
        </MenuItem>
        {/* Phones: what has no place in the five-app bar. */}
        {phone && recordings && (
          <MenuItem icon={recordings.icon} onSelect={() => { account.close(); nav(recordings.to) }}>
            {t('common.recordings')}
          </MenuItem>
        )}
        {phone && pending > 0 && (
          <MenuItem icon={<CloudUpload />} onSelect={() => { account.close(); usePending.getState().setOpen(true) }}>
            {t('layout.topBar.pendingChanges2', { pending })}
          </MenuItem>
        )}
        {phone && !standalone && (
          <MenuItem icon={<Download />} onSelect={() => { account.close(); useInstall.getState().setGuide(true) }}>
            {t('layout.topBar.installApp')}
          </MenuItem>
        )}
        {/* Every theme and phones: the account menu is everywhere, the ? button is not. */}
        <MenuItem icon={<BookOpen />} onSelect={() => { account.close(); openHelp() }}>
          {t('layout.topBar.help')}
        </MenuItem>
        <MenuSep />
        <MenuItem icon={<LogOut />} onSelect={() => { account.close(); void signOut() }}>
          {t('common.signOut')}
        </MenuItem>
      </Popover>
    </>
  )
}

export function QuickSettings({ onAll }: { onAll: () => void }) {
  const s = useSettings()
  const upd = useUpdateSettings()
  const set = (patch: Partial<S>) => upd.mutate(patch)
  const radio = <K extends keyof S>(k: K, v: S[K], label: string, desc?: string) => (
    <label className="choice" style={{ padding: '4px 0' }}>
      <input type="radio" className="radio" name={String(k)} checked={s[k] === v} onChange={() => set({ [k]: v } as Partial<S>)} />
      <span>
        {label}
        {desc && <div className="desc">{desc}</div>}
      </span>
    </label>
  )
  const layouts = useLayouts().data?.layouts ?? []
  const theme = useThemeDef()
  const inboxTypes: [InboxType, string][] = [
    ['default', t('common.categories')],
    ['simple', t('common.simple')],
    ['important_first', t('common.importantFirst')],
    ['unread_first', t('layout.topBar.unreadFirst')],
    ['starred_first', t('layout.topBar.starredFirst')],
    ['priority', t('layout.topBar.customSections')],
    ...layouts.filter((l) => !l.preset).map((l): [InboxType, string] => [`layout:${l.id}`, l.name]),
  ]
  return (
    <div style={{ padding: '8px 20px 16px', maxHeight: '80vh', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h3 style={{ fontSize: 16, fontWeight: 500 }}>{t('layout.topBar.quickSettings')}</h3>
      </div>
      <button type="button" className="btn outlined" style={{ width: '100%', marginBottom: 12 }} onClick={onAll}>
        {t('layout.topBar.seeAllSettings')}
      </button>
      <div className="field-label" style={{ margin: '8px 0 6px' }}>
        {t('layout.topBar.style')}
      </div>
      <ThemePicker compact />
      <div className="field-label" style={{ margin: '8px 0 4px' }}>
        {t('layout.topBar.textAndSpacing')}
      </div>
      {radio('density', 'comfortable', t('common.roomy'))}
      {radio('density', 'default', t('common.standard'))}
      {radio('density', 'compact', t('common.compact'))}
      <div className="field-label" style={{ margin: '12px 0 4px' }}>
        {t('layout.topBar.lightOrDark')}
      </div>
      {radio('theme', 'system', t('common.automatic'))}
      {radio('theme', 'light', t('common.light'))}
      {radio('theme', 'dark', t('common.dark'))}
      <div className="field-label" style={{ margin: '12px 0 4px' }}>
        {t('layout.topBar.inboxType')}
      </div>
      {inboxTypes.map(([v, l]) => (
        <span key={v}>{radio('inbox_type', v, l)}</span>
      ))}
      <div className="field-label" style={{ margin: '12px 0 4px' }}>
        {t('layout.topBar.readingPane')}
      </div>
      {theme.readingPane && !theme.paneChoices ? (
        <p className="muted" style={{ margin: '4px 0', fontSize: 13 }}>
          {t('layout.topBar.theLayoutPlacesTheReading', { themeName: theme.name })}
        </p>
      ) : theme.readingPane ? (
        <>
          {([['none', t('layout.topBar.noSplit')], ['right', t('layout.topBar.rightOfInbox')], ['below', t('layout.topBar.belowInbox')]] as const)
            .filter(([v]) => theme.paneChoices!.includes(v))
            .map(([v, l]) => {
              const byTheme = s.reading_pane_by_theme ?? {}
              const current = byTheme[theme.id] ?? theme.readingPane!(true)
              return (
                <label key={v} className="choice" style={{ padding: '4px 0' }}>
                  <input type="radio" className="radio" name="reading_pane_theme" checked={current === v}
                    onChange={() => set({ reading_pane_by_theme: { ...byTheme, [theme.id]: v } })} />
                  <span>{l}</span>
                </label>
              )
            })}
        </>
      ) : (
        <>
          {radio('reading_pane', 'none', t('layout.topBar.noSplit'))}
          {radio('reading_pane', 'right', t('layout.topBar.rightOfInbox'))}
          {radio('reading_pane', 'below', t('layout.topBar.belowInbox'))}
        </>
      )}
      <div className="field-label" style={{ margin: '12px 0 4px' }}>
        {t('layout.topBar.conversationView')}
      </div>
      <label className="choice">
        <input type="checkbox" className="checkbox" checked={s.conversation_view} onChange={(e) => set({ conversation_view: e.target.checked })} />
        <span>{t('layout.topBar.groupMessagesIntoConversations')}</span>
      </label>
    </div>
  )
}
