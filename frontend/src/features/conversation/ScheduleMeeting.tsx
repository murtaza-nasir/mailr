// "Meeting with these people" from a thread (Create from email; once "Schedule a meeting") (mail.schedule_meeting) and the Calendar
// app's editor opened from mail (mail.to_event). First a small step: the title from the subject,
// the thread's people as ticked checkboxes (lists and robots left out), the identity that received
// the thread and the length. Then the app's editor opens on its Find-a-time tab; Save invites the
// guests through the question whether to send updates and the outbox rules, links the event to the conversation, and
// offers a reply on the thread with the details.
// ACT mail.schedule_meeting @web.reader, ACT event.find_time @web.reader, ACT event.create @web.editor

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMe } from '../../api/hooks'
import type { ConversationSummary, Message } from '../../api/types'
import { Popover } from '../../components/Popover'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { addDaysISO, startOfDay, todayISO } from '../../lib/dates'
import { mailPath } from '../../lib/views'
import { toast } from '../../stores/ui'
import { LazyEventEditor as EventEditor } from '../calendar/LazyEventEditor'
import { useCalPrefs } from '../calendar/prefs'
import { nextHalfHour } from '../calendar/halfHour'
import { useCalUI, type EditorInit } from '../calendar/store'
import type { ApiEvent, EventWrite } from '../calendar/types'
import { CalHost, CalOverlays, claimedEditors } from '../rail/CalHost'
import { meetingTitle, participants, receivingIdentity, replyText, type Person } from './meeting'
import { t } from '../../i18n'

/**
 * The Calendar app's editor over the reader. A new event made here is linked to the conversation
 * (an EventLink), sent from `identityId`, and starts with a link to the thread as its description:
 * all three are seeds of the editor (EditorInit.seed).
 */
export function MailEventEditor({ init, conversationId, identityId, description, onSaved, onClose }: {
  init: EditorInit
  /** The conversation the new event links to; none for an event made from a note. */
  conversationId?: number
  identityId?: number | null
  description?: string
  onSaved?: (ev: ApiEvent, body: EventWrite) => void
  onClose: () => void
}) {
  const prefs = useCalPrefs()
  const narrow = useMediaQuery('(max-width: 720px)')
  const mine = useRef<EditorInit>({
    ...init,
    seed: { ...init.seed, ...(conversationId ? { conversationId } : {}), ...(identityId ? { identityId } : {}), ...(description ? { description: init.seed?.description || description } : {}) },
    onSaved,
  })
  const editor = useCalUI((s) => s.editor)
  const opened = useRef(false)
  const today = todayISO(prefs.zone)
  const from = useMemo(() => startOfDay(today, prefs.zone), [today, prefs.zone])
  const to = useMemo(() => startOfDay(addDaysISO(today, 14), prefs.zone), [today, prefs.zone])

  useEffect(() => {
    const own = mine.current
    claimedEditors.add(own)
    useCalUI.getState().set({ editor: own })
    return () => {
      if (useCalUI.getState().editor === own) useCalUI.getState().set({ editor: null })
    }
  }, [])
  // The editor closes itself (Save, Cancel); then this closes too.
  useEffect(() => {
    if (editor === mine.current) opened.current = true
    else if (opened.current) onClose()
  }, [editor, onClose])

  return (
    <CalHost from={from} to={to} date={today}>
      {editor === mine.current && <EventEditor init={mine.current} mode={narrow ? 'sheet' : 'modal'} />}
      <CalOverlays />
    </CalHost>
  )
}

const LENGTHS = [15, 30, 45, 60, 90]

/** "Meeting with these people" (the Create from email menu): the first step, over `anchor` while
 * it is set, then the Calendar app's editor on its Find-a-time tab. */
export function ScheduleMeetingPicker({ conv, messages, anchor, onClose, onReply }: {
  conv: Pick<ConversationSummary, 'id' | 'subject'>
  messages: Message[]
  anchor: HTMLElement | DOMRect | null
  onClose: () => void
  /** Start a reply on the thread with this text. */
  onReply: (text: string) => void
}) {
  const me = useMe().data
  const prefs = useCalPrefs()
  const [init, setInit] = useState<EditorInit | null>(null)
  const identities = useMemo(() => me?.identities ?? [], [me?.identities])
  const people = useMemo(() => participants(messages, [me?.user?.email ?? '', ...identities.map((i) => i.email)].filter(Boolean)), [messages, me?.user?.email, identities])
  const received = useMemo(() => receivingIdentity(messages, identities), [messages, identities])
  const [title, setTitle] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [identity, setIdentity] = useState<number | null>(null)
  const [length, setLength] = useState(30)

  // Fill the step each time it opens.
  const [openedFor, setOpenedFor] = useState<typeof anchor>(null)
  if (anchor !== openedFor) {
    setOpenedFor(anchor)
    if (anchor) {
      setTitle(meetingTitle(conv.subject || ''))
      setChosen(new Set(people.map((p) => p.email)))
      setIdentity(received?.id ?? identities.find((i) => i.is_default)?.id ?? identities[0]?.id ?? null)
      setLength(prefs.defaultDuration <= 30 ? 30 : prefs.defaultDuration > 60 ? 60 : prefs.defaultDuration)
    }
  }
  const pick = { open: !!anchor, anchor, close: onClose }

  const go = () => {
    const guests = people.filter((p) => chosen.has(p.email)).map((p: Person) => ({ email: p.email, name: p.name || undefined }))
    const start = nextHalfHour(new Date())
    pick.close()
    setInit({ mode: 'new', tab: 'findtime', seed: { summary: title.trim() || meetingTitle(conv.subject || ''), guests, start, end: new Date(start.getTime() + length * 60_000) } })
  }

  const saved = (ev: ApiEvent, body: EventWrite) => {
    const start = body.all_day ? (body.start ?? ev.start) : new Date(body.start ?? ev.start)
    const end = body.all_day ? (body.end ?? ev.end ?? ev.start) : new Date(body.end ?? ev.end ?? ev.start)
    const text = replyText(body.summary ?? ev.summary, start, end, !!body.all_day, body.location ?? '', prefs)
    toast({ message: t('conversation.scheduleMeeting.meetingSavedAndLinkedTo'), duration: 15_000, actionLabel: t('conversation.scheduleMeeting.replyOnTheThread'), onAction: () => onReply(text) })
  }

  const thread = typeof window !== 'undefined' ? `${window.location.origin}${mailPath('all', conv.id)}` : ''
  return (
    <>
      <Popover anchor={pick.anchor} open={pick.open} onClose={pick.close} placement="bottom-end" role="dialog" label={t('conversation.scheduleMeeting.meetingWithThesePeople')} className="sm-pick" minWidth={320}>
        <form className="sm-form" onSubmit={(e) => { e.preventDefault(); go() }}>
          <b className="sm-head">{t('conversation.scheduleMeeting.meetingWithThesePeople')}</b>
          <label className="sm-field">
            <span>{t('common.title')}</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </label>
          <fieldset className="sm-people">
            <legend>{t('conversation.scheduleMeeting.invite')}</legend>
            {people.length === 0 && <p className="muted">{t('conversation.scheduleMeeting.noOneElseIsOn')}</p>}
            {people.map((p) => (
              <label key={p.email} className="sm-person">
                <input type="checkbox" className="checkbox" checked={chosen.has(p.email)}
                  onChange={(e) => setChosen((s) => { const n = new Set(s); if (e.target.checked) n.add(p.email); else n.delete(p.email); return n })} />
                <span className="sm-name">{p.name || p.email}</span>
                {p.name && <span className="sm-addr">{p.email}</span>}
              </label>
            ))}
          </fieldset>
          <div className="sm-row">
            <label className="sm-field">
              <span>{t('conversation.scheduleMeeting.length')}</span>
              <select className="select" value={length} onChange={(e) => setLength(Number(e.target.value))}>
                {LENGTHS.map((m) => <option key={m} value={m}>{m < 60 ? t('conversation.scheduleMeeting.min', { m }) : m === 60 ? t('common.n1Hour') : t('conversation.scheduleMeeting.n1Hours')}</option>)}
              </select>
            </label>
            {identities.length > 1 && (
              <label className="sm-field">
                <span>{t('common.from')}</span>
                <select className="select" value={identity ?? ''} onChange={(e) => setIdentity(Number(e.target.value) || null)}>
                  {identities.map((i) => <option key={i.id} value={i.id}>{i.email}</option>)}
                </select>
              </label>
            )}
          </div>
          <div className="sm-actions">
            <button type="button" className="btn text" onClick={pick.close}>{t('common.cancel')}</button>
            <button type="submit" className="btn filled">{t('common.findATime')}</button>
          </div>
        </form>
      </Popover>
      {init && (
        <MailEventEditor init={init} conversationId={conv.id} identityId={identity} description={t('conversation.scheduleMeeting.fromTheEmailThread', { thread })}
          onSaved={saved} onClose={() => setInit(null)} />
      )}
    </>
  )
}
