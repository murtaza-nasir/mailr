// The quick-action sheet for one reminder (web.sheet, web.triage): the title, the when line and a status chip;
// two to four large actions (Task: Done, Snooze, Reschedule. Event before its start: Snooze,
// Remind before. Event in progress: Snooze, Dismiss); a text row (Dismiss, Open). Snooze, Remind
// before and Reschedule expand the sheet in place; Back returns to the actions. Opening the sheet
// handles nothing: only an action does.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { AlarmClock, AlarmClockPlus, ArrowLeft, BellOff, Bot, CalendarClock, CalendarDays, Check, CircleCheck, ClipboardList, ExternalLink, StickyNote } from 'lucide-react'
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useSettings } from '../../api/hooks'
import { resolveDatePrefs, formatTime } from '../../lib/dates'
import { offsetLabel, offsets } from '../../lib/quickChoices'
import { overdueText, type SheetSpec } from '../../lib/whenSheet'
import { describe, join } from '../tasks/when'
import { WhenPicker } from '../tasks/WhenPicker'
import { FullPicker, WhenSheet } from './WhenSheet'
import '../tasks/tasks.css'
import { LastTime } from '../speakr/LastTime'
import { openAsk, reminderAbout, useAskTarget } from '../ask/askStore'
import '../ask/ask.css'
import type { ReminderActions } from './api'
import { anchorOf, GENERIC_TITLE, recordPath, started, whenLine, type ReminderItem } from './logic'
import { t } from '../../i18n'

type Mode = 'actions' | 'snooze' | 'offset' | 'reschedule'

export function useReminderPrefs() {
  const s = useSettings() as unknown as { timezone?: string; week_start?: number | string; time_format?: string; morning_hour?: number }
  return useMemo(() => ({ ...resolveDatePrefs(s), morningHour: s.morning_hour ?? 9 }), [s])
}

export function ReminderSheet({ item, actions, onDone, onOpen, counter, nav: navSlot, initialMode }: {
  item: ReminderItem
  /** Opened from "Remind before…" on a notification: the offsets show at once. */
  initialMode?: 'offset' | null
  actions: ReminderActions
  /** After an action: the sheet closes, or the queue moves on. */
  /** Called as soon as an action is chosen, with the action's request still on its way. */
  onDone: (settled?: Promise<unknown>) => void
  /** Leaving for the record (the event or the task). */
  onOpen?: () => void
  /** "2 of 5" in a triage queue. */
  counter?: ReactNode
  nav?: ReactNode
}) {
  const nav = useNavigate()
  const prefs = useReminderPrefs()
  const now = new Date()
  const start = anchorOf(item.start, prefs.zone)
  const [mode, setMode] = useState<Mode>(() => (initialMode === 'offset' && item.kind === 'event' && start && offsets(now, start).length ? 'offset' : 'actions'))
  const running = started(item, now, prefs.zone)
  const task = item.kind === 'task'
  const note = item.kind === 'note'
  // The status sits at the end of the when line as a small badge (the sheet's header).
  const line = whenLine(item, now, prefs).replace(/ · overdue .*$/, '')
  const dateOnly = !!item.start && item.start.length === 10
  const overdue = task && start ? overdueText(start, now, dateOnly) : null
  const time = (d: Date) => formatTime(d, prefs)
  // A note reminder snoozes with the task presets: it has no start to count back from.
  const spec = (verb: SheetSpec['verb']): SheetSpec => ({ verb, kind: item.kind === 'event' ? 'event' : 'task', now, anchor: note ? null : start, dateOnly, morningHour: prefs.morningHour })
  const anchorText = line.startsWith('Due ') ? line.slice(4) : null

  const go = (fn: () => Promise<boolean>) => {
    const settled = fn()
    onDone(settled)
  }

  const primary: { id: string; label: string; icon: ReactNode; run: () => void }[] = task
    ? [
        { id: 'done', label: t('common.done'), icon: <Check size={18} />, run: () => go(() => actions.done(item)) },
        { id: 'snooze', label: t('common.snooze'), icon: <AlarmClock size={18} />, run: () => setMode('snooze') },
        { id: 'reschedule', label: t('common.reschedule'), icon: <CalendarClock size={18} />, run: () => setMode('reschedule') },
      ]
    : running || !start || note
      ? [
          { id: 'snooze', label: t('common.snooze'), icon: <AlarmClock size={18} />, run: () => setMode('snooze') },
          { id: 'dismiss', label: t('common.dismiss'), icon: <BellOff size={18} />, run: () => go(() => actions.dismiss(item)) },
        ]
      : [
          { id: 'snooze', label: t('common.snooze'), icon: <AlarmClock size={18} />, run: () => setMode('snooze') },
          ...(offsets(now, start).length ? [{ id: 'offset', label: t('reminders.reminderSheet.remindBefore'), icon: <AlarmClockPlus size={18} />, run: () => setMode('offset') }] : []),
          { id: 'dismiss', label: t('common.dismiss'), icon: <BellOff size={18} />, run: () => go(() => actions.dismiss(item)) },
        ]
  const open = () => {
    onOpen?.()
    nav(recordPath(item))
  }
  // ACT agent.ask_about @web.sheet @web.triage: the ask capture about this reminder's event or task. It handles nothing;
  // the sheet (or the tray, or the queue) closes and the reminder stays where it was.
  const about = reminderAbout(item)
  useAskTarget(about)

  return (
    <div className="qs rm-sheet" data-testid="reminder-sheet" data-key={item.key}>
      <div className="qs-head">
        {mode !== 'actions' && (
          <button type="button" className="icon-btn sm" aria-label={t('common.back')} onClick={() => setMode('actions')}>
            <ArrowLeft size={18} />
          </button>
        )}
        <span className={`rm-kind ${item.kind}`} aria-hidden>{task ? <CircleCheck size={18} /> : note ? <StickyNote size={18} /> : <CalendarDays size={18} />}</span>
        <div className="rm-head-text">
          <div className="qs-title" dir="auto">{item.title || GENERIC_TITLE[item.kind]}</div>
          <div className="qs-when-line">
            {line}
            {item.missed && <span className="ws-badge missed">{t('reminders.reminderSheet.missed')}</span>}
            {overdue && !item.missed && <span className="ws-badge overdue">{overdue}</span>}
            {running && <span className="ws-badge live">{t('common.inProgress')}</span>}
          </div>
          {item.detail && <div className="rm-detail muted">{item.detail}</div>}
          {/* ACT event.last_recording @web.sheet: the last recording of this meeting (Speakr accounts only). */}
          {item.kind === 'event' && <LastTime uid={item.uid} occurrence={item.occurrence} onOpen={onOpen} />}
        </div>
        {counter && <span className="rm-counter">{counter}</span>}
        <button type="button" className="icon-btn sm rm-ask" aria-label={t('reminders.reminderSheet.ask', { name: assistantNameNow() })} data-tip={t('reminders.reminderSheet.askShiftA', { name: assistantNameNow() })} data-testid="reminder-ask"
          onClick={(e) => {
            openAsk(about, e.currentTarget)
            onOpen?.()
          }}>
          <Bot size={18} />
        </button>
      </div>

      {mode === 'actions' && (
        <>
          <div className="ws-acts" style={{ '--n': primary.length } as CSSProperties}>
            {primary.map((b, i) => (
              <button key={b.id} type="button" className={`ws-act${i === 0 ? ' ws-main' : ''}`} onClick={b.run} data-act={b.id}>
                {b.icon}
                {b.label}
              </button>
            ))}
          </div>
          <div className="ws-sec">
            {/* ACT reminder.dismiss @web.sheet */}
            {task && (
              <button type="button" className="btn text sm" onClick={() => go(() => actions.dismiss(item))}>
                <BellOff size={16} />{' '}{t('common.dismiss')}
              </button>
            )}
            <button type="button" className="btn text sm" onClick={open}>
              <ExternalLink size={16} /> {task ? t('reminders.reminderSheet.openTask') : note ? t('reminders.reminderSheet.openNote') : t('reminders.reminderSheet.openEvent')}
            </button>
            {/* ACT event.prep @web.sheet: an event with guests links to its meeting prep pack. */}
            {!task && item.prep && (
              <button type="button" className="btn text sm" data-testid="reminder-prep" onClick={() => {
                onOpen?.()
                nav(item.prep!)
              }}>
                <ClipboardList size={16} />{' '}{t('reminders.reminderSheet.prep')}
              </button>
            )}
            {navSlot}
          </div>
        </>
      )}

      {/* ACT task.snooze @web.sheet, ACT event.snooze @web.sheet: the when sheet; only the reminder moves. */}
      {mode === 'snooze' && (
        <WhenSheet spec={spec('snooze')} confirm={t('common.snooze')} time={time} anchorText={anchorText} groupLabel={t('reminders.reminderSheet.snoozeUntil')}
          onPick={(at) => go(() => actions.snooze(item, at))}
          full={(close) => <FullPicker initial={new Date(now.getTime() + 60 * 60_000)} now={now} confirm={t('common.snooze')} time={time} onBack={close}
            onPick={(at) => go(() => actions.snooze(item, at))} />} />
      )}

      {/* ACT event.remind_before @web.triage */}
      {mode === 'offset' && start && (
        <WhenSheet spec={spec('remind')} confirm={t('common.remindMe')} time={time} groupLabel={t('reminders.reminderSheet.remindMeBeforeItStarts')}
          onPick={(at, _d, tile) => {
            const m = tile?.minutes ?? Math.max(0, Math.round((start.getTime() - at.getTime()) / 60_000))
            go(() => actions.remindBefore(item, m, offsetLabel(m)))
          }} />
      )}

      {/* ACT task.reschedule @web.sheet: the due moves; Pick date & time… is the typed form with its accelerators. */}
      {mode === 'reschedule' && task && (
        <WhenSheet spec={spec('reschedule')} confirm={t('common.reschedule')} time={time} groupLabel={t('reminders.reminderSheet.rescheduleTo')}
          onPick={(at, only) => {
            const v = only ? join(isoDay(at), null, prefs.zone) : at.toISOString()
            go(() => actions.rescheduleTask(item, v, describe(v, new Date(), prefs).split(' · ')[0]))
          }}
          full={() => (
            <WhenPicker value={item.start} now={now} prefs={prefs} label={t('common.reschedule')} noDate={false}
              onPick={(v) => {
                if (!v) return
                go(() => actions.rescheduleTask(item, v, describe(v, new Date(), prefs).split(' · ')[0]))
              }} />
          )} />
      )}
    </div>
  )
}

/** The local calendar day of [d] as YYYY-MM-DD. */
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
