// The detail pane's field inputs: repeat (task variant),
// reminders (offsets before the due plus "At a time…"), list picker, tags token field, subtasks checklist and
// notes with a light markdown preview.

import { Check, ChevronDown, GripVertical, Plus, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BlockedImage } from '../../lib/markdownImages'
import { appLinkLabel, appPath } from '../../lib/appLinks'
import { appNavigate } from '../../lib/appNav'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { DAY_CODES, describeRepeat, formatRRule, matchSmartOption, parseRRule, smartOptions, type DayCode, type Freq } from '../../lib/rrule'
import { WEEKDAY_SHORT, parseISO, type DatePrefs, type ISODate } from '../../lib/dates'
import { describeMoment, offsetLabel } from '../../lib/quickChoices'
import { taskRequest } from './api'
import { autoColor } from './colors'
import { isDone, type TagInfo, type TaskItem, type TaskList } from './model'
import { WhenPicker } from './WhenPicker'
import { isDateOnly, split } from './when'
import { t as tr } from '../../i18n'
import { rehypeDirAuto } from '../../lib/rehypeDirAuto'

// ---------------------------------------------------------------- repeat

interface RepeatProps {
  rrule: string | null
  repeatFrom: string
  anchor: ISODate
  onChange: (rrule: string | null, repeatFrom?: 'due' | 'completion') => void
  disabled?: boolean
}

/** ACT task.set_repeat @web.pane: smart options for the due day, a custom editor, and "from due / completion". */
export function RepeatField({ rrule, repeatFrom, anchor, onChange, disabled }: RepeatProps) {
  const a = useAnchor<HTMLButtonElement>()
  const rule = parseRRule(rrule)
  const readOnly = !!rule?.unsupported
  const [custom, setCustom] = useState(false)
  const options = smartOptions(anchor)
  const current = matchSmartOption(rrule, anchor)
  const label = rrule ? describeRepeat(rrule, anchor, { count: 2 }) : tr('common.doesNotRepeat')
  return (
    <div className="tk-field-stack">
      <button type="button" className="tk-field-btn" disabled={disabled} onClick={(e) => a.toggle(e.currentTarget)} aria-label={tr('common.repeat')} data-testid="pane-repeat">
        <span className="ellipsis">{readOnly ? tr('tasks.paneFields.customRuleSetInAnother') : label}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {rrule && (
        <div className="tk-seg" role="radiogroup" aria-label={tr('tasks.paneFields.repeatFrom')}>
          {(['due', 'completion'] as const).map((f) => (
            <button key={f} type="button" role="radio" aria-checked={(repeatFrom || 'due') === f} className={(repeatFrom || 'due') === f ? 'on' : ''} disabled={disabled}
              onClick={() => onChange(rrule, f)}>
              {f === 'due' ? tr('tasks.paneFields.fromDueDate') : tr('tasks.paneFields.fromCompletion')}
            </button>
          ))}
        </div>
      )}
      {custom && <CustomRepeat initial={readOnly ? null : rrule} anchor={anchor} onDone={(r) => {
        setCustom(false)
        if (r) onChange(r)
      }} />}
      <Popover anchor={a.anchor} open={a.open} onClose={a.close} label={tr('common.repeat')}>
        {options.map((o) => (
          <MenuItem key={o.id} checked={current === o.id} onSelect={() => {
            a.close()
            if (o.id === 'custom') setCustom(true)
            else onChange(o.rrule)
          }}>{o.label}</MenuItem>
        ))}
      </Popover>
    </div>
  )
}

function CustomRepeat({ initial, anchor, onDone }: { initial: string | null; anchor: ISODate; onDone: (r: string | null) => void }) {
  const r0 = parseRRule(initial)
  const [freq, setFreq] = useState<Freq>(r0?.freq ?? 'WEEKLY')
  const [interval, setInterval] = useState(r0?.interval ?? 1)
  const [days, setDays] = useState<DayCode[]>(r0?.byDay?.filter((d) => !d.nth).map((d) => d.day) ?? [DAY_CODES[new Date(`${anchor}T12:00:00Z`).getUTCDay()]])
  const [ends, setEnds] = useState<'never' | 'on' | 'after'>(r0?.count ? 'after' : r0?.until ? 'on' : 'never')
  const [until, setUntil] = useState(r0?.until && /^\d{4}-\d{2}-\d{2}$/.test(r0.until) ? r0.until : '')
  const [count, setCount] = useState(r0?.count ?? 5)
  const value = formatRRule({
    freq, interval: Math.max(1, interval), byDay: freq === 'WEEKLY' && days.length ? days.map((d) => ({ day: d })) : undefined,
    count: ends === 'after' ? count : undefined, until: ends === 'on' && until ? until : undefined,
  })
  return (
    <div className="tk-custom-repeat" role="group" aria-label={tr('tasks.paneFields.customRepeat')}>
      <div className="tk-row-fields">
        <span>{tr('common.every')}</span>
        <input type="number" min={1} max={99} value={interval} aria-label={tr('tasks.paneFields.interval')} onChange={(e) => setInterval(Number(e.target.value) || 1)} />
        <select value={freq} aria-label={tr('common.unit')} onChange={(e) => setFreq(e.target.value as Freq)}>
          <option value="DAILY">{interval > 1 ? 'days' : 'day'}</option>
          <option value="WEEKLY">{interval > 1 ? 'weeks' : 'week'}</option>
          <option value="MONTHLY">{interval > 1 ? 'months' : 'month'}</option>
          <option value="YEARLY">{interval > 1 ? 'years' : 'year'}</option>
        </select>
      </div>
      {freq === 'WEEKLY' && (
        <div className="tk-days">
          {DAY_CODES.map((d, i) => (
            <button key={d} type="button" aria-pressed={days.includes(d)} className={days.includes(d) ? 'on' : ''}
              onClick={() => setDays((x) => (x.includes(d) ? x.filter((y) => y !== d) : [...x, d]))}>{WEEKDAY_SHORT[i].slice(0, 2)}</button>
          ))}
        </div>
      )}
      <div className="tk-row-fields">
        <span>{tr('common.ends')}</span>
        <select value={ends} aria-label={tr('common.ends')} onChange={(e) => setEnds(e.target.value as 'never')}>
          <option value="never">{tr('common.never')}</option>
          <option value="on">{tr('tasks.paneFields.onADate')}</option>
          <option value="after">{tr('tasks.paneFields.after2')}</option>
        </select>
        {ends === 'on' && <input type="date" value={until} aria-label={tr('tasks.paneFields.until')} onChange={(e) => setUntil(e.target.value)} />}
        {ends === 'after' && <><input type="number" min={1} value={count} aria-label={tr('tasks.paneFields.times')} onChange={(e) => setCount(Number(e.target.value) || 1)} /><span>{tr('tasks.paneFields.times2')}</span></>}
      </div>
      <div className="tk-when-result">{describeRepeat(value, anchor)}</div>
      <div className="tk-row-fields end">
        <button type="button" className="btn text sm" onClick={() => onDone(null)}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled sm" onClick={() => onDone(value)}>{tr('tasks.paneFields.setRepeat')}</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- reminders

/** Reminder offsets for tasks: relative to the due; date-only dues count from local midnight at 9:00. */
export function reminderOptions(dateOnly: boolean, morning = 9): { minutes: number; label: string }[] {
  if (dateOnly) {
    const m = morning * 60
    return [
      { minutes: -m, label: tr('tasks.paneFields.onTheDayAt00', { morning }) },
      { minutes: 1440 - m, label: tr('tasks.paneFields.n1DayBeforeAt00', { morning }) },
      { minutes: 2880 - m, label: tr('tasks.paneFields.n2DaysBeforeAt00', { morning }) },
      { minutes: 10080 - m, label: tr('tasks.paneFields.n1WeekBeforeAt00', { morning }) },
    ]
  }
  return [0, 5, 10, 15, 30, 60, 120, 1440, 2880].map((m) => ({ minutes: m, label: m === 0 ? tr('tasks.paneFields.atDueTime') : offsetLabel(m) }))
}

export function reminderLabel(m: number, dateOnly: boolean): string {
  const o = reminderOptions(dateOnly).find((x) => x.minutes === m)
  if (o) return o.label
  if (m < 0) return tr('tasks.paneFields.after', { replace: offsetLabel(-m).replace(' before', '') })
  return offsetLabel(m)
}

interface RemindProps {
  task: TaskItem
  now: Date
  prefs: DatePrefs
  /** `reminders` (minutes before the due) or `reminders_at` (instants): the task API's two fields. */
  onChange: (patch: { reminders: number[] } | { reminders_at: string[] }, label: string) => void
  disabled?: boolean
}

/** ACT task.remind_at @web.pane: the reminder chips (before the due, and at a set time, each with its
 *  remove button), the offset choices, and "At a time…" (an absolute reminder, `reminders_at`). */
export function ReminderField({ task, now, prefs, onChange, disabled }: RemindProps) {
  const a = useAnchor<HTMLButtonElement>()
  const at = useAnchor<HTMLButtonElement>()
  const mins = task.reminders ?? []
  const ats = [...(task.reminders_at ?? [])].sort()
  const dateOnly = isDateOnly(task.due)
  const opts = reminderOptions(dateOnly)
  return (
    <div className="tk-chips-field">
      {mins.map((m) => (
        <span key={m} className="tk-chip">
          {reminderLabel(m, dateOnly)}
          {!disabled && <button type="button" aria-label={tr('tasks.paneFields.removeReminder', { reminderLabel: reminderLabel(m, dateOnly) })} onClick={() => onChange({ reminders: mins.filter((x) => x !== m) }, tr('tasks.paneFields.reminderRemoved'))}><X size={12} /></button>}
        </span>
      ))}
      {ats.map((iso) => {
        const label = describeMoment(new Date(iso), now)
        return (
          <span key={iso} className="tk-chip" data-testid="remind-at">
            {label}
            {!disabled && <button type="button" aria-label={tr('tasks.paneFields.removeReminder', { reminderLabel: label })} onClick={() => onChange({ reminders_at: ats.filter((x) => x !== iso) }, tr('tasks.paneFields.reminderRemoved'))}><X size={12} /></button>}
          </span>
        )
      })}
      <button type="button" className="tk-chip tk-chip-add" disabled={disabled || !task.due} title={task.due ? undefined : tr('tasks.paneFields.setADueDateFirst')} onClick={(e) => a.toggle(e.currentTarget)}>
        <Plus size={12} aria-hidden />{' '}{tr('common.remindMe')}
      </button>
      <Popover anchor={a.anchor} open={a.open} onClose={a.close} label={tr('common.remindMe')}>
        {opts.map((o) => (
          <MenuItem key={o.minutes} checked={mins.includes(o.minutes)} onSelect={() => {
            a.close()
            onChange({ reminders: mins.includes(o.minutes) ? mins.filter((x) => x !== o.minutes) : [...mins, o.minutes].sort((x, y) => x - y) }, tr('tasks.paneFields.reminderAdded'))
          }}>{o.label}</MenuItem>
        ))}
        <MenuSep />
        <MenuItem onSelect={() => {
          const el = a.anchor
          a.close()
          if (el instanceof HTMLElement) at.show(el as HTMLButtonElement)
        }}>{tr('tasks.paneFields.atATime')}</MenuItem>
      </Popover>
      <Popover anchor={at.anchor} open={at.open} onClose={at.close} role="dialog" className="tk-when-pop" label={tr('common.remindMeAt')} noFocus>
        <WhenPicker value={null} now={now} prefs={prefs} label={tr('common.remindMeAt')} noDate={false} laterToday needTime onPick={(v) => {
          at.close()
          if (!v) return
          // An instant of its own (reminders_at), not minutes before the due: a time days after the
          // due is kept as it is (10 Oct 2026).
          const iso = parseISO(v, prefs.zone)!.toISOString()
          if (!ats.includes(iso)) onChange({ reminders_at: [...ats, iso].sort() }, tr('tasks.paneFields.reminderAdded'))
        }} />
      </Popover>
    </div>
  )
}

// ---------------------------------------------------------------- list

export function ListPicker({ value, lists, onPick, disabled }: { value: string | undefined; lists: TaskList[]; onPick: (l: TaskList) => void; disabled?: boolean }) {
  const a = useAnchor<HTMLButtonElement>()
  const cur = lists.find((l) => l.id === value)
  return (
    <>
      <button type="button" className="tk-field-btn" disabled={disabled} aria-label={tr('common.list')} data-testid="pane-list" onClick={(e) => a.toggle(e.currentTarget)}>
        <i className="tk-dot" style={{ background: cur?.color || autoColor(cur?.name ?? '') }} />
        <span className="ellipsis">{cur?.name ?? tr('tasks.paneFields.chooseAList')}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      <Popover anchor={a.anchor} open={a.open} onClose={a.close} label={tr('common.moveToList')}>
        {lists.filter((l) => !l.read_only).map((l) => (
          <MenuItem key={l.id} icon={<i className="tk-dot" style={{ background: l.color || autoColor(l.name) }} />} onSelect={() => {
            a.close()
            if (l.id !== value) onPick(l)
          }}>
            {l.name}{l.id === value ? ' ✓' : ''}
          </MenuItem>
        ))}
      </Popover>
    </>
  )
}

// ---------------------------------------------------------------- parent

/**
 * ACT task.reparent @web.pane: "Subtask of", one level. The choices are the open top-level tasks of the
 * same list; "None" makes it a task of its own. A task with subtasks stays at the top level.
 */
export function ParentPicker({ task, onPick, disabled }: { task: TaskItem; onPick: (parent: TaskItem | null) => void; disabled?: boolean }) {
  const a = useAnchor<HTMLButtonElement>()
  const [q, setQ] = useState('')
  const parents = useQuery<TaskItem[]>({
    // Outside the ['tasks'] caches, which the task writes edit in place.
    queryKey: ['taskParents', task.list ?? ''],
    queryFn: async ({ signal }) => {
      const r = await taskRequest<{ items: TaskItem[] } | TaskItem[]>('GET', '/tasks', undefined, { list: task.list, parent: 'none', status: 'open', sort: 'title' }, signal)
      return Array.isArray(r) ? r : r.items ?? []
    },
    enabled: a.open || !!task.parent,
    staleTime: 30_000,
  })
  const all = (parents.data ?? []).filter((x) => x.uid !== task.uid && !x.parent)
  const current = task.parent ? all.find((x) => x.uid === task.parent) : null
  const hasKids = (task.subtask_count ?? 0) > 0
  const shown = all.filter((x) => !q.trim() || x.summary.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 50)
  return (
    <>
      <button type="button" className="tk-field-btn" disabled={disabled || (hasKids && !task.parent)} aria-label={tr('tasks.paneFields.subtaskOf')} data-testid="pane-parent"
        title={hasKids && !task.parent ? tr('tasks.paneFields.aTaskWithSubtasksStays') : undefined} onClick={(e) => a.toggle(e.currentTarget)}>
        <span className="ellipsis">{task.parent ? current?.summary ?? tr('tasks.paneFields.aTask') : hasKids ? tr('tasks.paneFields.topLevelItHasSubtasks') : tr('tasks.paneFields.noneTopLevel')}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      <Popover anchor={a.anchor} open={a.open} onClose={() => { a.close(); setQ('') }} label={tr('tasks.paneFields.subtaskOf')} className="tk-parent-pop">
        <input className="input tk-parent-filter" value={q} placeholder={tr('tasks.paneFields.findATask')} aria-label={tr('tasks.paneFields.findAParentTask')} autoFocus onChange={(e) => setQ(e.target.value)} />
        <MenuItem checked={!task.parent} onSelect={() => { a.close(); setQ(''); if (task.parent) onPick(null) }}>{tr('tasks.paneFields.noneTopLevel')}</MenuItem>
        <MenuSep />
        {parents.isLoading && <div className="muted tk-parent-empty">{tr('common.loading2')}</div>}
        {!parents.isLoading && !shown.length && <div className="muted tk-parent-empty">{tr('tasks.paneFields.noOtherOpenTaskIn')}</div>}
        {shown.map((x) => (
          <MenuItem key={x.uid} checked={task.parent === x.uid} onSelect={() => { a.close(); setQ(''); if (task.parent !== x.uid) onPick(x) }}>{x.summary || '(untitled)'}</MenuItem>
        ))}
      </Popover>
    </>
  )
}

// ---------------------------------------------------------------- tags

/** ACT task.tag @web.pane: a token field with autocomplete; Enter creates a tag. */
export function TagField({ value, all, onChange, disabled }: { value: string[]; all: TagInfo[]; onChange: (tags: string[], label: string) => void; disabled?: boolean }) {
  const [text, setText] = useState('')
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLInputElement>(null)
  const q = text.trim().replace(/^@/, '').toLowerCase()
  const sugg = useMemo(() => (q ? all.filter((t) => t.name.toLowerCase().includes(q) && !value.some((v) => v.toLowerCase() === t.name.toLowerCase())).slice(0, 6) : []), [q, all, value])
  const add = (name: string) => {
    const n = name.trim().replace(/^@/, '')
    if (!n || value.some((v) => v.toLowerCase() === n.toLowerCase())) return setText('')
    onChange([...value, n], tr('tasks.paneFields.tagged', { n }))
    setText('')
    setHi(0)
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation()
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      add(sugg[hi]?.name ?? text)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, sugg.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Backspace' && !text && value.length) {
      onChange(value.slice(0, -1), tr('tasks.paneFields.tagRemoved'))
    } else if (e.key === 'Escape') {
      ref.current?.blur()
    }
  }
  return (
    <div className="tk-chips-field tk-tags-field" onClick={() => ref.current?.focus()}>
      {value.map((t) => (
        <span key={t} className="tk-chip tk-chip-tag">@{t}
          {!disabled && <button type="button" aria-label={tr('tasks.paneFields.removeTag', { t })} onClick={(e) => {
            e.stopPropagation()
            onChange(value.filter((x) => x !== t), tr('tasks.paneFields.removed', { t }))
          }}><X size={12} /></button>}
        </span>
      ))}
      {!disabled && (
        <span className="tk-tag-input-wrap">
          <input ref={ref} className="tk-tag-input" value={text} aria-label={tr('tasks.paneFields.addATag')} placeholder={value.length ? '' : tr('tasks.paneFields.addTags')} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} />
          {sugg.length > 0 && (
            <div className="tk-suggest" role="listbox">
              {sugg.map((s, i) => (
                <button key={s.name} type="button" role="option" aria-selected={i === hi} className={i === hi ? 'on' : ''} onMouseDown={(e) => e.preventDefault()} onClick={() => add(s.name)}>
                  @{s.name} <span className="muted">{s.count}</span>
                </button>
              ))}
            </div>
          )}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- subtasks

interface SubProps {
  subtasks: TaskItem[]
  onAdd: (title: string) => void
  onToggle: (t: TaskItem) => void
  onOpen: (t: TaskItem) => void
  onReorder: (t: TaskItem, before: TaskItem | null) => void
  disabled?: boolean
}

/** The subtasks checklist: Enter adds (the grammar applies), drag reorders, the box completes. */
export function Subtasks({ subtasks, onAdd, onToggle, onOpen, onReorder, disabled }: SubProps) {
  const [text, setText] = useState('')
  const [drag, setDrag] = useState<TaskItem | null>(null)
  return (
    <div className="tk-subtasks" data-testid="pane-subtasks">
      {subtasks.map((s) => (
        <div key={s.uid} className={`tk-subtask${isDone(s) ? ' done' : ''}`} draggable={!disabled}
          onDragStart={() => setDrag(s)} onDragEnd={() => setDrag(null)}
          onDragOver={(e) => drag && drag.uid !== s.uid && e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            if (drag && drag.uid !== s.uid) onReorder(drag, s)
            setDrag(null)
          }}>
          <GripVertical size={12} className="tk-sub-grip" aria-hidden />
          <button type="button" className={`tk-check-ring sm${isDone(s) ? ' on' : ''}`} role="checkbox" aria-checked={isDone(s)} aria-label={`${isDone(s) ? tr('tasks.paneFields.reopen') : tr('common.complete')} ${s.summary}`} disabled={disabled} onClick={() => onToggle(s)}>
            {isDone(s) && <Check size={11} strokeWidth={3} aria-hidden />}
          </button>
          <button type="button" className="tk-subtask-title" onClick={() => onOpen(s)}>{s.summary}</button>
        </div>
      ))}
      {!disabled && (
        <div className="tk-subtask add">
          <Plus size={14} aria-hidden />
          <input value={text} placeholder={tr('tasks.paneFields.addASubtask')} aria-label={tr('tasks.paneFields.addASubtask')} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter' && text.trim()) {
              e.preventDefault()
              onAdd(text.trim())
              setText('')
            }
          }} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- notes

/** Notes: a text area, with a light markdown preview (bold, lists, links; checkboxes read-only). */
/** A link in a task's notes. A bare address into this app ("https://…/notes/n/…#i-…") shows as
 *  "Open in Notes" and opens here; other links open in a new tab. */
function NotesLink({ href, children }: { href?: string; children?: ReactNode }) {
  const path = appPath(href)
  if (path == null) return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
  const bare = typeof children === 'string' ? children === href : Array.isArray(children) && children.length === 1 && children[0] === href
  return (
    <a href={path} onClick={(e) => {
      e.preventDefault()
      appNavigate(path)
    }}>{bare ? appLinkLabel(href) ?? children : children}</a>
  )
}

export function Notes({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const [edit, setEdit] = useState(!value)
  if (!edit && value)
    return (
      <div className="tk-notes-preview" role="button" tabIndex={0} aria-label={tr('tasks.paneFields.editNotes')} onClick={(e) => {
        if ((e.target as HTMLElement).closest('a')) return
        if (!disabled) setEdit(true)
      }} onKeyDown={(e) => e.key === 'Enter' && !disabled && setEdit(true)}>
        <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeDirAuto]} components={{
          a: ({ href, children }) => <NotesLink href={href}>{children}</NotesLink>,
          input: (p) => <input {...p} disabled readOnly />,
          // Notes can be written by an agent from email text: images become links, never loads.
          img: BlockedImage,
        }}>{value}</Markdown>
      </div>
    )
  return (
    <textarea className="tk-notes" dir="auto" value={value} placeholder={tr('common.notes')} aria-label={tr('common.notes')} disabled={disabled} rows={4}
      autoFocus={!!value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') e.currentTarget.blur()
      }}
      onBlur={(e) => {
        onChange(e.target.value)
        if (e.target.value) setEdit(false)
      }} />
  )
}

export { split }
