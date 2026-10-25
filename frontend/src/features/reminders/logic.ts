// Reminders on the web: the pure parts of the tray,
// the quick-action sheet, the triage queue and the browser notifications. The server plans and
// fires reminders; the web shows them and acts on them. public/sw.js repeats the
// button and summary rules for Web Push, because a service worker cannot import this module.

import { diffDaysISO, formatDate, formatTime, isISODate, parseISO, todayISO, toISODate, type DatePrefs } from '../../lib/dates'
import { t as tr } from '../../i18n'

/** A note reminder has the key n|<note id>|abs|<instant>. */
export type ReminderKind = 'event' | 'task' | 'note'

/** One row of GET /reminders/active. */
export interface ReminderItem {
  key: string
  kind: ReminderKind
  uid: string
  occurrence: string
  minutes: number
  title: string
  detail: string
  /** The event's start or the task's due: an instant, or a date for all-day and date-only. */
  start: string | null
  fire_at: string
  fired_at: string | null
  missed: boolean
  state: 'fired' | 'snoozed'
  actions: string[]
  /** An event with guests: the event detail with its meeting prep open (event.prep). */
  prep?: string | null
}

/** The SSE `notification` event of kind `reminder` (the Web Push payload adds `token`). */
export interface ReminderPayload {
  type?: string
  kind?: string
  key: string
  title?: string
  body?: string
  start?: string | null
  item?: { kind: ReminderKind; uid?: string; occurrence?: string }
  click?: string
  missed?: boolean
  actions?: string[]
  /** Web Push only: one signed token per button (snooze, done, dismiss), each valid for that action. */
  tokens?: Record<string, string>
  /** The event's video link, when it has one. */
  join_url?: string | null
  /** The user's sound setting. */
  sound?: boolean
  /** A repeat of a reminder nobody acted on (Remind again); same key, so it replaces the first. */
  again?: boolean
  /** An event with guests: the path of its meeting prep (event.prep). */
  prep?: string | null
}

/** Remind again: the choices, worded as on Android. */
export const REPEAT_CHOICES: [number, string][] = [
  [0, tr('common.off')], [5, tr('reminders.logic.every5Minutes')], [10, tr('reminders.logic.every10Minutes')], [15, tr('reminders.logic.every15Minutes')], [30, tr('reminders.logic.every30Minutes')],
]

/** The minutes the Remind again choice shows: 0 when off. */
export function repeatMinutes(r: { enabled?: boolean; every_min?: number } | undefined | null): number {
  return r?.enabled && r.every_min ? r.every_min : 0
}

/** The line under Remind again, as on Android: "Every 10 minutes, up to three times, until you act on it". */
export function repeatSummary(r: { enabled?: boolean; every_min?: number } | undefined | null): string {
  const m = repeatMinutes(r)
  if (!m) return tr('common.off')
  const label = REPEAT_CHOICES.find(([v]) => v === m)?.[1] ?? tr('reminders.logic.everyMinutes', { m })
  return tr('reminders.logic.upToThreeTimesUntil', { label })
}

/** The query parameters that open the sheet (one key) or the triage queue from a notification. */
export const SHEET_PARAM = 'reminder'
export const TRIAGE_PARAM = 'reminders'
/** The tag of the "4 reminders" summary and of the quiet "3 missed reminders" notification. */
export const SUMMARY_TAG = 'mailr-reminders'
export const MISSED_TAG = 'mailr-reminders-missed'
/** More than this many at once become one summary notification. */
export const STACK_LIMIT = 3

const MIN = 60_000

export function kindOfKey(key: string): ReminderKind {
  return key.startsWith('t|') ? 'task' : key.startsWith('n|') ? 'note' : 'event'
}

/** A payload as a tray item, for a reminder the tray has not loaded yet. */
export function itemFromPayload(p: ReminderPayload, now = new Date()): ReminderItem {
  const kind = p.item?.kind ?? kindOfKey(p.key)
  const parts = p.key.split('|')
  return {
    key: p.key, kind, uid: p.item?.uid ?? parts[1] ?? '', occurrence: p.item?.occurrence ?? (kind === 'note' ? parts[3] : parts[2]) ?? '',
    minutes: kind === 'note' ? 0 : Number(parts[3] ?? 0) || 0, title: p.title ?? GENERIC_TITLE[kind],
    detail: p.body ?? '', start: p.start ?? null, fire_at: now.toISOString(), fired_at: now.toISOString(),
    missed: !!p.missed, state: 'fired', actions: p.actions ?? [], prep: p.prep ?? null,
  }
}

/** The title of a reminder that names nothing (a local-only record). */
export const GENERIC_TITLE: Record<ReminderKind, string> = { event: 'Event reminder', task: tr('reminders.logic.taskReminder'), note: tr('reminders.logic.noteReminder') }

/** When a start (or due) falls, as a Date; date-only values are local midnight in the zone. */
export function anchorOf(start: string | null | undefined, zone: string): Date | null {
  if (!start) return null
  return parseISO(start, zone)
}

function relDay(day: string, today: string): string {
  const d = diffDaysISO(today, day)
  if (d === 0) return 'today'
  if (d === 1) return 'tomorrow'
  if (d === -1) return 'yesterday'
  return formatDate(day, 'day')
}

function span(mins: number): string {
  const m = Math.abs(Math.round(mins))
  if (m < 60) return tr('reminders.logic.min', { m })
  const h = Math.floor(m / 60)
  const rest = m % 60
  if (h < 24) return rest ? tr('reminders.logic.hMin', { h, rest }) : `${h} h`
  const d = Math.floor(h / 24)
  return tr('reminders.logic.days', { count: d })
}

/**
 * The when line of the quick-action sheet and the tray: "Starts in 25 min", "Started 5 min ago", "Tomorrow ·
 * all day", "Due today 5:00 PM · overdue 2 h", "Due tomorrow".
 */
export function whenLine(r: Pick<ReminderItem, 'kind' | 'start'>, now: Date, prefs: Pick<DatePrefs, 'zone' | 'hour12'>): string {
  if (!r.start) return r.kind === 'task' ? tr('common.noDueDate') : ''
  const today = todayISO(prefs.zone, now)
  if (r.kind === 'note') {
    // A note reminder's start is the moment it was set for: "Reminder for today 9:00 AM".
    const at = parseISO(r.start, prefs.zone)
    return at ? tr('reminders.logic.reminderFor', { relDay: relDay(toISODate(at, prefs.zone), today), time: formatTime(at, prefs) }) : ''
  }
  if (isISODate(r.start)) {
    const day = r.start.slice(0, 10)
    if (r.kind === 'task') {
      const late = diffDaysISO(day, today)
      return tr('reminders.logic.due', { relDay: relDay(day, today), value: late > 0 ? tr('reminders.logic.overdueDays', { count: late }) : '' })
    }
    const rel = relDay(day, today)
    return tr('reminders.logic.allDay', { rel: rel[0].toUpperCase(), rel2: rel.slice(1) })
  }
  const at = parseISO(r.start, prefs.zone)
  if (!at) return ''
  const mins = (at.getTime() - now.getTime()) / MIN
  const day = toISODate(at, prefs.zone)
  const clock = formatTime(at, prefs)
  if (r.kind === 'task') {
    const base = tr('reminders.logic.due2', { relDay: relDay(day, today), clock })
    return mins < -1 ? tr('reminders.logic.overdue', { base, span: span(-mins) }) : base
  }
  if (mins > 0 && mins <= 90) return tr('reminders.logic.startsInMin', { max: Math.max(1, Math.round(mins)) })
  if (mins <= 0 && mins > -12 * 60) return Math.round(-mins) < 1 ? tr('reminders.logic.startingNow') : tr('reminders.logic.startedAgo', { span: span(-mins) })
  return tr('reminders.logic.starts', { relDay: relDay(day, today), clock })
}

/** Is an event reminder's event already running (the sheet offers Snooze and Dismiss then)? */
export function started(r: Pick<ReminderItem, 'kind' | 'start'>, now: Date, zone: string): boolean {
  if (r.kind !== 'event') return false
  const at = anchorOf(r.start, zone)
  return !!at && at.getTime() <= now.getTime()
}

export interface Choice {
  id: string
  label: string
  at: Date
}

function atHour(d: Date, h: number): Date {
  const x = new Date(d)
  x.setHours(h, 0, 0, 0)
  return x
}

/**
 * The snooze presets. Task: 10 min, 1 hour, 3 hours, This evening (only before
 * 17:00), Tomorrow morning, Next week (Monday morning). Event before its start: 5 min, 10 min,
 * When it starts (only when more than 2 min are left); after its start: 5 min, 15 min.
 */
export function snoozeChoices(kind: ReminderKind, now: Date, start: Date | null, morningHour = 9): Choice[] {
  const plus = (m: number) => new Date(now.getTime() + m * MIN)
  if (kind === 'event') {
    if (start && start.getTime() > now.getTime()) {
      const out: Choice[] = [
        { id: '5m', label: tr('reminders.logic.n5Min'), at: plus(5) },
        { id: '10m', label: tr('reminders.logic.n10Min'), at: plus(10) },
      ]
      if (start.getTime() - now.getTime() > 2 * MIN) out.push({ id: 'start', label: tr('reminders.logic.whenItStarts'), at: start })
      return out
    }
    return [
      { id: '5m', label: tr('reminders.logic.n5Min'), at: plus(5) },
      { id: '15m', label: tr('reminders.logic.n15Min'), at: plus(15) },
    ]
  }
  const out: Choice[] = [
    { id: '10m', label: tr('reminders.logic.n10Min'), at: plus(10) },
    { id: '1h', label: tr('common.n1Hour'), at: plus(60) },
    { id: '3h', label: tr('reminders.logic.n3Hours'), at: plus(180) },
  ]
  if (now.getHours() < 17) out.push({ id: 'evening', label: tr('reminders.logic.thisEvening'), at: atHour(now, 18) })
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)
  out.push({ id: 'tomorrow', label: tr('common.tomorrowMorning'), at: atHour(tomorrow, morningHour) })
  const monday = new Date(now)
  monday.setDate(monday.getDate() + (((8 - monday.getDay()) % 7) || 7))
  out.push({ id: 'nextweek', label: tr('common.nextWeek'), at: atHour(monday, morningHour) })
  return out
}

/**
 * The triage order of notification.open_stack: missed first, then fired (oldest first), then
 * snoozed ones whose time has come.
 */
export function stackOrder(items: ReminderItem[]): ReminderItem[] {
  const rank = (r: ReminderItem) => (r.missed ? 0 : r.state === 'fired' ? 1 : 2)
  const t = (r: ReminderItem) => Date.parse(r.fired_at ?? r.fire_at) || 0
  return [...items].sort((a, b) => rank(a) - rank(b) || t(a) - t(b) || a.key.localeCompare(b.key))
}

export interface Button {
  action: 'done' | 'snooze' | 'dismiss' | 'join' | 'before' | 'prep'
  title: string
}

/**
 * The two buttons of a web notification. Task: Done, Snooze 1 h. Event: Snooze 5 min, Dismiss;
 * Join replaces Dismiss when the event has a conference link and starts within 5 minutes.
 */
/** The notification's buttons; ACT event.join @web.notification takes the place of Dismiss near the start. */
export function notificationButtons(kind: ReminderKind, start: Date | null, now: Date, joinUrl?: string | null, prep?: string | null): Button[] {
  if (kind === 'task') return [{ action: 'done', title: tr('common.done') }, { action: 'snooze', title: tr('reminders.logic.snooze1H') }]
  // A note reminder: nothing to complete or join.
  if (kind === 'note') return [{ action: 'snooze', title: tr('reminders.logic.snooze1H') }, { action: 'dismiss', title: tr('common.dismiss') }]
  const soon = !!start && start.getTime() - now.getTime() <= 5 * MIN && now.getTime() - start.getTime() < 60 * MIN
  // ACT event.prep @web.notification: an event with guests offers Prep (its meeting prep pack) in the second
  // place, before the start; Remind before (or Dismiss) moves to the third, which a two-button browser drops.
  // ACT event.remind_before @web.notification: offered while the start is more than 5 minutes away. It opens the
  // sheet on its offsets. A browser that shows two buttons drops the last one (Dismiss).
  if (start && start.getTime() - now.getTime() > 5 * MIN)
    return prep
      ? [{ action: 'snooze', title: tr('reminders.logic.snooze5Min') }, { action: 'prep', title: tr('reminders.logic.prep') }, { action: 'before', title: tr('reminders.logic.remindBefore') }]
      : [{ action: 'snooze', title: tr('reminders.logic.snooze5Min') }, { action: 'before', title: tr('reminders.logic.remindBefore') }, { action: 'dismiss', title: tr('common.dismiss') }]
  const prepBtn: Button[] = prep ? [{ action: 'prep', title: tr('reminders.logic.prep') }] : []
  if (joinUrl && soon) return [{ action: 'snooze', title: tr('reminders.logic.snooze5Min') }, { action: 'join', title: tr('common.join') }, ...prepBtn]
  return [{ action: 'snooze', title: tr('reminders.logic.snooze5Min') }, ...prepBtn, { action: 'dismiss', title: tr('common.dismiss') }]
}

/** The query value that opens the sheet on the Remind before offsets (`?reminder=<key>&expand=before`). */
export const EXPAND_PARAM = 'expand'

/** "4 reminders", or "3 task reminders" when they are all of one kind. */
export function summaryTitle(kinds: ReminderKind[]): string {
  const n = kinds.length
  const one = kinds.every((k) => k === kinds[0])
  const what = one ? tr('reminders.logic.reminder', { kinds: kinds[0] }) : 'reminder'
  return tr('reminders.logic.s', { count: n, what })
}

/**
 * What a burst of fired reminders becomes: each its own notification, or, when more than three
 * are showing at once, one summary that opens the triage queue.
 */
export function planBurst(showing: string[], incoming: string[]): { summary: boolean; total: number } {
  const all = new Set([...showing, ...incoming])
  return { summary: all.size > STACK_LIMIT, total: all.size }
}

/** The notifications to close: shown for keys the tray no longer lists (handled elsewhere). */
export function staleTags(shown: string[], active: ReminderItem[]): string[] {
  const keep = new Set(active.map((r) => r.key))
  return shown.filter((k) => k !== SUMMARY_TAG && k !== MISSED_TAG && !keep.has(k))
}

/** Missed reminders not yet announced on this device, for the one quiet "3 missed" notice. */
export function newMissed(items: ReminderItem[], announced: Set<string>): ReminderItem[] {
  return items.filter((r) => r.missed && !announced.has(r.key))
}

/** The URL a notification click opens when no mailr tab is open. */
export function clickUrl(click: string | undefined, key: string | null, expand?: 'before'): string {
  const base = click && click.startsWith('/') ? click : '/mail/inbox'
  if (!key) return `${base}${base.includes('?') ? '&' : '?'}${TRIAGE_PARAM}=review`
  return `${base}${base.includes('?') ? '&' : '?'}${SHEET_PARAM}=${encodeURIComponent(key)}${expand ? `&${EXPAND_PARAM}=${expand}` : ''}`
}

/** The app link for a reminder's record: the event occurrence or the task. */
export function recordPath(r: Pick<ReminderItem, 'kind' | 'uid' | 'occurrence'>): string {
  if (r.kind === 'task') return `/tasks/task/${encodeURIComponent(r.uid)}`
  if (r.kind === 'note') return `/notes/n/${encodeURIComponent(r.uid)}`
  return `/calendar/event/${encodeURIComponent(r.uid)}/${encodeURIComponent(r.occurrence)}`
}

/**
 * Reminders that fired after the triage queue opened and are not in it yet (reminder.fire on
 * web.triage): fired, not snoozed, in the order they fired.
 */
export function joinedWhileOpen(items: ReminderItem[], queue: string[], openedAt: number): string[] {
  const have = new Set(queue)
  return items
    .filter((r) => !have.has(r.key) && r.state === 'fired' && r.fired_at && Date.parse(r.fired_at) >= openedAt - 1000)
    .sort((a, b) => Date.parse(a.fired_at!) - Date.parse(b.fired_at!))
    .map((r) => r.key)
}
