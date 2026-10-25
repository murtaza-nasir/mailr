// "Repeat…" in a list's ⋮ menu: the dialog that sets it, and
// the quiet line the editor shows under the list ("Repeats weekly on Sunday · next Sun 11 Oct").
// A typed repeat ("every sunday", "every 3 days") is read with the task grammar (repeatModel.ts).

import { useQueryClient } from '@tanstack/react-query'
import { Repeat } from 'lucide-react'
import { useState } from 'react'
import { Dialog } from '../../components/Dialog'
import { MenuItem } from '../../components/Popover'
import type { DayCode } from '../../lib/rrule'
import { setRepeat } from './actions'
import { useRemindPrefs } from './remind'
import { listRepeat, repeatFromWords, repeatPhrase, repeatRule, repeatWords, type ListRepeat } from './repeatModel'
import type { Note } from './types'
import { t, appLocale } from '../../i18n'
import { formatInstant } from '../../lib/dates'

const DAY_NAMES: Record<DayCode, string> = { SU: t('notes.repeat.sunday'), MO: t('notes.repeat.monday'), TU: t('notes.repeat.tuesday'), WE: t('notes.repeat.wednesday'), TH: t('notes.repeat.thursday'), FR: t('notes.repeat.friday'), SA: t('notes.repeat.saturday') }
/** The days in the order of the week (Monday first, as the date settings usually start). */
const WEEK: DayCode[] = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

/** "Repeat…" in the ⋮ menu, with the current repeat as its hint. */
// ACT note.repeat @web.note @web.notes
export function RepeatMenuItem({ note, onOpen }: { note: Note; onOpen: () => void }) {
  if (note.kind !== 'checklist') return null
  const on = !!note.repeat?.rule
  return (
    <MenuItem icon={<Repeat size={16} />} disabled={note.level === 'view' || !!note.deleted_at} onSelect={onOpen}
      tip={on ? repeatWords(note.repeat!.rule) : t('notes.repeat.untickEveryItemWeeklyOn')}>
      {on ? t('notes.repeat.repeat', { repeatPhrase: repeatPhrase(note.repeat!.rule) }) : t('notes.repeat.repeat2')}
    </MenuItem>
  )
}

/** "Repeats weekly on Sunday · next Sun 11 Oct" under a repeating list. */
export function repeatLine(note: Pick<Note, 'repeat'>, zone?: string): string {
  const r = note.repeat
  if (!r?.rule) return ''
  const words = repeatWords(r.rule)
  const next = r.next ? new Date(r.next) : null
  // The reset's day where it happens (the setter's zone), else the viewer's.
  const tz = r.zone || zone
  const when = next && !Number.isNaN(next.getTime())
    ? formatInstant(next, appLocale(), { weekday: 'short', day: 'numeric', month: 'short' }, tz || undefined)
    : ''
  return t('notes.repeat.repeats', { charAt: words.charAt(0).toLowerCase(), words: words.slice(1), value: when ? t('notes.repeat.next', { when }) : '' })
}

type Choice = 'none' | 'weekly' | 'daily'

/** The dialog: Does not repeat, Weekly on a day, Every N days, or typed words. */
export function RepeatDialog({ note, onClose }: { note: Note; onClose: () => void }) {
  const qc = useQueryClient()
  const prefs = useRemindPrefs()
  const cur = listRepeat(note.repeat?.rule)
  const today = WEEK[(new Date().getDay() + 6) % 7]
  const [choice, setChoice] = useState<Choice>(cur?.freq ?? 'none')
  const [day, setDay] = useState<DayCode>(cur?.freq === 'weekly' ? cur.day : today)
  const [weeks] = useState(cur?.freq === 'weekly' ? cur.interval : 1)
  const [days, setDays] = useState(String(cur?.freq === 'daily' ? cur.interval : 3))
  const [typed, setTyped] = useState('')
  const typedRule = typed.trim() ? repeatFromWords(typed, prefs.zone) : null
  const n = Number(days)
  const daysOk = Number.isInteger(n) && n >= 1 && n <= 365
  const chosen: ListRepeat | null | 'invalid' = typed.trim()
    ? (typedRule ?? 'invalid')
    : choice === 'none' ? null : choice === 'weekly' ? { freq: 'weekly', day, interval: weeks } : daysOk ? { freq: 'daily', interval: n } : 'invalid'
  const save = () => {
    if (chosen === 'invalid') return
    const rule = chosen ? repeatRule(chosen) : null
    onClose()
    if (rule === (note.repeat?.rule ?? null)) return
    void setRepeat(qc, note.id, rule)
  }
  const radio = (v: Choice, label: string) => (
    <input type="radio" className="radio" name="nt-repeat" checked={!typed.trim() && choice === v} aria-label={label}
      onChange={() => {
        setChoice(v)
        setTyped('')
      }} />
  )
  return (
    <Dialog open onClose={onClose} title={t('notes.repeat.repeatThisList')} initialFocus="input[name=nt-repeat]:checked"
      footer={(
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={chosen === 'invalid'} onClick={save} data-testid="repeat-save">{t('common.save')}</button>
        </>
      )}>
      <div className="nt-repeat" data-testid="repeat-dialog">
        <p className="muted nt-small">{t('notes.repeat.onEachRepeatEveryTicked')}</p>
        <label className="choice">{radio('none', t('common.doesNotRepeat'))}<span>{t('common.doesNotRepeat')}</span></label>
        <label className="choice">
          {radio('weekly', t('notes.repeat.weekly'))}
          <span>{t('notes.repeat.weeklyOn')}</span>
          <select className="select nt-repeat-day" aria-label={t('notes.repeat.dayOfTheWeek')} value={day} onChange={(e) => {
            setDay(e.target.value as DayCode)
            setChoice('weekly')
            setTyped('')
          }}>
            {WEEK.map((d) => <option key={d} value={d}>{DAY_NAMES[d]}</option>)}
          </select>
          {weeks > 1 && <span className="muted">{t('notes.repeat.everyWeeks', { count: weeks })}</span>}
        </label>
        <label className="choice">
          {radio('daily', t('notes.repeat.everyFewDays'))}
          <span>{t('common.every')}</span>
          <input className="input nt-repeat-n" type="number" inputMode="numeric" min={1} max={365} aria-label={t('notes.repeat.numberOfDays')} value={days}
            onChange={(e) => {
              setDays(e.target.value)
              setChoice('daily')
              setTyped('')
            }} />
          <span>{t('notes.repeat.days', { count: n })}</span>
        </label>
        {choice === 'daily' && !daysOk && !typed.trim() && <p className="nt-small nt-error" role="alert">{t('notes.repeat.typeANumberOfDays')}</p>}
        <label className="field nt-repeat-typed">
          <span className="field-label">{t('notes.repeat.orTypeIt')}</span>
          <input className="input" value={typed} placeholder={t('notes.repeat.everySundayEvery3Days')} maxLength={60} onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                save()
              }
            }} />
          {typed.trim() && <span className={typedRule ? 'hint' : 'error'} role={typedRule ? undefined : 'alert'}>
            {typedRule ? repeatWords(repeatRule(typedRule)) : t('notes.repeat.aListRepeatsWeeklyOn')}
          </span>}
        </label>
      </div>
    </Dialog>
  )
}

