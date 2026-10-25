// The small menus a row, a key or the selection bar opens: reschedule, priority,
// move to list, tags (tri-state for a selection), and the row's ⋮ menu with snooze vs
// reschedule vs remind vs defer, each saying what it changes.

import { Bell, CalendarClock, CalendarPlus, Copy, Flag, FolderInput, Mail, Tag, Trash2, CornerDownRight, CornerUpLeft, Plus } from 'lucide-react'
import { useState } from 'react'
import { MenuHeader, MenuItem, MenuSep } from '../../components/Popover'
import type { DatePrefs } from '../../lib/dates'
import { autoColor } from './colors'
import { PRIORITY_NAMES, priorityLevel, type PriorityLevel, type TagInfo, type TaskItem, type TaskList } from './model'
import { WhenPicker } from './WhenPicker'
import { t as tr, tx } from '../../i18n'

export function PriorityMenu({ tasks, onPick }: { tasks: TaskItem[]; onPick: (l: PriorityLevel) => void }) {
  const levels = new Set(tasks.map((t) => priorityLevel(t.priority)))
  return (
    <>
      {([3, 2, 1, 0] as PriorityLevel[]).map((l) => (
        <MenuItem key={l} icon={<Flag size={16} className={`pri-${['none', 'low', 'med', 'high'][l]}`} />} shortcut={String(l)}
          checked={levels.size === 1 && levels.has(l) ? true : levels.has(l) ? 'mixed' : false}
          onSelect={() => onPick(l)}>{PRIORITY_NAMES[l]}</MenuItem>
      ))}
    </>
  )
}

export function MoveMenu({ lists, current, onPick }: { lists: TaskList[]; current?: string; onPick: (l: TaskList) => void }) {
  return (
    <>
      <MenuHeader>{tr('common.moveToList')}</MenuHeader>
      {lists.filter((l) => !l.read_only).map((l) => (
        <MenuItem key={l.id} icon={<i className="tk-dot" style={{ background: l.color || autoColor(l.name) }} />} checked={current ? l.id === current : undefined} onSelect={() => onPick(l)}>{l.name}</MenuItem>
      ))}
    </>
  )
}

/** Tags for one task or a selection: each tag is on, off or mixed (tri-state); a typed name adds one. */
export function TagsMenu({ tasks, tags, onAdd, onRemove }: { tasks: TaskItem[]; tags: TagInfo[]; onAdd: (t: string) => void; onRemove: (t: string) => void }) {
  const [text, setText] = useState('')
  const state = (name: string): boolean | 'mixed' => {
    const n = tasks.filter((t) => (t.categories ?? []).some((c) => c.toLowerCase() === name.toLowerCase())).length
    return n === 0 ? false : n === tasks.length ? true : 'mixed'
  }
  const names = [...new Set([...tags.map((t) => t.name), ...tasks.flatMap((t) => t.categories ?? [])])]
  return (
    <>
      <div className="tk-menu-input">
        <Tag size={14} aria-hidden />
        <input value={text} placeholder={tr('tasks.menus.addATag')} aria-label={tr('tasks.menus.addATag')} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter' && text.trim()) {
            onAdd(text.trim().replace(/^@/, ''))
            setText('')
          }
        }} />
      </div>
      {names.filter((n) => !text || n.toLowerCase().includes(text.toLowerCase())).map((n) => {
        const s = state(n)
        return <MenuItem key={n} checked={s} onSelect={() => (s === true ? onRemove(n) : onAdd(n))}>@{n}</MenuItem>
      })}
      {text.trim() && !names.some((n) => n.toLowerCase() === text.trim().toLowerCase()) && (
        <MenuItem icon={<Plus size={16} />} onSelect={() => {
          onAdd(text.trim().replace(/^@/, ''))
          setText('')
        }}>{tr('tasks.menus.create', { replace: text.trim().replace(/^@/, '') })}</MenuItem>
      )}
    </>
  )
}

export type RowVerb = 'reschedule' | 'start' | 'remind' | 'priority' | 'move' | 'tags' | 'duplicate' | 'delete' | 'email' | 'indent' | 'outdent' | 'skip'

/** The row's ⋮ menu. Each verb says what it changes. */
export function RowMenu({ task, canIndent, onVerb }: { task: TaskItem; canIndent: boolean; onVerb: (v: RowVerb) => void }) {
  return (
    <>
      {/* ACT task.reschedule @web.tasks */}
      <MenuItem icon={<CalendarClock size={16} />} shortcut="r" onSelect={() => onVerb('reschedule')}>{tx('tasks.menus.rescheduleMovesTheDue', undefined, { span: (c) => <span className="muted">{c}</span> })}</MenuItem>
      {/* ACT task.set_start @web.tasks */}
      <MenuItem icon={<CalendarPlus size={16} />} shortcut="s" onSelect={() => onVerb('start')}>{tx('tasks.menus.deferHidesItUntilA', undefined, { span: (c) => <span className="muted">{c}</span> })}</MenuItem>
      {/* ACT task.remind_at @web.pane */}
      <MenuItem icon={<Bell size={16} />} disabled={!task.due} onSelect={() => onVerb('remind')}>{tx('tasks.menus.remindMeAddsAReminder', undefined, { span: (c) => <span className="muted">{c}</span> })}</MenuItem>
      {/* ACT task.skip @web.tasks */}
      {task.recurring && <MenuItem onSelect={() => onVerb('skip')}>{tr('tasks.menus.skipThisOccurrence')}</MenuItem>}
      <MenuSep />
      <MenuItem icon={<Flag size={16} />} shortcut="0–3" onSelect={() => onVerb('priority')}>{tr('tasks.menus.priority')}</MenuItem>
      <MenuItem icon={<FolderInput size={16} />} shortcut="v" onSelect={() => onVerb('move')}>{tr('tasks.menus.moveToList')}</MenuItem>
      <MenuItem icon={<Tag size={16} />} shortcut="l" onSelect={() => onVerb('tags')}>{tr('tasks.menus.tags')}</MenuItem>
      {task.parent ? (
        <MenuItem icon={<CornerUpLeft size={16} />} shortcut="Shift+Tab" onSelect={() => onVerb('outdent')}>{tr('tasks.menus.makeItATaskAgain')}</MenuItem>
      ) : canIndent ? (
        <MenuItem icon={<CornerDownRight size={16} />} shortcut="Tab" onSelect={() => onVerb('indent')}>{tr('tasks.menus.makeASubtask')}</MenuItem>
      ) : null}
      {task.conversation_id != null && task.conversation_id !== '' && <MenuItem icon={<Mail size={16} />} onSelect={() => onVerb('email')}>{tr('common.openTheEmail')}</MenuItem>}
      <MenuSep />
      <MenuItem icon={<Copy size={16} />} shortcut="Ctrl+D" onSelect={() => onVerb('duplicate')}>{tr('common.duplicate')}</MenuItem>
      <MenuItem icon={<Trash2 size={16} />} shortcut="#" danger onSelect={() => onVerb('delete')}>{tr('common.delete2')}</MenuItem>
    </>
  )
}

export function ReschedulePanel(p: { value: string | null; now: Date; prefs: DatePrefs; label?: string; start?: boolean; onPick: (v: string | null) => void; extra?: React.ReactNode }) {
  return <WhenPicker value={p.value} now={p.now} prefs={p.prefs} label={p.label ?? (p.start ? tr('tasks.menus.deferUntil') : tr('common.reschedule'))} laterToday={!p.start} onPick={p.onPick} extra={p.extra} />
}
