// Drag conversations onto the mail navigation. A row (or the selection it belongs
// to) is dragged from the list in any theme; nav targets highlight with the verb a drop runs.
// The drop runs the toolbar's own requests through useActions, so the toast, Undo, the row
// motion and the audit log are the ones the Move to and Label as commands give.
//
// Desktop pointer only: on touch the rows are not draggable (long-press selection stays).
// The drops (lib/mailDrop.ts): ACT mail.label @web.nav, ACT mail.move @web.nav, ACT mail.archive @web.nav, ACT mail.delete @web.nav,
// ACT mail.spam @web.nav, ACT mail.star @web.nav, ACT mail.important @web.nav.

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, type DragEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import { create } from 'zustand'
import { useActions } from '../../api/hooks'
import type { Label } from '../../api/types'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { dragLabel, dropPlan, type DropPlan } from '../../lib/mailDrop'
import { mailPath } from '../../lib/views'
import { useUI } from '../../stores/ui'
import { summariesFor } from './commands'
import { t as tr } from '../../i18n'
import { isRtl } from '../../lib/rtl'

export const MAIL_DRAG_TYPE = 'application/x-mailr-conversations'

export interface MailDrag {
  ids: number[]
  /** How many conversations the drag stands for ("Select all N" counts the whole list). */
  count: number
  from: string
  q: string
  convId: number | null
  contextLabelId: number | null
  labels: Label[]
  /** The drag carries the selection (cleared after the drop, as after a toolbar command). */
  selection: boolean
}

export interface DropOver {
  key: string
  verb: string
  rect: DOMRect
  place: 'right' | 'below'
}

interface DragStore {
  drag: MailDrag | null
  over: DropOver | null
  start: (d: MailDrag) => void
  end: () => void
  setOver: (o: DropOver | null) => void
}

// While a drag is on: the dragged rows dim, and toasts and tooltips let the drag through to
// the nav entries they cover (list.css, layout.css).
function markDrag(ids: number[] | null) {
  for (const el of document.querySelectorAll('[data-dragging]')) el.removeAttribute('data-dragging')
  if (!ids) {
    delete document.body.dataset.mailDrag
    return
  }
  document.body.dataset.mailDrag = ''
  for (const id of ids) for (const el of document.querySelectorAll(`[data-mail-list] [data-id="${id}"]`)) el.setAttribute('data-dragging', '')
}

export const useMailDrag = create<DragStore>((set) => ({
  drag: null,
  over: null,
  start: (drag) => {
    markDrag(drag.ids)
    set({ drag, over: null })
  },
  end: () => {
    markDrag(null)
    set({ drag: null, over: null })
  },
  setOver: (over) => set({ over }),
}))

/** Rows are draggable with a mouse or a pen, not with a finger. */
export function useCanDragRows(): boolean {
  return useMediaQuery('(hover: hover) and (pointer: fine)')
}

// Where a drag may not start: the row's own controls keep their clicks (a small jiggle while
// ticking a checkbox must not become a drag).
const NO_DRAG_FROM = 'button, input, label, select, textarea, [contenteditable="true"], [data-no-drag]'

/**
 * Props for the list's scroll container: a dragstart on a row picks the conversations (the
 * selection when the row is in it) and draws a compact drag image.
 */
export function useListDrag(view: string, q: string, openId: number | null, labels: Label[]) {
  const qc = useQueryClient()
  const canDrag = useCanDragRows()
  const downOn = useRef<EventTarget | null>(null)
  if (!canDrag) return { canDrag, props: {} }
  return {
    canDrag,
    props: {
      onPointerDownCapture: (e: PointerEvent) => {
        downOn.current = e.target
      },
      onDragStart: (e: DragEvent<HTMLElement>) => {
        const row = (e.target as HTMLElement).closest?.<HTMLElement>('[data-id]')
        if (!row || !e.currentTarget.contains(row)) return
        if ((downOn.current as HTMLElement | null)?.closest?.(NO_DRAG_FROM)) {
          e.preventDefault()
          return
        }
        const id = Number(row.dataset.id)
        const ui = useUI.getState()
        const inSel = ui.allMatching ? !ui.excluded.has(id) : ui.selected.has(id)
        const ids = inSel && ui.selected.size ? [...ui.selected] : [id]
        const count = inSel && ui.allMatching && ui.allMatchingQuery ? ui.allMatchingQuery.total - ui.excluded.size : ids.length
        const subjects = summariesFor(qc, ids.slice(0, 1)).map((c) => c.subject)
        e.dataTransfer.setData(MAIL_DRAG_TYPE, JSON.stringify(ids))
        e.dataTransfer.effectAllowed = 'move'
        setDragImage(e, dragLabel(subjects, count), count)
        const drag: MailDrag = {
          ids, count, from: view, q, convId: openId, contextLabelId: ui.contextLabelId, labels, selection: inSel && ui.selected.size > 0,
        }
        // After the browser has taken the drag: a re-render inside dragstart can cancel it.
        setTimeout(() => useMailDrag.getState().start(drag), 0)
      },
      onDragEnd: () => useMailDrag.getState().end(),
    },
  }
}

function setDragImage(e: DragEvent, text: string, count: number) {
  const el = document.createElement('div')
  el.className = 'mail-drag-image'
  if (count > 1) {
    const n = document.createElement('span')
    n.className = 'mail-drag-count'
    n.textContent = count > 999 ? '999+' : String(count)
    el.appendChild(n)
  }
  const t = document.createElement('span')
  t.className = 'mail-drag-text'
  t.textContent = text
  el.appendChild(t)
  // The browser snapshots the element now; it only has to be in the layout for this moment.
  document.body.appendChild(el)
  try {
    e.dataTransfer.setDragImage(el, 14, 14)
  } catch {
    /* some browsers refuse a custom image: the row is then the image */
  }
  setTimeout(() => el.remove(), 0)
}

/** Runs a drop plan on the dragged conversations: the same request and toast as the commands. */
function useRunDrop() {
  const { run } = useActions()
  const nav = useNavigate()
  return (plan: DropPlan, drag: MailDrag) => {
    const n = drag.count
    const message = plan.req.action === 'star' ? (tr('mail.dragDrop.conversationsStarred', { count: n })) : undefined
    void run({ ids: drag.ids, ...plan.req }, {
      message,
      onDone: () => {
        if (drag.selection) useUI.getState().clearSelection()
        // The open conversation left the list it was opened from: back to that list.
        if (plan.removes && drag.convId && drag.ids.includes(drag.convId)) nav(mailPath(drag.from, null, drag.q || undefined))
      },
    })
  }
}

/**
 * A nav entry as a drop target for dragged conversations. `view` is the entry's view key; a
 * target that takes no drop from here (the view itself, Sent, Drafts) gets no highlight and
 * the browser shows "no drop".
 */
export function useDropTarget(view: string, place: DropOver['place'] = 'right') {
  const key = useId()
  const active = useMailDrag((s) => s.drag !== null)
  const over = useMailDrag((s) => s.over?.key === key)
  const runDrop = useRunDrop()
  if (!active) return { over: false, props: {} }
  const planFor = (e: DragEvent) => {
    const d = useMailDrag.getState().drag
    return d ? dropPlan(view, { from: d.from, shift: e.shiftKey, labels: d.labels, contextLabelId: d.contextLabelId }) : null
  }
  return {
    over,
    props: {
      'data-drop-key': key,
      onDragOver: (e: DragEvent<HTMLElement>) => {
        const st = useMailDrag.getState()
        const plan = planFor(e)
        if (!plan) {
          if (st.over?.key === key) st.setOver(null)
          return
        }
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        const rect = e.currentTarget.getBoundingClientRect()
        const o = st.over
        if (!o || o.key !== key || o.verb !== plan.verb || Math.abs(o.rect.top - rect.top) > 1) st.setOver({ key, verb: plan.verb, rect, place })
      },
      onDrop: (e: DragEvent<HTMLElement>) => {
        const st = useMailDrag.getState()
        const plan = planFor(e)
        const drag = st.drag
        st.end()
        if (!plan || !drag) return
        e.preventDefault()
        e.stopPropagation()
        runDrop(plan, drag)
      },
    },
  }
}

/**
 * Opens a collapsed nav section (More, Categories, Labels, a label with sublabels, the Cards
 * drawer) when a conversation drag rests on its header for a moment.
 */
export function useDragExpand(open: boolean, expand: () => void, delay = 600) {
  const active = useMailDrag((s) => s.drag !== null)
  const timer = useRef<number | null>(null)
  const depth = useRef(0)
  const clear = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }
  useEffect(() => {
    if (!active) {
      clear()
      depth.current = 0
    }
  }, [active])
  useEffect(() => clear, [])
  if (!active || open) return {}
  return {
    onDragEnter: () => {
      depth.current += 1
      if (timer.current === null)
        timer.current = window.setTimeout(() => {
          timer.current = null
          if (useMailDrag.getState().drag) expand()
        }, delay)
    },
    onDragLeave: () => {
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) clear()
    },
  }
}

/** Whether a conversation drag is under way (shells that open a target list for it). */
export function useDragging(): boolean {
  return useMailDrag((s) => s.drag !== null)
}

/**
 * The verb beside the highlighted target ("Label: Financial", "Move to Trash"). Drawn once,
 * in a portal, so no nav pane clips it. Also clears the highlight when the pointer is over
 * something that is not a target, and ends a drag the browser abandoned.
 */
export function DropHint() {
  const drag = useMailDrag((s) => s.drag)
  const over = useMailDrag((s) => s.over)
  useEffect(() => {
    if (!drag) return
    const onOver = (e: globalThis.DragEvent) => {
      const t = e.target as HTMLElement | null
      if (!t?.closest?.('[data-drop-key]') && useMailDrag.getState().over) useMailDrag.getState().setOver(null)
    }
    // A drop outside the window or Esc: dragend reaches the row only while it is still in the list.
    const onEnd = () => useMailDrag.getState().end()
    document.addEventListener('dragover', onOver)
    document.addEventListener('dragend', onEnd)
    document.addEventListener('drop', onEnd)
    // A row unmounted mid-drag (the virtual list scrolled) gets no dragend: the next press ends it.
    document.addEventListener('pointerdown', onEnd)
    return () => {
      document.removeEventListener('dragover', onOver)
      document.removeEventListener('dragend', onEnd)
      document.removeEventListener('drop', onEnd)
      document.removeEventListener('pointerdown', onEnd)
    }
  }, [drag])
  if (!drag || !over) return null
  const r = over.rect
  const style =
    over.place === 'below'
      ? { top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - 240)) }
      // Beside the target, on its end side (the right, or the left in a right-to-left page) when there is room.
      : (isRtl() ? r.left - 220 >= 0 : r.right + 220 > window.innerWidth)
        ? { top: r.top + r.height / 2, right: window.innerWidth - r.left + 8, transform: 'translateY(-50%)' }
        : { top: r.top + r.height / 2, left: r.right + 8, transform: 'translateY(-50%)' }
  return createPortal(
    <div className="drop-hint" role="status" aria-live="polite" style={style} data-drop-hint>
      {over.verb}
    </div>,
    document.body,
  )
}
