// How a cell shows and how its value is picked: chips for options and links (a link the viewer
// cannot see says "Not shared with you"), the select menu with a filter line and "Create option",
// the multi-select checklist, the link picker, and the type icons of the headers.

import {
  AlignLeft, Calendar, CalendarClock, CheckSquare, ChevronDown, CircleDollarSign, Hash, Link2, List, ListChecks, Mail, Plus, StickyNote, Table2, X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { request } from '../../api/client'
import { tablePath } from './api'
import { cellText, optionsOf, refKey } from './model'
import type { CellValue, Column, ColumnType, LinkRef, RefInfo, SelectOption } from './types'
import { t as tr } from '../../i18n'

export const TYPE_ICON: Record<ColumnType, ReactNode> = {
  text: <AlignLeft size={14} />, number: <Hash size={14} />, currency: <CircleDollarSign size={14} />, date: <Calendar size={14} />,
  datetime: <CalendarClock size={14} />, checkbox: <CheckSquare size={14} />, select: <ChevronDown size={14} />,
  multi_select: <List size={14} />, link: <Link2 size={14} />,
}

export function OptionChip({ o, onRemove }: { o: SelectOption | undefined; onRemove?: () => void }) {
  if (!o) return null
  return (
    <span className={`tb-chip tb-opt-${o.color}`} dir="auto">
      {o.name}
      {onRemove && <button type="button" aria-label={tr('tables.cells.remove', { itemName: o.name })} onClick={onRemove}><X size={11} /></button>}
    </span>
  )
}

const REF_ICON: Record<string, ReactNode> = {
  row: <Table2 size={12} />, conversation: <Mail size={12} />, event: <Calendar size={12} />, task: <ListChecks size={12} />, note: <StickyNote size={12} />,
}

export function refTitle(info: RefInfo | undefined): string {
  if (!info) return '…'
  if (info.state === 'hidden') return tr('tables.cells.notSharedWithYou')
  if (info.state === 'deleted') return tr('common.deleted')
  return info.title
}

export function RefChip({ r, info, onOpen, onRemove }: { r: LinkRef; info: RefInfo | undefined; onOpen?: () => void; onRemove?: () => void }) {
  const t = refTitle(info)
  const off = info?.state !== 'ok'
  return (
    <span className={`tb-chip tb-ref${off ? ' off' : ''}`} data-ref-state={info?.state ?? 'loading'}>
      <button type="button" disabled={!onOpen || off} onClick={(e) => {
        e.stopPropagation()
        onOpen?.()
      }}>{REF_ICON[r.kind]}<span dir="auto">{t}</span></button>
      {onRemove && <button type="button" aria-label={tr('tables.cells.removeTheLinkTo', { t })} onClick={(e) => {
        e.stopPropagation()
        onRemove()
      }}><X size={11} /></button>}
    </span>
  )
}

/** A cell's content in the grid (one line; chips that do not fit say "+2"). */
export function CellView({ col, value, refs, onOpenRef, tz }: { col: Column; value: CellValue | undefined; refs: Record<string, RefInfo>; onOpenRef?: (r: LinkRef) => void; tz?: string }) {
  if (value == null || value === '' || value === false || (Array.isArray(value) && !value.length)) return null
  if (col.type === 'checkbox') return <span className="tb-check on" aria-label={tr('common.checked')}>✓</span>
  if (col.type === 'select') return <OptionChip o={optionsOf(col).find((o) => o.id === value)} />
  if (col.type === 'multi_select') {
    const opts = optionsOf(col)
    const ids = value as string[]
    const shown = ids.slice(0, 3)
    return (
      <span className="tb-chips">
        {shown.map((id) => <OptionChip key={id} o={opts.find((o) => o.id === id)} />)}
        {ids.length > shown.length && <span className="tb-more">+{ids.length - shown.length}</span>}
      </span>
    )
  }
  if (col.type === 'link') {
    const refsList = value as LinkRef[]
    const shown = refsList.slice(0, 2)
    return (
      <span className="tb-chips">
        {shown.map((r) => <RefChip key={refKey(r)} r={r} info={refs[refKey(r)]} onOpen={onOpenRef ? () => onOpenRef(r) : undefined} />)}
        {refsList.length > shown.length && <span className="tb-more">+{refsList.length - shown.length}</span>}
      </span>
    )
  }
  // Written text takes its own direction; numbers and dates keep the column's alignment.
  return <span className="tb-text" dir={col.type === 'text' ? 'auto' : undefined}>{cellText(col, value, refs, tz)}</span>
}

// ---------------------------------------------------------------- select menus

/** The options with a filter line; typing a new name offers "Create option". Multi-select shows
 * checkboxes and stays open. */
export function SelectPicker({ col, value, onPick, onCreate, canCreate, onClose }: {
  col: Column; value: CellValue | undefined; onPick: (v: CellValue) => void; onCreate: (name: string) => void; canCreate: boolean; onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const multi = col.type === 'multi_select'
  const opts = optionsOf(col)
  const chosen = new Set(multi ? ((value as string[] | null) ?? []) : value ? [value as string] : [])
  const shown = opts.filter((o) => o.name.toLowerCase().includes(q.trim().toLowerCase()))
  const exact = opts.some((o) => o.name.toLowerCase() === q.trim().toLowerCase())
  const pick = (o: SelectOption) => {
    if (multi) {
      const next = chosen.has(o.id) ? [...chosen].filter((x) => x !== o.id) : [...chosen, o.id]
      onPick(opts.filter((x) => next.includes(x.id)).map((x) => x.id))
    } else {
      onPick(chosen.has(o.id) ? null : o.id)
      onClose()
    }
  }
  const total = shown.length + (q.trim() && !exact && canCreate ? 1 : 0)
  return (
    <div className="tb-picker" role="dialog" aria-label={tr('tables.cells.optionsOf', { colName: col.name })}>
      <input className="tb-picker-filter" autoFocus value={q} placeholder={canCreate ? tr('tables.cells.findOrCreateAnOption') : tr('tables.cells.findAnOption')} aria-label={tr('tables.cells.filterTheOptions')}
        onChange={(e) => {
          setQ(e.target.value)
          setActive(0)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((a) => Math.min(total - 1, a + 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(0, a - 1))
          } else if (e.key === 'Enter') {
            e.preventDefault()
            if (active < shown.length) pick(shown[active])
            else if (q.trim()) {
              onCreate(q.trim())
              setQ('')
            }
          } else if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            onClose()
          }
        }} />
      <ul role="listbox" aria-multiselectable={multi || undefined}>
        {shown.map((o, i) => (
          <li key={o.id} role="option" aria-selected={chosen.has(o.id)} className={i === active ? 'active' : undefined} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o)}>
            {multi && <input type="checkbox" className="checkbox" readOnly checked={chosen.has(o.id)} tabIndex={-1} aria-hidden />}
            <OptionChip o={o} />
          </li>
        ))}
        {q.trim() && !exact && canCreate && (
          <li role="option" aria-selected={false} className={active === shown.length ? 'active' : undefined} onMouseDown={(e) => e.preventDefault()} onClick={() => {
            onCreate(q.trim())
            setQ('')
          }}><Plus size={14} aria-hidden />{' '}{tr('tables.cells.createOption', { q: q.trim() })}</li>
        )}
        {!shown.length && !q.trim() && <li className="muted">{tr('tables.cells.noOptionsYetTypeA')}</li>}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------- link picker

interface Candidate { ref: LinkRef; title: string; sub: string | null }

/** Search the column's target for links (only what the user can see); chips for the current ones. */
export function LinkPicker({ table, col, value, refs, onChange, onClose }: {
  table: string; col: Column; value: CellValue | undefined; refs: Record<string, RefInfo>; onChange: (v: LinkRef[]) => void; onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<Candidate[]>([])
  const cur = useMemo(() => ((value as LinkRef[] | null) ?? []), [value])
  const multiple = !!col.config.multiple
  const [titles, setTitles] = useState<Record<string, string>>({})
  useEffect(() => {
    const ctl = new AbortController()
    const t = setTimeout(() => {
      request<{ items: Candidate[] }>(`${tablePath(table)}/columns/${encodeURIComponent(col.id)}/targets`, { query: { q }, signal: ctl.signal })
        .then((r) => setItems(r.items ?? []))
        .catch(() => undefined)
    }, 150)
    return () => {
      clearTimeout(t)
      ctl.abort()
    }
  }, [q, table, col.id])
  const keys = new Set(cur.map(refKey))
  return (
    <div className="tb-picker tb-link-picker" role="dialog" aria-label={tr('tables.cells.linksOf', { colName: col.name })}>
      {cur.length > 0 && (
        <div className="tb-chips wrap">
          {cur.map((r) => <RefChip key={refKey(r)} r={r} info={refs[refKey(r)] ?? (titles[refKey(r)] ? { kind: r.kind, state: 'ok', title: titles[refKey(r)] } : undefined)}
            onRemove={() => onChange(cur.filter((x) => refKey(x) !== refKey(r)))} />)}
        </div>
      )}
      <input className="tb-picker-filter" autoFocus value={q} placeholder={tr('common.search')} aria-label={tr('tables.cells.searchForALink')} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            onClose()
          }
        }} />
      <ul role="listbox">
        {items.map((it) => {
          const k = refKey(it.ref)
          return (
            <li key={k} role="option" aria-selected={keys.has(k)} onMouseDown={(e) => e.preventDefault()} onClick={() => {
              setTitles((t) => ({ ...t, [k]: it.title }))
              if (keys.has(k)) onChange(cur.filter((x) => refKey(x) !== k))
              else onChange(multiple ? [...cur, it.ref] : [it.ref])
              if (!multiple) onClose()
            }}>
              {multiple && <input type="checkbox" className="checkbox" readOnly checked={keys.has(k)} tabIndex={-1} aria-hidden />}
              <span>{it.title}</span>{it.sub && <span className="muted tb-sub">{it.sub}</span>}
            </li>
          )
        })}
        {!items.length && <li className="muted">{tr('tables.cells.nothingFound')}</li>}
      </ul>
    </div>
  )
}
