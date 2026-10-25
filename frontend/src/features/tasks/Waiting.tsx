// ACT mail.follow_up: Change date and Cancel on a waiting conversation.
// "Waiting for reply": the mail.follow_up reminders, read-only rows (they are
// conversations, not tasks) by follow-up date, with Open / Change date / Cancel / Make it a task.
// Today's "Follow-ups" section uses the same rows for the ones due by today.

import { AlarmClock, CalendarClock, ListPlus, Mail, X } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useActions, useConversations } from '../../api/hooks'
import type { ConversationSummary } from '../../api/types'
import { Popover, useAnchor } from '../../components/Popover'
import { formatDate, formatTime, parseISO, todayISO, toISODate, type DatePrefs } from '../../lib/dates'
import { mailPath } from '../../lib/views'
import { toast } from '../../stores/ui'
import { WhenPicker } from './WhenPicker'
import { t } from '../../i18n'

export function useFollowUps(enabled = true) {
  const q = useConversations({ view: 'all', q: 'is:follow_up', limit: 100 }, enabled)
  const items = (q.data?.items ?? []).filter((c) => c.follow_up_at).sort((a, b) => (a.follow_up_at! < b.follow_up_at! ? -1 : 1))
  return { items, loading: q.isLoading }
}

function who(c: ConversationSummary): string {
  const p = c.participants?.filter((x) => !(x as { me?: boolean }).me) ?? []
  return p.map((x) => x.name || x.email).slice(0, 2).join(', ')
}

export function FollowUpRow({ c, now, prefs, onMakeTask }: { c: ConversationSummary; now: Date; prefs: DatePrefs; onMakeTask: (c: ConversationSummary) => void }) {
  const nav = useNavigate()
  const { run } = useActions()
  const pick = useAnchor<HTMLButtonElement>()
  const at = parseISO(c.follow_up_at!, prefs.zone)!
  const day = toISODate(at, prefs.zone)
  const today = todayISO(prefs.zone, now)
  const due = at.getTime() <= now.getTime()
  const text = day === today ? t('tasks.waiting.today', { time: formatTime(at, prefs, { compact: true }) }) : formatDate(day, 'day')
  return (
    <div className="tk-row tk-follow" role="row" data-testid="followup-row">
      <span className="tk-handle-cell" />
      <span className="tk-check-cell"><AlarmClock size={16} className="tk-follow-icon" aria-hidden /></span>
      <span className="tk-main">
        <span className="tk-line">
          <button type="button" className="tk-title tk-follow-title" dir="auto" onClick={() => nav(mailPath('all', c.id))}>{c.subject || t('common.noSubject')}</button>
          <span className="tk-trail">
            <span className={`tk-due tone-${due ? 'overdue' : day <= today ? 'urgent' : 'normal'}`} title={t('tasks.waiting.whenToCheckForA')}>{due ? t('tasks.waiting.noReply', { text }) : text}</span>
            <span className="tk-follow-actions">
              <button type="button" className="icon-btn sm" aria-label={t('common.open')} title={t('common.open')} onClick={() => nav(mailPath('all', c.id))}><Mail size={15} /></button>
              <button type="button" className="icon-btn sm" aria-label={t('tasks.waiting.changeDate')} title={t('tasks.waiting.changeDate')} onClick={(e) => pick.toggle(e.currentTarget)}><CalendarClock size={15} /></button>
              <button type="button" className="icon-btn sm" aria-label={t('tasks.waiting.cancelFollowUp')} title={t('tasks.waiting.cancelFollowUp')} onClick={() => void run({ ids: [c.id], action: 'cancel_follow_up' })}><X size={15} /></button>
              <button type="button" className="icon-btn sm" aria-label={t('tasks.waiting.makeItATask')} title={t('tasks.waiting.makeItATask')} onClick={() => onMakeTask(c)}><ListPlus size={15} /></button>
            </span>
          </span>
        </span>
        <span className="tk-meta"><span className="tk-meta-list">{t('tasks.waiting.waitingForReply')}</span>{who(c) && <span>{who(c)}</span>}</span>
      </span>
      <Popover anchor={pick.anchor} open={pick.open} onClose={pick.close} role="dialog" className="tk-when-pop" label={t('tasks.waiting.checkForAReplyOn')} noFocus>
        <WhenPicker value={c.follow_up_at!} now={now} prefs={prefs} label={t('tasks.waiting.checkForAReplyOn')} noDate={false} needTime onPick={(v) => {
          pick.close()
          if (!v) return
          void run({ ids: [c.id], action: 'follow_up', until: v }, { message: t('tasks.waiting.followUpDateChanged') })
        }} />
      </Popover>
    </div>
  )
}

export function WaitingList({ items, now, prefs, onMakeTask }: { items: ConversationSummary[]; now: Date; prefs: DatePrefs; onMakeTask: (c: ConversationSummary) => void }) {
  if (!items.length)
    return (
      <div className="tk-empty">
        <AlarmClock size={36} aria-hidden />
        <h2>{t('tasks.waiting.nobodyToChase')}</h2>
        <p>{t('tasks.waiting.setAFollowUpOn')}</p>
      </div>
    )
  return (
    <div className="tk-list" role="grid" aria-label={t('tasks.waiting.waitingForReply')}>
      <div className="tk-scroll">
        {items.map((c) => <FollowUpRow key={c.id} c={c} now={now} prefs={prefs} onMakeTask={onMakeTask} />)}
      </div>
    </div>
  )
}

export function followUpTaskBody(c: ConversationSummary, zone: string): Record<string, unknown> {
  return { summary: t('tasks.waiting.followUp', { subject: c.subject || 'email' }), due: c.follow_up_at ?? undefined, conversation_id: c.id, tz: zone }
}

export function toastFollowUp() {
  toast({ message: t('tasks.waiting.theFollowUpIsA') })
}
