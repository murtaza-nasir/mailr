// A task on the calendar grid ("Show tasks on calendar"): a checkbox chip at its due time, or in
// the all-day strip when it has a date only. The box completes it; the title opens it in Tasks.
// Dragging the chip moves the due (ACT task.reschedule @web.calendar): onto a time in a day column it gets that
// time, onto the all-day strip the date only, onto a Month day the date with its time kept. A
// repeating task asks the series question first, as in the Tasks app.

import { useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { toast, toastError } from '../../stores/ui'
import { dueScope, undoBody } from '../tasks/actions'
import type { TaskItem } from '../tasks/model'
import { join, split } from '../tasks/when'
import { useCal } from './context'
import { calApi } from './data'
import { gridInstant } from './geometry'
import type { ApiTask } from './types'
import { TableDateChip } from './tableDates'
import { t as tr, appLocale } from '../../i18n'
import { clockOpts, formatInstant } from '../../lib/dates'
import { ItemBoundary } from '../../components/bits'

/**
 * Where a chip dropped with the pointer at (x, y) lands: the new due, or null off the calendar.
 * `top` is where the chip's top edge is (its due time in a day column).
 */
export function dropDue(x: number, y: number, top: number, t: ApiTask, zone: string, snap: number): string | null {
  const hit = document.elementsFromPoint(x, y)
  const month = hit.map((el) => el.closest<HTMLElement>('[data-mday]')).find(Boolean)
  if (month) return join(month.dataset.mday!, split(t.due, zone).hm, zone)
  // The sticky strip lies over the scrolled columns, so it is checked first.
  if (hit.some((el) => el.closest('.cal-strip'))) {
    // The strip has no cells of its own: the day column under the same x.
    const cols = [...document.querySelectorAll<HTMLElement>('.cal-col[data-day]')]
    const c = cols.find((el) => {
      const r = el.getBoundingClientRect()
      return x >= r.left && x < r.right
    })
    return c ? c.dataset.day! : null
  }
  const col = hit.map((el) => el.closest<HTMLElement>('.cal-col[data-day]')).find(Boolean)
  const tg = col?.closest<HTMLElement>('.cal-tg')
  if (col && tg) {
    const hourH = parseFloat(getComputedStyle(tg).getPropertyValue('--hour-h')) || 48
    const r = col.getBoundingClientRect()
    const min = Math.max(0, Math.min(1440 - snap, Math.round((((top - r.top) / hourH) * 60) / snap) * snap))
    return gridInstant(col.dataset.day!, min, zone).toISOString()
  }
  return null
}

export function TaskChip({ t, style, timed }: { t: ApiTask; style?: CSSProperties; timed?: boolean }) {
  // A table's dated row rides with the date-only tasks.
  // A failure shows "Couldn't show this task" in its place (components/bits ItemBoundary).
  if (t.table_row) return <ItemBoundary what="task" compact><TableDateChip t={t} style={style} /></ItemBoundary>
  return <ItemBoundary what="task" compact><TaskChipInner t={t} style={style} timed={timed} /></ItemBoundary>
}

function TaskChipInner({ t, style, timed }: { t: ApiTask; style?: CSSProperties; timed?: boolean }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const cal = useCal()
  const [done, setDone] = useState(false)
  const [shift, setShift] = useState<{ x: number; y: number } | null>(null)
  const press = useRef<{ x: number; y: number; dy: number; moved: boolean } | null>(null)
  const ro = cal.readOnly

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['tasks'] })
  }

  // ACT task.reschedule: the drop's due, with the series question for a repeating task and an undo.
  const moveTo = async (due: string) => {
    if (due === t.due) return
    const before = t as unknown as TaskItem
    const extra = await dueScope(before, due, cal.prefs.zone)
    if (!extra) return
    const changes = { due, tz: cal.prefs.zone, ...extra }
    try {
      const out = await api.patch<ApiTask>(`/tasks/${encodeURIComponent(t.uid)}`, { ...changes, ...(t.etag ? { etag: t.etag } : {}) })
      refresh()
      const when = due.length === 10 ? due : formatInstant(new Date(due), appLocale(), { weekday: 'short', hour: 'numeric', minute: '2-digit', ...clockOpts() }, cal.prefs.zone)
      toast({
        message: tr('calendar.taskChip.due', { when }), actionLabel: tr('common.undo'), duration: 7000,
        onAction: () => void api.patch(`/tasks/${encodeURIComponent(t.uid)}`, { ...undoBody(before, changes), ...(out.etag ? { etag: out.etag } : {}) })
          .then(refresh, (e) => toastError(e, tr('common.couldNotUndo'))),
      })
    } catch (e) {
      refresh()
      toastError(e, tr('calendar.taskChip.couldNotMoveTheTask'))
    }
  }

  const onPointerDown = (ev: RPointerEvent<HTMLButtonElement>) => {
    if (ev.button !== 0 || ro) return
    ev.stopPropagation()
    const chip = ev.currentTarget.closest('.cal-task')?.getBoundingClientRect()
    press.current = { x: ev.clientX, y: ev.clientY, dy: chip ? ev.clientY - chip.top : 0, moved: false }
    const onMove = (m: PointerEvent) => {
      const p = press.current
      if (!p) return
      if (!p.moved && Math.abs(m.clientX - p.x) + Math.abs(m.clientY - p.y) < 5) return
      p.moved = true
      setShift({ x: m.clientX - p.x, y: m.clientY - p.y })
    }
    const onUp = (u: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      const p = press.current
      press.current = null
      setShift(null)
      if (!p?.moved) return
      // The click that follows a drag must not open the task.
      const swallow = (c: MouseEvent) => {
        c.stopPropagation()
        c.preventDefault()
      }
      window.addEventListener('click', swallow, { capture: true, once: true })
      setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0)
      const due = dropDue(u.clientX, u.clientY, u.clientY - p.dy, t, cal.prefs.zone, cal.prefs.snap)
      if (due) void moveTo(due)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const dragStyle: CSSProperties = shift ? { transform: `translate(${shift.x}px, ${shift.y}px)`, pointerEvents: 'none', zIndex: 40, opacity: 0.85 } : {}
  return (
    <div className={`cal-task${timed ? ' timed' : ''}${done ? ' done' : ''}${shift ? ' dragging' : ''}`} style={{ ...style, ...dragStyle }} data-testid="cal-task">
      <button
        type="button"
        className="cal-task-box"
        role="checkbox"
        aria-checked={done}
        aria-label={tr('calendar.taskChip.complete', { summary: t.summary })}
        onClick={() => {
          // ACT task.complete @web.calendar
          setDone(true)
          calApi.completeTask(t.uid, t.etag).then(() => {
            void qc.invalidateQueries({ queryKey: ['tasks'] })
            toast({ message: tr('common.taskCompleted') })
          }, (e) => {
            setDone(false)
            toastError(e, tr('calendar.taskChip.couldNotCompleteTheTask'))
          })
        }}
      >
        {done && <Check aria-hidden />}
      </button>
      <button type="button" className="cal-task-title" dir="auto" onPointerDown={onPointerDown} onClick={() => nav(`/tasks/task/${encodeURIComponent(t.uid)}`)}>
        {t.summary || tr('common.noTitle')}
      </button>
    </div>
  )
}
