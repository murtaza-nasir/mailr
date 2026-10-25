// "Due <date>" on a list row and in the reader, with a small menu to say it is not a deadline
// (mail.dismiss_deadline): for this conversation, or for everything from its sender. A click, a
// right-click or a long press opens the menu. The chip leaves every list and the reader at once;
// other windows follow from the server's change event. has:deadline leaves the conversation too.
// ACT mail.dismiss_deadline @web.list @web.reader

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { CalendarX, UserX } from 'lucide-react'
import { useRef } from 'react'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import type { ConversationDetail, ConversationSummary, Page } from '../../api/types'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { toast, toastError, useUI } from '../../stores/ui'
import { t, dateLocale } from '../../i18n'

type Sig = NonNullable<ConversationSummary['signals']>[number]

const DAY = new Intl.DateTimeFormat(dateLocale('en-US'), { month: 'short', day: 'numeric' })
const LONG_PRESS_MS = 500

/** A deadline is urgent when it falls within the next two days (or has passed). */
export function dueUrgent(date: string | null | undefined, now = new Date()): boolean {
  if (!date) return false
  const due = new Date(`${date}T00:00:00`)
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((due.getTime() - today.getTime()) / 86_400_000) <= 2
}

/** The date alone, for the short chip a narrow row shows (the full text stays its label). */
export function dueShort(date: string | null | undefined): string {
  return date ? DAY.format(new Date(`${date}T12:00:00`)) : t('mail.deadlineChip.deadline')
}

export function dueText(date: string | null | undefined): string {
  return date ? t('mail.deadlineChip.due', { format: DAY.format(new Date(`${date}T12:00:00`)) }) : t('mail.deadlineChip.deadline')
}

/** A list page without the deadline signal on these conversations. */
export function withoutDeadline(page: Page<ConversationSummary>, ids: ReadonlySet<number>): Page<ConversationSummary> {
  let changed = false
  const items = page.items.map((c) => {
    if (!ids.has(c.id) || !(c.signals ?? []).some((s) => s.name === 'deadline')) return c
    changed = true
    return { ...c, signals: (c.signals ?? []).filter((s) => s.name !== 'deadline') }
  })
  return changed ? { ...page, items } : page
}

/** Drop the chip from every cached list, the open conversation and the held row. */
export function dropDeadline(qc: QueryClient, ids: number[]): void {
  const set = new Set(ids)
  for (const [key, data] of qc.getQueriesData<Page<ConversationSummary>>({ queryKey: qk.conversations })) {
    if (data?.items) qc.setQueryData(key, withoutDeadline(data, set))
  }
  for (const id of ids) {
    const det = qc.getQueryData<ConversationDetail>(qk.conversation(id))
    if (det) qc.setQueryData(qk.conversation(id), { ...det, conversation: withoutDeadline({ items: [det.conversation] } as Page<ConversationSummary>, set).items[0] })
  }
  const held = useUI.getState().held
  if (held && set.has(held.id)) useUI.getState().setHeld({ ...held, c: withoutDeadline({ items: [held.c] } as Page<ConversationSummary>, set).items[0] })
}

export function DeadlineChip({ cid, sig }: { cid: number; sig: Sig }) {
  const qc = useQueryClient()
  const pop = useAnchor<HTMLElement>()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pressed = useRef(false)
  const text = dueText(sig.date)
  const urgent = dueUrgent(sig.date)

  const dismiss = async (sender: boolean) => {
    pop.close()
    const snapshot = qc.getQueriesData({ queryKey: qk.conversations })
    const detail = qc.getQueryData(qk.conversation(cid))
    dropDeadline(qc, [cid])
    try {
      const r = await api.post<{ rejected: number; conversation_ids: number[]; senders: string[] }>(
        `/conversations/${cid}/deadline/dismiss`, { sender })
      if (sender && r?.conversation_ids?.length) dropDeadline(qc, r.conversation_ids)
      toast({ message: sender ? t('mail.deadlineChip.noDeadlinesFromThisSender') : t('mail.deadlineChip.notADeadlineRemoved') })
    } catch (e) {
      for (const [k, d] of snapshot) qc.setQueryData(k, d)
      qc.setQueryData(qk.conversation(cid), detail)
      toastError(e, t('mail.deadlineChip.couldNotRemoveTheDeadline'))
    } finally {
      void qc.invalidateQueries({ queryKey: qk.conversations })
      void qc.invalidateQueries({ queryKey: qk.conversation(cid) })
    }
  }

  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation()
  const clear = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }

  return (
    <>
      <button type="button" className={`signal-chip deadline${urgent ? ' urgent' : ''}`} title={sig.reason || text}
        aria-haspopup="menu" aria-expanded={pop.open} data-testid="deadline-chip"
        onClick={(e) => {
          e.stopPropagation()
          e.preventDefault()
          if (pressed.current) {
            pressed.current = false
            return
          }
          pop.toggle(e.currentTarget)
        }}
        onContextMenu={(e) => {
          e.preventDefault()
          e.stopPropagation()
          pop.show(e.currentTarget)
        }}
        onPointerDown={(e) => {
          e.stopPropagation()
          if (e.pointerType !== 'touch') return
          const el = e.currentTarget
          clear()
          timer.current = setTimeout(() => {
            pressed.current = true
            pop.show(el)
          }, LONG_PRESS_MS)
        }}
        onPointerUp={clear} onPointerLeave={clear} onPointerCancel={clear}
        onMouseDown={stop} onDoubleClick={stop} aria-label={text}>
        {/* A narrow row shows the date alone (list.css, container rowline); the label keeps "Due". */}
        <span className="due-full" aria-hidden>{text}</span>
        <span className="due-short" aria-hidden>{dueShort(sig.date)}</span>
      </button>
      {/* React events bubble out of the portalled menu to the row; they end here so a choice
          does not also open the email. */}
      <span hidden={!pop.open} onClick={stop} onMouseDown={stop} onContextMenu={stop} onPointerDown={stop}>
        <Popover anchor={pop.anchor} open={pop.open} onClose={pop.close} label={t('mail.deadlineChip.deadline')} minWidth={240}>
          <MenuItem icon={<CalendarX />} onSelect={() => void dismiss(false)}>{t('mail.deadlineChip.notADeadline')}</MenuItem>
          <MenuItem icon={<UserX />} onSelect={() => void dismiss(true)}>{t('mail.deadlineChip.notADeadlineFromThis')}</MenuItem>
        </Popover>
      </span>
    </>
  )
}
