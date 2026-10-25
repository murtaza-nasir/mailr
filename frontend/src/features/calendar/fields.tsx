// The editor's inputs: typed date and time fields (the quick-add grammar, arrow keys, a
// mini-month or time list popup), the repeat options (smart options, Custom… with a live
// summary), the guest field (a token field with People autocomplete), reminder chips (offsets
// before the start) and the colour palette.

import { Check, ChevronLeft, ChevronRight, Plus, X } from 'lucide-react'
import { useEffect, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent, type Ref } from 'react'
import { useMe, usePeople } from '../../api/hooks'
import {
  addDaysISO, addMonthsISO, formatDate, isISODate, monthGrid, parseISODate, startOfMonthISO, todayISO, WEEKDAY_SHORT,
  type ISODate, type Weekday,
} from '../../lib/dates'
import { OFFSETS, offsetLabel } from '../../lib/quickChoices'
import { parse } from '../../lib/quickadd'
import { DAY_CODES, describeRepeat, formatRRule, isEditable, matchSmartOption, parseRRule, smartOptions, type DayCode, type RRule } from '../../lib/rrule'
import { useGroups } from '../people/api'
import { useSearchAnswer } from '../people/searchAnswer'
import { GroupChip, GroupOption } from '../people/GroupChip'
import { addGroup, groupOptions, partition, prune, removeGroup, type Expanded } from '../people/groupChips'
import { SWATCHES } from './colour'
import { formatDuration, parseTimeText, timeSlots } from './geometry'
import { orgDomains, outsideWarning } from './guests'
import { t as tr } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

// ---------- date ----------

/** A typed date: the date part of the quick-add grammar ("tom", "fri", "12 oct", "+3d", "eom"), or ISO. */
export function parseDateText(text: string, now: Date, zone: string): ISODate | null {
  const t = text.trim().replace(/^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+(?=\d)/i, '')
  if (!t) return null
  if (isISODate(t)) return t
  const r = parse(t, { mode: 'task', now, tz: zone })
  if (!r.due || r.title.replace(/[,.\s]/g, '')) return null
  return r.due.slice(0, 10)
}

export function DateField({ value, onChange, label, now, zone, weekStart, invalid: outerInvalid }: {
  value: ISODate; onChange: (d: ISODate) => void; label: string; now: Date; zone: string; weekStart: Weekday; invalid?: boolean
}) {
  const [text, setText] = useState(formatDate(value, 'dayYear'))
  const [editing, setEditing] = useState(false)
  const [bad, setBad] = useState(false)
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(value)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!editing) setText(formatDate(value, 'dayYear'))
    setCursor(value)
  }, [value, editing])
  const commit = () => {
    const d = parseDateText(text, now, zone)
    if (d) {
      setBad(false)
      onChange(d)
      setText(formatDate(d, 'dayYear'))
      setEditing(false)
      return true
    }
    setBad(true)
    return false
  }
  const step = (n: number, unit: 'day' | 'month') => {
    const d = unit === 'day' ? addDaysISO(value, n) : addMonthsISO(value, n)
    onChange(d)
    setEditing(false)
    setBad(false)
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault()
      const dir = e.key === 'ArrowUp' ? -1 : 1
      if (open) {
        // Arrows move the focused day in the popup; Enter picks it.
        setCursor(addDaysISO(cursor, dir * 7))
        return
      }
      step(dir * (e.shiftKey ? 7 : 1), 'day')
    } else if (open && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !editing) {
      e.preventDefault()
      setCursor(addDaysISO(cursor, arrowStep(e.key)))
    } else if (e.key === 'PageUp' || e.key === 'PageDown') {
      e.preventDefault()
      step(e.key === 'PageUp' ? -1 : 1, 'month')
    } else if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      if (editing) {
        if (commit()) setOpen(false)
      } else if (open) {
        onChange(cursor)
        setOpen(false)
      }
    } else if (e.key === 'Escape') {
      if (open || editing) {
        e.preventDefault()
        e.stopPropagation()
        setText(formatDate(value, 'dayYear'))
        setEditing(false)
        setBad(false)
        setOpen(false)
      }
    }
  }
  return (
    <div className="cal-datefield" ref={wrap} onBlur={(e) => { if (!wrap.current?.contains(e.relatedTarget as Node)) { if (editing) commit(); setOpen(false) } }}>
      <input
        className={`input cal-when-input${bad || outerInvalid ? ' invalid' : ''}`}
        value={text}
        aria-label={label}
        aria-invalid={bad || undefined}
        onFocus={(e) => { e.currentTarget.select(); setOpen(true) }}
        onChange={(e) => { setText(e.target.value); setEditing(true); setBad(false) }}
        onKeyDown={onKey}
      />
      {open && (
        <div className="cal-pop-under cal-datepop" role="dialog" aria-label={tr('calendar.fields.pickADate', { label })}>
          <div className="cal-datepop-presets">
            {[[tr('common.today'), todayISO(zone, now)], [tr('common.tomorrow'), addDaysISO(todayISO(zone, now), 1)], [tr('common.nextWeek'), addDaysISO(todayISO(zone, now), 7)]].map(([l, d]) => (
              <button key={l} type="button" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => { onChange(d); setOpen(false) }}>{l}</button>
            ))}
          </div>
          <MiniPicker value={value} cursor={cursor} today={todayISO(zone, now)} weekStart={weekStart} onPick={(d) => { onChange(d); setOpen(false) }} onCursor={setCursor} />
        </div>
      )}
    </div>
  )
}

function MiniPicker({ value, cursor, today, weekStart, onPick, onCursor }: {
  value: ISODate; cursor: ISODate; today: ISODate; weekStart: Weekday; onPick: (d: ISODate) => void; onCursor: (d: ISODate) => void
}) {
  const month = startOfMonthISO(cursor)
  const weeks = useMemo(() => monthGrid(month, weekStart), [month, weekStart])
  const m = parseISODate(month).month
  return (
    <div className="cal-minipick" onMouseDown={(e) => e.preventDefault()}>
      <div className="cal-mini-head">
        <span className="cal-mini-title">{formatDate(month, 'monthYear')}</span>
        <button type="button" tabIndex={-1} className="icon-btn xs" aria-label={tr('common.previousMonth')} onClick={() => onCursor(addMonthsISO(cursor, -1))}><ChevronLeft size={16} /></button>
        <button type="button" tabIndex={-1} className="icon-btn xs" aria-label={tr('common.nextMonth')} onClick={() => onCursor(addMonthsISO(cursor, 1))}><ChevronRight size={16} /></button>
      </div>
      <div className="cal-mini-grid">
        {weeks[0].map((d) => <span key={d} className="cal-mini-wd">{formatDate(d, 'weekday').slice(0, 1)}</span>)}
        {weeks.flat().map((d) => (
          <button key={d} type="button" tabIndex={-1} className={`cal-mini-day${parseISODate(d).month === m ? '' : ' out'}${d === today ? ' today' : ''}${d === value ? ' current' : ''}${d === cursor ? ' cursor' : ''}`} onClick={() => onPick(d)}>
            {Number(d.slice(8))}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------- time ----------

export function TimeField({ value, onChange, label, step, hour12, durationFrom, invalid }: {
  value: number; onChange: (m: number) => void; label: string; step: number; hour12: boolean
  /** For the end field: the start, so the list shows durations ("4:30 PM (1 h 30 min)"). */
  durationFrom?: number; invalid?: boolean
}) {
  const show = (m: number) => {
    const h = Math.floor(m / 60) % 24
    const mm = String(m % 60).padStart(2, '0')
    return hour12 ? `${h % 12 || 12}:${mm} ${h < 12 ? 'AM' : 'PM'}` : `${String(h).padStart(2, '0')}:${mm}`
  }
  const [text, setText] = useState(show(value))
  const [editing, setEditing] = useState(false)
  const [bad, setBad] = useState(false)
  const [open, setOpen] = useState(false)
  const list = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!editing) setText(show(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editing, hour12])
  useEffect(() => {
    if (open) list.current?.querySelector('.current')?.scrollIntoView({ block: 'center' })
  }, [open])
  const commit = () => {
    const m = parseTimeText(text)
    if (m == null) {
      setBad(true)
      return false
    }
    setBad(false)
    setEditing(false)
    onChange(m)
    return true
  }
  const slots = useMemo(() => {
    const all = timeSlots(15)
    return durationFrom == null ? all : all.map((m) => (m <= durationFrom ? m + 1440 : m)).sort((a, b) => a - b).filter((m) => m > durationFrom && m - durationFrom <= 1440)
  }, [durationFrom])
  return (
    <div className="cal-timefield" ref={wrap} onBlur={(e) => { if (!wrap.current?.contains(e.relatedTarget as Node)) { if (editing) commit(); setOpen(false) } }}>
      <input
        className={`input cal-when-input time${bad || invalid ? ' invalid' : ''}`}
        value={text}
        aria-label={label}
        aria-invalid={bad || undefined}
        onFocus={(e) => { e.currentTarget.select(); setOpen(true) }}
        onChange={(e) => { setText(e.target.value); setEditing(true); setBad(false) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault()
            const d = (e.key === 'ArrowUp' ? -1 : 1) * (e.shiftKey ? 60 : step)
            onChange(value + d)
            setEditing(false)
          } else if (e.key === 'Enter') {
            e.preventDefault()
            e.stopPropagation()
            if (commit()) setOpen(false)
          } else if (e.key === 'Escape' && (open || editing)) {
            e.preventDefault()
            e.stopPropagation()
            setEditing(false)
            setBad(false)
            setOpen(false)
          }
        }}
      />
      {open && (
        <div className="cal-pop-under cal-timepop" ref={list} role="listbox" aria-label={label} onMouseDown={(e) => e.preventDefault()}>
          {slots.map((m) => (
            <button key={m} type="button" tabIndex={-1} role="option" aria-selected={m % 1440 === value % 1440} className={m % 1440 === value % 1440 ? 'current' : ''} onClick={() => { onChange(durationFrom != null && m >= 1440 ? m : m % 1440); setOpen(false); setEditing(false) }}>
              {show(m % 1440)}
              {durationFrom != null && <span className="muted"> ({formatDuration(m - durationFrom)})</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------- repeat ----------

export function RepeatPicker({ value, onChange, date }: { value: string | null; onChange: (r: string | null) => void; date: ISODate }) {
  const opts = smartOptions(date)
  const current = matchSmartOption(value, date)
  const rule = parseRRule(value)
  const locked = !!rule && !isEditable(rule)
  const [custom, setCustom] = useState(current === 'custom' && !!value && !locked)
  return (
    <div className="cal-repeat">
      <select
        className="input"
        aria-label={tr('common.repeat')}
        value={locked ? 'locked' : custom ? 'custom' : current}
        onChange={(e) => {
          const id = e.target.value
          if (id === 'custom') {
            setCustom(true)
            if (!value) onChange(`FREQ=WEEKLY;BYDAY=${DAY_CODES[new Date(`${date}T12:00:00Z`).getUTCDay()]}`)
            return
          }
          setCustom(false)
          onChange(opts.find((o) => o.id === id)?.rrule ?? null)
        }}
      >
        {locked && <option value="locked">{tr('calendar.fields.customRuleSetInAnother')}</option>}
        {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      {custom && value && !locked && <CustomRepeat value={value} onChange={onChange} date={date} />}
      {value && <p className="cal-repeat-summary" aria-live="polite">{describeRepeat(value, date)}</p>}
    </div>
  )
}

function CustomRepeat({ value, onChange, date }: { value: string; onChange: (r: string) => void; date: ISODate }) {
  const rule = parseRRule(value) ?? { freq: 'WEEKLY', interval: 1, raw: value } as RRule
  const set = (patch: Partial<RRule>) => onChange(formatRRule({ ...rule, ...patch, raw: undefined }))
  const day = parseISODate(date).day
  const wd = DAY_CODES[new Date(`${date}T12:00:00Z`).getUTCDay()]
  const nth = Math.ceil(day / 7)
  const ends = rule.until ? 'on' : rule.count ? 'after' : 'never'
  return (
    <div className="cal-custom-repeat">
      <div className="cal-row-inline">
        <span>{tr('common.every')}</span>
        <input type="number" min={1} max={99} className="input num" value={rule.interval} aria-label={tr('calendar.fields.interval')} onChange={(e) => set({ interval: Math.max(1, Number(e.target.value) || 1) })} />
        <select className="input" aria-label={tr('common.unit')} value={rule.freq} onChange={(e) => {
          const freq = e.target.value as RRule['freq']
          set({ freq, byDay: freq === 'WEEKLY' ? [{ day: wd }] : undefined, byMonthDay: undefined, byMonth: undefined })
        }}>
          <option value="DAILY">{rule.interval > 1 ? 'days' : 'day'}</option>
          <option value="WEEKLY">{rule.interval > 1 ? 'weeks' : 'week'}</option>
          <option value="MONTHLY">{rule.interval > 1 ? 'months' : 'month'}</option>
          <option value="YEARLY">{rule.interval > 1 ? 'years' : 'year'}</option>
        </select>
      </div>
      {rule.freq === 'WEEKLY' && (
        <div className="cal-weekdays" role="group" aria-label={tr('calendar.fields.on')}>
          {DAY_CODES.map((c: DayCode, i) => {
            const on = rule.byDay?.some((b) => b.day === c) ?? c === wd
            return (
              <button key={c} type="button" className={`cal-wdtoggle${on ? ' on' : ''}`} aria-pressed={on} aria-label={WEEKDAY_SHORT[i]} onClick={() => {
                const cur = rule.byDay?.map((b) => b.day) ?? [wd]
                const next = on ? cur.filter((x) => x !== c) : [...cur, c]
                if (next.length) set({ byDay: DAY_CODES.filter((x) => next.includes(x)).map((d) => ({ day: d })) })
              }}>{WEEKDAY_SHORT[i][0]}</button>
            )
          })}
        </div>
      )}
      {rule.freq === 'MONTHLY' && (
        <select className="input" aria-label={tr('calendar.fields.monthlyOn')} value={rule.byDay?.length ? 'nth' : 'day'} onChange={(e) => set(e.target.value === 'nth' ? { byDay: [{ day: wd, nth: nth > 4 ? -1 : nth }], byMonthDay: undefined } : { byDay: undefined, byMonthDay: day })}>
          <option value="day">{tr('calendar.fields.onDay', { day })}</option>
          <option value="nth">{tr('calendar.fields.onThe', { value: ['first', 'second', 'third', 'fourth', 'last'][Math.min(nth, 5) - 1], date: formatDate(date, 'weekdayLong') })}</option>
        </select>
      )}
      <div className="cal-ends" role="radiogroup" aria-label={tr('common.ends')}>
        <span>{tr('common.ends')}</span>
        <label className="cal-radio"><input type="radio" checked={ends === 'never'} onChange={() => set({ until: undefined, count: undefined })} />{' '}{tr('common.never')}</label>
        <label className="cal-radio"><input type="radio" checked={ends === 'on'} onChange={() => set({ until: addDaysISO(date, 90), count: undefined })} />{' '}{tr('calendar.fields.on')}
          {ends === 'on' && <input type="date" className="input" value={untilISO(rule.until)} onChange={(e) => e.target.value && set({ until: e.target.value })} aria-label={tr('calendar.fields.endDate')} />}
        </label>
        <label className="cal-radio"><input type="radio" checked={ends === 'after'} onChange={() => set({ count: 10, until: undefined })} />{' '}{tr('calendar.fields.after')}
          {ends === 'after' && <><input type="number" min={1} max={999} className="input num" value={rule.count ?? 10} onChange={(e) => set({ count: Math.max(1, Number(e.target.value) || 1) })} aria-label={tr('calendar.fields.occurrences')} />{' '}{tr('calendar.fields.times')}</>}
        </label>
      </div>
    </div>
  )
}

function untilISO(u: string | undefined): string {
  if (!u) return ''
  return isISODate(u) ? u : `${u.slice(0, 4)}-${u.slice(4, 6)}-${u.slice(6, 8)}`
}

// ---------- guests ----------

export interface Guest {
  email: string
  name?: string
  partstat?: string
  optional?: boolean
}

const EMAILS = /[^\s<>,;"']+@[^\s<>,;"']+\.[^\s<>,;"']+/g

// People ranked by strength (ACT person.find @web.calendar) and contact groups: a group whose name
// matches is offered first; choosing it adds the members with one summary chip that opens to them
// (ACT group.expand @web.calendar).
/** What Save asks the guest field before it writes (EventEditor). */
export interface GuestFieldHandle {
  /** Typed text not yet a chip becomes guests: returns the new guest list, `guests` unchanged when
   *  nothing is typed, or null when the text is not an address (the field then says so). */
  commit(): Guest[] | null
}

/** The guests typed text stands for: every address in it (as Enter takes them), or null when there
 *  is text but no address. */
export function typedGuests(text: string): Guest[] | null {
  const t = text.trim()
  if (!t) return []
  const found = t.match(EMAILS)
  return found ? found.map((email) => ({ email })) : null
}

export function GuestField({ guests, onChange, handle }: { guests: Guest[]; onChange: (g: Guest[]) => void; handle?: Ref<GuestFieldHandle> }) {
  const [q, setQ] = useState('')
  const [bad, setBad] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const [expanded, setExpanded] = useState<Expanded[]>([])
  const [openGroup, setOpenGroup] = useState<number | null>(null)
  const peopleQ = usePeople(q.trim())
  const groupsQ = useGroups(q.trim(), 'active', q.trim().length > 0)
  // Only suggestions for what is typed now (never the last search's); Enter before they arrive acts when they do.
  const search = useSearchAnswer(q.trim(), [peopleQ, groupsQ], () => enter())
  const people = search.answered ? peopleQ.data ?? [] : []
  const groups = search.answered ? groupsQ.data ?? [] : []
  const groupOpts = groupOptions(groups, q, expanded)
  const me = useMe().data
  const domains = useMemo(() => orgDomains([me?.user?.email ?? '', ...(me?.identities ?? []).map((i) => i.email)]), [me])
  const outside = outsideWarning(guests, domains)
  const personOpts = q.trim() ? people.filter((p) => p.email && !guests.some((g) => g.email.toLowerCase() === p.email.toLowerCase())).slice(0, 6) : []
  type Opt = { group: (typeof groupOpts)[number]; person?: undefined } | { person: (typeof personOpts)[number]; group?: undefined }
  const options: Opt[] = [...groupOpts.map((g) => ({ group: g })), ...personOpts.map((p) => ({ person: p }))]
  const chips = partition(guests, prune(guests, expanded))
  /** Enter: the highlighted suggestion, else the addresses typed; false when there is neither. */
  const enter = () => {
    const opt = options[active]
    const typed = q.trim().match(EMAILS)
    if (!opt && !typed) return false
    if (opt?.group) pickGroup(opt.group)
    else add(opt?.person ? [{ email: opt.person.email, name: opt.person.name }] : typed!.map((email) => ({ email })))
    return true
  }
  const change = (g: Guest[]) => {
    setExpanded((x) => prune(g, x))
    onChange(g)
  }
  const pickGroup = (g: (typeof groupOpts)[number]) => {
    const res = addGroup(guests, g, g.members.map((m) => ({ email: m.email, name: m.name })))
    setExpanded((x) => [...prune(guests, x), res.expanded])
    onChange(res.value)
    setQ('')
    setActive(0)
  }
  const merged = (list: Guest[]) => {
    const seen = new Set(guests.map((g) => g.email.toLowerCase()))
    return [...guests, ...list.filter((g) => !seen.has(g.email.toLowerCase()) && seen.add(g.email.toLowerCase()))]
  }
  const add = (list: Guest[]) => {
    const next = merged(list)
    if (next.length !== guests.length) onChange(next)
    setQ('')
    setActive(0)
    setBad(null)
  }
  // Save never drops what is typed here: an address becomes a guest (also while an Enter waits for
  // the suggestions), and other text stops Save with a message under the field.
  useImperativeHandle(handle, () => ({
    commit() {
      const typed = typedGuests(q)
      if (typed === null) {
        setBad(tr('calendar.fields.isNotAnEmailAddress', { q: q.trim() }))
        return null
      }
      if (!typed.length) return guests
      const next = merged(typed)
      add(typed)
      return next
    },
  }))
  return (
    <div className="cal-guests">
      <div className="cal-guest-input">
        <input
          className="input"
          placeholder={tr('calendar.fields.addGuests')}
          aria-label={tr('calendar.fields.addGuests')}
          value={q}
          role="combobox"
          aria-expanded={options.length > 0}
          aria-autocomplete="list"
          aria-invalid={bad ? true : undefined}
          aria-describedby={bad ? 'cal-guests-bad' : undefined}
          onChange={(e) => {
            setQ(e.target.value)
            setBad(null)
          }}
          onPaste={(e) => {
            const found = e.clipboardData.getData('text').match(EMAILS)
            if (found && found.length > 1) {
              e.preventDefault()
              add(found.map((email) => ({ email })))
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && options.length) { e.preventDefault(); setActive((active + 1) % options.length) }
            else if (e.key === 'ArrowUp' && options.length) { e.preventDefault(); setActive((active - 1 + options.length) % options.length) }
            else if (e.key === 'Enter' && q.trim() && !search.ready) {
              e.preventDefault()
              e.stopPropagation()
              search.deferEnter()
            } else if (e.key === 'Enter' || e.key === ',' || (e.key === 'Tab' && q.trim())) {
              if (enter()) {
                e.preventDefault()
                e.stopPropagation()
              }
            } else if (e.key === 'Backspace' && !q && guests.length) change(guests.slice(0, -1))
          }}
        />
        {options.length > 0 && (
          <div className="cal-pop-under cal-people" role="listbox" onMouseDown={(e) => e.preventDefault()}>
            {options.map((o, i) => o.group !== undefined ? (
              <GroupOption key={`g${o.group.id}`} className="" name={o.group.name} count={o.group.count} active={i === active} onPick={() => pickGroup(o.group)} />
            ) : o.person && (
              <button key={o.person.email} type="button" role="option" aria-selected={i === active} className={i === active ? 'active' : ''} onClick={() => add([{ email: o.person.email, name: o.person.name }])}>
                <span className="cal-guest-avatar">{(o.person.name || o.person.email)[0]?.toUpperCase()}</span>
                <span className="cal-people-name">{o.person.name || o.person.email}</span>
                <span className="muted">{o.person.email}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {bad && <p className="cal-note error" id="cal-guests-bad" role="alert" data-testid="cal-guests-bad">{bad}</p>}
      {guests.length >= 50 && <p className="cal-note warn">{tr('calendar.fields.n50Guests')}</p>}
      {outside && <p className="cal-note warn" data-testid="cal-guests-outside">{outside}</p>}
      <ul className="cal-guest-chips">
        {chips.groups.map((s) => (
          <li key={`g${s.group.id}`} className="cal-guest-group">
            <GroupChip shown={s} className="cal-guest-chip" open={openGroup === s.group.id}
              onToggle={() => setOpenGroup((o) => (o === s.group.id ? null : s.group.id))}
              onRemove={() => { setExpanded((x) => x.filter((g) => g !== s.group)); onChange(removeGroup(guests, s.group)) }}
              renderMember={(g) => <GuestChip key={g.email} g={g} guests={guests} onChange={change} />} />
          </li>
        ))}
        {chips.loose.map((g) => <li key={g.email}><GuestChip g={g} guests={guests} onChange={change} /></li>)}
      </ul>
    </div>
  )
}

function GuestChip({ g, guests, onChange }: { g: Guest; guests: Guest[]; onChange: (g: Guest[]) => void }) {
  return (
    <span className="cal-guest-chip" data-testid="cal-guest">
      <span className="cal-guest-avatar">{(g.name || g.email)[0]?.toUpperCase()}</span>
      <span className="cal-guest-name" title={g.email}>{g.name || g.email}</span>
      {g.partstat && g.partstat !== 'NEEDS-ACTION' && <span className={`cal-guest-ps ${g.partstat.toLowerCase()}`}>{g.partstat === 'ACCEPTED' ? tr('common.yes') : g.partstat === 'DECLINED' ? tr('common.no') : tr('common.maybe')}</span>}
      <button type="button" className={`cal-opt${g.optional ? ' on' : ''}`} aria-pressed={!!g.optional} onClick={() => onChange(guests.map((x) => x === g ? { ...x, optional: !x.optional } : x))}>{tr('calendar.fields.optional')}</button>
      <button type="button" className="icon-btn xs" aria-label={tr('calendar.fields.remove', { itemName: g.name || g.email })} onClick={() => onChange(guests.filter((x) => x !== g))}><X size={14} /></button>
    </span>
  )
}

// ---------- reminders ----------

/** The all-day options: minutes from local midnight of the day ("On the day at 9:00" = −540). */
export function allDayOffsets(morningHour: number): { m: number; label: string }[] {
  const h = morningHour * 60
  const at = `${morningHour}:00`
  return [
    { m: -h, label: tr('calendar.fields.onTheDayAt', { at }) },
    { m: 1440 - h, label: tr('calendar.fields.n1DayBeforeAt', { at }) },
    { m: 2880 - h, label: tr('calendar.fields.n2DaysBeforeAt', { at }) },
    { m: 10080 - h, label: tr('calendar.fields.n1WeekBeforeAt', { at }) },
  ]
}

export function ReminderChips({ value, onChange, allDay, morningHour }: { value: number[]; onChange: (v: number[]) => void; allDay: boolean; morningHour: number }) {
  const [custom, setCustom] = useState(false)
  const [amount, setAmount] = useState(1)
  const [unit, setUnit] = useState(allDay ? 1440 : 60)
  const opts = allDay ? allDayOffsets(morningHour) : OFFSETS.map((m) => ({ m, label: offsetLabel(m) }))
  const known = new Set(opts.map((o) => o.m))
  const extra = value.filter((m) => !known.has(m))
  const toggle = (m: number) => onChange(value.includes(m) ? value.filter((x) => x !== m) : [...value, m].sort((a, b) => a - b))
  return (
    <div className="cal-chips" role="group" aria-label={tr('common.reminders')}>
      {opts.map((o) => (
        <button key={o.m} type="button" className={`chip cal-chip${value.includes(o.m) ? ' on' : ''}`} aria-pressed={value.includes(o.m)} onClick={() => toggle(o.m)}>
          {value.includes(o.m) && <Check size={14} aria-hidden />}{o.label}
        </button>
      ))}
      {extra.map((m) => (
        <button key={m} type="button" className="chip cal-chip on" aria-pressed onClick={() => toggle(m)}><Check size={14} aria-hidden />{offsetLabel(m)}</button>
      ))}
      {custom ? (
        <span className="cal-custom-offset">
          <input type="number" min={1} max={99} className="input num" value={amount} onChange={(e) => setAmount(Math.max(1, Number(e.target.value) || 1))} aria-label={tr('common.amount')} />
          <select className="input" value={unit} onChange={(e) => setUnit(Number(e.target.value))} aria-label={tr('common.unit')}>
            <option value={1}>{tr('calendar.fields.minutes')}</option>
            <option value={60}>{tr('calendar.fields.hours')}</option>
            <option value={1440}>{tr('calendar.fields.days')}</option>
            <option value={10080}>{tr('calendar.fields.weeks')}</option>
          </select>
          <span>{tr('calendar.fields.before')}</span>
          <button type="button" className="btn tonal sm" onClick={() => { toggle(amount * unit); setCustom(false) }}>{tr('common.add')}</button>
        </span>
      ) : (
        <button type="button" className="chip cal-chip" onClick={() => setCustom(true)}><Plus size={14} aria-hidden />{tr('common.custom')}</button>
      )}
    </div>
  )
}

// ---------- colour ----------

export function ColourPicker({ value, onChange, calendarColour, dark }: { value: string | null; onChange: (c: string | null) => void; calendarColour: string; dark: boolean }) {
  return (
    <div className="cal-swatches" role="radiogroup" aria-label={tr('common.colour')}>
      <button type="button" role="radio" aria-checked={!value} aria-label={tr('calendar.fields.calendarColour')} data-tip={tr('calendar.fields.calendarColour')} className="cal-swatch cal-swatch-cal" style={{ background: calendarColour }} onClick={() => onChange(null)}>
        {!value && <Check size={14} aria-hidden />}
      </button>
      {SWATCHES.map((s) => (
        <button key={s.id} type="button" role="radio" aria-checked={value === s.light} aria-label={s.name} data-tip={s.name} className="cal-swatch" style={{ background: dark ? s.dark : s.light }} onClick={() => onChange(s.light)}>
          {value === s.light && <Check size={14} aria-hidden />}
        </button>
      ))}
    </div>
  )
}
