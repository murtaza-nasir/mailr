// The phone frame every theme gets at phone width and in the installed app (styles/phone.css):
// a bottom tab bar above the home indicator with Mail, Calendar, Tasks, Notes and Ambrose, Ambrose
// far right. Five is the ceiling: nothing is ever added past five.
// Mail folders and labels are in each theme's mail drawer (the top bar's menu button); Settings,
// Recordings, Pending changes and Install app are in the account menu (TopBar's AccountButton).
// The theme's shell keeps its own top bar and content; this adds the frame around it.
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { NavLink } from 'react-router'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { mailPath } from '../../lib/views'
import { useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { AmbroseIcon } from '../layout/AmbroseButton'
import { useAppNav, useMailRoute, type AppNavEntry } from '../layout/mailboxes'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { useSidebarCounts } from '../layout/sidebarCounts'
import { useInboxCount } from '../layout/inboxCount'
import { inboxNavCounts } from '../../lib/inboxCount'
import { useAmbroseReturn } from '../rail/assistant/ambroseNav'
import { ArrowLeft } from 'lucide-react'
import { t } from '../../i18n'

/** The phone frame is on: phone width (the installed app on a tablet keeps the desktop frame). */
export function usePhoneChrome(): boolean {
  return useMediaQuery('(max-width: 900px)')
}

function badge(n: number | undefined) {
  if (!n) return null
  return <span className="tab-badge" aria-hidden>{n > 99 ? '99+' : n}</span>
}

function AppTab({ a, unread }: { a: AppNavEntry; unread?: number }) {
  const panel = useUI((s) => s.panel)
  const active = a.active && !panel
  return (
    <NavLink
      to={a.app === 'mail' ? mailPath('inbox') : a.to}
      className={`tab${active ? ' active' : ''}`}
      aria-current={active ? 'page' : undefined}
      data-tab={a.app}
      onClick={(e) => {
        useUI.getState().setPanel(null)
        // Mail while already on the list: back to the top, as in native apps.
        if (a.app === 'mail' && active) {
          const list = document.querySelector('.list-scroll')
          if (list && list.scrollTop > 0) {
            e.preventDefault()
            list.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
          }
        }
      }}
    >
      <span className="tab-ind">
        {a.icon}
        {badge(unread)}
      </span>
      <span className="tab-label">{a.title}</span>
    </NavLink>
  )
}

/** Mail, Calendar, Tasks, Notes, then Ambrose. Recordings is in the account menu on phones. */
export function PhoneTabBar() {
  const apps = useAppNav().filter((a) => a.app !== 'recordings')
  const panel = useUI((s) => s.panel)
  const aName = useAssistantName()
  // The same number as the Inbox in the navigation (the Inbox count setting, lib/inboxCount.ts):
  // the badge said 19 where the Inbox said 16.
  const inbox = useSidebarCounts().data?.views?.inbox
  const ic = useInboxCount()
  const unread = inboxNavCounts(ic.mode, ic.data, inbox ? { unread: inbox.unread ?? 0, total: inbox.total ?? 0 } : undefined).unread
  return (
    <nav className="phone-tabs no-print" aria-label={t('common.apps')} data-testid="phone-tabs">
      {apps.map((a) => (
        <AppTab key={a.app} a={a} unread={a.app === 'mail' ? unread : undefined} />
      ))}
      <button
        type="button"
        className={`tab${panel === 'agent' ? ' active' : ''}`}
        aria-pressed={panel === 'agent'}
        data-tab="assistant"
        onClick={() => useUI.getState().setPanel(panel === 'agent' ? null : 'agent')}
      >
        <span className="tab-ind">
          <AmbroseIcon />
        </span>
        <span className="tab-label">{aName}</span>
      </button>
    </nav>
  )
}

/**
 * "Back to Ambrose": while an item opened from Ambrose's chat has the screen (ambroseNav.ts), a pill
 * at the bottom shows the chat again as it was. The Ambrose tab and the back gesture do the same; the
 * pill is there also where the tab bar is not (an open conversation).
 */
export function AmbroseReturn() {
  const away = useUI((s) => s.ambroseAway && !s.panel)
  const name = useAssistantName()
  useEffect(() => {
    const el = document.documentElement
    if (away) el.dataset.ambroseAway = 'true'
    else delete el.dataset.ambroseAway
    return () => {
      delete el.dataset.ambroseAway
    }
  }, [away])
  if (!away) return null
  return (
    <button type="button" className="ambrose-return no-print" data-testid="ambrose-return" onClick={() => useUI.getState().setPanel('agent')}>
      <ArrowLeft size={18} aria-hidden />
      <AmbroseIcon size={18} aria-hidden />
      <span>{t('pwa.phoneChrome.backTo', { name })}</span>
    </button>
  )
}

/** The frame: the tab bar (not over an open conversation or a full-screen compose). */
export function PhoneChrome() {
  const phone = usePhoneChrome()
  const { convId, isMail } = useMailRoute()
  const composing = useCompose((s) => s.windows.some((w) => w.mode === 'normal' || w.mode === 'maximized'))
  const reading = isMail && !!convId
  const show = phone && !reading && !composing
  useAmbroseReturn(phone)
  useEffect(() => {
    // data-phone: the phone frame is on (top bars trimmed, Cards' own bottom bar gone);
    // data-phone-chrome: the tab bar shows; data-phone-reading: a conversation has the screen.
    const el = document.documentElement
    const set = (k: string, on: boolean) => {
      if (on) el.dataset[k] = 'true'
      else delete el.dataset[k]
    }
    set('phone', phone)
    set('phoneChrome', show)
    set('phoneReading', phone && reading)
    return () => {
      delete el.dataset.phone
      delete el.dataset.phoneChrome
      delete el.dataset.phoneReading
    }
  }, [phone, show, reading])
  if (!phone) return null
  return (
    <>
      {/* On the page itself, not in the theme's main region (which may clip or transform). */}
      {show && createPortal(<PhoneTabBar />, document.body)}
      {!composing && createPortal(<AmbroseReturn />, document.body)}
    </>
  )
}
