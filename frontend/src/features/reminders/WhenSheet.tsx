// The when sheet on the web: what the quick-action sheet
// expands into for Snooze, Reschedule and Remind before. A line on what the verb changes, preset
// tiles in titled groups, the custom amount + unit with From now | From due time and a live
// result, and a footer with the verb and "Pick date & time…". Keys: 1–9 pick the tiles in order,
// Enter in the custom row confirms. The presets come from lib/whenSheet.ts. Used by ACT task.snooze @web.sheet,
// ACT task.reschedule @web.sheet, ACT event.snooze @web.sheet, ACT event.reschedule @web.sheet and ACT event.remind_before @web.sheet.

import { CalendarDays } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  baseLabel, customResult, hasBaseToggle, initialAmount, nearest, rememberBase, rememberedBase, resultLine, sheetGroups, UNITS,
  verbHint, type Base, type SheetSpec, type Tile, type Unit,
} from '../../lib/whenSheet'
import './whenSheet.css'
import { t as tr, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

export interface WhenSheetProps {
  spec: SheetSpec
  /** The verb on the primary button: Snooze, Reschedule, Move, Remind me. */
  confirm: string
  /** Formats a time of day with the user's clock (12 or 24 hours). */
  time?: (d: Date) => string
  /** The due or start, as the header shows it ("today 12:00 PM"), for the snooze line. */
  anchorText?: string | null
  /** A tile or the custom value; [tile] is set for a tile. */
  onPick: (at: Date, dateOnly: boolean, tile?: Tile) => void
  /** The full picker behind "Pick date & time…"; leaves the button out when absent. */
  full?: (close: () => void) => ReactNode
  /** The name of the tile group, for tests and screen readers ("Snooze until"). */
  groupLabel: string
}

export function WhenSheet({ spec, confirm, time, anchorText, onPick, full, groupLabel }: WhenSheetProps) {
  const groups = useMemo(() => sheetGroups(spec), [spec])
  const toggle = hasBaseToggle(spec)
  const [base, setBase] = useState<Base>(() => rememberedBase(spec))
  const start = initialAmount(spec.verb)
  const [unit, setUnit] = useState<Unit>(start.unit)
  const [amount, setAmount] = useState(start.amount)
  const [showFull, setShowFull] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const result = customResult(spec, base, amount, unit)
  const past = result.dateOnly
    ? new Date(result.at).setHours(23, 59, 59, 999) < spec.now.getTime()
    : result.at.getTime() <= spec.now.getTime()
  // A snooze and a reminder need a moment ahead; a reschedule may land in the past (the task is overdue).
  const blocked = past && spec.verb !== 'reschedule'
  const tiles = groups.flatMap((g) => g.tiles)

  const chooseBase = (b: Base) => {
    setBase(b)
    rememberBase(spec.verb, b)
  }

  // 1–9 pick the numbered tiles while the sheet is open, wherever the focus is (the sheet opens over
  // the page and keeps no focus of its own), except while typing in a field. The listener runs first
  // (capture), so the page's own number keys (a task's priority) wait until the sheet is closed.
  const pick = useRef<(e: globalThis.KeyboardEvent) => void>(() => undefined)
  pick.current = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || !/^[1-9]$/.test(e.key)) return
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
    const tile = tiles[Number(e.key) - 1]
    if (!tile) return
    e.preventDefault()
    e.stopPropagation()
    onPick(tile.at, tile.dateOnly, tile)
  }
  useEffect(() => {
    if (showFull) return
    const on = (e: globalThis.KeyboardEvent) => pick.current(e)
    window.addEventListener('keydown', on, true)
    return () => window.removeEventListener('keydown', on, true)
  }, [showFull])

  if (showFull && full) {
    return (
      <div className="ws ws-full" data-testid="when-sheet-full">
        {full(() => setShowFull(false))}
      </div>
    )
  }

  let n = 0
  return (
    <div className="ws" ref={rootRef} data-testid="when-sheet" data-verb={spec.verb}>
      <p className="ws-hint">{verbHint(spec, anchorText ?? null)}</p>
      {groups.length > 0 && (
        <div className="ws-groups" role="group" aria-label={groupLabel}>
          {groups.map((g) => (
            <section key={g.id} className="ws-group" aria-label={g.label}>
              <h4 className="ws-label">{g.label}</h4>
              <div className="ws-grid">
                {g.tiles.map((t) => {
                  n += 1
                  const key = n <= 9 ? String(n) : null
                  return (
                    <button key={t.id} type="button" className="ws-tile" data-tile={t.id} onClick={() => onPick(t.at, t.dateOnly, t)}
                      aria-keyshortcuts={key ?? undefined}>
                      <span className="ws-tile-label">{t.label}</span>
                      <span className="ws-tile-at">{tileTime(t, spec.now, time)}</span>
                      {key && <kbd aria-hidden>{key}</kbd>}
                    </button>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <section className="ws-custom" aria-label={tr('reminders.whenSheet.custom')}>
        <h4 className="ws-label">{spec.verb === 'remind' ? tr('reminders.whenSheet.customBeforeItStarts') : tr('reminders.whenSheet.custom')}</h4>
        {toggle && (
          <div className="ws-seg" role="radiogroup" aria-label={tr('reminders.whenSheet.countedFrom')}>
            {(['now', 'due'] as Base[]).map((b) => (
              <button key={b} type="button" role="radio" aria-checked={base === b} className={base === b ? 'on' : ''} onClick={() => chooseBase(b)}>
                {b === 'now' ? tr('reminders.whenSheet.fromNow') : baseLabel(spec.kind)}
              </button>
            ))}
          </div>
        )}
        <div className="ws-spin" onKeyDown={(e) => {
          if (e.key === 'Enter' && !blocked) {
            e.preventDefault()
            e.stopPropagation()
            onPick(result.at, result.dateOnly)
          }
        }}>
          <select aria-label={tr('common.amount')} value={amount} onChange={(e) => setAmount(Number(e.target.value))}>
            {UNITS.find((u) => u.id === unit)!.amounts.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <span className="ws-spin-rule" aria-hidden />
          <select aria-label={tr('common.unit')} value={unit} onChange={(e) => {
            const u = e.target.value as Unit
            setUnit(u)
            setAmount(nearest(u, amount))
          }}>
            {UNITS.map((u) => <option key={u.id} value={u.id}>{u.label}{spec.verb === 'remind' ? ' before' : ''}</option>)}
          </select>
        </div>
        <div className={`ws-result${past ? ' past' : ''}`} aria-live="polite" data-testid="when-result">
          {spec.verb === 'remind' ? resultLine(result.at, spec.now, false, time).replace(/^→ (Today|Tomorrow)?/, (_m, d?: string) => tr('reminders.whenSheet.remindsYou', { value: d ? d.toLowerCase() : 'on' })) : resultLine(result.at, spec.now, result.dateOnly, time)}
          {blocked && <span className="ws-past-note">{' '}{tr('reminders.whenSheet.alreadyPassed')}</span>}
        </div>
      </section>

      <div className="ws-foot">
        {full && (
          <button type="button" className="btn text" onClick={() => setShowFull(true)}>
            <CalendarDays size={16} />{' '}{tr('reminders.whenSheet.pickDateTime')}
          </button>
        )}
        <span className="ws-foot-gap" />
        <button type="button" className="btn filled" disabled={blocked} onClick={() => onPick(result.at, result.dateOnly)} data-testid="when-confirm">
          {confirm}
        </button>
      </div>
    </div>
  )
}

function tileTime(t: Tile, now: Date, time?: (d: Date) => string): string {
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((startOf(t.at) - startOf(now)) / 86_400_000)
  const clock = time ? time(t.at) : t.at.toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })
  const day = days === 0 ? tr('common.today') : days === 1 ? tr('common.tomorrow') : t.at.toLocaleDateString(appLocale(), { weekday: 'short', day: 'numeric', month: 'short' })
  if (t.dateOnly) return day
  return days === 0 ? clock : `${day}, ${clock}`
}

const pad = (n: number) => String(n).padStart(2, '0')
const dayValue = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** "Pick date & time…" for a snooze or an event: a day and a time, the live result, Back and the verb. */
export function FullPicker({ initial, now, confirm, time, onBack, onPick }: {
  initial: Date; now: Date; confirm: string; time?: (d: Date) => string
  onBack: () => void; onPick: (at: Date) => void
}) {
  const [day, setDay] = useState(dayValue(initial))
  const [hm, setHm] = useState(`${pad(initial.getHours())}:${pad(initial.getMinutes() - (initial.getMinutes() % 5))}`)
  const at = new Date(`${day}T${hm}:00`)
  const bad = Number.isNaN(at.getTime())
  const past = !bad && at.getTime() <= now.getTime()
  return (
    <div className="ws-pick" data-testid="when-full">
      <div className="ws-pick-fields">
        <label>{tr('common.day')}<input type="date" className="input" value={day} min={dayValue(now)} onChange={(e) => e.target.value && setDay(e.target.value)} /></label>
        <label>{tr('common.time')}<input type="time" className="input" step={300} value={hm} onChange={(e) => e.target.value && setHm(e.target.value)} /></label>
      </div>
      <div className={`ws-result${past ? ' past' : ''}`} aria-live="polite">{bad ? tr('common.notADate') : resultLine(at, now, false, time)}</div>
      <div className="ws-foot">
        <button type="button" className="btn text" onClick={onBack}>{tr('reminders.whenSheet.backToPresets')}</button>
        <span className="ws-foot-gap" />
        <button type="button" className="btn filled" disabled={bad || past} onClick={() => onPick(at)}>{confirm}</button>
      </div>
    </div>
  )
}
