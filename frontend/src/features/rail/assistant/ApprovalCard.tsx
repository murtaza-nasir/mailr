// An action the assistant or an agent wants to take, waiting for the user. The card's top line says
// what kind of request it is ("New task") and who asked when; its heading is the object's own title
// (two lines, "more" for the rest); the key facts sit on one line, longer parts (an email's text, a
// list) below; the reason is one short line with its whole text behind ⓘ. One row of actions: Allow
// once, Deny, the grants menu ("+1 ▾": Allow for 12 hours / this chat, Always allow…, with how many
// other waiting requests each also approves) and ⋮ (Ask the assistant). Decisions go to
// POST /approvals/{id}/decide; the card then changes in place to its outcome ("Approved · moved 6
// messages to Trash") and the chat continues after it. The same card decides in the "Waiting for
// you" bell (ApprovalsSection) and in Settings. Ask opens the ask capture with the approval as its
// chip: the assistant explains it, but only these buttons decide it.
// ACT approval.approve @web.reader, ACT approval.deny @web.reader, ACT agent.ask_about @web.panel @web.reader @web.settings

import { assistantNameNow } from './useAssistantName'
import {
  AlertTriangle, Archive, AudioLines, Bot, Brain, CalendarDays, Check, ChevronDown, Clock, EllipsisVertical, FilePen, Filter, Info, Mail, MailX, Send, Settings2,
  ShieldAlert, ShieldCheck, Sparkles, SquareCheck, StickyNote, Tag, Trash2, Users, Workflow, X,
} from 'lucide-react'
import { QueryClientContext } from '@tanstack/react-query'
import { Fragment, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement } from 'react'
import { api } from '../../../api/client'
import { Popover, MenuItem, useAnchor } from '../../../components/Popover'
import { formatRelative } from '../../../lib/format'
import { alsoLabel, scopeHelp } from '../../approvals/grant'
import { headingOf, kindLabel, reasonOf, splitFields } from '../../approvals/cardHead'
import { postDecision, type Scope } from '../../approvals/decide'
import { openAsk } from '../../ask/askStore'
import { ApprovalDisplay, Fact } from './present/ApprovalDisplay'
import { ReviewPlan, type MoveKey } from './ReviewPlan'
import { localOutcome } from './present/types'
import { displayTitle, msgText } from './present/serverText'
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

/** The heading: the object's own title, two lines at most, with "more" when it is longer. */
function Heading({ text, decided }: { text: string; decided: boolean }) {
  const ref = useRef<HTMLParagraphElement>(null)
  const id = useId()
  const [all, setAll] = useState(false)
  const [clamped, setClamped] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || all) return
    const check = () => setClamped(isClamped(el))
    check()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(check)
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [text, all])
  return (
    <div className="ap-heading-row">
      <p ref={ref} id={id} className={`ap-heading${all ? '' : ' clamp'}${decided ? ' decided' : ''}`} dir="auto">{text}</p>
      {(clamped || all) && (
        <button type="button" className="pv-link ap-heading-more" aria-expanded={all} aria-controls={id} onClick={() => setAll(!all)}>
          {all ? tr('rail.assistant.present.approvalDisplay.showLess') : tr('approvals.cards.more')}
        </button>
      )}
    </div>
  )
}

/** Whether a clamped heading hides some of its text. */
export function isClamped(el: HTMLElement): boolean {
  return el.scrollHeight > el.clientHeight + 1
}

/** "Asks first: written after reading email", with the whole reason behind ⓘ. */
function Reason({ short, full }: { short: string; full: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const more = full && full !== short
  return (
    <div className="ap-reason">
      <div className="ap-reason-line">
        <span className="ap-reason-short">{short}</span>
        {more && (
          <button type="button" className="icon-btn xs ap-reason-info" aria-label={tr('approvals.cards.whyItAsks')} data-tip={tr('approvals.cards.whyItAsks')}
            aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
            <Info size={14} aria-hidden />
          </button>
        )}
      </div>
      {more && open && <p id={id} className="ap-reason-full" dir="auto">{full}</p>}
    </div>
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
  /** Who asked and when, on the card's top line ("claude-code · 1 hour ago"); the chat leaves it out. */
  requester?: string
  askedAt?: string | null
  /** When the request expires, on the top line too (Settings). */
  expiresAt?: string | null
}

export function ApprovalCard({ a, onStatus, onDecided, actorName = assistantNameNow(), sessionLabel = tr('rail.assistant.approvalCard.allowForThisChat'), requester, askedAt, expiresAt }: Props) {
  const [busy, setBusy] = useState(false)
  const [always, setAlways] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [details, setDetails] = useState(false)
  const grantsMenu = useAnchor()
  const moreMenu = useAnchor()
  // A review card (agent/review.py): the user's own moves on it, sent with Apply (apply_changes).
  const [changes, setChanges] = useState<Record<number, MoveKey>>({})
  const changed = Object.keys(changes).length
  // The app's query cache, when there is one (the card also renders on its own in tests).
  const qc = useContext(QueryClientContext)
  const name = assistantNameNow()
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

  const decide = async (decision: 'approve' | 'deny', scope: Scope = 'once') => {
    setBusy(true)
    setErr(null)
    try {
      const r = await postDecision(a.id, decision, scope, sessionLabel, qc,
        review && changed && decision === 'approve' ? { changes } : {})
      let status = r.status
      onStatus(a.id, status, r)
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
  const heading = headingOf({ action: a.action, title: a.title, display, head: a.head })
  const kind = kindLabel(a) ?? ACTION_TEXT[a.action] ?? null
  const reason = display ? reasonOf({ action: a.action, display, head: a.head }) : null
  const split = display ? splitFields(display.fields, heading) : null
  const verb = ACTION_TEXT[a.action] ?? a.action
  const help = scopeHelp(verb, sessionLabel)
  const also = a.also_covers
  // How many other waiting requests the grants would approve too: on the grants menu's button.
  const alsoN = also?.session || also?.always_derived || 0
  // A card a hard rule held (the turn read mail, a review) is decided once: no grants on it.
  const grants = a.grants !== false
  const when = [askedAt ? formatRelative(askedAt) : '', expiresAt ? tr('approvals.cards.expires', { relative: formatRelative(expiresAt) }) : '']
    .filter(Boolean).join(' · ')
  const askLabel = tr('rail.assistant.approvalCard.ask', { name })
  return (
    <div
      className={`approval${danger ? ' danger' : ''}${pending ? '' : ' decided'}`}
      role="group"
      aria-label={pending ? tr('rail.assistant.approvalCard.approvalNeeded', { title }) : `${title}: ${outcome.text}`}
      data-approval-id={a.id}
      data-status={a.status}
    >
      <div className="ap-top">
        <span className="ap-icon" aria-hidden>
          {(display && DISPLAY_ICON[display.icon]) ?? (a.action === 'mail.send' ? <Mail size={14} /> : <ShieldCheck size={14} />)}
        </span>
        {kind && <span className="ap-kind">{kind}</span>}
        {(requester || when) && (
          <span className="ap-who" dir="auto" data-testid="approval-who" title={[requester, when].filter(Boolean).join(' · ')}>
            {requester && <bdi>{requester}</bdi>}
            {requester && when ? ' · ' : ''}
            {when}
          </span>
        )}
      </div>
      <Heading text={heading} decided={!pending} />
      {danger && pending && (
        <div className="ap-warning">
          <AlertTriangle size={14} />{' '}{tr('rail.assistant.approvalCard.thisCantBeUndoneFrom')}
        </div>
      )}
      {review ? (
        (pending || details) && <ReviewPlan review={review} changes={changes} onChanges={setChanges} editable={pending && !busy} />
      ) : display && split ? (
        (pending || details) && (
          <>
            {split.facts.length > 0 && (
              <p className="ap-facts" data-testid="approval-facts">
                {split.facts.map((f, i) => (
                  <Fragment key={i}>
                    {i > 0 && <span className="ap-sep" aria-hidden> · </span>}
                    <Fact f={f} />
                  </Fragment>
                ))}
              </p>
            )}
            <ApprovalDisplay d={display} fields={split.blocks} reason={false} />
          </>
        )
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
      {pending && reason && <Reason short={reason.short} full={reason.full} />}
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
              <span className="ap-spacer" />
              <MoreMenu menu={moreMenu} label={askLabel} onAsk={(el) => openAsk({ kind: 'approval', id: a.id, title }, el)}
                askName={tr('rail.assistant.approvalCard.askAboutThisApproval', { name, title })} />
            </div>
          ) : always ? (
            <div className="ap-always">
              <p>
                {tx('rail.assistant.approvalCard.fromNowOnLetWithout', { actorName, verb, value: DERIVED[a.action] ? ` ${DERIVED[a.action]}` : '', value2: a.action === 'mail.send' && tr('rail.assistant.approvalCard.aFirstMessageToA') }, { b: (c) => <b>{c}</b> })}
              </p>
              <div className="ap-buttons">
                <button type="button" className="btn filled sm ap-grant" aria-label={`${tr('rail.assistant.approvalCard.createRuleAndAllow')}${alsoLabel(also?.always_derived)}`}
                  disabled={busy} data-tip={help.always} onClick={() => void decide('approve', 'always')}>
                  <span>{tr('rail.assistant.approvalCard.createRuleAndAllow')}</span>
                  {alsoLabel(also?.always_derived) && <span className="ap-also">{alsoLabel(also?.always_derived).trim()}</span>}
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
              <button type="button" className="btn text sm ap-deny" disabled={busy} data-tip={help.deny} onClick={() => void decide('deny')}>
                {tr('rail.assistant.approvalCard.deny')}
              </button>
              <span className="ap-spacer" />
              {grants && (
                <>
                  <button type="button" className="btn tonal sm ap-grants" disabled={busy} aria-haspopup="menu" aria-expanded={grantsMenu.open}
                    aria-label={alsoN ? tr('approvals.cards.moreWaysToAllowAlso', { count: alsoN }) : tr('approvals.cards.moreWaysToAllow')}
                    data-tip={alsoN ? tr('approvals.cards.moreWaysToAllowAlso', { count: alsoN }) : tr('approvals.cards.moreWaysToAllow')}
                    onClick={(e) => grantsMenu.toggle(e.currentTarget)}>
                    {alsoN > 0 && <span>{tr('approvals.cards.plusN', { n: alsoN })}</span>}
                    <ChevronDown size={14} aria-hidden />
                  </button>
                  <Popover anchor={grantsMenu.anchor} open={grantsMenu.open} onClose={grantsMenu.close} placement="bottom-end"
                    label={tr('approvals.cards.moreWaysToAllow')}>
                    <MenuItem tip={help.session} onSelect={() => { grantsMenu.close(); void decide('approve', 'session') }}>
                      <GrantItem label={sessionLabel} n={also?.session} />
                    </MenuItem>
                    <MenuItem tip={help.always} onSelect={() => { grantsMenu.close(); setAlways(true) }}>
                      <GrantItem label={tr('rail.assistant.approvalCard.alwaysAllow')} n={also?.always_derived} />
                    </MenuItem>
                  </Popover>
                </>
              )}
              <MoreMenu menu={moreMenu} label={askLabel} onAsk={(el) => openAsk({ kind: 'approval', id: a.id, title }, el)}
                askName={tr('rail.assistant.approvalCard.askAboutThisApproval', { name, title })} />
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

/** A grant in the grants menu: its name, and under it how many other waiting requests it also approves. */
export function GrantItem({ label, n }: { label: string; n: number | undefined }) {
  const more = alsoLabel(n)
  return (
    <span className="ap-grant-item">
      <span>{label}</span>
      {more && <span className="ap-also">{more}</span>}
    </span>
  )
}

/** ⋮ on a card: Ask the assistant about it (it explains; only the card's buttons decide). */
function MoreMenu({ menu, label, askName, onAsk }: { menu: ReturnType<typeof useAnchor>; label: string; askName: string; onAsk: (el: HTMLElement) => void }) {
  const opener = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={opener} type="button" className="icon-btn sm ap-more" aria-label={tr('rail.assistant.approvalCard.moreChoices')}
        data-tip={tr('rail.assistant.approvalCard.moreChoices')} aria-haspopup="menu" aria-expanded={menu.open}
        onClick={(e) => menu.toggle(e.currentTarget)}>
        <EllipsisVertical size={16} />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="bottom-end" label={tr('rail.assistant.approvalCard.moreChoices')}>
        <MenuItem icon={<Bot size={16} />} tip={askName} onSelect={() => { menu.close(); if (opener.current) onAsk(opener.current) }}>
          {label}
        </MenuItem>
      </Popover>
    </>
  )
}
