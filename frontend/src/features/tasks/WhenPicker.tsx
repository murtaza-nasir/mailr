// The when picker (web typed form) for tasks: presets with accelerators, a typed field through the
// grammar, a mini-month and a time with "No time". Used by task.reschedule (the due),
// task.set_start (defer), the bulk reschedule and "Remind me at a time…".

import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { addMonthsISO, formatDate, monthGrid, todayISO, WEEKDAY_SHORT, type DatePrefs, type ISODate } from '../../lib/dates'
import { describe, isDateOnly, join, parseTime, parseWhen, presetHint, presets, split } from './when'
import { t as tr } from '../../i18n'

interface Props {
  value: string | null
  onPick: (value: string | null) => void
  now: Date
  prefs: DatePrefs
  /** "Reschedule", "Start", "Remind at". */
  label: string
  noDate?: boolean
  laterToday?: boolean
  /** Extra rows under the presets (bulk: Shift by +1 day / +1 week). */
  extra?: ReactNode
  /** A time is required (an absolute reminder). */
  needTime?: boolean
}

export function WhenPicker({ value, onPick, now, prefs, label, noDate = true, laterToday = true, extra, needTime }: Props) {
  const zone = prefs.zone
  const cur = split(value, zone)
  const today = todayISO(zone, now)
  const [text, setText] = useState('')
  const [month, setMonth] = useState<ISODate>((cur.day ?? today).slice(0, 8) + '01')
  const [hm, setHm] = useState<string | null>(cur.hm ?? (needTime ? '09:00' : null))
  const [timeText, setTimeText] = useState(cur.hm ?? (needTime ? '09:00' : ''))
  const inputRef = useRef<HTMLInputElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const list = useMemo(() => presets(now, value, zone, { noDate, laterToday }), [now, value, zone, noDate, laterToday])
  const typed = text.trim() ? parseWhen(text, now, zone, cur.hm) : undefined
  const invalid = !!text.trim() && typed === undefined

  // The popover places itself after the first frame; focus waits until it can be focused.
  useEffect(() => {
    let n = 0
    let raf = 0
    const tick = () => {
      const el = rootRef.current
      if (!el || el.contains(document.activeElement)) return
      el.focus()
      if (document.activeElement !== el && n++ < 20) raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [])

  const pick = (v: string | null) => onPick(needTime && v && isDateOnly(v) ? join(v, hm ?? '09:00', zone) : v)

  // Accelerators work before typing starts; any other character starts the typed field.
  const onRootKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== rootRef.current) return
    if (e.ctrlKey || e.metaKey || e.altKey) return
    const p = list.find((x) => x.key === e.key)
    if (p) {
      e.preventDefault()
      e.stopPropagation()
      pick(p.value)
      return
    }
    if (e.key.length === 1 && e.key !== ' ') {
      e.preventDefault()
      e.stopPropagation()
      setText(e.key)
      inputRef.current?.focus()
    }
  }

  const onTextKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      if (typed !== undefined) pick(typed)
    }
  }

  const grid = monthGrid(month, prefs.weekStart)
  const commitTime = (t: string) => {
    const v = parseTime(t)
    if (!v) return
    setHm(v)
    setTimeText(v)
    if (cur.day) onPick(join(cur.day, v, zone))
  }

  return (
    <div className="tk-when" ref={rootRef} tabIndex={-1} onKeyDown={onRootKey} aria-label={label} data-testid="when-picker">
      <div className="tk-when-head">{label}</div>
      <input
        ref={inputRef}
        className={`tk-when-input${invalid ? ' invalid' : ''}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onTextKey}
        placeholder={tr('tasks.whenPicker.typeADateFri3p')}
        aria-label={tr('tasks.whenPicker.typeADate')}
        aria-invalid={invalid}
      />
      <div className={`tk-when-result${invalid ? ' invalid' : ''}`} aria-live="polite">
        {text.trim() ? (invalid ? tr('common.notADate') : describe(typed ?? null, now, prefs)) : value ? tr('tasks.whenPicker.now', { describe: describe(value, now, prefs) }) : tr('tasks.whenPicker.noDateSet')}
      </div>
      <div className="tk-when-body">
        <div className="tk-when-presets" role="group" aria-label={tr('tasks.whenPicker.presets')}>
          {list.map((p) => (
            <button key={p.id} type="button" className="tk-preset" onClick={() => pick(p.value)} data-preset={p.id}>
              <span className="tk-preset-label">{p.label}</span>
              <span className="tk-preset-hint">{presetHint(p.value, prefs)}</span>
              {p.key && <kbd>{p.key}</kbd>}
            </button>
          ))}
          {extra}
        </div>
        <div className="tk-mini" aria-label={tr('tasks.whenPicker.pickADay')}>
          <div className="tk-mini-head">
            <button type="button" className="icon-btn sm" aria-label={tr('common.previousMonth')} onClick={() => setMonth(addMonthsISO(month, -1))}><ChevronLeft size={16} /></button>
            <span>{formatDate(month, month.slice(0, 4) === today.slice(0, 4) ? 'month' : 'monthYear')}</span>
            <button type="button" className="icon-btn sm" aria-label={tr('common.nextMonth')} onClick={() => setMonth(addMonthsISO(month, 1))}><ChevronRight size={16} /></button>
          </div>
          <div className="tk-mini-grid" role="grid">
            {grid[0].map((d) => <span key={`h${d}`} className="tk-mini-dow">{WEEKDAY_SHORT[new Date(`${d}T12:00:00Z`).getUTCDay()].slice(0, 2)}</span>)}
            {grid.flat().map((d) => (
              <button
                key={d}
                type="button"
                role="gridcell"
                className={`tk-mini-day${d.slice(0, 7) !== month.slice(0, 7) ? ' out' : ''}${d === today ? ' today' : ''}${d === cur.day ? ' on' : ''}`}
                aria-label={formatDate(d, 'long')}
                aria-selected={d === cur.day}
                onClick={() => pick(join(d, hm, zone))}
              >
                {Number(d.slice(8))}
              </button>
            ))}
          </div>
          <div className="tk-when-time">
            {!needTime && (
              <label className="tk-check">
                <input type="checkbox" checked={!hm} onChange={(e) => {
                  const next = e.target.checked ? null : (parseTime(timeText) ?? '09:00')
                  setHm(next)
                  if (!next) setTimeText('')
                  else setTimeText(next)
                  if (cur.day) onPick(join(cur.day, next, zone))
                }} />
                {tr('tasks.whenPicker.noTime')}
              </label>
            )}
            <input
              className="tk-time-input"
              aria-label={tr('common.time')}
              placeholder={tr('common.time')}
              disabled={!hm && !needTime}
              value={timeText}
              onChange={(e) => setTimeText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  e.stopPropagation()
                  commitTime(timeText)
                }
              }}
              onBlur={() => timeText && parseTime(timeText) !== hm && commitTime(timeText)}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
