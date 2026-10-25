import { bindingsFor, type ShortcutScope } from '../../lib/shortcuts'
import { t } from '../../i18n'

// The hints name actions; their keys come from the binding tables (lib/shortcuts), so a strip
// never shows a key the app does not bind (hard-coded capitals R and A read as Shift+R and
// Shift+A, two other actions).
export type Hint = [actions: string[], label: string]

export const LIST: Hint[] = [
  [['older', 'newer'], 'move'],
  [['open'], 'open'],
  [['select'], 'select'],
  [['archive'], 'archive'],
  [['delete'], 'delete'],
  [['compose'], 'compose'],
  [['search'], 'search'],
]
export const CONV: Hint[] = [
  [['reply'], 'reply'],
  [['replyAll'], t('themes.focus.hints.replyAll')],
  [['forward'], 'forward'],
  [['archive'], 'archive'],
  [['older', 'newer'], t('themes.focus.hints.nextPrev')],
  [['backToList'], 'back'],
]
export const SELECTED: Hint[] = [
  [['archive'], 'archive'],
  [['delete'], 'delete'],
  [['label'], 'label'],
  [['move'], 'move'],
  [['snooze'], 'snooze'],
  [['escape'], 'clear'],
]
export const CALENDAR: Hint[] = [
  [['calendar.next', 'calendar.prev'], t('themes.focus.hints.nextPrev')],
  [['calendar.today'], 'today'],
  [['calendar.viewDay', 'calendar.viewWeek', 'calendar.viewMonth'], t('themes.focus.hints.dayWeekMonth')],
  [['calendar.create'], 'create'],
  [['calendar.search'], 'search'],
  [['goInbox'], 'mail'],
  [['goTasks'], 'tasks'],
]
export const TASKS: Hint[] = [
  [['tasks.next', 'tasks.prev'], 'move'],
  [['tasks.complete'], 'complete'],
  [['tasks.quickAdd'], 'add'],
  [['tasks.reschedule'], 'reschedule'],
  [['tasks.select'], 'select'],
  [['goInbox'], 'mail'],
  [['goCalendar'], 'calendar'],
]
export const NOTES: Hint[] = [
  [['notes.next', 'notes.prev'], 'move'],
  [['notes.open'], 'open'],
  [['notes.newNote'], 'note'],
  [['notes.newList'], 'list'],
  [['notes.pin'], 'pin'],
  [['notes.search'], 'search'],
  [['goInbox'], 'mail'],
]
export const RECORDINGS: Hint[] = [
  [['recordings.next', 'recordings.prev'], t('themes.focus.hints.nextPrev')],
  [['recordings.open'], 'open'],
  [['recordings.search'], 'search'],
  [['recordings.record'], 'record'],
  [['goInbox'], 'mail'],
  [['goCalendar'], 'calendar'],
]

const NAMES: Record<string, string> = { enter: '↵', escape: t('common.esc'), space: t('common.space'), tab: t('common.tab'), delete: t('themes.focus.hints.del') }

/** The keys of one key token as caps: "g" → [g], "I" (Shift+i) → [Shift, i], "enter" → [↵]. */
function caps(token: string): string[] {
  if (token.length === 1 && token >= 'A' && token <= 'Z') return [t('themes.focus.hints.shift'), token.toLowerCase()]
  if (token.includes('+')) return token.split('+').map((p) => (p === 'ctrl' ? t('themes.focus.hints.ctrl') : p === 'alt' ? t('common.alt') : p === 'shift' ? t('themes.focus.hints.shift') : NAMES[p] ?? p))
  return [NAMES[token] ?? token]
}

/** The caps to show for an action: its first binding that is not an arrow key. */
export function hintKeys(scope: ShortcutScope, action: string): string[] {
  const list = bindingsFor(scope).filter((b) => b.action === action)
  const b = list.find((x) => !x.keys.startsWith('arrow')) ?? list[0]
  return b ? b.keys.split(' ').flatMap(caps) : []
}

/** The hints of a scope as (caps, label); a hint whose actions have no binding is left out. */
export function hintsFor(scope: ShortcutScope, hints: Hint[]): [string[], string][] {
  const out: [string[], string][] = []
  for (const [actions, label] of hints) {
    const keys = actions.flatMap((a) => hintKeys(scope, a))
    if (keys.length) out.push([keys, label])
  }
  return out
}
