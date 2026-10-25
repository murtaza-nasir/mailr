// Calendar in the reader: InviteCard above an invitation (invite.view) and the confirm step of
// "Create event" / "Add to tasks" from a message (mail.to_event, mail.to_task), which now opens the
// Calendar app's editor and the task editor. A calendar file that is not an invitation gets
// "Add to calendar" with a calendar picker (event.import_ics).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarCheck, CalendarClock, CalendarPlus, CircleCheck, Info, MapPin, MessageSquare, TriangleAlert, Users } from 'lucide-react'
import { useMemo, useState, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router'
import { api, API } from '../../api/client'
import { useSettings } from '../../api/hooks'
import type { EventChange, Message } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog } from '../../components/Dialog'
import { mixedScript, nameAddressMismatch, safeText } from '../../lib/safeText'
import { RSVP_TEXT } from '../../lib/calendarForm'
import { eventPath } from '../../lib/routes'
import { addDaysISO, formatDate, formatTime, isISODate, parseISO, resolveDatePrefs, startOfDay } from '../../lib/dates'
import { toast, toastError } from '../../stores/ui'
import { isMissingRoute, useCollections, useRange } from '../calendar/data'
import type { ApiEvent } from '../calendar/types'
// The task editor loads when "Add to tasks" first opens it.
const TaskEditor = lazy(() => import('../rail/QuickSheet').then((m) => ({ default: m.TaskEditor })))
import { isInvitationMethod, parseIcs } from './icsImport'
import { conflictText, dayOf, diffLines, guestName, moment, organizerTime, overlaps, previousLine, proposalText, timeline, type Invite, type Itip } from './inviteLogic'
import { MailEventEditor } from './ScheduleMeeting'
import './calendar-bits.css'
import { t } from '../../i18n'

type Rsvp = 'accepted' | 'tentative' | 'declined'
const enc = encodeURIComponent

function usePrefs() {
  const s = useSettings() as unknown as { timezone?: string; week_start?: number; time_format?: string }
  return useMemo(() => resolveDatePrefs(s), [s])
}

/** "Tue 6 Oct, 3:00 – 4:00 PM" in my zone. */
function whenText(inv: Pick<Invite, 'start' | 'end'>, p: ReturnType<typeof usePrefs>): string {
  if (!inv.start) return ''
  if (isISODate(inv.start)) {
    const last = inv.end && isISODate(inv.end) ? addDaysISO(inv.end.slice(0, 10), -1) : inv.start.slice(0, 10)
    return last > inv.start.slice(0, 10) ? t('conversation.calendarBits.allDay', { date: formatDate(inv.start.slice(0, 10), 'day'), date2: formatDate(last, 'day') }) : t('conversation.calendarBits.allDay2', { date: formatDate(inv.start.slice(0, 10), 'day') })
  }
  const s = parseISO(inv.start, p.zone)
  const e = inv.end ? parseISO(inv.end, p.zone) : null
  if (!s) return ''
  const sameDay = e && formatDate(e, 'day', p.zone) === formatDate(s, 'day', p.zone)
  return t('conversation.calendarBits.text', { date: formatDate(s, 'day', p.zone), time: formatTime(s, p), value: e ? ` – ${sameDay ? formatTime(e, p) : `${formatDate(e, 'day', p.zone)}, ${formatTime(e, p)}`}` : '' })
}

/** The invitation card above an iMIP message. ACT invite.view @web.reader */
export function InviteCard({ m }: { m: Message }) {
  const q = useQuery<Invite>({ queryKey: ['invite', m.id], queryFn: ({ signal }) => api.get<Invite>(`/messages/${m.id}/invite`, undefined, signal), retry: false, staleTime: 60_000 })
  if (q.isLoading)
    return (
      <div className="invite-card">
        <Skeleton width="60%" />
      </div>
    )
  const i = q.data
  if (!i || q.isError) return null
  const plainFile = !isInvitationMethod(i.method) && !i.my_status && !i.itip
  if (plainFile) return <ImportCard m={m} inv={i} />
  return <InviteBody m={m} i={i} />
}

function InviteBody({ m, i }: { m: Message; i: Invite }) {
  const qc = useQueryClient()
  const p = usePrefs()
  const key = ['invite', m.id]
  const [note, setNote] = useState<string | null>(null)
  const [declinedProposal, setDeclinedProposal] = useState(() => {
    try {
      return localStorage.getItem(`mailr.counter.kept.${m.id}`) === '1'
    } catch {
      return false
    }
  })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: key })
    void qc.invalidateQueries({ queryKey: ['calendar'] })
  }
  // My day around the slot: the conflict line and the mini timeline.
  const day = i.start ? dayOf(i.start, p.zone) : null
  const from = useMemo(() => (day ? startOfDay(day, p.zone) : new Date(0)), [day, p.zone])
  const to = useMemo(() => (day ? startOfDay(addDaysISO(day, 1), p.zone) : new Date(0)), [day, p.zone])
  const range = useRange(from, to, !!day && !i.cancelled && i.method !== 'REPLY')
  // The invitation's own copy in the calendar never counts as a clash with itself.
  const match = i.calendar_match ?? null
  const dayEvents = (range.data?.events ?? []).filter((e) => !match || e.uid !== match.uid)
  const clash = i.start && !i.cancelled ? overlaps(dayEvents, i.start, i.end, i.uid, p.zone) : []
  const tl = i.start && !i.cancelled && i.method !== 'REPLY' && i.method !== 'COUNTER' ? timeline(dayEvents, i.start, i.end, i.uid, p.zone) : null
  // An update: what changed against my calendar copy (before the ingest job applied it).
  const mine = useQuery<ApiEvent | null>({
    queryKey: ['calendar', 'event', i.uid, null],
    queryFn: async ({ signal }) => {
      try {
        return await api.get<ApiEvent>(`/calendar/events/${enc(i.uid)}`, undefined, signal)
      } catch {
        return null
      }
    },
    enabled: i.in_calendar && i.method === 'REQUEST' && !i.cancelled && !i.recurring,
    staleTime: 30_000,
  })
  // After the ingest job applied an update, my copy matches it; the record says where it was.
  const was = previousLine(i, p)
  const diff = [...(was ? [was] : []), ...(mine.data ? diffLines(i, { ...mine.data, location: mine.data.location ?? '' }, p) : []).filter((l) => !was || !l.startsWith(t('conversation.inviteLogic.timeChanged')))]
  const theirTime = organizerTime(i.start, i.tz ?? i.itip?.organizer_tz, p)

  const reply = useMutation({
    // ACT event.rsvp @web.reader
    mutationFn: (response: Rsvp) => api.post<{ calendar: unknown; outbox: unknown }>('/calendar/invite-reply', { message_id: m.id, response, comment: note?.trim() ?? '' }),
    onSuccess: (r, response) => {
      const calFailed = typeof r?.calendar === 'string' && r.calendar.startsWith('error')
      toast({ message: t('conversation.calendarBits.sentToTheOrganiser', { value: response === 'accepted' ? t('common.yes') : response === 'declined' ? t('common.no') : t('common.maybe'), value2: calFailed ? t('conversation.calendarBits.yourCalendarCouldNotBe') : '' }) })
      setNote(null)
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.replyNotSent')),
  })
  // Adding without answering: the event goes into the calendar as "maybe" and the organiser is not told.
  // Going through the invitation (not a fresh event) keeps its UID, so a later Yes/No updates the same event.
  const add = useMutation({
    mutationFn: () => api.post('/calendar/invite-reply', { message_id: m.id, response: 'tentative', send: false }),
    onSuccess: () => {
      toast({ message: t('conversation.calendarBits.addedToYourCalendarAs') })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.notAdded')),
  })
  const nav = useNavigate()
  // ACT invite.link_existing @web.reader: an event that is this invitation under another UID takes the
  // invitation's identity in place (organizer updates reach it); Undo puts the old copy back.
  // A match by time and title only: linking makes this invitation's organizer the organizer of the
  // user's own event, so the user first confirms that organizer, shown with its address.
  const [confirmLink, setConfirmLink] = useState<string | null>(null)
  const link = useMutation({
    mutationFn: ({ uid, organizer }: { uid: string; organizer?: string }) =>
      api.post<{ event: ApiEvent; undo: { path: string } }>(`/messages/${m.id}/invite/link`, organizer ? { uid, organizer } : { uid }),
    onSuccess: (r) => {
      toast({
        message: t('conversation.calendarBits.linkedToTheInvitation'),
        actionLabel: t('common.undo'),
        onAction: () => void api.post(r.undo.path).then(refresh).catch((e) => toastError(e, t('common.notChanged'))),
      })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.notLinked')),
  })
  // ACT invite.apply_cancel @web.reader ("Remove from calendar" after the organizer cancelled)
  // ACT invite.remove_cancelled @web.reader: never silently; the copy is kept on the message and the toast's Undo puts it back.
  const remove = useMutation({
    mutationFn: () => api.post<{ undo: { path: string } }>(`/messages/${m.id}/itip/remove`),
    onSuccess: (r) => {
      toast({
        message: t('conversation.calendarBits.removedFromYourCalendar'),
        actionLabel: t('common.undo'),
        onAction: () => void api.post(r.undo.path).then(refresh).catch((e) => toastError(e, t('common.notChanged'))),
      })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.notRemoved')),
  })
  // ACT invite.apply_unverified @web.reader: a change kept back because its sender could not be verified
  const applyUnverified = useMutation({
    mutationFn: () => api.post<Itip>(`/messages/${m.id}/itip/apply`),
    onSuccess: (r) => {
      toast({ message: r?.applied ? t('conversation.calendarBits.yourCalendarWasUpdated') : r?.detail || t('conversation.calendarBits.nothingWasChanged') })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.notApplied')),
  })
  // ACT event.accept_proposal @web.reader: the card shows who gets the updated invitation, and sends
  // that list; a proposal from an unverified sender needs "I trust this" first.
  const cc = i.counter_check
  const [trustAsk, setTrustAsk] = useState(false)
  const accept = useMutation({
    mutationFn: (trust: boolean) => api.post<ApiEvent>(`/calendar/events/${enc(i.uid)}/counter/accept`,
      { message_id: m.id, ...(cc ? { notify: cc.notify } : {}), ...(trust ? { trust: true } : {}) }),
    onSuccess: () => {
      setTrustAsk(false)
      toast({ message: t('conversation.calendarBits.movedToTheGuestsGet', { moment: moment(i.itip?.proposed?.start, p) }) })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.theNewTimeWasNot')),
  })

  // ACT event.decline_proposal @web.reader: the guest gets a DECLINECOUNTER (POST …/counter/decline); a
  // server without the route keeps the time and the user may reply in words.
  const decline = useMutation({
    mutationFn: async () => {
      try {
        await api.post(`/calendar/events/${enc(i.uid)}/counter/decline`, { message_id: m.id })
        return true
      } catch (e) {
        if (isMissingRoute(e)) return false
        throw e
      }
    },
    onSuccess: (sent) => {
      setDeclinedProposal(true)
      try {
        localStorage.setItem(`mailr.counter.kept.${m.id}`, '1')
      } catch {
        /* private mode */
      }
      toast({ message: sent ? t('conversation.calendarBits.theTimeStaysAsIt', { guestName: guestName(i, m.from.email) }) : t('conversation.calendarBits.theTimeStaysAsIt2') })
      refresh()
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.theProposalWasNotDeclined')),
  })

  const orgName = safeText(i.organizer.name).trim()
  const orgOther = nameAddressMismatch(orgName, i.organizer.email)
  const orgWarning = orgOther
    ? t('conversation.calendarBits.organizerNameShowsOther', { other: orgOther, email: i.organizer.email })
    : mixedScript(orgName) || mixedScript(i.organizer.email) ? t('conversation.calendarBits.organizerMixesLetters') : null
  // Linking needs an organizer the server can stand behind: verified, or the user's own forward.
  const canLink = i.sender === 'organizer' || i.sender === 'forwarded'
  const isRequest = !i.cancelled && (i.method === 'REQUEST' || i.method === '' || i.method === 'PUBLISH')
  const current = (i.my_status ?? '').toUpperCase()
  const busy = reply.isPending || add.isPending
  const it = i.itip
  const proposal = !declinedProposal ? proposalText(i, m.from.email, p) : null
  const guests = i.attendees.length
  const outcome = it?.detail && !(it.method === 'COUNTER' && proposal)
    ? it.method === 'REPLY' && it.applied && !it.detail.includes('—') ? t('conversation.calendarBits.calendarUpdated', { guestName: guestName(i, it.attendee), string: String(it.partstat ?? '').toLowerCase() }) : it.detail
    : null

  return (
    <section className={`invite-card${i.cancelled ? ' cancelled' : ''}`} aria-label={t('conversation.calendarBits.invitation')} data-testid="invite-card">
      <div className="invite-head">
        <CalendarCheck aria-hidden />
        <div className="invite-title">
          <b>{i.cancelled ? t('conversation.calendarBits.cancelled', { summary: i.summary }) : i.summary || m.subject}</b>
          <div className="muted">
            {whenText(i, p)}
            {theirTime ? t('conversation.calendarBits.forTheOrganizer', { theirTime }) : ''}
            {i.recurring ? ' · repeats' : ''}
          </div>
          {i.location && (
            <div className="muted invite-where">
              <MapPin size={14} aria-hidden /> {i.location}
            </div>
          )}
          {i.organizer.email && (
            <div className="muted invite-where" data-testid="invite-organizer">
              {/* The address always, beside the name the invitation gives. */}
              <Users size={14} aria-hidden /> {orgName ? <><bdi>{orgName}</bdi> &lt;<bdi>{i.organizer.email}</bdi>&gt;</> : <bdi>{i.organizer.email}</bdi>}
              {guests ? t('conversation.calendarBits.guests', { count: guests }) : ''}
            </div>
          )}
          {orgWarning && (
            <div className="invite-warning" role="note" data-testid="invite-organizer-warning">
              <TriangleAlert size={14} aria-hidden /> {orgWarning}
            </div>
          )}
          {i.sender === 'forwarded' && i.organizer.email && (
            <div className="muted invite-where" data-testid="invite-forwarded">
              <Info size={14} aria-hidden /> {t('conversation.calendarBits.forwardedClaims', { address: i.organizer.email })}
            </div>
          )}
        </div>
      </div>

      {/* The sender's own definition of a zone was too costly to use. */}
      {i.zones_replaced?.length ? (
        <div className="muted invite-zone-note" data-testid="invite-zone-note">
          <Info size={14} aria-hidden /> {t('conversation.calendarBits.zoneReplaced', { zone: i.zones_replaced[0] })}
        </div>
      ) : null}
      {/* ACT invite.apply_reply @web.reader, ACT invite.apply_update @web.reader, ACT invite.apply_cancel: what the ingest job did */}
      {outcome && (
        <div className={`invite-status${it?.applied ? ' ok' : ''}`} role="status" data-testid="invite-outcome">
          {it?.applied ? <CircleCheck size={15} aria-hidden /> : <Info size={15} aria-hidden />}
          <span>{outcome}{it?.comment ? ` · “${it.comment}”` : ''}</span>
          {it?.pending && (
            <button type="button" className="btn text sm" data-testid="invite-apply-unverified"
              disabled={applyUnverified.isPending} onClick={() => applyUnverified.mutate()}>
              {t('conversation.calendarBits.applyAnyway')}
            </button>
          )}
        </div>
      )}
      {/* ACT invite.apply_update: an update not applied yet, against my copy */}
      {diff.length > 0 && (
        <ul className="invite-diff" aria-label={t('conversation.calendarBits.whatChanged')}>
          {diff.map((d) => <li key={d}>{d}</li>)}
        </ul>
      )}
      {clash.length > 0 && (
        <div className="invite-conflicts" data-testid="invite-conflict">
          <TriangleAlert size={14} aria-hidden /> {conflictText(clash)}
        </div>
      )}
      {tl && <MiniTimeline tl={tl} prefs={p} />}

      {proposal && (
        <div className="invite-proposal" data-testid="invite-proposal">
          <span>{proposal}</span>
          <button type="button" className="btn filled sm" disabled={accept.isPending || trustAsk}
            onClick={() => (cc && !cc.verified ? setTrustAsk(true) : accept.mutate(false))}>{t('common.accept')}</button>
          {/* ACT invite.counter @web.reader, ACT event.decline_proposal */}
          <button type="button" className="btn outlined sm" disabled={decline.isPending} onClick={() => decline.mutate()}>{t('conversation.calendarBits.decline')}</button>
        </div>
      )}
      {proposal && cc && (
        <div className="invite-proposal-check" data-testid="invite-proposal-check">
          {!cc.verified && (
            <div className="invite-proposal-warning" role="note" data-testid="invite-proposal-unverified">
              <TriangleAlert size={14} aria-hidden /> {t('conversation.calendarBits.proposalUnverified', { address: cc.sender })}
            </div>
          )}
          <div className="muted" data-testid="invite-proposal-notify">
            {t('conversation.calendarBits.proposalNotify', { count: cc.notify.length, addresses: cc.notify.join(', ') })}
          </div>
          {trustAsk && (
            <div className="invite-actions" role="group" data-testid="invite-proposal-trust">
              <button type="button" className="btn tonal sm" disabled={accept.isPending} onClick={() => accept.mutate(true)}>
                {t('conversation.calendarBits.proposalTrust')}
              </button>
              <button type="button" className="btn text sm" onClick={() => setTrustAsk(false)}>{t('common.cancel')}</button>
            </div>
          )}
        </div>
      )}

      {/* "The organizer cancelled" and Remove only for a verified cancellation (the server checks it
          again). Otherwise the card says nobody verified it. */}
      {i.cancelled && i.in_calendar && (i.cancel_verified ? (
        <div className="invite-actions">
          <span className="muted">{t('conversation.calendarBits.theOrganizerCancelledThisEvent')}</span>
          <button type="button" className="btn outlined sm" disabled={remove.isPending} onClick={() => remove.mutate()}>{t('conversation.calendarBits.removeFromCalendar')}</button>
        </div>
      ) : (
        <div className="invite-actions" data-testid="invite-cancel-unverified">
          <span className="muted">{t('conversation.calendarBits.cancelNotVerified')}</span>
        </div>
      ))}

      {isRequest && i.my_status != null && (
        <>
          <div className="invite-actions" role="group" aria-label={t('common.going')}>
            <span className="muted">{RSVP_TEXT[current] ?? t('common.going')}</span>
            {(['accepted', 'tentative', 'declined'] as Rsvp[]).map((r) => (
              <button
                key={r}
                type="button"
                className={`btn sm ${current === r.toUpperCase() ? 'tonal' : 'outlined'}`}
                aria-pressed={current === r.toUpperCase()}
                disabled={busy}
                onClick={() => reply.mutate(r)}
              >
                {r === 'accepted' ? t('common.yes') : r === 'declined' ? t('common.no') : t('common.maybe')}
              </button>
            ))}
            {note == null && (
              <button type="button" className="btn text sm" onClick={() => setNote('')}>
                <MessageSquare size={16} />{' '}{t('conversation.calendarBits.addANote')}
              </button>
            )}
            {!i.in_calendar && !match && i.start && (
              <button type="button" className="btn text sm" disabled={busy} onClick={() => add.mutate()} data-tip={t('conversation.calendarBits.addsItAsMaybeWithout')}>
                <CalendarPlus />{' '}{t('conversation.calendarBits.addToCalendar')}
              </button>
            )}
            {(i.in_calendar || match) && (
              <span className="invite-mine" data-testid="invite-in-calendar">
                <span className="muted">{t('conversation.calendarBits.inYourCalendar')}</span>
                {match && (
                  <button type="button" className="btn text sm" onClick={() => nav(eventPath(match.uid, match.occurrence ?? ''))}>{t('common.open')}</button>
                )}
                {match && match.by !== 'uid' && canLink && (
                  <button type="button" className="btn text sm" disabled={link.isPending} data-testid="invite-link"
                    onClick={() => (match.by === 'link' ? link.mutate({ uid: match.uid }) : setConfirmLink(match.uid))}
                    data-tip={t('conversation.calendarBits.linkTip')}>
                    {t('conversation.calendarBits.linkToThisInvite')}
                  </button>
                )}
              </span>
            )}
          </div>
          <ConfirmDialog
            open={confirmLink !== null}
            title={t('conversation.calendarBits.linkConfirmTitle')}
            body={t('conversation.calendarBits.linkConfirmBody', { organizer: orgName ? `${orgName} <${i.organizer.email}>` : i.organizer.email })}
            confirmLabel={t('conversation.calendarBits.linkToThisInvite')}
            busy={link.isPending}
            onClose={() => setConfirmLink(null)}
            onConfirm={() => {
              if (confirmLink) link.mutate({ uid: confirmLink, organizer: i.organizer.email })
              setConfirmLink(null)
            }}
          />
          {note != null && (
            <textarea className="textarea invite-note" rows={2} autoFocus placeholder={t('conversation.calendarBits.aNoteForTheOrganiser')} aria-label={t('conversation.calendarBits.noteForTheOrganiser')}
              value={note} onChange={(e) => setNote(e.target.value)} />
          )}
        </>
      )}
    </section>
  )
}

function MiniTimeline({ tl, prefs }: { tl: NonNullable<ReturnType<typeof timeline>>; prefs: ReturnType<typeof usePrefs> }) {
  const pct = (f: number) => `${(f * 100).toFixed(2)}%`
  return (
    <div className="invite-tl" aria-label={t('conversation.calendarBits.yourDayAroundThisTime')} role="img">
      <div className="invite-tl-track">
        {tl.hours.slice(1, -1).map((h, n) => <span key={h} className="invite-tl-tick" style={{ insetInlineStart: pct((n + 1) / (tl.hours.length - 1)) }} />)}
        {tl.blocks.map((b, n) => (
          <span key={n} className={`invite-tl-block${b.clash ? ' clash' : ''}`} style={{ insetInlineStart: pct(b.left), width: pct(b.width) }} title={b.title}>
            <span>{b.title}</span>
          </span>
        ))}
        <span className="invite-tl-slot" style={{ insetInlineStart: pct(tl.slot.left), width: pct(tl.slot.width) }} />
      </div>
      <div className="invite-tl-hours" aria-hidden>
        {tl.hours.map((h, n) => (
          <span key={h} style={{ insetInlineStart: pct(n / (tl.hours.length - 1)) }}>{formatTime(new Date(h), prefs, { compact: true })}</span>
        ))}
      </div>
    </div>
  )
}

/** An .ics file that is not an invitation: "Add to calendar" with a calendar picker. ACT event.import_ics @web.reader */
function ImportCard({ m, inv }: { m: Message; inv: Invite }) {
  const qc = useQueryClient()
  const p = usePrefs()
  const cols = useCollections().data ?? []
  const writable = cols.filter((c) => c.events !== false && !c.read_only && !c.error)
  const [cal, setCal] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<number | null>(null)
  const target = cal || writable[0]?.url || writable[0]?.id || ''
  const run = async () => {
    setBusy(true)
    try {
      let created: { uid: string }[] = []
      let skipped = 0
      const att = m.attachments.find((a) => /^(text\/calendar|application\/ics)$/i.test(a.type) || /\.ics$/i.test(a.filename))
      try {
        // POST /calendar/import: every VEVENT as the file wrote it; UIDs already there are skipped.
        const part = att && /^\d+$/.test(String(att.part)) ? Number(att.part) : undefined
        const r = await api.post<{ imported?: { uid: string }[]; skipped?: unknown[]; items?: { uid: string }[] }>('/calendar/import',
          { message_id: m.id, calendar: target || undefined, ...(part != null ? { part } : {}) })
        created = r.imported ?? r.items ?? []
        skipped = r.skipped?.length ?? 0
      } catch (e) {
        if (!isMissingRoute(e)) throw e
        // The server cannot import yet: read the file here and add each event.
        if (!att) throw new Error(t('conversation.calendarBits.theCalendarFileWasNot'), { cause: e })
        const res = await fetch(`${API}/messages/${m.id}/attachments/${att.part}`, { credentials: 'include' })
        if (!res.ok) throw new Error(t('conversation.calendarBits.theCalendarFileCouldNot', { status: res.status }), { cause: e })
        const events = parseIcs(await res.text())
        for (const ev of events) {
          const { uid: _uid, ...body } = ev
          created.push(await api.post<{ uid: string }>('/calendar/events', { ...body, calendar: target || undefined, conversation_id: m.conversation_id }))
        }
      }
      setDone(created.length)
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      const name = writable.find((c) => (c.url ?? c.id) === target)?.name ?? t('conversation.calendarBits.yourCalendar')
      toast({
        message: t('conversation.calendarBits.addedEventsTo', { count: created.length, itemName: name, value: skipped ? t('conversation.calendarBits.alreadyThere', { skipped }) : '' }),
        actionLabel: created.length ? t('common.undo') : undefined,
        onAction: created.length ? () => void Promise.all(created.map((c) => api.del(`/calendar/events/${enc(c.uid)}?send_updates=false`))).then(() => {
          setDone(null)
          void qc.invalidateQueries({ queryKey: ['calendar'] })
        }) : undefined,
      })
    } catch (e) {
      toastError(e, t('conversation.calendarBits.notAdded'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="invite-card" aria-label={t('conversation.calendarBits.calendarFile')} data-testid="import-card">
      <div className="invite-head">
        <CalendarPlus aria-hidden />
        <div className="invite-title">
          <b dir="auto">{inv.summary || m.subject}</b>
          <div className="muted">{whenText(inv, p)}{inv.recurring ? ' · repeats' : ''}</div>
          {inv.location && <div className="muted invite-where"><MapPin size={14} aria-hidden /> {inv.location}</div>}
        </div>
      </div>
      <div className="invite-actions">
        {done != null ? (
          <span className="muted">{t('conversation.calendarBits.addedToYourCalendar')}</span>
        ) : (
          <>
            {writable.length > 1 && (
              <select className="select sm" aria-label={t('common.calendar')} value={target} onChange={(e) => setCal(e.target.value)}>
                {writable.map((c) => <option key={c.url ?? c.id} value={c.url ?? c.id}>{c.name}</option>)}
              </select>
            )}
            <button type="button" className="btn tonal sm" disabled={busy || !writable.length} onClick={() => void run()}>
              <CalendarPlus />{' '}{t('conversation.calendarBits.addToCalendar')}
            </button>
            {!writable.length && <span className="muted">{t('conversation.calendarBits.connectACalendarFirst')}</span>}
          </>
        )}
      </div>
    </section>
  )
}

interface Draft {
  title: string
  start: string | null
  end: string | null
  all_day?: boolean
  due: string | null
  location: string
  notes: string
}

/**
 * "Create event" / "Add to tasks": the suggestion (heuristic, then a model; nothing is saved) opens
 * the Calendar app's editor or the task editor, prefilled and linked back to the email.
 * ACT mail.to_event @web.reader, ACT mail.to_task @web.reader
 */
export function FromMessageDialog({ m, kind, onClose }: { m: Message; kind: 'event' | 'task' | null; onClose: () => void }) {
  const suggest = useQuery<{ draft: Draft; source: string }>({
    queryKey: ['from-message', m.id, kind],
    queryFn: () => api.post('/calendar/from-message', { message_id: m.id, kind }),
    enabled: !!kind,
    staleTime: Infinity,
    retry: false,
  })
  if (!kind) return null
  if (suggest.isLoading)
    return (
      <div className="scrim" role="presentation">
        <div className="dialog fm-wait" role="status" aria-label={t('conversation.calendarBits.readingTheEmail')}>
          <Skeleton width="70%" />
          <Skeleton width="50%" style={{ marginTop: 8 }} />
          <p className="muted small">{t('conversation.calendarBits.readingTheEmailForA')}</p>
        </div>
      </div>
    )
  const d = suggest.data?.draft
  const link = `${window.location.origin}/mail/all/${m.conversation_id}`
  const notes = [d?.notes, suggest.data?.source && suggest.data.source !== 'heuristic' ? '' : null].filter(Boolean).join('\n')
  if (kind === 'task')
    return (
      <Suspense fallback={null}>
        <TaskEditor task={null} onClose={onClose} heading={t('conversation.calendarBits.addToTasks')} saveLabel={t('common.addTask')} conversationId={m.conversation_id}
          seed={{ summary: d?.title ?? m.subject, notes, due: d?.due ?? null }} />
      </Suspense>
    )
  // The suggestion's times, made sane: an all-day end is exclusive and after the start; a timed
  // end after the start (else an hour).
  const allDay = !!d?.start && (!!d.all_day || isISODate(d.start))
  const start = d?.start ? (allDay ? d.start.slice(0, 10) : parseISO(d.start) ?? undefined) : undefined
  let end: Date | string | undefined = d?.end ? (allDay ? d.end.slice(0, 10) : parseISO(d.end) ?? undefined) : undefined
  if (typeof start === 'string' && (typeof end !== 'string' || end <= start)) end = addDaysISO(start, 1)
  if (start instanceof Date && (!(end instanceof Date) || end <= start)) end = new Date(start.getTime() + 3600_000)
  return (
    <MailEventEditor
      init={{ mode: 'new', seed: { summary: d?.title ?? m.subject, ...(start ? { start } : {}), ...(end ? { end } : {}), allDay, location: d?.location ?? '' } }}
      conversationId={m.conversation_id}
      identityId={null}
      description={[notes, t('conversation.calendarBits.fromTheEmail', { link })].filter(Boolean).join('\n\n')}
      onClose={onClose}
    />
  )
}

/** "Move Ops Review to Wed 28 Oct, 1 – 3 PM?": one suggestion for a move an email announces in words,
 *  with the event and the sentence it came from (backend intel/event_changes.py). Nothing changes until
 *  Move; then it is your own change, with Undo. ACT event.change_suggestion @web.reader */
export function EventChangeCard({ m, c }: { m: Message; c: EventChange }) {
  const qc = useQueryClient()
  const p = usePrefs()
  const [gone, setGone] = useState(false)
  const close = (status: 'applied' | 'dismissed') => api.post(`/messages/${m.id}/event-change`, { status })
  const move = useMutation({
    mutationFn: async () => {
      await api.patch(`/calendar/events/${enc(c.uid)}`, { start: c.new_start, end: c.new_end })
      await close('applied')
    },
    onSuccess: () => {
      setGone(true)
      toast({
        message: t('conversation.calendarBits.movedEvent', { title: c.summary }),
        actionLabel: t('common.undo'),
        onAction: () => void api.patch(`/calendar/events/${enc(c.uid)}`, { start: c.old_start, end: c.old_end ?? undefined })
          .then(() => void qc.invalidateQueries({ queryKey: ['calendar'] })).catch((e) => toastError(e, t('common.notChanged'))),
      })
      void qc.invalidateQueries({ queryKey: ['calendar'] })
    },
    onError: (e) => toastError(e, t('conversation.calendarBits.notMoved')),
  })
  const dismiss = useMutation({ mutationFn: () => close('dismissed'), onSuccess: () => setGone(true) })
  if (gone) return null
  return (
    <section className="invite-card event-change" data-testid="event-change" aria-label={t('conversation.calendarBits.suggestedMove')}>
      <div className="invite-head">
        <CalendarClock aria-hidden />
        <div className="invite-title">
          <b>{t('conversation.calendarBits.moveTo', { title: c.summary, when: whenText({ start: c.new_start, end: c.new_end }, p) })}</b>
          <div className="muted">{t('conversation.calendarBits.nowAt', { when: whenText({ start: c.old_start, end: c.old_end }, p) })}</div>
          <blockquote className="event-change-quote">{c.sentence}</blockquote>
        </div>
      </div>
      <div className="invite-actions">
        <button type="button" className="btn filled sm" disabled={move.isPending} onClick={() => move.mutate()}>{t('conversation.calendarBits.move')}</button>
        <button type="button" className="btn text sm" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>{t('conversation.calendarBits.notNow')}</button>
      </div>
    </section>
  )
}
