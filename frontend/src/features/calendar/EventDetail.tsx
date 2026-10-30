// Event detail (web.sheet): colour and title; the when line with both
// zones when they differ and a status chip; the repeat summary or "Moved from …"; location with a
// map link and Join; the calendar; organizer and guests; my RSVP; reminders (inline);
// the description; the linked email; and the actions. Each theme places it its own way
// (useThemeDef().apps.eventDetail): a popover at the event, the reading pane, a centred card, or a
// bottom sheet. The popover has its own keys (e, #, Del, y, s, v, Ctrl+D, Esc).

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import {
  AlarmClock, Ban, Check, ChevronDown, CircleHelp, Copy, CopyPlus, ExternalLink, Lock, Mail, MapPin, Palette, Pencil, Repeat, Smartphone, Sparkles, Trash2,
  Users, Video, X, FolderInput,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router'
import { useMe } from '../../api/hooks'
import { Floating } from './Floating'
import { formatRange, formatTime, toISODate, zoneAbbr } from '../../lib/dates'
import { OFFSETS, offsetLabel, offsets } from '../../lib/quickChoices'
import { useActiveReminders, useReminderActions } from '../reminders/api'
import { snoozeChoices } from '../reminders/logic'
import { summarizeRRule } from '../../lib/rrule'
import { eventPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { useCompose, emptyPayload } from '../../stores/compose'
import { toast } from '../../stores/ui'
import { openAsk, useAskTarget } from '../ask/askStore'
import { EventRecordings } from '../speakr/EventRecordings'
import { LinkedPanel } from '../notes/LinkedPanel'
import { MeetingPrep } from './MeetingPrep'
import { offersPrep } from './prep'
import { guestCount } from './actions'
import { guestRecipients } from './emailGuests'
import { displayColour, SWATCHES } from './colour'
import { useCal } from './context'
import {
  guestLine, inProgress, isCancelled, isTentative, joinTarget, movedFrom, RSVP_LABEL, titleOf,
} from './eventInfo'
import { useCalUI } from './store'
import type { CalEvent } from './types'
import { t as tr } from '../../i18n'
import { appLinkLabel, appPath } from '../../lib/appLinks'
import { appNavigate } from '../../lib/appNav'
import { ItemBoundary } from '../../components/bits'
import { mayRestoreFocus } from '../../hooks/useModalFocus'
import { phoneFreshness } from './phone'

export type DetailMode = 'popover' | 'pane' | 'card' | 'sheet'

/** Linkified plain text: URLs become links. */
function linkify(text: string): ReactNode[] {
  return text.split(/(https?:\/\/[^\s<>"]+)/g).map((p, i) => {
    if (!(i % 2)) return p
    // A link into this app (an event made from a note): "Open in Notes", opened here.
    const path = appPath(p)
    if (path != null) {
      return <a key={i} href={path} onClick={(e) => {
        e.preventDefault()
        appNavigate(path)
      }}>{appLinkLabel(p) ?? p}</a>
    }
    return <a key={i} href={p} target="_blank" rel="noreferrer">{p}</a>
  })
}

const looksLikeAddress = (s: string) => /\d/.test(s) && /[a-z]{3,}/i.test(s) && !/^https?:/.test(s)

type EventDetailProps = {
  e: CalEvent; mode: DetailMode; anchor: DOMRect | null; onClose: () => void; deleted?: boolean; onRestore?: () => void
}

/** The event's detail (popover, pane, sheet or card). A failure stays inside it: "Couldn't show
 *  this event" with Close (components/bits ItemBoundary), not the whole screen. */
export function EventDetail(props: EventDetailProps) {
  return (
    <ItemBoundary key={props.e.key} what="event" onClose={props.onClose}>
      <EventDetailBody {...props} />
    </ItemBoundary>
  )
}

function EventDetailBody({ e, mode, anchor, onClose, deleted, onRestore }: EventDetailProps) {
  const cal = useCal()
  const nav = useNavigate()
  const route = useLocation()
  const { prefs, actions } = cal
  const [guestsOpen, setGuestsOpen] = useState(false)
  const [descOpen, setDescOpen] = useState(false)
  const [sub, setSub] = useState<null | 'colour' | 'move' | 'note' | 'reminders'>(null)
  const [note, setNote] = useState('')
  const colour = displayColour(e.colour, cal.dark)
  const join = joinTarget(e)
  const ro = e.readOnly || cal.readOnly
  const col = e.calendar ? cal.collectionByUrl.get(e.calendar) : undefined
  const fresh = e.phone_busy ? phoneFreshness(col, cal.now) : null
  const evZone = e.tz && e.tz !== 'UTC' ? e.tz : null
  const otherZone = !e.allDay && evZone && zoneAbbr(e.startAt, evZone) !== zoneAbbr(e.startAt, prefs.zone) ? evZone : null
  const when = formatRange(e.allDay ? e.firstDay : e.startAt, e.allDay ? nextDayOf(e.lastDay) : e.endAt, e.allDay, prefs)
  const whenOther = otherZone
    ? `${formatTime(e.startAt, { ...prefs, zone: otherZone })}–${formatTime(e.endAt, { ...prefs, zone: otherZone })} ${otherZone.split('/').pop()!.replace(/_/g, ' ')}`
    : null
  const moved = movedFrom(e, prefs)
  // A cancellation from someone else says who: "Cancelled by Dana Lee".
  const cancelledBy = isCancelled(e) && e.is_organizer === false && e.organizer ? tr('calendar.eventDetail.cancelledBy', { who: e.organizer_name || e.organizer }) : null
  const status = isCancelled(e) ? cancelledBy ?? tr('common.cancelled') : inProgress(e, cal.now) ? tr('common.inProgress') : isTentative(e) ? tr('calendar.eventDetail.tentative') : moved ? tr('calendar.eventDetail.moved') : null
  const amGuest = !!e.my_status
  const desc = e.description?.trim() ?? ''
  // ACT agent.ask_about @web.calendar: Ask (the button, Shift+A, the palette) attaches this occurrence.
  const askAbout = { kind: 'event' as const, uid: e.uid, occurrence: e.occurrence || null, title: titleOf(e) }
  useAskTarget(deleted ? null : askAbout)
  const remActs = useReminderActions()
  const me = useMe().data
  const active = (useActiveReminders().data ?? []).find((r) => r.kind === 'event' && r.uid === e.uid && (!e.occurrence || !r.occurrence || r.occurrence === e.occurrence))

  // The colour's Undo puts back the colour it had. "" is no colour of its own (the calendar's shows).
  const recolour = (color: string) => actions.update(e, { color }, { verb: tr('calendar.eventDetail.recolour'), label: tr('calendar.eventDetail.colourChanged'), undo: { color: e.color ?? '' } })

  const edit = () => {
    if (ro) return
    onClose()
    useCalUI.getState().set({ editor: { mode: 'edit', event: e } })
  }
  const del = () => {
    onClose()
    void actions.remove(e) // ACT event.delete @web.sheet
  }
  // ACT event.cancel @web.sheet: the organizer of an event with guests may cancel it with a message.
  const canCancel = !ro && guestCount(e) > 0 && e.is_organizer !== false && !isCancelled(e)
  const cancelEvent = () => {
    onClose()
    void actions.cancel(e)
  }
  const duplicate = () => {
    onClose()
    useCalUI.getState().set({ editor: { mode: 'duplicate', event: e } }) // ACT event.duplicate @web.sheet
  }
  const reply = (r: 'accepted' | 'tentative' | 'declined') => void actions.rsvp(e, r, note) // ACT event.rsvp @web.sheet

  const onKey = (ev: React.KeyboardEvent) => {
    const t = ev.target as HTMLElement
    if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') return
    const k = ev.key
    if (k === 'e' && !ro) { ev.preventDefault(); edit() }
    else if ((k === '#' || k === 'Delete' || k === 'Backspace') && !ro) { ev.preventDefault(); del() }
    else if (k === 'd' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); duplicate() }
    else if (amGuest && k === 'y') { ev.preventDefault(); reply('accepted') }
    else if (amGuest && k === 's') { ev.preventDefault(); reply('tentative') }
    else if (amGuest && k === 'v') { ev.preventDefault(); reply('declined') }
    else if (k === 'Escape' && mode !== 'popover') { ev.preventDefault(); onClose() }
  }

  const body = deleted ? (
    <div className="cal-detail-gone" role="alert">
      <p><b>{tr('calendar.eventDetail.thisEventWasDeletedElsewhere')}</b></p>
      <p className="cal-note">{tr('calendar.eventDetail.isNoLongerInYour', { titleOf: titleOf(e) })}</p>
      <div className="cal-detail-actions">
        <button type="button" className="btn text" onClick={onClose}>{tr('common.close')}</button>
        {onRestore && <button type="button" className="btn filled" onClick={onRestore}>{tr('common.restoreAsNew')}</button>}
      </div>
    </div>
  ) : (
    <>
      <div className="cal-detail-tools">
        {!ro && <button type="button" className="icon-btn sm" aria-label={tr('calendar.eventDetail.editE')} data-tip={tr('calendar.eventDetail.editE')} onClick={edit}><Pencil size={18} /></button>}
        {!ro && <button type="button" className="icon-btn sm" aria-label={tr('calendar.eventDetail.delete')} data-tip={tr('common.delete2')} onClick={del}><Trash2 size={18} /></button>}
        <button type="button" className="icon-btn sm" aria-label={tr('common.copyLink')} data-tip={tr('common.copyLink')} onClick={() => {
          void navigator.clipboard?.writeText(location.origin + eventPath(e.uid, e.occurrence))
          toast({ message: tr('calendar.eventDetail.linkCopied') })
        }}><Copy size={18} /></button>
        <button type="button" className="icon-btn sm cal-detail-close" aria-label={tr('common.close')} data-tip={tr('common.close')} onClick={onClose}><X size={18} /></button>
      </div>
      <div className="cal-detail-title">
        <span className="cal-detail-swatch" style={{ background: colour }} aria-hidden />
        <h2 id="cal-detail-title" dir="auto" className={isCancelled(e) ? 'struck' : ''}>{titleOf(e)}</h2>
      </div>
      <div className="cal-detail-when">
        <span>{when}{!e.allDay && evZone ? ` ${zoneAbbr(e.startAt, prefs.zone)}` : ''}</span>
        {whenOther && <span className="cal-detail-zone"> · {whenOther}</span>}
        {status && <span className={`cal-status s-${status.toLowerCase().replace(/ /g, '-')}`}>{status}</span>}
      </div>
      {(e.rrule || moved) && (
        <Line icon={<Repeat />}>
          {e.rrule ? summarizeRRule(e.rrule, toISODate(e.startAt, prefs.zone)) : null}
          {moved && <span className="cal-detail-moved">{e.rrule ? ' · ' : ''}{moved}</span>}
        </Line>
      )}
      {e.location && (
        <Line icon={<MapPin />}>
          <span className="cal-detail-loc" dir="auto">{linkify(e.location)}</span>
          {looksLikeAddress(e.location) && (
            <a className="cal-maplink" href={`https://www.openstreetmap.org/search?query=${encodeURIComponent(e.location)}`} target="_blank" rel="noreferrer">{tr('calendar.eventDetail.map')}{' '}<ExternalLink size={12} aria-hidden /></a>
          )}
        </Line>
      )}
      {join && (
        <Line icon={<Video />}>
          {/* ACT event.join @web.sheet */}
          <a className="btn tonal sm cal-joinbtn" href={join.url} target="_blank" rel="noreferrer">{join.label}</a>
          <span className="cal-detail-conf">{join.url.replace(/^https?:\/\//, '').slice(0, 40)}</span>
        </Line>
      )}
      <Line icon={<span className="cal-dot" style={{ background: colour }} />}>
        {col?.name ?? tr('common.calendar')}{e.readOnly && <span className="cal-ro"><Lock size={12} aria-hidden />{' '}{tr('common.readOnlyCalendar')}</span>}
      </Line>
      {fresh && (
        // ACT calendar.phone_list @web.calendar: a phone calendar's busy time says where it comes from and how fresh it is.
        <Line icon={<Smartphone />}>
          <span className={`cal-phone-fresh${fresh.stale ? ' stale' : ''}`} data-testid="phone-freshness">
            {fresh.text}{fresh.stale && <span className="cal-phone-stale">{' · '}{tr('calendar.phone.stale')}</span>}
          </span>
          <span className="cal-note cal-phone-note">{tr(e.phone_details ? 'calendar.phone.eventChangeOnThePhone' : 'calendar.phone.changeOnThePhone')}</span>
        </Line>
      )}
      {(e.attendee_list?.length ?? 0) > 0 && (
        <div className="cal-detail-guests">
          <button type="button" className="cal-line cal-line-btn" aria-expanded={guestsOpen} onClick={() => setGuestsOpen(!guestsOpen)}>
            <span className="cal-line-icon" aria-hidden><Users /></span>
            <span className="cal-line-body">{guestLine(e)}</span>
            <ChevronDown className={`cal-chev${guestsOpen ? ' open' : ''}`} size={16} aria-hidden />
          </button>
          {guestsOpen && (
            <ul className="cal-guest-list">
              {e.organizer && (
                <li><span className="cal-guest-avatar">{(e.organizer_name || e.organizer)[0]?.toUpperCase()}</span><span>{e.organizer_name || e.organizer}</span><span className="cal-guest-role">{tr('calendar.eventDetail.organizer')}</span></li>
              )}
              {e.attendee_list!.map((a) => (
                <li key={a.email}>
                  <span className="cal-guest-avatar">{(a.name || a.email)[0]?.toUpperCase()}</span>
                  <span title={a.email}>{a.name || a.email}</span>
                  <span className={`cal-guest-ps ${(a.partstat ?? '').toLowerCase()}`}>{partstatIcon(a.partstat)} {RSVP_LABEL[(a.partstat ?? 'NEEDS-ACTION').toUpperCase()] ?? a.partstat}</span>
                  {a.role === 'OPT-PARTICIPANT' && <span className="cal-guest-role">{tr('calendar.eventDetail.optional')}</span>}
                  {a.comment && <span className="cal-guest-comment">“{a.comment}”</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {amGuest && (
        <div className="cal-rsvp" role="group" aria-label={tr('common.going')}>
          <span className="cal-rsvp-q">{tr('common.going')}</span>
          {(['accepted', 'tentative', 'declined'] as const).map((r) => {
            const on = (e.my_status ?? '').toLowerCase() === r
            return (
              <button key={r} type="button" className={`cal-rsvp-btn${on ? ' on' : ''}`} aria-pressed={on} onClick={() => reply(r)} title={r === 'accepted' ? 'y' : r === 'tentative' ? 's' : 'v'}>
                {on && <Check size={14} aria-hidden />}{r === 'accepted' ? tr('common.yes') : r === 'tentative' ? tr('common.maybe') : tr('common.no')}
              </button>
            )
          })}
          {sub === 'note' ? (
            <input className="input cal-rsvp-note" placeholder={tr('calendar.eventDetail.noteToTheOrganizer')} value={note} onChange={(ev) => setNote(ev.target.value)} autoFocus aria-label={tr('calendar.eventDetail.noteToTheOrganizer')} />
          ) : (
            <button type="button" className="btn text sm" onClick={() => setSub('note')}>{tr('calendar.eventDetail.addNote')}</button>
          )}
        </div>
      )}
      {/* A phone calendar's busy time has no reminders: the phone's own calendar app reminds. */}
      {!e.phone_busy && (
        <div className="cal-line">
          <span className="cal-line-icon" aria-hidden><AlarmClock /></span>
          <span className="cal-line-body cal-reminders">
            {(e.reminders ?? []).length ? (e.reminders ?? []).map((m) => <span key={m} className="cal-mini-chip">{offsetLabel(m)}</span>) : <span className="muted">{tr('calendar.eventDetail.noReminders')}</span>}
            {!ro && <button type="button" className="btn text sm" onClick={() => setSub(sub === 'reminders' ? null : 'reminders')}>{sub === 'reminders' ? tr('common.done') : tr('common.edit2')}</button>}
          </span>
        </div>
      )}
      {active && (
        // Snooze and Remind before, only while a reminder of this occurrence is up.
        <div className="cal-detail-sub cal-detail-active" role="group" aria-label={tr('calendar.eventDetail.activeReminder')} data-testid="cal-detail-reminder">
          <span className="cal-note"><AlarmClock size={14} aria-hidden />{' '}{tr('calendar.eventDetail.reminder', { detail: active.detail || 'now' })}</span>
          <div className="cal-chips">
            {snoozeChoices('event', cal.now, e.allDay ? null : e.startAt).map((c) => (
              // ACT event.snooze @web.sheet
              <button key={c.id} type="button" className="chip cal-chip" onClick={() => void remActs.snooze(active, c.at)}>{tr('calendar.eventDetail.snooze', { label: c.label })}</button>
            ))}
            {!e.allDay && offsets(cal.now, e.startAt).filter((m) => m <= 120).map((m) => (
              // ACT event.remind_before @web.sheet
              <button key={`b${m}`} type="button" className="chip cal-chip" onClick={() => void remActs.remindBefore(active, m, offsetLabel(m))}>{offsetLabel(m)}</button>
            ))}
          </div>
        </div>
      )}
      {sub === 'reminders' && (
        <div className="cal-chips cal-detail-sub">
          {OFFSETS.map((m) => {
            const on = (e.reminders ?? []).includes(m)
            return (
              <button key={m} type="button" className={`chip cal-chip${on ? ' on' : ''}`} aria-pressed={on} onClick={() => {
                const next = on ? (e.reminders ?? []).filter((x) => x !== m) : [...(e.reminders ?? []), m].sort((a, b) => a - b)
                void actions.update(e, { reminders: next }, { verb: tr('calendar.eventDetail.changeRemindersFor'), label: tr('calendar.eventDetail.remindersChanged'), undo: { reminders: e.reminders ?? [] } }) // ACT event.remind_before @web.sheet
              }}>{offsetLabel(m)}</button>
            )
          })}
        </div>
      )}
      {desc && (
        <div className={`cal-detail-desc bidi${descOpen ? ' open' : ''}`}>
          <p>{linkify(desc)}</p>
          {desc.split('\n').length > 6 || desc.length > 360 ? <button type="button" className="btn text sm" onClick={() => setDescOpen(!descOpen)}>{descOpen ? tr('common.less') : tr('common.more')}</button> : null}
        </div>
      )}
      {/* ACT note.link @web.calendar, ACT note.unlink @web.calendar, ACT table.link @web.calendar, ACT table.unlink @web.calendar */}
      <LinkedPanel className="cal-detail-links" kind="event" target={{ uid: e.uid, ...(e.occurrence ? { occurrence: e.occurrence } : {}) }} title={titleOf(e)} />
      <EventRecordings uid={e.uid} occurrence={e.occurrence} onOpen={mode === 'popover' ? onClose : undefined}
        ev={e.allDay ? undefined : { title: e.summary, start: e.startAt, end: e.endAt, guests: (e.attendee_list ?? []).map((a) => a.name || a.email).filter(Boolean) }} />
      {/* ACT event.prep @web.sheet: an event with guests that has not ended offers its meeting prep pack. */}
      {offersPrep(e, cal.now) && (
        <MeetingPrep key={e.key} uid={e.uid} occurrence={e.occurrence} initiallyOpen={new URLSearchParams(route.search).get('prep') === '1'}
          onLeave={mode === 'popover' || mode === 'card' || mode === 'sheet' ? onClose : undefined} />
      )}
      {e.conversation_id && (
        <button type="button" className="cal-mailchip" onClick={() => { onClose(); nav(mailPath('inbox', Number(e.conversation_id))) }}>
          <Mail size={14} aria-hidden />{' '}{tr('calendar.eventDetail.fromAnEmail')}
        </button>
      )}
      {sub === 'colour' && !ro && (
        <div className="cal-swatches cal-detail-sub" role="radiogroup" aria-label={tr('calendar.eventDetail.eventColour')}>
          <button type="button" role="radio" aria-checked={!e.color} className="cal-swatch cal-swatch-cal" style={{ background: displayColour(cal.colourOf(col), cal.dark) }} aria-label={tr('calendar.eventDetail.calendarColour')} data-tip={tr('calendar.eventDetail.calendarColour')} onClick={() => void recolour('')} />
          {SWATCHES.map((s) => (
            <button key={s.id} type="button" role="radio" aria-checked={e.color === s.light} aria-label={s.name} data-tip={s.name} className="cal-swatch" style={{ background: cal.dark ? s.dark : s.light }}
              onClick={() => void recolour(s.light) /* ACT event.set_color @web.sheet */}>
              {e.color === s.light && <Check size={14} aria-hidden />}
            </button>
          ))}
        </div>
      )}
      {sub === 'move' && !ro && (
        <div className="cal-detail-sub cal-movelist" role="listbox" aria-label={tr('calendar.eventDetail.moveToCalendar')}>
          {cal.collections.filter((c) => c.events !== false && !c.read_only && (c.url ?? c.id) !== e.calendar).map((c) => (
            <button key={c.url ?? c.id} type="button" className="menu-item" role="option" aria-selected={false} onClick={() => {
              setSub(null)
              void actions.moveCalendar(e, c.url ?? c.id, c.name) // ACT event.move_calendar @web.sheet
            }}>
              <span className="cal-dot" style={{ background: displayColour(cal.colourOf(c), cal.dark) }} /> {c.name}
            </button>
          ))}
        </div>
      )}
      <div className="cal-detail-actions">
        {!ro && <button type="button" className="btn text sm" onClick={duplicate}><CopyPlus size={16} aria-hidden />{' '}{tr('common.duplicate')}</button>}
        {canCancel && <button type="button" className="btn text sm" onClick={cancelEvent}><Ban size={16} aria-hidden />{' '}{tr('common.cancelEvent')}</button>}
        {(e.attendee_list?.length ?? 0) > 0 && (
          <button type="button" className="btn text sm" onClick={() => {
            // ACT event.email_guests @web.sheet
            onClose()
            // Everyone on the event but me (my addresses are left out), the organizer included.
            const to = guestRecipients(e, 'all', [me?.user?.email ?? '', ...(me?.identities ?? []).map((i) => i.email)])
            useCompose.getState().open({ payload: { ...emptyPayload(), to, subject: titleOf(e), html: `<p><a href="${location.origin}${eventPath(e.uid, e.occurrence)}">${titleOf(e)}</a> · ${when}</p>` } })
          }}><Mail size={16} aria-hidden />{' '}{tr('calendar.eventDetail.emailGuests')}</button>
        )}
        {!ro && <button type="button" className="btn text sm" aria-expanded={sub === 'colour'} onClick={() => setSub(sub === 'colour' ? null : 'colour')}><Palette size={16} aria-hidden />{' '}{tr('common.colour')}</button>}
        {!ro && cal.collections.filter((c) => c.events !== false && !c.read_only).length > 1 && (
          <button type="button" className="btn text sm" aria-expanded={sub === 'move'} onClick={() => setSub(sub === 'move' ? null : 'move')}><FolderInput size={16} aria-hidden />{' '}{tr('calendar.eventDetail.moveTo')}</button>
        )}
        <button type="button" className="btn text sm" data-tip={tr('calendar.eventDetail.askShiftA', { name: assistantNameNow() })} onClick={(ev) => {
          openAsk(askAbout, ev.currentTarget)
          if (mode !== 'pane') onClose()
        }}><Sparkles size={16} aria-hidden />{' '}{tr('calendar.eventDetail.ask', { name: assistantNameNow() })}</button>
      </div>
    </>
  )

  const inner = (
    // The popover takes focus (data-autofocus) so its keys (e, #, y/s/v) work at once.
    <div className={`cal-detail m-${mode}`} data-testid="cal-detail" tabIndex={-1} data-autofocus onKeyDown={onKey} style={{ '--ev': colour } as React.CSSProperties}>
      {body}
    </div>
  )

  if (mode === 'popover' && anchor) {
    return (
      <Floating anchor={anchor} onClose={onClose} label={titleOf(e)} className="cal-detail-pop">
        {inner}
      </Floating>
    )
  }
  if (mode === 'pane') {
    return <aside className="cal-pane" aria-labelledby="cal-detail-title">{inner}</aside>
  }
  return <Modalish mode={mode} onClose={onClose}>{inner}</Modalish>
}

/** The centred card (Focus) and the bottom sheet (Cards, and every theme on a phone). */
function Modalish({ mode, onClose, children }: { mode: DetailMode; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    const el = ref.current
    el?.querySelector<HTMLElement>('.cal-detail')?.focus({ preventScroll: true })
    const k = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', k, true)
    return () => {
      document.removeEventListener('keydown', k, true)
      // Edit closes the card and opens the editor; the card can go a moment after the editor came
      // (the route change renders later), and must not take the focus from the field being typed in.
      if (mayRestoreFocus(document.activeElement, el)) prev?.focus?.()
    }
  }, [onClose])
  return createPortal(
    <div className={`scrim cal-scrim ${mode === 'sheet' ? 'sheet' : 'card'}`} onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose() }}>
      <div ref={ref} className={`cal-${mode === 'sheet' ? 'sheet' : 'card'}`} role="dialog" aria-modal="true" aria-labelledby="cal-detail-title">
        {mode === 'sheet' && <div className="cal-sheet-grip" aria-hidden />}
        {children}
      </div>
    </div>,
    document.body,
  )
}

function Line({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="cal-line">
      <span className="cal-line-icon" aria-hidden>{icon}</span>
      <span className="cal-line-body">{children}</span>
    </div>
  )
}

function partstatIcon(p?: string) {
  const s = (p ?? '').toUpperCase()
  if (s === 'ACCEPTED') return <Check size={12} aria-hidden />
  if (s === 'DECLINED') return <X size={12} aria-hidden />
  return <CircleHelp size={12} aria-hidden />
}

function nextDayOf(d: string): string {
  const [y, m, day] = d.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, day + 1)).toISOString().slice(0, 10)
}

