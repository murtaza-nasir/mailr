// The small questions a change may ask, as promises: the series question ("This event / This and
// following / All events"), whether to send updates ("Send update to 3 guests?"), the organizer's
// cancel ("Cancel this event?", with a message) and the conflict sheet ("Changed elsewhere").
// Actions await them; <Prompts/> draws whichever is open. A delete or a cancel offers the event's
// .ics copy first.

import { Download } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { create } from 'zustand'
import { Dialog } from '../../components/Dialog'
import { downloadEventIcs } from './ics'
import type { Scope } from './types'
import { t } from '../../i18n'

type Resolver<T> = (v: T) => void

export type SendKind = 'update' | 'invite' | 'cancel' | 'uninvite'

/** The event a destructive prompt offers to download first. */
export interface IcsCopy {
  uid: string
  summary: string
}

export interface ConflictChoice {
  choice: 'theirs' | 'mine' | 'both' | 'restore' | null
}

type Prompt =
  | { kind: 'scope'; verb: string; exceptions: number | Promise<number>; ics?: IcsCopy; resolve: Resolver<Scope | null> }
  | { kind: 'send'; count: number; send: SendKind; ics?: IcsCopy; resolve: Resolver<{ send: boolean; message: string } | null> }
  | { kind: 'cancel'; title: string; count: number; ics?: IcsCopy; resolve: Resolver<{ message: string } | null> }
  | { kind: 'conflict'; title: string; deleted: boolean; theirs: string[]; mine: string[]; resolve: Resolver<ConflictChoice['choice']> }

interface PromptStore {
  current: Prompt | null
  open: (p: Prompt) => void
  close: () => void
}

const usePrompts = create<PromptStore>((set) => ({
  current: null,
  open: (p) => set({ current: p }),
  close: () => set({ current: null }),
}))

/**
 * The series question. Null when the user backs out. `verb` names the change ("Move", "Delete", "Change").
 * `exceptions` may arrive later (the series' override count is read from the server).
 */
export function askScope(verb: string, exceptions: number | Promise<number> = 0, ics?: IcsCopy): Promise<Scope | null> {
  return new Promise((resolve) => usePrompts.getState().open({ kind: 'scope', verb, exceptions, ics, resolve }))
}

/** Whether to send updates to the guests. Null is Back; {send: false} is Don't send. */
export function askSendUpdates(count: number, send: SendKind, ics?: IcsCopy): Promise<{ send: boolean; message: string } | null> {
  return new Promise((resolve) => usePrompts.getState().open({ kind: 'send', count, send, ics, resolve }))
}

/** The organizer's cancel (event.cancel): a message for the guests, or null (Back). */
export function askCancel(title: string, count: number, ics?: IcsCopy): Promise<{ message: string } | null> {
  return new Promise((resolve) => usePrompts.getState().open({ kind: 'cancel', title, count, ics, resolve }))
}

/** The conflict sheet: the same field changed elsewhere, or the event is gone. */
export function askConflict(o: { title: string; deleted: boolean; theirs: string[]; mine: string[] }): Promise<ConflictChoice['choice']> {
  return new Promise((resolve) => usePrompts.getState().open({ kind: 'conflict', ...o, resolve }))
}

export function sendText(count: number, kind: SendKind): string {
  const g = t('calendar.prompts.guests', { count })
  if (kind === 'invite') return t('calendar.prompts.sendInvitationToNewGuests', { count })
  if (kind === 'cancel') return t('calendar.prompts.sendCancellationTo', { g })
  if (kind === 'uninvite') return t('calendar.prompts.sendCancellationToRemovedGuests', { count })
  return t('calendar.prompts.sendUpdateTo', { g })
}

export function Prompts() {
  const current = usePrompts((s) => s.current)
  const close = usePrompts((s) => s.close)
  if (!current) return null
  const done = <T,>(resolve: Resolver<T>, v: T) => {
    close()
    resolve(v)
  }
  if (current.kind === 'scope') return <ScopePrompt p={current} onDone={(v) => done(current.resolve, v)} />
  if (current.kind === 'send') return <SendPrompt p={current} onDone={(v) => done(current.resolve, v)} />
  if (current.kind === 'cancel') return <CancelPrompt p={current} onDone={(v) => done(current.resolve, v)} />
  return <ConflictPrompt p={current} onDone={(v) => done(current.resolve, v)} />
}

const SCOPES: { id: Scope; label: string }[] = [
  { id: 'this', label: t('calendar.prompts.thisEvent') },
  { id: 'following', label: t('calendar.prompts.thisAndFollowingEvents') },
  { id: 'all', label: t('calendar.prompts.allEvents') },
]

/** "Download a copy (.ics)" before a destructive change. */
function IcsButton({ ics }: { ics?: IcsCopy }) {
  if (!ics) return null
  return (
    <button type="button" className="btn text sm cal-ics" onClick={() => void downloadEventIcs(ics.uid, ics.summary)}>
      <Download size={14} aria-hidden />{' '}{t('calendar.prompts.downloadACopyIcs')}
    </button>
  )
}

/** The count the scope prompt shows: the number given, or the server's once it answers. */
function useExceptions(e: number | Promise<number>): number {
  const [n, setN] = useState(typeof e === 'number' ? e : 0)
  useEffect(() => {
    if (typeof e === 'number') return
    let live = true
    void e.then((v) => live && setN(v), () => undefined)
    return () => {
      live = false
    }
  }, [e])
  return n
}

function ScopePrompt({ p, onDone }: { p: Extract<Prompt, { kind: 'scope' }>; onDone: (v: Scope | null) => void }) {
  const [pick, setPick] = useState<Scope>('this')
  const exceptions = useExceptions(p.exceptions)
  return (
    <Dialog
      open
      onClose={() => onDone(null)}
      title={t('calendar.prompts.recurringEvent', { verb: p.verb })}
      initialFocus="input[value='this']"
      footer={
        <>
          <button type="button" className="btn text" onClick={() => onDone(null)}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" data-testid="scope-ok" onClick={() => onDone(pick)}>{t('calendar.prompts.ok')}</button>
        </>
      }
    >
      <div
        className="cal-scope"
        role="radiogroup"
        aria-label={t('calendar.prompts.whichEvents')}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            onDone(pick)
          }
        }}
      >
        {SCOPES.map((s) => (
          <label key={s.id} className="cal-radio">
            <input type="radio" name="scope" value={s.id} checked={pick === s.id} onChange={() => setPick(s.id)} />
            <span>{s.label}</span>
          </label>
        ))}
        {pick === 'all' && exceptions > 0 && p.verb !== t('common.delete2') && p.verb !== t('common.cancel') && (
          <p className="cal-note" data-testid="scope-exceptions">{t('calendar.prompts.changedOccurrencesWillKeepTheir', { count: exceptions })}</p>
        )}
        {p.verb === t('common.delete2') && <IcsButton ics={p.ics} />}
      </div>
    </Dialog>
  )
}

function SendPrompt({ p, onDone }: { p: Extract<Prompt, { kind: 'send' }>; onDone: (v: { send: boolean; message: string } | null) => void }) {
  const [message, setMessage] = useState('')
  const [withNote, setWithNote] = useState(false)
  const sendRef = useRef<HTMLButtonElement>(null)
  useEffect(() => sendRef.current?.focus(), [])
  return (
    <Dialog
      open
      onClose={() => onDone(null)}
      title={sendText(p.count, p.send)}
      footer={
        <>
          <button type="button" className="btn text" onClick={() => onDone(null)}>{t('common.back')}</button>
          <button type="button" className="btn text" onClick={() => onDone({ send: false, message: '' })}>{t('calendar.prompts.dontSend')}</button>
          <button ref={sendRef} type="button" className="btn filled" onClick={() => onDone({ send: true, message })}>{t('common.send')}</button>
        </>
      }
    >
      <p className="cal-note">
        {p.send === 'cancel' ? t('calendar.prompts.theGuestsGetACancellation')
          : p.send === 'uninvite' ? t('calendar.prompts.theGuestsYouRemovedGet')
            : t('calendar.prompts.theGuestsGetTheChange')}
      </p>
      {p.send === 'cancel' && <IcsButton ics={p.ics} />}
      {withNote ? (
        <textarea className="input cal-textarea" rows={3} placeholder={t('calendar.prompts.addAMessageOptional')} aria-label={t('calendar.prompts.messageToGuests')} value={message} onChange={(e) => setMessage(e.target.value)} />
      ) : (
        <button type="button" className="btn text sm" onClick={() => setWithNote(true)}>{t('calendar.prompts.addAMessage')}</button>
      )}
    </Dialog>
  )
}

/** ACT event.cancel @web.editor: the event stays, marked cancelled, and the guests get CANCEL with the message. */
function CancelPrompt({ p, onDone }: { p: Extract<Prompt, { kind: 'cancel' }>; onDone: (v: { message: string } | null) => void }) {
  const [message, setMessage] = useState('')
  return (
    <Dialog
      open
      onClose={() => onDone(null)}
      title={t('calendar.prompts.cancel', { title: p.title })}
      footer={
        <>
          <button type="button" className="btn text" onClick={() => onDone(null)}>{t('common.back')}</button>
          <button type="button" className="btn filled danger" data-testid="cancel-event-ok" onClick={() => onDone({ message: message.trim() })}>{t('common.cancelEvent')}</button>
        </>
      }
    >
      <p className="cal-note">
        {t('calendar.prompts.aCancellationByEmailThrough', { value: t('calendar.prompts.yourGuestsGet', { count: p.count }) })}
      </p>
      <textarea className="input cal-textarea" rows={3} placeholder={t('calendar.prompts.addAMessageOptional')} aria-label={t('calendar.prompts.messageToGuests')} value={message} onChange={(e) => setMessage(e.target.value)} autoFocus />
      <IcsButton ics={p.ics} />
    </Dialog>
  )
}

function ConflictPrompt({ p, onDone }: { p: Extract<Prompt, { kind: 'conflict' }>; onDone: (v: ConflictChoice['choice']) => void }) {
  if (p.deleted) {
    return (
      <Dialog
        open
        onClose={() => onDone(null)}
        title={t('calendar.prompts.thisEventWasDeletedElsewhere')}
        footer={
          <>
            <button type="button" className="btn text" onClick={() => onDone(null)}>{t('calendar.prompts.discardMyChange')}</button>
            <button type="button" className="btn filled" onClick={() => onDone('restore')}>{t('common.restoreAsNew')}</button>
          </>
        }
      >
        <p className="cal-note">{t('calendar.prompts.isNoLongerOnThe', { title: p.title })}</p>
      </Dialog>
    )
  }
  return (
    <Dialog
      open
      wide
      onClose={() => onDone(null)}
      title={t('common.changedElsewhere')}
      footer={
        <>
          <button type="button" className="btn text" onClick={() => onDone('both')}>{t('calendar.prompts.keepBoth')}</button>
          <button type="button" className="btn tonal" onClick={() => onDone('theirs')}>{t('common.keepTheirs')}</button>
          <button type="button" className="btn filled" onClick={() => onDone('mine')}>{t('common.useMine')}</button>
        </>
      }
    >
      <p className="cal-note">{t('calendar.prompts.changedOnAnotherDeviceWhile', { title: p.title })}</p>
      <div className="cal-conflict">
        <section>
          <h3>{t('calendar.prompts.theirVersion')}</h3>
          <ul>{p.theirs.map((l) => <li key={l}>{l}</li>)}</ul>
        </section>
        <section>
          <h3>{t('calendar.prompts.yourVersion')}</h3>
          <ul>{p.mine.map((l) => <li key={l}>{l}</li>)}</ul>
        </section>
      </div>
    </Dialog>
  )
}
