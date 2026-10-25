// The People tab of the recording sheet (web.recording, speaker.map): one
// compact row per speaker, the speaker's name and then who they are.
// - The user's own speaker shows as "You", with nothing to confirm.
// - A confirmed person shows with avatar, name, address and organization, a link to their card in
//   Settings → Mail → People & groups, and Not them: in this recording, or (for a named speaker) in every
//   recording.
// - Otherwise up to three ranked candidates show as chips (name, address, organization, why), each
//   with Confirm (✓) and Not them (✕); "Someone else…" searches People.
// A speaker name merged into another in the Recordings app's Participants list gets that name's
// person (participants.py); Separate there undoes it.
// A named speaker's choice applies to every recording with that speaker name; an unnamed label
// (SPEAKER_02), or a speaker this recording overrides, applies here only. When Speakr keeps an
// address per speaker, it is offered first ("address saved in Speakr"), and with edits to Speakr on,
// a confirmed person's address is saved to that speaker in Speakr; otherwise nothing is written there.

import { useQueryClient } from '@tanstack/react-query'
import { Check, UserRound, UserX, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Avatar, Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { usePeopleList } from '../people/api'
import { mapSpeaker, speakrKeys, unmapSpeaker, useRecordingPeople, type Recording, type SpeakerCandidate, type SpeakerPerson } from './api'
import { useRecordingSheet } from './store'
import './speaker-people.css'
import { t } from '../../i18n'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const ALL_NOTE = t('speakr.speakerPeople.appliesToAllRecordingsWith')

function who(p: { name?: string; email: string }): string {
  return p.name || p.email
}

function detail(p: { email: string; organization?: string; emails?: string[] }): string {
  const n = p.emails?.length ?? 1
  return [p.email, n > 1 ? t('speakr.speakerPeople.addresses', { count: n }) : '', p.organization ?? ''].filter(Boolean).join(' · ')
}

/** The person's card in Settings → Mail → People & groups (its Recordings list leads back here). */
function CardLink({ email, name }: { email: string; name: string }) {
  return (
    <Link className="spk-card-link" to={`/settings/mail/people?person=${encodeURIComponent(email)}`}
      aria-label={t('speakr.speakerPeople.openTheCardOf', { itemName: name })} data-tip={t('speakr.speakerPeople.openTheirCard')}
      onClick={() => useRecordingSheet.getState().close()}>
      <UserRound size={15} aria-hidden />
    </Link>
  )
}

// ACT speaker.map @web.recording
export function SpeakerPeople({ r }: { r: Recording }) {
  const q = useRecordingPeople(r.connector_id, r.recording_id, true)
  const qc = useQueryClient()
  const [picking, setPicking] = useState<SpeakerPerson | null>(null)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: speakrKeys.people(r.connector_id, r.recording_id) })
    void qc.invalidateQueries({ queryKey: ['speakr', 'person'] })
  }
  if (q.isLoading) return <div className="rec-wait"><Spinner small /> <span className="muted">{t('speakr.speakerPeople.askingSpeakr')}</span></div>
  if (q.isError) return <p className="error-text">{errText(q.error)}</p>
  const speakers = q.data?.speakers ?? []
  if (!speakers.length) return <p className="muted">{t('speakr.speakerPeople.speakrHasNoSpeakersFor')}</p>

  const spoken = (s: SpeakerPerson) => s.name || s.label
  const confirm = (s: SpeakerPerson, p: { email: string; name: string }) => {
    const all = s.applies === 'all'
    return mapSpeaker(r.connector_id, { key: s.map_key ?? s.key, speaker_name: spoken(s), email: p.email, name: p.name,
      speaker_id: s.speaker_id, recording_id: r.recording_id })
      .then((out) => {
        refresh()
        const saved = out?.written_to_speakr ? t('speakr.speakerPeople.theAddressIsSavedTo') : ''
        toast({ message: t('speakr.speakerPeople.is', { spoken: spoken(s), who: who(p), value: all ? ` ${ALL_NOTE}` : '', saved }) })
      })
      .catch((e) => toastError(e))
  }
  const notThem = (key: string, email: string, msg: string) =>
    unmapSpeaker(r.connector_id, key, email).then(() => { refresh(); toast({ message: msg }) }).catch((e) => toastError(e))

  return (
    <div className="spk-people">
      <p className="muted" style={{ marginTop: 0 }}>
        {t('speakr.speakerPeople.whoEachSpeakerIsA', { value: q.data?.speakr_email && r.write_back ? t('speakr.speakerPeople.aConfirmedAddressIsAlso') : t('speakr.speakerPeople.itIsKeptHereAnd') })}
      </p>
      <ul>
        {speakers.map((s) => (
          <li key={s.key} data-testid="rec-speaker" className="spk-row">
            <span className="spk-name" title={s.name && s.label ? s.label : undefined}><b>{spoken(s)}</b></span>
            <span className="spk-arrow" aria-hidden>→</span>
            <div className="spk-who">
              {s.me || s.person?.me ? (
                <span className="spk-person" data-testid="speaker-me">
                  <Avatar who={{ email: s.person?.email || '', name: s.person?.name || spoken(s) }} size="xs" />
                  <span className="spk-pname">{t('common.you')}</span>
                </span>
              ) : s.person ? (
                <span className="spk-person" data-testid="speaker-person">
                  <Avatar who={{ email: s.person.email, name: s.person.name }} size="xs" />
                  <span className="spk-text">
                    <span className="spk-pname">{who(s.person)}</span>
                    <span className="spk-detail">{detail(s.person)}{s.via_alias ? t('speakr.speakerPeople.samePersonAsRecordingsParticipants', { viaAlias: s.via_alias }) : s.applies === 'all' ? t('speakr.speakerPeople.everyRecording') : t('speakr.speakerPeople.thisRecording')}</span>
                  </span>
                  <CardLink email={s.person.email} name={who(s.person)} />
                  {s.via_alias ? null : s.applies === 'all' && s.override_key ? (
                    <>
                      <button type="button" className="icon-btn xs" aria-label={t('speakr.speakerPeople.notInThisRecording', { who: who(s.person) })} data-tip={t('speakr.speakerPeople.notThemInThisRecording')}
                        onClick={() => void notThem(s.override_key!, s.person!.email, t('speakr.speakerPeople.notThemInThisRecording2'))}><X size={16} /></button>
                      <button type="button" className="icon-btn xs" aria-label={t('speakr.speakerPeople.notInAnyRecording', { who: who(s.person) })} data-tip={t('speakr.speakerPeople.notThemInAnyRecording')}
                        onClick={() => void notThem(s.key, s.person!.email, t('speakr.speakerPeople.isNotInAnyRecording', { spoken: spoken(s), who: who(s.person!) }))}><UserX size={16} /></button>
                    </>
                  ) : (
                    <button type="button" className="icon-btn xs" aria-label={t('speakr.speakerPeople.not', { who: who(s.person) })} data-tip={t('speakr.speakerPeople.notThem')}
                      onClick={() => void notThem(s.map_key ?? s.key, s.person!.email, t('speakr.speakerPeople.removed'))}><X size={16} /></button>
                  )}
                </span>
              ) : (
                <>
                  {s.suggestions.map((p) => (
                    <Chip key={p.email} p={p} onConfirm={() => void confirm(s, p)}
                      onDismiss={() => void notThem(s.map_key ?? s.key, p.email, t('speakr.speakerPeople.not2', { who: who(p) }))} />
                  ))}
                  <button type="button" className="btn text sm spk-else" onClick={() => setPicking(s)}>
                    {s.suggestions.length ? t('speakr.speakerPeople.someoneElse') : t('speakr.speakerPeople.chooseAPerson')}
                  </button>
                  {s.applies === 'all' && s.suggestions.length > 0 && <span className="spk-note">{ALL_NOTE}</span>}
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
      {picking && (
        <PeoplePicker speaker={spoken(picking)} all={picking.applies === 'all'} onClose={() => setPicking(null)}
          onPick={(p) => { const s = picking; setPicking(null); void confirm(s, p) }} />
      )}
    </div>
  )
}

function Chip({ p, onConfirm, onDismiss }: { p: SpeakerCandidate; onConfirm: () => void; onDismiss: () => void }) {
  return (
    <span className="spk-chip" data-testid="speaker-suggestion" title={p.why || undefined}>
      <Avatar who={{ email: p.email, name: p.name }} size="xs" />
      <span className="spk-text">
        <span className="spk-pname">{who(p)}</span>
        <span className="spk-detail">{detail(p)}</span>
        {p.hints?.length ? <span className="spk-hint">{p.hints.join(' · ')}</span> : null}
      </span>
      <button type="button" className="icon-btn xs spk-yes" aria-label={t('speakr.speakerPeople.thisIs', { who: who(p), email: p.email })} data-tip={t('common.confirm')} onClick={onConfirm}><Check size={16} /></button>
      <button type="button" className="icon-btn xs" aria-label={t('speakr.speakerPeople.not3', { who: who(p), email: p.email })} data-tip={t('speakr.speakerPeople.notThem')} onClick={onDismiss}><X size={16} /></button>
    </span>
  )
}

// ACT speaker.map @web.recording (someone else: a search of People)
function PeoplePicker({ speaker, all, onPick, onClose }: { speaker: string; all: boolean; onPick: (p: { email: string; name: string }) => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const list = usePeopleList(q.trim(), 20)
  const rows = list.data ?? []
  const typed = q.trim().toLowerCase()
  const asAddress = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(typed) && !rows.some((p) => p.email.toLowerCase() === typed)
  return (
    <Dialog open onClose={onClose} title={t('speakr.speakerPeople.whoIs', { speaker })} initialFocus="input">
      <input className="input spk-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('speakr.speakerPeople.searchByNameAddressOr')}
        aria-label={t('speakr.speakerPeople.searchPeople')} />
      {all && <p className="spk-note">{ALL_NOTE}</p>}
      <ul className="spk-results" aria-label={t('common.people')}>
        {asAddress && (
          <li><button type="button" className="spk-result" onClick={() => onPick({ email: typed, name: '' })}>
            <Avatar who={{ email: typed, name: '' }} size="xs" /><span className="spk-text"><span className="spk-pname">{typed}</span></span>
          </button></li>
        )}
        {rows.map((p) => (
          <li key={p.email}>
            <button type="button" className="spk-result" onClick={() => onPick({ email: p.email, name: p.name })}>
              <Avatar who={p} size="xs" />
              <span className="spk-text">
                <span className="spk-pname">{who(p)}</span>
                <span className="spk-detail">{[p.email, p.title, p.organization].filter(Boolean).join(' · ')}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {!list.isLoading && !rows.length && !asAddress && <p className="muted">{q ? t('speakr.speakerPeople.nobodyMatchesTypeAnAddress') : t('speakr.speakerPeople.typeAName')}</p>}
    </Dialog>
  )
}
