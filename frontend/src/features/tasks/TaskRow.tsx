// One task row:
//   [⋮⋮] (○) Title text ......................... [due chip] [⟳] [🔔] [✉] [2/5]
//             ● List · @tag @tag · notes first line
// The checkbox ring is coloured by priority, with ⟳ inside for a repeating task. Desk draws the
// same row as table cells (title, due, priority, list, tags).
// The hover actions (reschedule, priority, more) sit over the end of the first line, outside the
// layout: showing them never changes the row's height or moves the title, chip or second line
// (tasks.css, "the row's hover actions").

import { Bell, Check, ChevronDown, ChevronRight, CornerDownRight, Flag, GripVertical, Lock, Mail, MoreHorizontal, NotebookText, Repeat, CalendarClock } from 'lucide-react'
import { memo, type CSSProperties, type DragEvent, type MouseEvent } from 'react'
import type { DatePrefs } from '../../lib/dates'
import { InlineTitle, type TitleChange } from './InlineTitle'
import { dueChip, isDone, PRIORITY_NAMES, priorityLevel, type RowEntry, type TaskItem, type TaskList } from './model'
import { t as tr } from '../../i18n'

export interface RowHandlers {
  onSelect: (t: TaskItem, e: MouseEvent) => void
  onOpen: (t: TaskItem) => void
  onToggle: (t: TaskItem) => void
  onEdit: (t: TaskItem) => void
  onTitle: (t: TaskItem, c: TitleChange) => void
  onCancelEdit: () => void
  onDue: (t: TaskItem, anchor: HTMLElement) => void
  onPriority: (t: TaskItem, anchor: HTMLElement) => void
  onMenu: (t: TaskItem, anchor: HTMLElement) => void
  /** Absent where subtasks do not open in place (the side panel): the count is shown, not a toggle. */
  onExpand?: (t: TaskItem) => void
  onTag: (tag: string) => void
  onEmail: (t: TaskItem) => void
  onDragStart: (t: TaskItem, e: DragEvent) => void
  onDragOver: (t: TaskItem, e: DragEvent) => void
  onDrop: (t: TaskItem, e: DragEvent) => void
  onDragEnd: () => void
}

interface Props {
  entry: RowEntry
  list?: TaskList
  showList: boolean
  focused: boolean
  selected: boolean
  selectMode: boolean
  editing: boolean
  expanded: boolean
  lingering: boolean
  readOnly: boolean
  draggable: boolean
  dropHint: 'before' | 'after' | 'inside' | null
  parentTitle?: string
  table: boolean
  now: Date
  prefs: DatePrefs
  lists: TaskList[]
  h: RowHandlers
}

const PRI_CLASS = ['', 'low', 'med', 'high']

export const TaskRow = memo(function TaskRow(p: Props) {
  const { entry, list, focused, selected, selectMode, editing, expanded, lingering, readOnly, now, prefs, h } = p
  const t = entry.task
  const done = isDone(t) || lingering
  const lvl = priorityLevel(t.priority)
  const chip = dueChip(t, now, prefs)
  const subs = t.subtask_count ?? 0
  const doneSubs = subs - (t.open_subtask_count ?? 0)
  // The first line of the notes, without the light markdown marks.
  const notesLine = (t.notes ?? '').split('\n').find((l) => l.trim())?.trim().replace(/^([-*+]|\d+\.)\s+(\[[ xX]\]\s+)?/, '').replace(/[*_`~]+/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
  const tags = t.categories ?? []
  const hasEmail = t.conversation_id != null && t.conversation_id !== ''
  const hoverActs = !readOnly && !selectMode && !p.table
  // How many buttons the hover actions hold: in a narrow row the end of the line fades under that width.
  const actCount = 3 + (subs > 0 && h.onExpand ? 1 : 0) + (hasEmail ? 1 : 0)
  const cls = [
    'tk-row', focused && 'focused', selected && 'selected', done && 'done', lingering && 'lingering', entry.depth && 'sub',
    p.dropHint && `drop-${p.dropHint}`, p.table && 'tk-row-table', readOnly && 'readonly',
  ].filter(Boolean).join(' ')

  const check = (
    <button
      type="button"
      className={`tk-check-ring pri-${PRI_CLASS[lvl] || 'none'}${done ? ' on' : ''}`}
      role="checkbox"
      aria-checked={done}
      aria-label={done ? tr('tasks.taskRow.reopen', { summary: t.summary }) : tr('tasks.taskRow.complete', { summary: t.summary })}
      disabled={readOnly}
      // ACT task.complete @web.tasks, ACT task.reopen @web.tasks
      onClick={(e) => {
        e.stopPropagation()
        h.onToggle(t)
      }}
    >
      {done ? <Check size={13} strokeWidth={3} aria-hidden /> : t.recurring ? <Repeat size={10} strokeWidth={2.5} aria-hidden /> : null}
    </button>
  )

  const title = editing ? (
    <InlineTitle initial={t.summary} now={now} prefs={prefs} lists={p.lists} onSave={(c) => h.onTitle(t, c)} onCancel={h.onCancelEdit} label={tr('tasks.taskRow.editTitle')} />
  ) : (
    <span className="tk-title" dir="auto" onDoubleClick={(e) => {
      e.stopPropagation()
      if (!readOnly) h.onEdit(t)
    }}>{t.summary || <em className="muted">{tr('common.untitled')}</em>}</span>
  )

  const dueBtn = chip ? (
    <button type="button" className={`tk-due tone-${chip.tone}`} title={chip.title} disabled={readOnly}
      // ACT task.reschedule @web.tasks
      onClick={(e) => {
        e.stopPropagation()
        h.onDue(t, e.currentTarget)
      }}>
      {chip.text}
    </button>
  ) : null

  const icons = (
    <span className="tk-icons">
      {t.recurring && <Repeat size={16} aria-label={tr('tasks.taskRow.repeats')} />}
      {(t.reminders?.length ?? 0) > 0 && <Bell size={16} aria-label={tr('tasks.taskRow.hasAReminder')} />}
      {hasEmail && (
        <button type="button" className="tk-icon-btn" aria-label={tr('common.openTheEmail')} title={tr('tasks.taskRow.fromEmail')}
          // ACT task.open_email @web.tasks
          onClick={(e) => {
            e.stopPropagation()
            h.onEmail(t)
          }}>
          <Mail size={16} />
        </button>
      )}
      {notesLine && <NotebookText size={16} aria-label={tr('tasks.taskRow.hasNotes')} />}
      {subs > 0 && (h.onExpand ? (
        <button type="button" className="tk-subs" aria-label={tr('tasks.taskRow.ofSubtasksDone', { doneSubs, count: subs })} aria-expanded={expanded}
          onClick={(e) => {
            e.stopPropagation()
            h.onExpand?.(t)
          }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {doneSubs}/{subs}
        </button>
      ) : (
        <span className="tk-subs" title={tr('tasks.taskRow.ofSubtasksDone', { doneSubs, count: subs })}>{doneSubs}/{subs}</span>
      ))}
    </span>
  )

  const meta = (
    <>
      {entry.orphan && p.parentTitle && <span className="tk-meta-parent"><CornerDownRight size={12} aria-hidden /> <bdi>{p.parentTitle}</bdi></span>}
      {entry.nested && <span className="tk-meta-nested" title={tr('tasks.taskRow.aDeeperSubtaskFromAnother')}>{tr('tasks.taskRow.nested')}</span>}
      {p.showList && list && <span className="tk-meta-list"><i className="tk-dot" style={{ background: list.color || 'var(--text-3)' }} />{list.name}</span>}
      {tags.map((tag) => (
        <button key={tag} type="button" className="tk-meta-tag" onClick={(e) => {
          e.stopPropagation()
          h.onTag(tag)
        }}>@{tag}</button>
      ))}
      {notesLine && !p.table && <span className="tk-meta-notes" dir="auto">{notesLine}</span>}
    </>
  )
  const hasMeta = (entry.orphan && p.parentTitle) || entry.nested || (p.showList && list) || tags.length > 0 || !!notesLine

  return (
    <div
      className={cls}
      role="row"
      aria-selected={selected}
      data-uid={t.uid}
      data-testid="task-row"
      style={hoverActs ? ({ '--tk-acts-n': actCount } as CSSProperties) : undefined}
      onClick={(e) => h.onSelect(t, e)}
      onDragOver={(e) => h.onDragOver(t, e)}
      onDrop={(e) => h.onDrop(t, e)}
    >
      <span className="tk-handle-cell" role="gridcell">
        {selectMode ? (
          <input type="checkbox" className="checkbox" checked={selected} aria-label={tr('tasks.taskRow.select', { summary: t.summary })} onClick={(e) => e.stopPropagation()} onChange={(e) => h.onSelect(t, { ...e, ctrlKey: true } as unknown as MouseEvent)} />
        ) : p.draggable ? (
          <span className="tk-handle" draggable aria-label={tr('tasks.taskRow.dragToMove')} title={tr('tasks.taskRow.dragToReorderOrOnto')}
            onDragStart={(e) => h.onDragStart(t, e)} onDragEnd={h.onDragEnd} onClick={(e) => e.stopPropagation()}>
            <GripVertical size={14} />
          </span>
        ) : readOnly ? <Lock size={12} className="muted" aria-label="Read-only" /> : null}
      </span>
      {p.table ? (
        <>
          <span className="tk-cell tk-cell-title" role="gridcell">{check}{title}{icons}</span>
          <span className="tk-cell tk-cell-due" role="gridcell">{dueBtn}</span>
          <span className="tk-cell tk-cell-pri" role="gridcell">{lvl ? <span className={`tk-pri pri-${PRI_CLASS[lvl]}`}><Flag size={12} aria-hidden />{PRIORITY_NAMES[lvl]}</span> : null}</span>
          <span className="tk-cell tk-cell-list" role="gridcell">{list && <><i className="tk-dot" style={{ background: list.color || 'var(--text-3)' }} />{list.name}</>}</span>
          <span className="tk-cell tk-cell-tags" role="gridcell">{tags.map((tag) => <button key={tag} type="button" className="tk-meta-tag" onClick={(e) => {
            e.stopPropagation()
            h.onTag(tag)
          }}>@{tag}</button>)}</span>
        </>
      ) : (
        <>
          <span className="tk-check-cell" role="gridcell">{check}</span>
          <span className="tk-main" role="gridcell">
            <span className="tk-line">
              {title}
              <span className="tk-trail">
                {dueBtn}
                {icons}
              </span>
            </span>
            {hasMeta && <span className="tk-meta">{meta}</span>}
          </span>
          {hoverActs && (
            <span className="tk-hover" role="gridcell">
              {/* The line's own buttons that the actions cover in a narrow row (the subtask toggle,
                  the email) come along, so they stay one click away (tasks.css hides them where
                  the row is wide and covers nothing). */}
              {subs > 0 && h.onExpand && (
                <button type="button" className="icon-btn sm tk-hover-dup" aria-label={tr('tasks.taskRow.ofSubtasksDone', { doneSubs, count: subs })} aria-expanded={expanded}
                  title={`${doneSubs}/${subs}`} onClick={(e) => {
                    e.stopPropagation()
                    h.onExpand?.(t)
                  }}>{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
              )}
              {hasEmail && (
                <button type="button" className="icon-btn sm tk-hover-dup" aria-label={tr('common.openTheEmail')} title={tr('tasks.taskRow.fromEmail')} onClick={(e) => {
                  e.stopPropagation()
                  h.onEmail(t)
                }}><Mail size={16} /></button>
              )}
              <button type="button" className="icon-btn sm" aria-label={tr('common.reschedule')} title={tr('tasks.taskRow.rescheduleR')} onClick={(e) => {
                e.stopPropagation()
                h.onDue(t, e.currentTarget)
              }}><CalendarClock size={16} /></button>
              <button type="button" className="icon-btn sm" aria-label={tr('common.priority')} title={tr('tasks.taskRow.priority03')} onClick={(e) => {
                e.stopPropagation()
                h.onPriority(t, e.currentTarget)
              }}><Flag size={16} /></button>
              <button type="button" className="icon-btn sm" aria-label={tr('tasks.taskRow.moreActionsFor', { summary: t.summary })} onClick={(e) => {
                e.stopPropagation()
                h.onMenu(t, e.currentTarget)
              }}><MoreHorizontal size={16} /></button>
            </span>
          )}
        </>
      )}
    </div>
  )
})
