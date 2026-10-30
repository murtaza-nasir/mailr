// An action the assistant wants to take, waiting for the user: a clear preview (the server's display
// model, drawn by present/ApprovalDisplay; older servers send title and lines) and the decision
// buttons. Decisions go to POST /approvals/{id}/decide; the card then changes
// in place to its outcome ("Approved · moved 6 messages to Trash") and the chat continues after it.
// The same card decides in the top bar's "Waiting for you" bell (ApprovalsSection), over any page.
// Ask (in the card's head) opens the ask capture with the approval as its chip: the assistant
// explains it ("why does this need approval?"), but only these buttons decide it.
// ACT approval.approve @web.reader, ACT approval.deny @web.reader, ACT agent.ask_about @web.panel @web.reader @web.settings

import { assistantNameNow } from './useAssistantName'
import {
  AlertTriangle, Archive, AudioLines, Bot, Brain, CalendarDays, Check, Clock, EllipsisVertical, FilePen, Filter, Mail, MailX, Send, Settings2,
  ShieldAlert, ShieldCheck, Sparkles, SquareCheck, StickyNote, Tag, Trash2, Users, Workflow, X,
} from 'lucide-react'
import { QueryClientContext } from '@tanstack/react-query'
import { useContext, useEffect, useState, type ReactElement } from 'react'
import { api } from '../../../api/client'
import { toast, toastError } from '../../../stores/ui'
import { alsoLabel, grantToast, scopeHelp, undoToast, type GrantResult } from '../../approvals/grant'
import { openAsk } from '../../ask/askStore'
import { ApprovalDisplay } from './present/ApprovalDisplay'
import { ReviewPlan, type MoveKey } from './ReviewPlan'
import { localOutcome } from './present/types'
import { asQuestion, displayTitle, msgText } from './present/serverText'
import type { ApprovalCardData, ApprovalPreview, ApprovalRef } from './useAssistant'
import { t as tr, tx, appLocale } from '../../../i18n'
import { clockOpts } from '../../../lib/dates'

const DISPLAY_ICON: Record<string, ReactElement> = {
  filter: <Filter size={16} />, order: <Workflow size={16} />, trash: <Trash2 size={16} />, spam: <ShieldAlert size={16} />,
  delete: <Trash2 size={16} />, archive: <Archive size={16} />, label: <Tag size={16} />, snooze: <Clock size={16} />,
  mail: <Mail size={16} />, send: <Send size={16} />, event: <CalendarDays size={16} />, task: <SquareCheck size={16} />,
  memory: <Brain size={16} />, skill: <Sparkles size={16} />, settings: <Settings2 size={16} />, people: <Users size={16} />,
  recording: <AudioLines size={16} />, unsubscribe: <MailX size={16} />, draft: <FilePen size={16} />,
  note: <StickyNote size={16} />,
}

const ACTION_TEXT: Record<string, string> = {
  'mail.send': tr('rail.assistant.approvalCard.sendEmail'),
  'mail.trash': tr('rail.assistant.approvalCard.moveMailToTrash'),
  'mail.spam': tr('rail.assistant.approvalCard.reportSpam'),
  'mail.delete': tr('rail.assistant.approvalCard.deleteMailForever'),
  'mail.label': tr('rail.assistant.approvalCard.labelAndOrganiseMail'),
  'mail.unsubscribe': tr('rail.assistant.approvalCard.unsubscribeFromMailingLists'),
  'rules.write': tr('rail.assistant.approvalCard.changeYourFilters'),
  'calendar.create': tr('rail.assistant.approvalCard.createCalendarEvents'),
  'calendar.update': tr('rail.assistant.approvalCard.changeCalendarEvents'),
  'calendar.delete': tr('rail.assistant.approvalCard.deleteCalendarEvents'),
  'calendar.rsvp': tr('rail.assistant.approvalCard.answerInvitations'),
  'tasks.create': tr('rail.assistant.approvalCard.createTasks'),
  'tasks.update': tr('rail.assistant.approvalCard.changeTasks'),
  'tasks.complete': tr('rail.assistant.approvalCard.completeTasks'),
  'tasks.delete': tr('rail.assistant.approvalCard.deleteTasks'),
  'labels.write': tr('rail.assistant.approvalCard.createOrChangeLabels'),
  'labels.delete': tr('rail.assistant.approvalCard.deleteLabels'),
  'calendar.invite': tr('rail.assistant.approvalCard.sendInvitationsAndUpdatesTo'),
  'calendar.cancel': tr('rail.assistant.approvalCard.cancelEventsWithGuests'),
  'calendar.phone_sync': tr('rail.assistant.approvalCard.sendBusyTimesFromPhoneCalendars'),
  'contacts.write': tr('rail.assistant.approvalCard.changeContactGroups'),
  'memory.write': tr('rail.assistant.approvalCard.keepAndChangeMemories'),
  'prefs.write': tr('rail.assistant.approvalCard.changeSettingsLayoutsAndSignatures'),
  'notes.create': tr('rail.assistant.approvalCard.makeNotesAndLists'),
  'notes.edit': tr('rail.assistant.approvalCard.changeNotesAndLists'),
  'notes.delete': tr('rail.assistant.approvalCard.moveNotesToTrashAnd'),
  'notes.labels': tr('rail.assistant.approvalCard.changeNoteLabels'),
}

const DERIVED: Record<string, string> = {
  'mail.send': tr('rail.assistant.approvalCard.toTheseRecipientsDomains'),
  'calendar.create': tr('rail.assistant.approvalCard.inThisCalendar'),
  'calendar.update': tr('rail.assistant.approvalCard.inThisCalendar'),
  'calendar.delete': tr('rail.assistant.approvalCard.inThisCalendar'),
  'tasks.create': tr('rail.assistant.approvalCard.inThisTaskList'),
  'tasks.update': tr('rail.assistant.approvalCard.inThisTaskList'),
  'tasks.delete': tr('rail.assistant.approvalCard.inThisTaskList'),
  'mail.label': tr('rail.assistant.approvalCard.withTheseLabels'),
  'notes.create': 'on this note',
  'notes.edit': 'on this note',
  'notes.delete': 'on this note',
}

const DONE = new Set(['executed', 'denied', 'expired', 'failed'])

export { asQuestion } from './present/serverText'

function fmtWhen(v: unknown): string {
  if (typeof v !== 'string' || !v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(v)
  return d.toLocaleString(appLocale(), dateOnly
    ? { weekday: 'short', month: 'short', day: 'numeric' }
    : { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() })
}

function EmailPreview({ e }: { e: NonNullable<ApprovalPreview['email']> }) {
  const to = (e.to ?? []).map((a) => (a.name ? `${a.name} <${a.email}>` : a.email)).join(', ')
  const cc = (e.cc ?? []).map((a) => a.email).join(', ')
  // The whole text, scrolled: never cut at an "On … wrote:" line (an agent can write a fake one to
  // hide what follows) or at a length. Remote images and link hosts are listed in full.
  const text = (e.text ?? '').trim()
  const images = (e.images ?? []).filter(Boolean)
  const links = (e.links ?? []).filter(Boolean)
  const files = (e.attachments ?? []).map((a) => (typeof a === 'string' ? a : String((a as { filename?: string })?.filename ?? ''))).filter(Boolean)
  return (
    <div className="ap-email">
      <div className="ap-kv"><span>{tr('common.from')}</span>{e.from}</div>
      <div className="ap-kv"><span>{tr('common.to')}</span>{to || '—'}</div>
      {cc && <div className="ap-kv"><span>{tr('common.cc')}</span>{cc}</div>}
      <div className="ap-kv"><span>{tr('common.subject')}</span><b>{e.subject || tr('common.noSubject')}</b></div>
      {text && <div className="ap-body">{text}</div>}
      {images.length > 0 && (
        <div className="ap-kv"><span>{tr('rail.assistant.approvalCard.remoteImages')}</span><ul className="ap-lines">{images.map((u, i) => <li key={i}>{u}</li>)}</ul></div>
      )}
      {links.length > 0 && (
        <div className="ap-kv"><span>{tr('rail.assistant.approvalCard.linksTo')}</span><ul className="ap-lines">{links.map((u, i) => <li key={i}>{u}</li>)}</ul></div>
      )}
      {files.length > 0 && <div className="ap-meta">{tr('rail.assistant.approvalCard.attachments', { files: files.join(', ') })}</div>}
    </div>
  )
}

function ThingPreview({ p }: { p: ApprovalPreview }) {
  const ev = p.event as Record<string, unknown> | undefined
  const task = p.task as Record<string, unknown> | undefined
  if (ev) {
    return (
      <div className="ap-thing">
        <CalendarDays size={16} aria-hidden />
        <div>
          <b>{String(ev.summary ?? ev.uid ?? tr('common.event'))}</b>
          {!!ev.start && <div>{fmtWhen(ev.start)}{ev.end ? ` – ${fmtWhen(ev.end)}` : ''}</div>}
          {!!ev.location && <div className="ap-meta">{String(ev.location)}</div>}
        </div>
      </div>
    )
  }
  if (task) {
    return (
      <div className="ap-thing">
        <SquareCheck size={16} aria-hidden />
        <div>
          <b>{String(task.summary ?? task.uid ?? tr('common.task'))}</b>
          {!!task.due && <div>{tr('rail.assistant.approvalCard.due', { when: fmtWhen(task.due) })}</div>}
        </div>
      </div>
    )
  }
  return null
}

/** A grant button's class and name. When it also covers other waiting requests, its name says so and its label takes two lines. */
function grantProps(className: string, label: string, n: number | undefined) {
  const more = alsoLabel(n)
  return more ? { className: `${className} ap-grant`, 'aria-label': `${label}${more}` } : { className }
}

/** "Allow for 12 hours", with "(also approves 2 other waiting)" under it when the grant covers others. */
function GrantLabel({ label, n }: { label: string; n: number | undefined }) {
  const more = alsoLabel(n).trim()
  return more ? (
    <>
      <span>{label}</span>
      <span className="ap-also">{more}</span>
    </>
  ) : (
    <>{label}</>
  )
}

interface Props {
  a: ApprovalRef
  /** A new status, with the server's card (display and outcome) when it sent one. */
  onStatus: (id: number, status: string, card?: ApprovalCardData) => void
  onDecided: (id: number) => void
  /** Who asked (Ambrose, "Claude Code"); used in the "Always allow" text. */
  actorName?: string
  /** Label for the time-limited grant: "Allow for this chat" (assistant) or "Allow for 12 hours" (tokens). */
  sessionLabel?: string
}

export function ApprovalCard({ a, onStatus, onDecided, actorName = assistantNameNow(), sessionLabel = tr('rail.assistant.approvalCard.allowForThisChat') }: Props) {
  const [busy, setBusy] = useState(false)
  const [always, setAlways] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [details, setDetails] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  // A review card (agent/review.py): the user's own moves on it, sent with Apply (apply_changes).
  const [changes, setChanges] = useState<Record<number, MoveKey>>({})
  const changed = Object.keys(changes).length
  // The app's query cache, when there is one (the card also renders on its own in tests).
  const qc = useContext(QueryClientContext)
  const p = a.preview ?? {}
  const display = a.display ?? p.display ?? null
  const pending = a.status === 'pending'

  // Pick up a decision made elsewhere (Settings, another device) when the window regains focus.
  useEffect(() => {
    if (!pending) return
    const check = () =>
      api
        .get<ApprovalCardData>(`/approvals/${a.id}`)
        .then((r) => {
          if (r.status !== 'pending') {
            onStatus(a.id, r.status, r)
            if (DONE.has(r.status)) onDecided(a.id)
          }
        })
        .catch(() => undefined)
    window.addEventListener('focus', check)
    return () => window.removeEventListener('focus', check)
  }, [pending, a.id, onStatus, onDecided])

  const decide = async (decision: 'approve' | 'deny', scope: 'once' | 'session' | 'always' = 'once') => {
    setBusy(true)
    setErr(null)
    try {
      const r = await api.post<ApprovalCardData & GrantResult>(`/approvals/${a.id}/decide`, {
        decision, scope, constraint: scope === 'always' ? 'derived' : 'none',
        ...(review && changed && decision === 'approve' ? { changes } : {}),
      })
      let status = r.status
      onStatus(a.id, status, r)
      // ACT approval.undo_grant @web.reader: the grant decided other waiting requests too. Say so,
      // with Undo for the ones that have not run yet (and the rule).
      const also = r.also_decided?.count ?? 0
      if (also > 0 && scope !== 'once' && r.rule_id) {
        void qc?.invalidateQueries({ queryKey: ['approvals'] })
        const ruleId = r.rule_id
        toast({
          message: decision === 'approve' ? grantToast(scope, sessionLabel, also) : tr('rail.assistant.approvalCard.deniedAlsoDeniedOtherRequests', { count: also }),
          actionLabel: tr('common.undo'),
          onAction: () =>
            void api
              .post<{ reverted: number[]; kept: number[] }>(`/approvals/grants/${ruleId}/undo`)
              .then((u) => {
                toast({ message: undoToast(u.reverted.length, u.kept.length) })
                void qc?.invalidateQueries({ queryKey: ['approvals'] })
              })
              .catch((e) => toastError(e, tr('common.undoFailed'))),
        })
      }
      // Wait (briefly) for an approved action to run, then let the chat continue.
      for (let i = 0; i < 30 && (status === 'approved' || status === 'executing'); i++) {
        await new Promise((res) => setTimeout(res, i < 10 ? 400 : 1000))
        const card = await api.get<ApprovalCardData>(`/approvals/${a.id}`)
        status = card.status
        onStatus(a.id, status, card)
      }
      onDecided(a.id)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const served = a.outcome && a.outcome.state === a.status ? a.outcome : null
  const outcome = served ? { ...served, text: msgText(served.msg, served.text) } : localOutcome(a.status)
  const statusIcon = {
    approved: <span key="s" className="spinner xs" />,
    executing: <span key="s" className="spinner xs" />,
    executed: <Check key="i" size={14} />,
    denied: <X key="i" size={14} />,
    expired: <Clock key="i" size={14} />,
    failed: <AlertTriangle key="i" size={14} />,
  }[a.status] as ReactElement | undefined

  const review = p.kind === 'review' ? p.review ?? null : null
  const danger = display?.danger ?? p.danger
  const title = display ? displayTitle(display) : a.title
  const verb = ACTION_TEXT[a.action] ?? a.action
  const help = scopeHelp(verb, sessionLabel)
  const also = a.also_covers
  // A card a hard rule held (the turn read mail, a review) is decided once: no grants on it.
  const grants = a.grants !== false
  return (
    <div
      className={`approval${danger ? ' danger' : ''}${pending ? '' : ' decided'}`}
      role="group"
      aria-label={pending ? tr('rail.assistant.approvalCard.approvalNeeded', { title }) : `${title}: ${outcome.text}`}
      data-approval-id={a.id}
      data-status={a.status}
    >
      <div className="ap-head">
        {(display && DISPLAY_ICON[display.icon]) ?? (a.action === 'mail.send' ? <Mail size={16} /> : <ShieldCheck size={16} />)}
        <span className="ap-title">{pending ? asQuestion(title) : title}</span>
        <button
          type="button"
          className="btn text sm ap-ask"
          aria-label={tr('rail.assistant.approvalCard.askAboutThisApproval', { name: assistantNameNow(), title })}
          data-tip={tr('rail.assistant.approvalCard.askAboutThisYouStill', { name: assistantNameNow() })}
          onClick={(e) => openAsk({ kind: 'approval', id: a.id, title }, e.currentTarget)}
        >
          <Bot size={14} aria-hidden />{' '}{tr('rail.assistant.approvalCard.ask', { name: assistantNameNow() })}
        </button>
      </div>
      {danger && pending && (
        <div className="ap-warning">
          <AlertTriangle size={14} />{' '}{tr('rail.assistant.approvalCard.thisCantBeUndoneFrom')}
        </div>
      )}
      {review ? (
        (pending || details) && <ReviewPlan review={review} changes={changes} onChanges={setChanges} editable={pending && !busy} />
      ) : display ? (
        (pending || details) && <ApprovalDisplay d={display} />
      ) : (
        <>
          {p.reason && <div className="ap-meta">{p.reason}</div>}
          {p.email ? <EmailPreview e={p.email} /> : <ThingPreview p={p} />}
          {!p.email && !p.event && !p.task && !!p.lines?.length && (
            <ul className="ap-lines">
              {p.lines.slice(0, 8).map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
        </>
      )}
      {pending ? (
        <>
          {review ? (
            <div className="ap-buttons">
              <button type="button" className="btn filled sm" disabled={busy} onClick={() => void decide('approve', 'once')}>
                {tr('rail.assistant.review.apply')}
              </button>
              <button type="button" className="btn text sm ap-deny" disabled={busy} onClick={() => void decide('deny')}>
                {tr('rail.assistant.review.discard')}
              </button>
              {changed > 0 && <span className="muted rv-changed">{tr('rail.assistant.review.changedByYou', { count: changed })}</span>}
            </div>
          ) : always ? (
            <div className="ap-always">
              <p>
                {tx('rail.assistant.approvalCard.fromNowOnLetWithout', { actorName, verb, value: DERIVED[a.action] ? ` ${DERIVED[a.action]}` : '', value2: a.action === 'mail.send' && tr('rail.assistant.approvalCard.aFirstMessageToA') }, { b: (c) => <b>{c}</b> })}
              </p>
              <div className="ap-buttons">
                <button type="button" {...grantProps('btn filled sm', tr('rail.assistant.approvalCard.createRuleAndAllow'), also?.always_derived)} disabled={busy} data-tip={help.always} onClick={() => void decide('approve', 'always')}>
                  <GrantLabel label={tr('rail.assistant.approvalCard.createRuleAndAllow')} n={also?.always_derived} />
                </button>
                <button type="button" className="btn text sm" disabled={busy} onClick={() => setAlways(false)}>
                  {tr('common.back')}
                </button>
              </div>
            </div>
          ) : (
            <div className="ap-buttons">
              <button type="button" className="btn filled sm" disabled={busy} data-tip={help.once} onClick={() => void decide('approve', 'once')}>
                {tr('rail.assistant.approvalCard.allowOnce')}
              </button>
              {grants && (
                <button type="button" {...grantProps('btn tonal sm', sessionLabel, also?.session)} disabled={busy} data-tip={help.session} onClick={() => void decide('approve', 'session')}>
                  <GrantLabel label={sessionLabel} n={also?.session} />
                </button>
              )}
              <button type="button" className="btn text sm ap-deny" disabled={busy} data-tip={help.deny} onClick={() => void decide('deny')}>
                {tr('rail.assistant.approvalCard.deny')}
              </button>
              {/* The standing rule is a rarer choice, behind ⋮ (shown in place: the card also sits
                  inside the approvals popover, where a second popover would close the first). */}
              {!grants ? null : moreOpen ? (
                <button type="button" {...grantProps('btn text sm', tr('rail.assistant.approvalCard.alwaysAllow'), also?.always_derived)} disabled={busy} data-tip={help.always} onClick={() => setAlways(true)}>
                  <GrantLabel label={tr('rail.assistant.approvalCard.alwaysAllow')} n={also?.always_derived} />
                </button>
              ) : (
                <button type="button" className="icon-btn sm ap-more" aria-label={tr('rail.assistant.approvalCard.moreChoices')} data-tip={tr('rail.assistant.approvalCard.moreChoices')} aria-expanded={false}
                  disabled={busy} onClick={() => setMoreOpen(true)}>
                  <EllipsisVertical size={16} />
                </button>
              )}
            </div>
          )}
          {err && <div className="ap-error">{err}</div>}
        </>
      ) : (
        <div className="ap-decided">
          <div className={`ap-status ${a.status}`} role="status">
            {statusIcon} {outcome.text}
          </div>
          {(review || (display && display.fields.length > 0)) && (
            <button type="button" className="btn text sm ap-details" aria-expanded={details} onClick={() => setDetails(!details)}>
              {details ? tr('rail.assistant.approvalCard.hideDetails') : tr('common.details')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
