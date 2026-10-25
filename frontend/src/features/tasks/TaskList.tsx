// The grouped, virtualised task list (the List layout). Sections with headers, rows
// with their subtasks, "+ Add to Sat" rows under Upcoming's days, and the drop targets of
// drag and drop: between rows (task.reorder), to the right under a row (task.reparent), and on a
// day header (task.reschedule).

import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDownUp, Plus } from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type DragEvent, type MouseEvent, type ReactNode } from 'react'
import type { DatePrefs } from '../../lib/dates'
import type { TitleChange } from './InlineTitle'
import type { RowEntry, Section, SortKey, TaskItem, TaskList as TList } from './model'
import { TaskRow, type RowHandlers } from './TaskRow'
import { t as tr } from '../../i18n'
import { xFromStart } from '../../lib/rtl'

export type Item =
  | { kind: 'header'; key: string; section: Section; count: number }
  | { kind: 'row'; key: string; entry: RowEntry; section: Section }
  | { kind: 'add'; key: string; section: Section }
  | { kind: 'more'; key: string }

export interface ListHandle {
  scrollTo: (uid: string) => void
  focusFirst: () => void
}

export interface DropTarget {
  uid: string
  pos: 'before' | 'after' | 'inside'
}

interface Props {
  items: Item[]
  lists: TList[]
  listOf: (t: TaskItem) => TList | undefined
  showList: boolean
  focused: string | null
  selected: Set<string>
  editing: string | null
  expanded: (uid: string) => boolean
  lingering: Set<string>
  readOnly: (t: TaskItem) => boolean
  manual: boolean
  table: boolean
  sort: SortKey
  onSort?: (s: SortKey) => void
  parentTitle: (uid: string) => string | undefined
  now: Date
  prefs: DatePrefs
  h: Omit<RowHandlers, 'onDragStart' | 'onDragOver' | 'onDrop' | 'onDragEnd'>
  onAddTo: (s: Section) => void
  onMore: () => void
  /** A drop between or under rows (manual order only). */
  onDropRow: (uids: string[], target: DropTarget) => void
  onDropDay: (uids: string[], day: string) => void
  /** The uids a drag carries (the selection when the row is selected). */
  dragUids: (t: TaskItem) => string[]
  empty?: ReactNode
  ariaLabel: string
}

// The drag payload, kept here too: some browsers hide dataTransfer contents until the drop.
export const DRAG_TYPE = 'application/x-mailr-tasks'
let dragging: string[] = []
export const currentDrag = () => dragging

export const TaskListView = forwardRef<ListHandle, Props>(function TaskListView(p, ref) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [hint, setHint] = useState<DropTarget | null>(null)
  const [dayHint, setDayHint] = useState<string | null>(null)
  const index = useMemo(() => new Map(p.items.map((it, i) => [it.kind === 'row' ? it.entry.task.uid : it.key, i])), [p.items])

  const virtual = useVirtualizer({
    count: p.items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (p.items[i].kind === 'header' ? 40 : p.items[i].kind === 'row' ? (p.table ? 36 : 52) : 36),
    overscan: 12,
    getItemKey: (i) => p.items[i].key,
  })

  useImperativeHandle(ref, () => ({
    scrollTo: (uid) => {
      const i = index.get(uid)
      if (i != null) virtual.scrollToIndex(i, { align: 'auto' })
    },
    focusFirst: () => scrollRef.current?.focus(),
  }))

  useEffect(() => {
    if (!p.focused) return
    const i = index.get(p.focused)
    if (i != null) virtual.scrollToIndex(i, { align: 'auto' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.focused])

  const rowHandlers: RowHandlers = {
    ...p.h,
    onDragStart: (t, e) => {
      dragging = p.dragUids(t)
      e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragging))
      e.dataTransfer.setData('text/plain', t.summary)
      e.dataTransfer.effectAllowed = 'move'
    },
    onDragOver: (t, e) => {
      if (!dragging.length || dragging.includes(t.uid)) return
      e.preventDefault()
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      const lower = e.clientY > r.top + r.height / 2
      // Dragged toward the end (the right, or the left right to left) under a row: it becomes a subtask (task.reparent).
      const inside = p.manual && lower && xFromStart(e.clientX, r) > 96
      const pos: DropTarget['pos'] = inside ? 'inside' : lower ? 'after' : 'before'
      if (!p.manual) {
        e.dataTransfer.dropEffect = 'none'
        setHint(null)
        return
      }
      e.dataTransfer.dropEffect = 'move'
      if (hint?.uid !== t.uid || hint.pos !== pos) setHint({ uid: t.uid, pos })
    },
    onDrop: (t, e) => {
      e.preventDefault()
      const uids = dragging
      const target = hint
      setHint(null)
      dragging = []
      if (!uids.length || !target || target.uid !== t.uid) return
      p.onDropRow(uids, target)
    },
    onDragEnd: () => {
      dragging = []
      setHint(null)
      setDayHint(null)
    },
  }

  const headerDrop = (s: Section) => ({
    onDragOver: (e: DragEvent) => {
      if (!s.dropDay || !dragging.length) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setDayHint(s.id)
    },
    onDragLeave: () => setDayHint((d) => (d === s.id ? null : d)),
    onDrop: (e: DragEvent) => {
      if (!s.dropDay) return
      e.preventDefault()
      const uids = dragging
      dragging = []
      setDayHint(null)
      if (uids.length) p.onDropDay(uids, s.dropDay)
    },
  })

  const cols: { key: SortKey; label: string }[] = [
    { key: 'title', label: tr('common.title') }, { key: 'due', label: tr('common.due') }, { key: 'priority', label: tr('common.priority') }, { key: 'manual', label: tr('common.list') }, { key: 'created', label: tr('common.tags') },
  ]

  return (
    <div className={`tk-list${p.table ? ' tk-table' : ''}`} role="grid" aria-label={p.ariaLabel} aria-rowcount={p.items.length}>
      {p.table && (
        <div className="tk-table-head" role="row">
          <span role="columnheader" />
          {/* A button cannot be a column header: the header holds the sort button. */}
          {cols.map((c, i) => (
            <span key={c.label} role="columnheader" className="tk-th-cell" aria-sort={p.sort === c.key && i < 3 ? 'ascending' : 'none'}>
              <button type="button" className={`tk-th${p.sort === c.key && i < 3 ? ' on' : ''}`}
                disabled={i >= 3}
                onClick={() => p.onSort?.(c.key)}>
                {c.label}{p.sort === c.key && i < 3 && <ArrowDownUp size={12} aria-hidden />}
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="tk-scroll" ref={scrollRef} tabIndex={-1} data-testid="tasks-list" role="rowgroup">
        {p.items.length === 0 && <div role="row"><div role="gridcell">{p.empty}</div></div>}
        <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
          {virtual.getVirtualItems().map((v) => {
            const it = p.items[v.index]
            return (
              <div key={v.key} data-index={v.index} ref={virtual.measureElement} className="tk-vrow" style={{ transform: `translateY(${v.start}px)` }}>
                {it.kind === 'header' && (
                  <div className={`tk-section${it.section.tone === 'danger' ? ' danger' : ''}${dayHint === it.section.id ? ' drop' : ''}${it.section.dropDay ? ' droppable' : ''}`}
                    role="row" data-section={it.section.id} {...headerDrop(it.section)}>
                    <span className="tk-section-title" role="rowheader">{it.section.title}</span>
                    <span className="tk-section-count">{it.count || ''}</span>
                  </div>
                )}
                {it.kind === 'row' && (
                  <TaskRow
                    entry={it.entry}
                    list={p.listOf(it.entry.task)}
                    showList={p.showList}
                    focused={p.focused === it.entry.task.uid}
                    selected={p.selected.has(it.entry.task.uid)}
                    selectMode={p.selected.size > 0}
                    editing={p.editing === it.entry.task.uid}
                    expanded={p.expanded(it.entry.task.uid)}
                    lingering={p.lingering.has(it.entry.task.uid)}
                    readOnly={p.readOnly(it.entry.task)}
                    draggable={p.manual && !p.readOnly(it.entry.task)}
                    dropHint={hint?.uid === it.entry.task.uid ? hint.pos : null}
                    parentTitle={it.entry.task.parent ? p.parentTitle(it.entry.task.parent) : undefined}
                    table={p.table}
                    now={p.now}
                    prefs={p.prefs}
                    lists={p.lists}
                    h={rowHandlers}
                  />
                )}
                {it.kind === 'add' && (
                  <button type="button" className="tk-add-to" onClick={() => p.onAddTo(it.section)}>
                    <Plus size={14} aria-hidden />{' '}{tr('tasks.taskList.addTo', { value: it.section.title === tr('common.tomorrow') ? tr('common.tomorrow') : it.section.title.split(' ')[0] })}
                  </button>
                )}
                {it.kind === 'more' && (
                  <button type="button" className="tk-add-to" onClick={p.onMore}>{tr('common.showMore')}</button>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
})

/** Rows of every section, flattened for the virtual list. */
export function flatten(sections: (Section & { rows: RowEntry[] })[], opts: { addRows: boolean; more: boolean }): Item[] {
  const out: Item[] = []
  for (const s of sections) {
    if (s.title) out.push({ kind: 'header', key: `h:${s.id}`, section: s, count: s.tasks.length })
    for (const r of s.rows) out.push({ kind: 'row', key: `r:${s.id}:${r.task.uid}`, entry: r, section: s })
    if (opts.addRows && s.keepEmpty) out.push({ kind: 'add', key: `a:${s.id}`, section: s })
  }
  if (opts.more) out.push({ kind: 'more', key: 'more' })
  return out
}

export type { TitleChange, MouseEvent }
