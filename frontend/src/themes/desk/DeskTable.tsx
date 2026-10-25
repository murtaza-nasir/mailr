// ACT mail.star @web.list, ACT mail.archive @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.untrash @web.list (Restore in Trash),
// ACT mail.delete @web.list, ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.snooze @web.list (the row's quick actions)
import { ArrowDown, ArrowUp, Paperclip, Star } from 'lucide-react'
import { memo, useCallback, useEffect, useLayoutEffect, useRef, type PointerEvent as RPointerEvent, type RefObject } from 'react'
import { rowSender, rowAccessibleName } from '../../features/mail/rowText'
import { formatDateTime } from '../../lib/format'
import { useUI } from '../../stores/ui'
import { useListPrefs } from '../listPrefs'
import type { ListRowProps, SortKey } from '../types'
import { ActionChip } from '../../features/mail/ActionChip'
import { RowLabelChips, RowMarker, RowMuted, RowQuickActions, RowSignals, rowLabels, useRowA11y } from '../../features/mail/RowParts'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'
import { dirSign } from '../../lib/rtl'
import { baseView } from '../../lib/views'
import { RowAccounts } from '../../features/layout/accounts'

/** Absolute, sortable dates for a table: "Today 14:05", "Sep 28 09:12", "2025-03-04". */
function tableDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const time = d.toLocaleTimeString(appLocale(), { hour: '2-digit', minute: '2-digit', ...clockOpts() })
  if (d.toDateString() === now.toDateString()) return t('themes.desk.deskTable.today', { time })
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return t('themes.desk.deskTable.yesterday', { time })
  if (d.getFullYear() === now.getFullYear()) return `${d.toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' })} ${time}`
  return d.toLocaleDateString(appLocale(), { year: 'numeric', month: '2-digit', day: '2-digit' })
}

/** One table row: star, attachment, from, subject, labels, message count, date. The quick actions
 *  (RowQuickActions) sit over the Date cell on hover and keyboard focus: no column for them, so
 *  nothing moves. The two-line narrow row has none (a swipe, a long press or right-click instead). */
function DeskRowImpl({ c, labelsById, selected, focused, tabStop, rowIndex, view, narrow, markers, hideLabelId, draggable, onOpen, onToggleSelect, onAction, onSnooze }: ListRowProps) {
  const unread = c.unread_count > 0
  const a11y = useRowA11y({ focused, selected, tabStop, rowIndex, onOpen: () => onOpen(c), name: rowAccessibleName(c, view) })
  const outgoing = baseView(view) === 'sent' || baseView(view) === 'drafts' || baseView(view) === 'scheduled'
  const sender = outgoing
    ? c.participants.filter((p) => !p.me).map((p) => p.name || p.email).join(', ') || rowSender(c)
    : rowSender(c).replace(/ \d+$/, '')
  const chips = rowLabels(c, labelsById, hideLabelId)
  const date = baseView(view) === 'snoozed' && c.snoozed_until ? c.snoozed_until : c.last_date
  return (
    <div
      className={`d-row${unread ? ' unread' : ''}${selected ? ' selected' : ''}${focused ? ' focused' : ''}${narrow ? ' narrow' : ''}`}
      {...a11y}
      data-id={c.id}
      draggable={draggable || undefined}
      onClick={(e) => {
        // Ctrl/⌘-click and Shift-click extend the selection, as in a desktop mail client.
        // With nothing selected yet, Shift-click selects from the open row to this one.
        if (e.shiftKey) {
          const ui = useUI.getState()
          const ids = ui.visibleIds
          const a = ids.indexOf(ui.focusedId ?? -1)
          const b = ids.indexOf(c.id)
          if (!ui.selected.size && a >= 0 && b >= 0) {
            ui.setSelected(new Set(ids.slice(Math.min(a, b), Math.max(a, b) + 1)))
            return
          }
        }
        if (e.ctrlKey || e.metaKey || e.shiftKey) onToggleSelect(c.id, e.shiftKey)
        else onOpen(c)
      }}
    >
      <span className="d-cell d-sel" role="gridcell" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" className="checkbox" checked={selected} onChange={() => undefined} onClick={(e) => onToggleSelect(c.id, e.shiftKey)} aria-label={selected ? t('common.deselect') : t('common.select')} />
      </span>
      <span className="d-cell d-star" role="gridcell" onClick={(e) => e.stopPropagation()}>
        <button type="button" className={`d-starbtn${c.starred ? ' on' : ''}`} aria-label={t('common.star')} aria-pressed={c.starred} onClick={() => onAction(c, c.starred ? 'unstar' : 'star')}>
          <Star />
        </button>
      </span>
      <span className="d-cell d-att" role="gridcell">
        {c.has_attachments && <Paperclip aria-label={t('common.hasAttachment')} />}
      </span>
      <span className="d-cell d-from" role="gridcell" title={c.participants.map((p) => p.email).join(', ')}>
        <span className={`d-dot${unread ? ' on' : ''}`} aria-hidden />
        {markers && (
          <span className="d-marker" onClick={(e) => e.stopPropagation()}>
            <RowMarker c={c} markers={markers} onAction={onAction} />
          </span>
        )}
        {outgoing && <span className="d-to">{t('themes.desk.deskTable.to')}{' '}</span>}
        <span className="d-from-name" dir="auto">{sender}</span>
        {c.draft_count > 0 && <span className="d-draft">{' '}{t('common.draft')}</span>}
        <RowAccounts c={c} />
        <RowMuted c={c} size={12} />
      </span>
      <span className="d-cell d-subject" role="gridcell">
        <span className="d-subject-text" dir="auto">{c.subject || t('common.noSubject')}</span>
        <RowSignals c={c} />
        {narrow && c.snippet && <span className="d-snippet"> — <bdi>{c.snippet}</bdi></span>}
        <ActionChip action={c.primary_action} withFact={false} />
      </span>
      <span className="d-cell d-labels" role="gridcell">
        <RowLabelChips labels={chips} />
      </span>
      <span className="d-cell d-count" role="gridcell">
        {c.message_count > 1 ? c.message_count : ''}
      </span>
      <span className="d-cell d-date" role="gridcell" title={formatDateTime(date)}>
        {tableDate(date)}
      </span>
      {!narrow && (
        <span className="d-actions" role="gridcell" onClick={(e) => e.stopPropagation()}>
          <RowQuickActions c={c} view={view} labelsById={labelsById} onAction={onAction} onSnooze={onSnooze} btn="d-act" />
        </span>
      )}
    </div>
  )
}

export const DeskRow = memo(DeskRowImpl)

const COLS: { key: SortKey | null; label: string; cls: string; resize?: 'from' | 'labels' }[] = [
  { key: null, label: '', cls: 'd-sel' },
  { key: 'star', label: t('common.star'), cls: 'd-star' },
  { key: 'attach', label: t('themes.desk.deskTable.attachment'), cls: 'd-att' },
  { key: 'from', label: t('common.from'), cls: 'd-from', resize: 'from' },
  { key: 'subject', label: t('common.subject'), cls: 'd-subject' },
  { key: 'labels', label: t('common.labels'), cls: 'd-labels', resize: 'labels' },
  { key: 'count', label: t('themes.desk.deskTable.messages'), cls: 'd-count' },
  { key: 'date', label: t('common.date'), cls: 'd-date' },
]

const WIDTH_KEY = 'mailr.desk.cols'
function loadWidths(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(WIDTH_KEY) ?? '{}') as Record<string, number>
  } catch {
    return {}
  }
}

/** Column widths are CSS variables on <html>, so every row follows the header while you drag. */
function applyWidths(w: Record<string, number>) {
  const el = document.documentElement
  if (w.from) el.style.setProperty('--d-from', `${w.from}px`)
  if (w.labels) el.style.setProperty('--d-labels', `${w.labels}px`)
}

function useColumnResize() {
  useEffect(() => {
    applyWidths(loadWidths())
    return () => {
      document.documentElement.style.removeProperty('--d-from')
      document.documentElement.style.removeProperty('--d-labels')
    }
  }, [])
  return useCallback((col: 'from' | 'labels', e: RPointerEvent<HTMLElement>) => {
    e.preventDefault()
    e.stopPropagation()
    const cell = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect()
    const startX = e.clientX
    const start = cell.width
    const min = col === 'from' ? 120 : 70
    const max = col === 'from' ? 420 : 320
    let w = start
    const move = (ev: PointerEvent) => {
      w = Math.max(min, Math.min(max, Math.round(start + (ev.clientX - startX) * dirSign())))
      applyWidths({ [col]: w })
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      try {
        localStorage.setItem(WIDTH_KEY, JSON.stringify({ ...loadWidths(), [col]: w }))
      } catch {
        /* private mode */
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [])
}

/** Least widths, so the headings ("From", "Labels", "#") stay readable. */
// A sender stays readable ("Dana Whitfield", not "D."): 140 px unless dragged narrower.
const FROM_MIN = 140
const LABELS_MIN = 64
const COUNT_MIN = 28

/**
 * Content-aware columns: From and Labels are only as wide as the widest row on screen needs, and
 * # is as wide as its numbers, gone when no row has one. Subject takes the rest. A width you set by
 * dragging still applies (it can make a column narrower); desk.css caps From at a share of the
 * table, so Subject always keeps room. The numbers go on the list's grid as CSS variables.
 */
function useContentColumns(head: RefObject<HTMLDivElement | null>, on: boolean) {
  useLayoutEffect(() => {
    const grid = head.current?.parentElement
    if (!on || !grid) return
    let frame = 0
    const measure = () => {
      const rows = grid.querySelectorAll('.d-row')
      // Not laid out (a hidden pane, a list still swapping in): every cell reads 0 and From fell to
      // its 80 px floor, which then stayed (senders read "M." and "J…"). Measure
      // again when the grid gets a size (the ResizeObserver below).
      if (!grid.getClientRects().length || !grid.getBoundingClientRect().width) return
      // Each row is its own grid: with the three columns at max-content for a moment (no paint in
      // between), every cell takes the width its content needs, padding included.
      grid.setAttribute('data-measuring', '')
      for (const v of ['--d-from-w', '--d-labels-w', '--d-count-w']) grid.style.setProperty(v, 'max-content')
      let from = 0
      let labels = 0
      let count = 0
      for (const row of rows) {
        const w = (sel: string) => row.querySelector(sel)?.getBoundingClientRect().width ?? 0
        from = Math.max(from, w('.d-from'))
        labels = Math.max(labels, row.querySelector('.d-labels')?.childElementCount ? w('.d-labels') : 0)
        if (row.querySelector('.d-count')?.textContent) count = Math.max(count, w('.d-count'))
      }
      for (const v of ['--d-from-w', '--d-labels-w']) grid.style.removeProperty(v)
      grid.style.setProperty('--d-from-need', `${Math.max(FROM_MIN, Math.ceil(from))}px`)
      grid.style.setProperty('--d-labels-need', `${Math.max(LABELS_MIN, Math.ceil(labels))}px`)
      grid.style.setProperty('--d-count-w', count ? `${Math.max(COUNT_MIN, Math.ceil(count))}px` : '0px')
      grid.toggleAttribute('data-no-count', !count)
      grid.removeAttribute('data-measuring')
    }
    measure()
    // Rows come and go as the list scrolls (virtualised), pages load and mail arrives.
    const mo = new MutationObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    })
    mo.observe(grid, { childList: true, subtree: true, characterData: true })
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    })
    ro.observe(grid)
    void document.fonts?.ready.then(measure)
    return () => {
      cancelAnimationFrame(frame)
      mo.disconnect()
      ro.disconnect()
      for (const v of ['--d-from-need', '--d-labels-need', '--d-count-w', '--d-from-w', '--d-labels-w']) grid.style.removeProperty(v)
      grid.removeAttribute('data-no-count')
      grid.removeAttribute('data-measuring')
    }
  }, [head, on])
}

/** Quick filter bar and sortable, resizable column headers. */
export function DeskHeader({ narrow }: { view: string; narrow: boolean }) {
  // No choice yet means the server's order: newest first.
  const sort = useListPrefs((s) => s.sort) ?? { key: 'date' as SortKey, dir: 'desc' as const }
  const quick = useListPrefs((s) => s.quick)
  const toggleSort = useListPrefs((s) => s.toggleSort)
  const toggleQuick = useListPrefs((s) => s.toggleQuick)
  const startResize = useColumnResize()
  const keyRef = useRef<HTMLDivElement>(null)
  useContentColumns(keyRef, !narrow)
  const anyQuick = quick.unread || quick.starred || quick.attachments
  return (
    <div className="d-head" ref={keyRef}>
      <div className="d-quick" role="toolbar" aria-label={t('themes.desk.deskTable.quickFilter')}>
        <span className="d-quick-label">{t('themes.desk.deskTable.quickFilter')}</span>
        {(
          [
            ['unread', t('common.unread')],
            ['starred', t('common.starred')],
            ['attachments', t('themes.desk.deskTable.attachment')],
          ] as const
        ).map(([k, label]) => (
          <button key={k} type="button" className={`d-toggle${quick[k] ? ' on' : ''}`} aria-pressed={quick[k]} onClick={() => toggleQuick(k)}>
            {label}
          </button>
        ))}
        {anyQuick && <span className="d-quick-note">{t('themes.desk.deskTable.filteringThisPage')}</span>}
      </div>
      {!narrow && (
        // Column headings are sort buttons above the grids (a list can hold several, one per
        // section), so they are not columnheaders: each button says how the list is sorted.
        <div className="d-cols">
          {COLS.map((col) => {
            const active = col.key && sort?.key === col.key
            const icon = col.key === 'star' ? <Star /> : col.key === 'attach' ? <Paperclip /> : null
            return (
              <span key={col.cls} className={`d-cell ${col.cls}${active ? ' sorted' : ''}`}>
                {col.key ? (
                  <button type="button" className="d-sort" onClick={() => toggleSort(col.key!)} aria-label={t('themes.desk.deskTable.sortBy', { colLabel: col.label, value: active ? (sort!.dir === 'asc' ? t('themes.desk.deskTable.sortedAscending') : t('themes.desk.deskTable.sortedDescending')) : '' })} data-tip={icon ? t('themes.desk.deskTable.sortBy2', { colLabel: col.label }) : undefined}>
                    {icon ?? <span>{col.key === 'count' ? '#' : col.label}</span>}
                    {active && (sort!.dir === 'asc' ? <ArrowUp className="d-arrow" /> : <ArrowDown className="d-arrow" />)}
                  </button>
                ) : null}
                {col.resize && <span className="d-resize" role="separator" aria-orientation="vertical" aria-label={t('themes.desk.deskTable.resize', { colLabel: col.label })} onPointerDown={(e) => startResize(col.resize!, e)} />}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
