// The mailbox navigation every shell draws: system views, categories and the user's labels,
// with the counts the server reports. Shells differ only in how they draw it.

import { AlarmClock, ArrowUpRight, AudioLines, CalendarDays, CircleCheck, Clock, Eraser, File, Hourglass, Inbox, Info, Mail, Mails, MessageSquare, Newspaper, OctagonAlert, Paperclip, Send, ShoppingBag, Star, StickyNote, Tag, Trash2, UserCheck, Users } from 'lucide-react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router'
import { useLabelList, useMe } from '../../api/hooks'
import type { Label } from '../../api/types'
import { convFromSlug } from '../../lib/pubid'
import { appFromPath, appHome } from '../../lib/routes'
import { useSpeakrStatus } from '../speakr/api'
import { inboxNavCounts } from '../../lib/inboxCount'
import { useInboxCount } from './inboxCount'
import { useSidebarCounts } from './sidebarCounts'
import { showScreenerNav } from '../mail/screener'
import { useOutboxCount } from '../mail/outbox'
import { t } from '../../i18n'

export interface NavEntry {
  view: string
  title: string
  icon: ReactNode
  /** The number the sidebar shows (unread, or total for drafts). */
  count?: number
  bold?: boolean
  /** Unread and total conversations, where the server counts them (folder panes show both). */
  unread?: number
  total?: number
  /** Sent: conversations with a follow-up still waiting for a reply (shown instead of an
   *  ever-growing total). */
  waiting?: number
  /** Tooltip, when it says more than the title (a nested label's full name). */
  tip?: string
}

export function useMailboxes() {
  const labels = useLabelList()
  const me = useMe().data
  const byRole = (r: Label['role']) => labels.find((l) => l.role === r)
  // Every number is the server's count of the list the entry opens (GET /counts, kept current
  // by the live `counts` event); /me's older counts stand in until it answers.
  const sc = useSidebarCounts().data
  const v = sc?.views ?? {}
  const counts = sc
    ? { waiting: sc.waiting, scheduled: v.scheduled?.total ?? 0 }
    : (me?.counts ?? {})
  const inbox = byRole('inbox')
  // Unread follows the Inbox count setting (lib/inboxCount.ts); the total stays the whole Inbox.
  const ic = useInboxCount()
  const inboxN = inboxNavCounts(ic.mode, ic.data, inbox)
  const drafts = v.drafts ?? byRole('drafts')
  const junk = v.spam ?? byRole('junk')
  const trash = v.trash ?? byRole('trash')
  // The Screener counts senders waiting (core/screener.counts).
  const screener = v.screener
  // The Outbox is listed only while something is on its way or was not sent.
  const ob = useOutboxCount()

  const primary: NavEntry[] = [
    { view: 'inbox', title: t('common.inbox'), icon: <Inbox />, ...inboxN },
    // Right under the Inbox while the Screener is on, with the senders waiting.
    ...(showScreenerNav(me?.settings?.screener)
      ? [{ view: 'screener', title: t('common.screener'), icon: <UserCheck />, count: screener?.total || undefined,
          bold: (screener?.unread ?? 0) > 0, unread: screener?.unread, total: screener?.total,
          tip: screener?.total ? t('layout.mailboxes.newSendersWaiting', { count: screener.total }) : undefined }]
      : []),
    { view: 'starred', title: t('common.starred'), icon: <Star /> },
    { view: 'snoozed', title: t('common.snoozed'), icon: <AlarmClock /> },
    { view: 'sent', title: t('common.sent'), icon: <Send />, waiting: counts.waiting || undefined,
      tip: counts.waiting ? t('layout.mailboxes.waitingForAReply', { waiting: counts.waiting }) : undefined },
    { view: 'drafts', title: t('common.drafts'), icon: <File />, count: drafts?.total, bold: (drafts?.total ?? 0) > 0, total: drafts?.total },
    ...(ob.total
      ? [{ view: 'outbox', title: t('common.outbox'), icon: <Hourglass />, count: ob.total, bold: ob.failed > 0, unread: ob.failed, total: ob.total,
          tip: ob.failed ? t('layout.mailboxes.notSent', { failed: ob.failed }) : t('layout.mailboxes.onTheWay', { total: ob.total }) }]
      : []),
  ]
  const secondary: NavEntry[] = [
    { view: 'important', title: t('common.important'), icon: <ArrowUpRight /> },
    { view: 'scheduled', title: t('common.scheduled'), icon: <Clock />, count: counts.scheduled, total: counts.scheduled },
    { view: 'all', title: t('common.allMail2'), icon: <Mails /> },
    { view: 'attachments', title: t('common.attachments'), icon: <Paperclip /> },
    // ACT subscription.list @web.nav, ACT cleanup.groups @web.nav
    { view: 'subscriptions', title: t('common.subscriptions'), icon: <Newspaper /> },
    { view: 'cleanup', title: t('common.cleanUp'), icon: <Eraser /> },
    { view: 'spam', title: t('common.spam'), icon: <OctagonAlert />, count: junk?.unread, bold: (junk?.unread ?? 0) > 0, unread: junk?.unread, total: junk?.total },
    { view: 'trash', title: t('common.trash'), icon: <Trash2 />, total: trash?.total },
  ]
  const categories: NavEntry[] = [
    { view: 'category:social', title: t('common.social'), icon: <Users /> },
    { view: 'category:updates', title: t('common.updates'), icon: <Info /> },
    { view: 'category:forums', title: t('common.forums'), icon: <MessageSquare /> },
    { view: 'category:promotions', title: t('common.promotions'), icon: <Tag /> },
    { view: 'category:purchases', title: t('common.purchases'), icon: <ShoppingBag /> },
  ]
  return { labels, primary, secondary, categories }
}

/** The mail route: view, open conversation and search, from the URL. */
export function useMailRoute() {
  const location = useLocation()
  const m = /^\/mail\/([^/]+)(?:\/([0-9A-Za-z]+))?/.exec(location.pathname)
  const q = new URLSearchParams(location.search).get('q') ?? ''
  const app = appFromPath(location.pathname)
  return {
    /** Which app the URL belongs to (mail, calendar, tasks, settings): see router.tsx. */
    app,
    isMail: !!m,
    isSettings: app === 'settings',
    view: m ? decodeURIComponent(m[1]) : 'inbox',
    convId: convFromSlug(m?.[2]),
    q,
    /** The current mail nav selection (first path segment under /mail/); '' outside mail. */
    current: app === 'mail' ? decodeURIComponent(location.pathname.split('/')[2] ?? '') : '',
  }
}

export interface AppNavEntry {
  app: 'mail' | 'calendar' | 'tasks' | 'notes' | 'recordings'
  title: string
  icon: ReactNode
  /** Where the entry goes: the last place in that app this session (lib/routes appHome). */
  to: string
  active: boolean
  /** The key that goes there. */
  keys: string
}

/** The apps as nav entries (switchers, spaces bars, rails), with the active one marked.
 *  Recordings is listed only when the user has a Speakr account (web.recordings). */
export function useAppNav(): AppNavEntry[] {
  const { pathname } = useLocation()
  const app = appFromPath(pathname)
  const speakr = useSpeakrStatus().data?.connected === true
  const out: AppNavEntry[] = [
    { app: 'mail', title: t('common.mail2'), icon: <Mail />, to: appHome('mail'), active: app === 'mail', keys: 'g i' },
    { app: 'calendar', title: t('common.calendar'), icon: <CalendarDays />, to: appHome('calendar'), active: app === 'calendar', keys: 'g c' },
    { app: 'tasks', title: t('common.tasks'), icon: <CircleCheck />, to: appHome('tasks'), active: app === 'tasks', keys: 'g k' },
    // Notes, checklists and tables (web.notes).
    { app: 'notes', title: t('common.notes'), icon: <StickyNote />, to: appHome('notes'), active: app === 'notes', keys: 'g n' },
  ]
  if (speakr) out.push({ app: 'recordings', title: t('common.recordings'), icon: <AudioLines />, to: appHome('recordings'), active: app === 'recordings', keys: 'g r' })
  return out
}
