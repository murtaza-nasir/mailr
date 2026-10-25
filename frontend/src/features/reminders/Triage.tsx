// The one-by-one triage queue on the web (web.triage): a pager of reminder sheets with a counter ("2 of 5"). The
// chevrons (or ←/→) move without acting; an action removes the card and moves on; Esc or a click
// outside leaves, and what was not acted on stays exactly as it was. The last action closes the
// queue with "All caught up". Opened by the tray's "Review all" and by a summary notification.
// The queue stays live while it is open: a reminder handled on another device drops out, and
// one that fires meanwhile joins at the end. ACT reminder.sync @web.triage, ACT reminder.fire @web.triage
// ACT notification.open_stack @web.triage, ACT reminder.dismiss @web.triage, ACT task.snooze @web.triage, ACT event.snooze @web.triage, ACT task.complete @web.triage,
// ACT event.remind_before @web.triage (the sheet's Remind before)

import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { toast } from '../../stores/ui'
import { useActiveReminders, useReminderActions, useReminderUI } from './api'
import { ReminderSheet } from './ReminderSheet'
import { joinedWhileOpen, type ReminderItem } from './logic'
import { t } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

export function Triage() {
  const keys = useReminderUI((s) => s.triage)
  if (!keys) return null
  return createPortal(<TriageDialog keys={keys} />, document.body)
}

function TriageDialog({ keys }: { keys: string[] }) {
  const active = useActiveReminders()
  const actions = useReminderActions()
  const close = () => useReminderUI.getState().set({ triage: null })
  const [handled, setHandled] = useState<Set<string>>(new Set())
  // The requests of the actions taken here: "All caught up" waits for them, so it follows their own
  // messages ("Snoozed until …") instead of appearing before the last one.
  const pending = useRef<Promise<unknown>[]>([])
  const [i, setI] = useState(0)
  const [openedAt] = useState(() => Date.now())

  // ACT reminder.fire: a reminder that fires while the queue is open joins it at the end.
  useEffect(() => {
    const fresh = joinedWhileOpen(active.data ?? [], keys, openedAt)
    if (fresh.length) useReminderUI.getState().set({ triage: [...keys, ...fresh] })
  }, [active.data, keys, openedAt])
  // The queue keeps its order; items handled anywhere drop out.
  const byKey = useMemo(() => new Map((active.data ?? []).map((r) => [r.key, r])), [active.data])
  const queue: ReminderItem[] = keys.map((k) => byKey.get(k)).filter((r): r is ReminderItem => !!r && !handled.has(r.key))
  const idx = Math.min(i, Math.max(0, queue.length - 1))
  const cur = queue[idx]

  useEffect(() => {
    if (active.isSuccess && keys.length && !queue.length) {
      close()
      if (handled.size) void Promise.allSettled(pending.current).then(() => toast({ message: t('reminders.triage.allCaughtUp') }))
    }
  }, [active.isSuccess, keys.length, queue.length, handled.size])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close()
      } else if (arrowStep(e.key) === 1 && !(e.target as HTMLElement)?.closest?.('input,textarea')) setI((x) => Math.min(x + 1, queue.length - 1))
      else if (arrowStep(e.key) === -1 && !(e.target as HTMLElement)?.closest?.('input,textarea')) setI((x) => Math.max(0, x - 1))
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [queue.length])

  if (!cur) return null
  return (
    <div className="scrim rm-triage-scrim" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="rm-triage" role="dialog" aria-modal="true" aria-label={t('reminders.triage.reviewReminders')}>
        <header className="rm-triage-head">
          <button type="button" className="icon-btn sm" aria-label={t('reminders.triage.previousReminder')} disabled={idx === 0} onClick={() => setI(idx - 1)}>
            <ChevronLeft size={20} />
          </button>
          <span className="rm-triage-count" aria-live="polite">{t('reminders.triage.of', { value: idx + 1, queueLength: queue.length })}</span>
          <button type="button" className="icon-btn sm" aria-label={t('reminders.triage.nextReminder')} disabled={idx >= queue.length - 1} onClick={() => setI(idx + 1)}>
            <ChevronRight size={20} />
          </button>
          <span className="spacer" />
          <button type="button" className="icon-btn sm" aria-label={t('common.close')} onClick={close}>
            <X size={20} />
          </button>
        </header>
        <div className="rm-dots" aria-hidden>
          {queue.map((r, n) => <span key={r.key} className={n === idx ? 'on' : r.missed ? 'missed' : ''} />)}
        </div>
        <ReminderSheet key={cur.key} item={cur} actions={actions}
          onOpen={close}
          onDone={(settled) => {
            if (settled) pending.current.push(settled)
            setHandled((h) => new Set(h).add(cur.key))
          }} />
      </div>
    </div>
  )
}
