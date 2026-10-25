// The quick-add bar (task mode): one line through the shared grammar
// (lib/quickadd.ts), each parsed token highlighted and shown as a chip that turns back into
// title text (× or a click on the highlight). Enter creates and keeps focus (rapid entry);
// Shift/Ctrl+Enter creates and opens the pane; Tab on an empty line after a create makes the
// next entry a subtask of the last one; pasting several lines asks "Create n tasks?".

import { CornerDownRight, Plus, X } from 'lucide-react'
import { forwardRef, useImperativeHandle, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import { formatDate, formatTime, parseISO, type DatePrefs } from '../../lib/dates'
import { parse, type QuickResult, type QuickToken } from '../../lib/quickadd'
import { summarizeRRule } from '../../lib/rrule'
import { offsetLabel } from '../../lib/quickChoices'
import { ask } from './actions'
import { PRIORITY_NAMES, priorityLevel, type TagInfo, type TaskItem, type TaskList } from './model'
import { useTasksUI } from './store'
import { join } from './when'
import { t as tr } from '../../i18n'

export interface Prefill {
  due?: string
  list?: string
  priority?: number
  tag?: string
  /** What the hint says: "Today", "Work", "@phone". */
  label?: string
}

export interface QuickAddHandle {
  focus: (text?: string) => void
}

interface Props {
  prefill: Prefill
  lists: TaskList[]
  tags: TagInfo[]
  prefs: DatePrefs
  now: Date
  defaultList: string | null
  /** Creates one task; returns it (or null). `open` asks for the pane. */
  onCreate: (body: Record<string, unknown>, opts: { open: boolean; newList?: string | null }) => Promise<TaskItem | null>
  onLeaveDown?: () => void
  parentTitle?: (uid: string) => string | undefined
  /** The bar sits at the bottom of the screen (the phone layout): chips above the line. */
  atBottom?: boolean
}

interface Chip {
  key: string
  kind: string
  label: string
  tokens: QuickToken[]
  isNew?: boolean
}

function chipsOf(r: QuickResult, tags: TagInfo[], prefs: DatePrefs): Chip[] {
  const out: Chip[] = []
  const byKind = (k: string) => r.tokens.filter((t) => t.kind === k)
  const dt = [...byKind('date'), ...byKind('time')]
  if (r.due && dt.length) {
    const label = r.due_all_day ? formatDate(r.due, 'day') : `${formatDate(r.due.slice(0, 10), 'day')}, ${formatTime(parseISO(join(r.due.slice(0, 10), r.due.slice(11, 16), prefs.zone))!, prefs, { compact: true })}`
    out.push({ key: 'due', kind: 'date', label: tr('tasks.quickAdd.due', { label }), tokens: dt })
  }
  if (r.start_date) out.push({ key: 'start', kind: 'start', label: tr('tasks.quickAdd.starts', { date: formatDate(r.start_date, 'day') }), tokens: byKind('start') })
  if (r.rrule) out.push({ key: 'repeat', kind: 'repeat', label: `${summarizeRRule(r.rrule, r.due?.slice(0, 10))}${r.repeat_from === 'completion' ? tr('tasks.quickAdd.afterDone') : ''}`, tokens: byKind('repeat') })
  r.reminders.forEach((m, i) => {
    const tok = byKind('reminder')[i]
    const label = m.minutes != null ? (m.minutes === 0 ? tr('tasks.quickAdd.remindAtDueTime') : tr('tasks.quickAdd.remindBefore', { replace: offsetLabel(m.minutes).replace(' before', '') })) : tr('tasks.quickAdd.remind', { replace: m.at?.replace('T', ' ') })
    out.push({ key: `rem${i}`, kind: 'reminder', label, tokens: tok ? [tok] : [] })
  })
  const lvl = priorityLevel(r.priority)
  if (lvl) out.push({ key: 'pri', kind: 'priority', label: tr('tasks.quickAdd.priority', { priorityNames: PRIORITY_NAMES[lvl] }), tokens: byKind('priority') })
  if (r.list_or_calendar) {
    const l = r.list_or_calendar
    out.push({ key: 'list', kind: 'collection', label: l.create ? tr('tasks.quickAdd.createList', { itemName: l.name }) : tr('tasks.quickAdd.list', { itemName: l.name }), tokens: byKind('collection'), isNew: l.create })
  }
  byKind('tag').forEach((t, i) => {
    const name = t.text.replace(/^@/, '')
    out.push({ key: `tag${i}`, kind: 'tag', label: `@${name}`, tokens: [t], isNew: !tags.some((x) => x.name.toLowerCase() === name.toLowerCase()) })
  })
  return out
}

/** The task body for one parsed line, with the view's defaults under the typed values. */
export function bodyFor(r: QuickResult, prefill: Prefill, defaultList: string | null, zone: string): Record<string, unknown> | null {
  const summary = r.title.trim()
  if (!summary) return null
  const due = r.due ? (r.due_all_day ? r.due : join(r.due.slice(0, 10), r.due.slice(11, 16), zone)) : prefill.due ?? null
  const b: Record<string, unknown> = { summary, tz: zone }
  if (due) b.due = due
  if (r.start_date) b.start = r.start_date
  if (r.rrule) {
    b.recurrence = r.rrule
    b.repeat_from = r.repeat_from ?? 'due'
  }
  const list = r.list_or_calendar && !r.list_or_calendar.create ? r.list_or_calendar.id : prefill.list ?? defaultList
  if (list) b.list = list
  const tags = [...r.tags, ...(prefill.tag ? [prefill.tag] : [])]
  const uniq = tags.filter((t, i) => tags.findIndex((x) => x.toLowerCase() === t.toLowerCase()) === i)
  if (uniq.length) b.categories = uniq
  const pri = r.priority || prefill.priority || 0
  if (pri) b.priority = pri
  if (r.reminders.length) {
    const dueAt = due ? parseISO(due, zone) : null
    const mins = r.reminders.map((m) => {
      if (m.minutes != null) return m.minutes
      if (m.at && dueAt) return Math.round((dueAt.getTime() - parseISO(m.at, zone)!.getTime()) / 60_000)
      return null
    }).filter((m): m is number => m != null)
    if (mins.length) b.reminders = mins
  }
  return b
}

export const QuickAdd = forwardRef<QuickAddHandle, Props>(function QuickAdd({ prefill, lists, tags, prefs, now, defaultList, onCreate, onLeaveDown, parentTitle, atBottom = false }, ref) {
  const [text, setText] = useState('')
  const [keep, setKeep] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const subtaskOf = useTasksUI((s) => s.subtaskOf)
  const lastCreated = useTasksUI((s) => s.lastCreated)
  const ui = useTasksUI.getState

  useImperativeHandle(ref, () => ({
    focus: (t?: string) => {
      if (t != null) setText(t)
      inputRef.current?.focus()
    },
  }))

  const qlists = useMemo(() => lists.map((l) => ({ id: l.id, name: l.name })), [lists])
  const result = useMemo(() => parse(text, { mode: 'task', now, tz: prefs.zone, lists: qlists, keep }), [text, now, prefs.zone, qlists, keep])
  const chips = useMemo(() => chipsOf(result, tags, prefs), [result, tags, prefs])

  const keepAsText = (c: Chip) => setKeep((k) => [...k, ...c.tokens.map((t) => t.text)])

  async function create(open: boolean, r = result): Promise<TaskItem | null> {
    const body = bodyFor(r, prefill, defaultList, prefs.zone)
    if (!body) return null
    if (subtaskOf) body.parent = subtaskOf
    const newList = r.list_or_calendar?.create ? r.list_or_calendar.name : null
    return onCreate(body, { open, newList })
  }

  const submit = async (open: boolean) => {
    if (busy) return
    if (!result.title.trim()) return
    setBusy(true)
    const t = await create(open)
    setBusy(false)
    if (!t) return
    setText('')
    setKeep([])
    // ACT task.subtask_add @web.tasks: Tab after a create makes the next entry its subtask.
    if (!subtaskOf) ui().setLastCreated(t.uid)
    if (!open) inputRef.current?.focus()
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void submit(e.shiftKey || e.ctrlKey || e.metaKey)
    } else if (e.key === 'Tab' && !e.shiftKey && !text && lastCreated && !subtaskOf) {
      e.preventDefault()
      ui().setSubtaskOf(lastCreated)
    } else if ((e.key === 'Tab' && e.shiftKey && subtaskOf) || (e.key === 'Backspace' && !text && subtaskOf)) {
      e.preventDefault()
      ui().setSubtaskOf(null)
    } else if (e.key === 'ArrowDown' && onLeaveDown) {
      e.preventDefault()
      onLeaveDown()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      inputRef.current?.blur()
    } else if (e.key === 'Backspace' && text) {
      // Backspace right after a token turns it back into text.
      const at = e.currentTarget.selectionStart ?? 0
      const tok = result.tokens.find((t) => t.end === at && e.currentTarget.selectionEnd === at)
      if (tok && !keep.includes(tok.text)) {
        e.preventDefault()
        setKeep((k) => [...k, tok.text])
      }
    }
  }

  const onPaste = async (e: ClipboardEvent<HTMLInputElement>) => {
    const lines = e.clipboardData.getData('text').split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    if (lines.length < 2) return
    e.preventDefault()
    const choice = await ask({ title: tr('tasks.quickAdd.createTasks', { count: lines.length }), body: tr('tasks.quickAdd.eachLineBecomesATask'), confirm: tr('tasks.quickAdd.createTasks2', { count: lines.length }) })
    if (choice !== 'confirm') return
    setBusy(true)
    for (const line of lines) {
      const r = parse(line, { mode: 'task', now, tz: prefs.zone, lists: qlists })
      await create(false, r)
    }
    setBusy(false)
  }

  // The highlight layer: the same text, with each token's span marked by kind.
  const spans = useMemo(() => {
    const out: { text: string; kind?: string }[] = []
    let at = 0
    for (const t of [...result.tokens].sort((a, b) => a.start - b.start)) {
      if (t.start < at) continue
      if (t.start > at) out.push({ text: text.slice(at, t.start) })
      out.push({ text: text.slice(t.start, t.end), kind: t.kind })
      at = t.end
    }
    out.push({ text: text.slice(at) })
    return out
  }, [result.tokens, text])

  const parentName = subtaskOf ? parentTitle?.(subtaskOf) : undefined

  const chipRow = (chips.length > 0 || !!prefill.label) && (
    <div className="tk-quick-chips">
      {chips.map((c) => (
        <span key={c.key} className={`tk-chip tk-chip-${c.kind}${c.isNew ? ' new' : ''}`}>
          {c.label}
          {c.isNew && c.kind === 'tag' && <em className="tk-new">{tr('tasks.quickAdd.new')}</em>}
          <button type="button" aria-label={tr('tasks.quickAdd.keepAsText', { map: c.tokens.map((t) => t.text).join(' ') })} onClick={() => keepAsText(c)}>
            <X size={12} />
          </button>
        </span>
      ))}
      {prefill.label && !chips.some((c) => c.kind === 'date') && <span className="tk-quick-hint">{tr('tasks.quickAdd.addsTo', { prefillLabel: prefill.label })}</span>}
    </div>
  )

  return (
    <div className={`tk-quick${atBottom ? ' tk-quick-bottom' : ''}`} data-testid="tasks-quick-add">
      {/* At the bottom of the screen the chips sit above the line, next to the list they add to. */}
      {atBottom && chipRow}
      <div className="tk-quick-line">
        {subtaskOf ? (
          <button type="button" className="tk-quick-sub" title={tr('tasks.quickAdd.subtaskModeShiftTabOr')} onClick={() => ui().setSubtaskOf(null)}>
            <CornerDownRight size={16} aria-hidden /> <span className="ellipsis">{tr('tasks.quickAdd.subtaskOf', { parentName: parentName ?? tr('tasks.quickAdd.theLastTask') })}</span> <X size={14} aria-label={tr('tasks.quickAdd.leaveSubtaskMode')} />
          </button>
        ) : (
          <Plus className="tk-quick-plus" size={20} aria-hidden />
        )}
        <div className="tk-quick-field">
          <div className="tk-quick-overlay" ref={overlayRef} aria-hidden>
            {spans.map((s, i) => (s.kind ? <mark key={i} className={`tk-tok tk-tok-${s.kind}`}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
          </div>
          <input
            ref={inputRef}
            className="tk-quick-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            onPaste={(e) => void onPaste(e)}
            onScroll={(e) => {
              if (overlayRef.current) overlayRef.current.scrollLeft = e.currentTarget.scrollLeft
            }}
            onClick={(e) => {
              // A click on a highlighted token turns it back into title text.
              const at = e.currentTarget.selectionStart ?? -1
              const tok = result.tokens.find((t) => at > t.start && at < t.end)
              if (tok && e.detail === 2) setKeep((k) => [...k, tok.text])
            }}
            placeholder={subtaskOf ? tr('tasks.quickAdd.addASubtask') : tr('tasks.quickAdd.addATaskTryCall')}
            aria-label={tr('tasks.quickAdd.addATask')}
            aria-busy={busy}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
      </div>
      {!atBottom && chipRow}
    </div>
  )
})
