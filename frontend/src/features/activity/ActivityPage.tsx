// Settings → Activity (backend core/activity.py): what you, Ambrose and your
// agents changed recently across mail, calendar, tasks, notes and tables, newest first and grouped
// by day, each with Undo while it can still be taken back, and your administrator's changes to your
// account. Reached from the bell's footer and from Settings; no top-level entry.
//
// Undo sends the entry's own request (the routes the apps use, with this session's rights), then
// marks the entry undone. Only the routes an entry can name are sent (ALLOWED).
//
// ACT activity.list @web.settings, ACT activity.undo @web.settings

import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, CalendarDays, CircleCheck, History, Mail, Settings2, ShieldCheck, StickyNote, Table2, UserRound } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { addDaysISO, formatDate, formatTime, todayISO } from '../../lib/dates'
import { eventPath, notePath, tableRoutePath, taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { useCalPrefs } from '../calendar/prefs'
import { toast, toastError } from '../../stores/ui'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import './activity.css'
import { t as tr } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

export interface ActivityItem {
  id: number
  at: string
  who: 'you' | 'ambrose' | 'agent' | 'admin' | 'system'
  by: string | null
  app: 'mail' | 'calendar' | 'tasks' | 'notes' | 'tables' | 'settings' | 'account'
  text: string
  target: { kind: string; id: string | number; title: string } | null
  count: number
  undo: { method: string; path: string; body?: unknown } | null
  undone: boolean
  ok: boolean
  /** The forward Ambrose was acting on (Trusted addresses): its conversation. */
  via?: { kind: 'conversation'; id: string | number; title: string }
}

type Who = 'all' | 'you' | 'ambrose' | 'agents' | 'admin'
const FILTERS: { id: Who; label: string }[] = [
  { id: 'all', label: tr('activity.activityPage.everyone') },
  { id: 'you', label: tr('common.you') },
  { id: 'ambrose', label: tr('activity.activityPage.text', { name: assistantNameNow() }) },
  { id: 'agents', label: tr('activity.activityPage.agents') },
  { id: 'admin', label: tr('activity.activityPage.administrator') },
]

/** The requests an entry may send as its Undo. */
export const ALLOWED = /^\/(?:(?:conversations|messages)\/actions|calendar\/events(?:\/[^/]+(?:\/revert)?)?|tasks(?:\/[^/]+)?|notes\/[^/]+(?:\/[a-z-]+)?|tables\/[^/]+(?:\/[a-z-]+)?)$/

const APP_ICON: Record<ActivityItem['app'], ReactNode> = {
  mail: <Mail size={18} />,
  calendar: <CalendarDays size={18} />,
  tasks: <CircleCheck size={18} />,
  notes: <StickyNote size={18} />,
  tables: <Table2 size={18} />,
  settings: <Settings2 size={18} />,
  account: <ShieldCheck size={18} />,
}

/** Who made the change, in words: "You", "Ambrose", the agent's name, "Your administrator". */
export function whoLabel(a: Pick<ActivityItem, 'who' | 'by'>, assistant = tr('activity.activityPage.text', { name: assistantNameNow() })): string {
  if (a.who === 'you') return tr('common.you')
  if (a.who === 'ambrose') return assistant
  if (a.who === 'agent') return a.by ? tr('activity.activityPage.agent', { by: a.by }) : tr('activity.activityPage.anAgent')
  if (a.who === 'admin') return a.by ? tr('activity.activityPage.yourAdministrator', { by: a.by }) : tr('activity.activityPage.yourAdministrator2')
  return 'Mailr'
}

/** Rows grouped by the day they happened on: Today, Yesterday, then dates. */
export function groupByDay(items: ActivityItem[], zone: string, now: Date = new Date()): { label: string; items: ActivityItem[] }[] {
  const today = todayISO(zone, now)
  const out: { label: string; items: ActivityItem[] }[] = []
  for (const a of items) {
    const day = todayISO(zone, new Date(a.at))
    const label = day === today ? tr('common.today') : day === addDaysISO(today, -1) ? tr('common.yesterday') : formatDate(day, 'dayYear')
    const last = out[out.length - 1]
    if (last?.label === label) last.items.push(a)
    else out.push({ label, items: [a] })
  }
  return out
}

function targetPath(t: ActivityItem['target']): string | null {
  if (!t) return null
  const id = String(t.id)
  switch (t.kind) {
    case 'conversation': return mailPath('all', Number(t.id))
    case 'event': return eventPath(id, '')
    case 'task': return taskPath(id)
    case 'note': return notePath(id)
    case 'table': return tableRoutePath(id)
    default: return null
  }
}

async function send(u: NonNullable<ActivityItem['undo']>) {
  if (!ALLOWED.test(u.path)) throw new Error(tr('activity.activityPage.thisChangeCannotBeUndone'))
  const body = (u.body ?? undefined) as Record<string, unknown> | undefined
  const m = u.method.toUpperCase()
  if (m === 'DELETE') await api.del(u.path)
  else if (m === 'PATCH') await api.patch(u.path, body)
  else if (m === 'PUT') await api.put(u.path, body)
  else await api.post(u.path, body)
}

export function ActivityPage() {
  const prefs = useCalPrefs()
  const qc = useQueryClient()
  const nav = useNavigate()
  const [who, setWho] = useState<Who>('all')
  const aName = useAssistantName()
  const [busy, setBusy] = useState<number | null>(null)
  const q = useInfiniteQuery({
    queryKey: ['activity', who],
    queryFn: ({ pageParam, signal }) => api.get<{ items: ActivityItem[]; next_cursor: string | null }>(
      '/activity', { limit: 50, ...(who === 'all' ? {} : { who }), ...(pageParam ? { cursor: pageParam } : {}) }, signal),
    initialPageParam: '' as string,
    getNextPageParam: (last) => last.next_cursor || undefined,
    staleTime: 10_000,
  })
  const items = (q.data?.pages ?? []).flatMap((p) => p.items)
  const groups = groupByDay(items, prefs.zone)

  const undo = async (a: ActivityItem) => {
    if (!a.undo) return
    setBusy(a.id)
    try {
      await send(a.undo)
      await api.post(`/activity/${a.id}/undone`)
      toast({ message: tr('activity.activityPage.undone', { text: a.text }) })
      // The undo can touch any app: everything refetches what it shows.
      void qc.invalidateQueries()
    } catch (e) {
      toastError(e, tr('common.couldNotUndo'))
      void qc.invalidateQueries({ queryKey: ['activity'] })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="activity-page" data-testid="activity-page" aria-labelledby="activity-title">
      <header className="activity-head">
        <h2 id="activity-title"><History size={20} aria-hidden />{' '}{tr('common.activity')}</h2>
        <p className="muted">
          {tr('activity.activityPage.whatYouAndYourAgents', { name: aName })}
        </p>
        <div className="activity-filters" role="radiogroup" aria-label={tr('activity.activityPage.showChangesBy')}>
          {FILTERS.map((f) => ({ ...f, label: f.id === 'ambrose' ? aName : f.label })).map((f) => (
            <button key={f.id} type="button" role="radio" aria-checked={who === f.id} className={`chip${who === f.id ? ' on' : ''}`} onClick={() => setWho(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
      </header>
      {q.isLoading && <div className="activity-skel" aria-busy="true" aria-label={tr('common.loading')}>{[0, 1, 2, 3].map((i) => <span key={i} className="skeleton" />)}</div>}
      {q.isError && <p className="activity-empty" role="alert">{tr('activity.activityPage.theActivityCouldNotBe')}{' '}<button type="button" className="btn text sm" onClick={() => void q.refetch()}>{tr('common.tryAgain')}</button></p>}
      {!q.isLoading && !q.isError && !items.length && (
        <p className="activity-empty">{who === 'all' ? tr('activity.activityPage.noChangesYetChangesTo') : tr('activity.activityPage.noChangesByThisPerson')}</p>
      )}
      {groups.map((g) => (
        <div key={g.label} className="activity-day">
          <h3>{g.label}</h3>
          <ul className="activity-list">
            {g.items.map((a) => {
              const to = targetPath(a.target)
              return (
                <li key={a.id} className={`activity-row${a.undone ? ' undone' : ''}`} data-testid="activity-row">
                  <span className={`activity-icon ${a.app}`} aria-hidden>{APP_ICON[a.app] ?? <History size={18} />}</span>
                  <div className="activity-main">
                    {to ? (
                      <button type="button" className="activity-text link" onClick={() => nav(to)}>{a.text}</button>
                    ) : (
                      <span className="activity-text">{a.text}</span>
                    )}
                    <span className="activity-meta">
                      <span className={`activity-who ${a.who}`}>
                        {a.who === 'ambrose' || a.who === 'agent' ? <Bot size={13} aria-hidden /> : <UserRound size={13} aria-hidden />}
                        {whoLabel(a, aName)}
                      </span>
                      <span>{formatTime(new Date(a.at), prefs)}</span>
                      {a.count > 1 && <span>{tr('activity.activityPage.changes', { count: a.count })}</span>}
                      {a.via && (
                        // The step came from a note you forwarded from a trusted address.
                        <button type="button" className="activity-via link" data-testid="activity-via" onClick={() => nav(targetPath(a.via ?? null) ?? '/')}>
                          {a.via.title ? tr('activity.activityPage.fromYourForward', { title: a.via.title }) : tr('activity.activityPage.fromAnEmailYouForwarded')}
                        </button>
                      )}
                      {!a.ok && <span className="activity-failed">{tr('activity.activityPage.didNotComplete')}</span>}
                    </span>
                  </div>
                  {a.undone ? (
                    <span className="activity-undone">{tr('common.undone')}</span>
                  ) : a.undo ? (
                    <button type="button" className="btn tonal sm" disabled={busy === a.id} onClick={() => void undo(a)} aria-label={tr('activity.activityPage.undo', { text: a.text })}>
                      {tr('common.undo')}
                    </button>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
      {q.hasNextPage && (
        <div className="activity-more">
          <button type="button" className="btn text" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>{tr('activity.activityPage.showEarlierChanges')}</button>
        </div>
      )}
    </section>
  )
}

export default ActivityPage
