// "Waiting for you": one bell in the top bar of every theme, on the phone too. Its
// sections are Approvals (decided in place, "Allow all n" for one chat or agent), Not sent (Retry,
// and a link into the Outbox) and Reminders (now and missed). The badge counts what needs you:
// waiting approvals, messages not sent, and reminders. It is quiet (grey) when only missed
// reminders are left. The footer holds the links out: Activity, the approval history and the
// notification settings.
// ACT draft.retry_send @web.nav, ACT draft.outbox @web.nav

import { AlertCircle, Bell, BellRing, Settings } from 'lucide-react'
import { useEffect, useRef, useState, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router'
import { Popover, useAnchor } from '../../components/Popover'
import { mailPath } from '../../lib/views'
// The approvals list (with the assistant's approval cards) loads when the bell first opens.
const ApprovalsSection = lazy(() => import('../approvals/ApprovalsSection').then((m) => ({ default: m.ApprovalsSection })))
import { useApprovals } from '../approvals/approvals'
import { clearApprovalFocus, useApprovalFocus, type ApprovalFocus } from '../approvals/focus'
import { notSent, plainSendError, useActiveOutbox, useOutboxActions, who, type OutboxRow } from '../mail/outbox'
import { useActiveReminders, useReminderUI } from '../reminders/api'
import { stackOrder } from '../reminders/logic'
import { ReminderHost } from '../reminders/ReminderHost'
import { ReminderBanner, ReminderSheetInPlace, RemindersSection } from '../reminders/RemindersSection'
import { Triage } from '../reminders/Triage'
import { waitingLabel } from './label'
import './waiting.css'
import { t } from '../../i18n'

export function WaitingBell({ className = 'icon-btn' }: { className?: string }) {
  const reminders = stackOrder(useActiveReminders().data ?? [])
  const approvals = useApprovals('pending').data ?? []
  const failed = (useActiveOutbox().data ?? []).filter(notSent)
  const bell = useAnchor()
  const btn = useRef<HTMLButtonElement>(null)
  const request = useApprovalFocus()
  // The approval the bell was opened on (from a link), until the bell closes.
  const [target, setTarget] = useState<ApprovalFocus | null>(null)
  // A reminder's sheet, open in place of the list.
  const [sheet, setSheet] = useState<string | null>(null)
  const counts = { approvals: approvals.length, notSent: failed.length, reminders: reminders.length, missed: reminders.filter((r) => r.missed).length }
  const n = counts.approvals + counts.notSent + counts.reminders
  const urgent = counts.approvals + counts.notSent > 0
  const quiet = !urgent && counts.missed === counts.reminders
  const label = waitingLabel(counts)

  const close = () => {
    bell.close()
    setTarget(null)
    setSheet(null)
    useReminderUI.getState().set({ trayOpen: false })
  }

  // An approval link: open the bell on it. The bell may be hidden (a print view); the popover
  // then hangs from the top right corner.
  const show = bell.show
  useEffect(() => {
    if (!request) return
    clearApprovalFocus()
    setTarget(request)
    setSheet(null)
    const el = btn.current
    const r = el?.getBoundingClientRect()
    show(el && r && r.width > 0 ? el : new DOMRect(window.innerWidth - 12, 48, 0, 0))
  }, [request, show])

  const current = sheet ? reminders.find((r) => r.key === sheet) : null
  return (
    <>
      <ReminderHost />
      <Triage />
      <button
        ref={btn}
        type="button"
        className={`${className} rm-bell wb-bell${n ? ' has' : ''}${urgent ? ' urgent' : ''}`}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={bell.open}
        data-tip={label}
        data-testid="waiting-bell"
        onClick={(e) => {
          if (bell.open) close()
          else {
            bell.show(e.currentTarget)
            useReminderUI.getState().set({ trayOpen: true })
          }
        }}
      >
        {n ? <BellRing /> : <Bell />}
        {n > 0 && <span className={`badge${quiet ? ' quiet' : ''}`}>{n > 99 ? '99+' : n}</span>}
      </button>
      <Popover anchor={bell.anchor} open={bell.open} onClose={close} placement="bottom-end" role="dialog" label={t('waiting.waitingBell.waitingForYou')} className="wb-pop rm-tray" minWidth={360}>
        {current ? (
          <ReminderSheetInPlace item={current} onBack={() => setSheet(null)} onLeave={close} />
        ) : (
          <WaitingBody target={target} reminders={reminders} failed={failed} empty={n === 0} onOpenReminder={setSheet} onLeave={close} />
        )}
      </Popover>
    </>
  )
}

function WaitingBody({ target, reminders, failed, empty, onOpenReminder, onLeave }: {
  target: ApprovalFocus | null
  reminders: ReturnType<typeof stackOrder>
  failed: OutboxRow[]
  empty: boolean
  onOpenReminder: (key: string) => void
  onLeave: () => void
}) {
  const nav = useNavigate()
  const go = (to: string) => {
    onLeave()
    nav(to)
  }
  return (
    <div className="wb-body">
      <header className="wb-head">
        <b>{t('waiting.waitingBell.waitingForYou')}</b>
      </header>
      <ReminderBanner />
      <div className="wb-scroll">
        <Suspense fallback={null}>
          <ApprovalsSection target={target} />
        </Suspense>
        <NotSentSection rows={failed} onOutbox={() => go(mailPath('outbox'))} />
        <RemindersSection items={reminders} onOpen={onOpenReminder} onLeave={onLeave} />
        {empty && !target && (
          <div className="rm-empty wb-empty">
            <Bell size={28} aria-hidden />
            <p>{t('waiting.waitingBell.nothingIsWaitingForYou')}</p>
          </div>
        )}
      </div>
      {/* The links out: Activity (what changed, with Undo) and the approval history. */}
      <footer className="wb-foot">
        {/* ACT activity.list @web.nav */}
        <button type="button" className="btn text sm" onClick={() => go('/settings/you/activity')} data-testid="bell-activity">
          {t('common.activity')}
        </button>
        <button type="button" className="btn text sm" onClick={() => go('/settings/agents/agents#approvals')}>
          {t('waiting.waitingBell.approvalHistoryAndRules')}
        </button>
        <span className="spacer" />
        <button type="button" className="icon-btn sm" aria-label={t('waiting.waitingBell.notificationSettings')} data-tip={t('waiting.waitingBell.notificationSettings')} onClick={() => go('/settings/notifications/notifications')}>
          <Settings size={18} />
        </button>
      </footer>
    </div>
  )
}

/** Messages that could not be sent, with Retry; the Outbox holds Edit and Discard. */
function NotSentSection({ rows, onOutbox }: { rows: OutboxRow[]; onOutbox: () => void }) {
  const a = useOutboxActions()
  if (!rows.length) return null
  return (
    <section className="wb-section wb-notsent" role="region" aria-label={t('common.notSent')}>
      <header className="wb-section-head">
        <h3>{t('common.notSent')}</h3>
        <span className="spacer" />
        <button type="button" className="btn text sm" onClick={onOutbox}>
          {t('waiting.waitingBell.openOutbox')}
        </button>
      </header>
      <ul className="wb-list">
        {rows.map((o) => (
          <li key={o.id} className="wb-notsent-row" data-testid="waiting-not-sent">
            <AlertCircle size={18} aria-hidden className="wb-bad" />
            <span className="wb-row-text">
              <span className="wb-row-title" dir="auto">{o.subject || t('common.noSubject')}</span>
              <span className="wb-row-sub">{t('waiting.waitingBell.to', { who: who(o), plainSendError: plainSendError(o.error) })}</span>
            </span>
            <button type="button" className="btn tonal sm" onClick={() => void a.retry(o)}>
              {t('common.retry')}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
