// A title edited in place (the row's F2 / double-click, and the pane's title): the grammar's
// tokens are offered as chips and applied only when clicked, never silently.

import { useMemo, useState, type KeyboardEvent } from 'react'
import { buildTitle, parse, type QuickToken } from '../../lib/quickadd'
import { formatDate, type DatePrefs } from '../../lib/dates'
import { summarizeRRule } from '../../lib/rrule'
import { PRIORITY_NAMES, priorityLevel, type TaskList } from './model'
import { join } from './when'
import { t as tr } from '../../i18n'

export interface TitleChange {
  summary: string
  extra: Record<string, unknown>
}

interface Offer {
  label: string
  tokens: QuickToken[]
  apply: Record<string, unknown>
}

/** What the grammar finds in a title, as changes the user can apply one by one. */
export function offersFor(text: string, now: Date, prefs: DatePrefs, lists: TaskList[]): Offer[] {
  const r = parse(text, { mode: 'task', now, tz: prefs.zone, lists: lists.map((l) => ({ id: l.id, name: l.name })) })
  const k = (kind: string) => r.tokens.filter((t) => t.kind === kind)
  const out: Offer[] = []
  if (r.due) {
    const due = r.due_all_day ? r.due : join(r.due.slice(0, 10), r.due.slice(11, 16), prefs.zone)
    out.push({ label: tr('tasks.inlineTitle.due', { date: formatDate(r.due.slice(0, 10), 'day'), value: r.due_all_day ? '' : ' ' + r.due.slice(11, 16) }), tokens: [...k('date'), ...k('time')], apply: { due, tz: prefs.zone } })
  }
  if (r.start_date) out.push({ label: tr('tasks.inlineTitle.starts', { date: formatDate(r.start_date, 'day') }), tokens: k('start'), apply: { start: r.start_date } })
  if (r.rrule) out.push({ label: summarizeRRule(r.rrule), tokens: k('repeat'), apply: { recurrence: r.rrule, repeat_from: r.repeat_from ?? 'due' } })
  if (r.priority) out.push({ label: tr('tasks.inlineTitle.priority', { priorityNames: PRIORITY_NAMES[priorityLevel(r.priority)] }), tokens: k('priority'), apply: { priority: r.priority } })
  if (r.list_or_calendar?.id) out.push({ label: tr('tasks.inlineTitle.moveTo', { listOrCalendarName: r.list_or_calendar.name }), tokens: k('collection'), apply: { list: r.list_or_calendar.id } })
  if (r.tags.length) out.push({ label: r.tags.map((t) => `@${t}`).join(' '), tokens: k('tag'), apply: { addTags: r.tags } })
  return out.filter((o) => o.tokens.length)
}

interface Props {
  initial: string
  now: Date
  prefs: DatePrefs
  lists: TaskList[]
  onSave: (c: TitleChange) => void
  onCancel: () => void
  className?: string
  label?: string
}

export function InlineTitle({ initial, now, prefs, lists, onSave, onCancel, className = '', label = tr('common.title') }: Props) {
  const [text, setText] = useState(initial)
  const [extra, setExtra] = useState<Record<string, unknown>>({})
  const offers = useMemo(() => offersFor(text, now, prefs, lists), [text, now, prefs, lists])

  const save = () => {
    const summary = text.trim()
    if (!summary) return onCancel()
    onSave({ summary, extra })
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation()
    if (e.key === 'Enter') {
      e.preventDefault()
      save()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    }
  }
  return (
    <div className={`tk-inline ${className}`} onClick={(e) => e.stopPropagation()}>
      <input
        className="tk-inline-input"
        value={text}
        autoFocus
        aria-label={label}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        onBlur={(e) => {
          if (e.relatedTarget && (e.currentTarget.parentElement?.contains(e.relatedTarget as Node))) return
          save()
        }}
        onFocus={(e) => e.currentTarget.select()}
      />
      {offers.length > 0 && (
        <div className="tk-inline-offers">
          {offers.map((o) => (
            <button
              key={o.label}
              type="button"
              className="tk-chip tk-chip-offer"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                setText(buildTitle(text, o.tokens))
                setExtra((x) => ({ ...x, ...o.apply }))
              }}
            >
              {tr('tasks.inlineTitle.apply', { label: o.label })}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
