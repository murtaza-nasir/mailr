// The body of a review card (backend agent/review.py): the proposed moves grouped by sender, then what is
// kept and why, and what the review could not decide. Every sender, each of a sender's conversations, and
// every kept or unsure row has a move chip (Move to Trash, Archive, Mark read, a label, Keep): choosing one
// moves the row to that group. The card approves with the changes (POST /approvals/{id}/decide `changes`,
// agent/review.py apply_changes).
// ACT agent.review_move @web.reader

import { useMemo, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Filter } from 'lucide-react'
import { api } from '../../../api/client'
import { toast, toastError } from '../../../stores/ui'
import type { ReviewPreview, ReviewSender } from './useAssistant'
import { t as tr, appLocale } from '../../../i18n'

type Move = ReviewPreview['groups'][number]
/** A move as the card and `changes` name it: trash, archive, read, label:<id>, keep (and unsure, the
 *  review's own "not sure", which the user can only leave). */
export type MoveKey = string

/** One conversation on the card, with the move the review proposed. */
interface Item {
  id: number
  from: string
  email?: string
  subject: string
  date?: string | null
  orig: MoveKey
  reason?: string
  /** The review's own sender row offered "Always …" for this conversation's sender. */
  suggest?: boolean
  /** A decided card: kept because the user chose Keep. */
  byYou?: boolean
}

const keyOf = (g: Move): MoveKey => (g.move === 'label' ? `label:${g.label_id ?? ''}` : g.move)

/** The filter for a sender's future mail that does what this review does to their mail now. */
function filterFor(g: Move, s: ReviewSender): { name: string; query: string; actions: Record<string, unknown> } | null {
  if (!s.email) return null
  const actions = g.move === 'trash' ? { trash: true } : g.move === 'archive' ? { archive: true }
    : g.move === 'read' ? { mark_read: true } : g.label_id ? { add_labels: [g.label_id] } : null
  if (!actions) return null
  return { name: `${s.from}: ${tr(MOVE_KEY[g.move], { label: g.label ?? '' })}`, query: `from:${s.email}`, actions }
}

// ACT agent.review_filter @web.reader: "Always …" on a sender of a review card makes a filter for their
// future mail (POST /rules, as the filter editor does), with Undo.
function AlwaysButton({ g, s }: { g: Move; s: ReviewSender }) {
  const [made, setMade] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const f = filterFor(g, s)
  if (!f || !s.suggest_filter) return null
  if (made != null) {
    return <span className="rv-always done muted"><Check size={14} aria-hidden /> {tr('rail.assistant.review.filterMade')}</span>
  }
  const make = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ id?: number; rule?: { id: number } }>('/rules', { ...f, enabled: true })
      const id = r.id ?? r.rule?.id ?? null
      setMade(id ?? 0)
      toast({
        message: tr('rail.assistant.review.filterMadeFor', { from: s.from }),
        actionLabel: id != null ? tr('common.undo') : undefined,
        onAction: id != null ? () => {
          void api.del(`/rules/${id}`).then(() => setMade(null)).catch((e) => toastError(e, tr('common.undoFailed')))
        } : undefined,
      })
    } catch (e) {
      toastError(e, tr('rail.assistant.review.filterFailed'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <button type="button" className="btn text sm rv-always" disabled={busy} onClick={() => void make()}
      data-tip={tr('rail.assistant.review.alwaysTip', { email: s.email ?? '' })}>
      <Filter size={14} aria-hidden /> {tr(ALWAYS_KEY[g.move], { label: g.label ?? '' })}
    </button>
  )
}

const ALWAYS_KEY = {
  trash: 'rail.assistant.review.alwaysTrash',
  archive: 'rail.assistant.review.alwaysArchive',
  read: 'rail.assistant.review.alwaysRead',
  label: 'rail.assistant.review.alwaysLabel',
} as const

const MOVE_KEY = {
  trash: 'rail.assistant.review.moveTrash',
  archive: 'rail.assistant.review.moveArchive',
  read: 'rail.assistant.review.moveRead',
  label: 'rail.assistant.review.moveLabel',
} as const

const ASIDE_KEY: Record<string, string> = {
  excepted: 'rail.assistant.review.asideExcepted',
  starred: 'rail.assistant.review.asideStarred',
  important: 'rail.assistant.review.asideImportant',
  replied: 'rail.assistant.review.asideReplied',
  known: 'rail.assistant.review.asideKnown',
  invitation: 'rail.assistant.review.asideInvitation',
}

/** Every conversation the card shows, with the move the review proposed. */
function itemsOf(review: ReviewPreview): Item[] {
  const out: Item[] = []
  for (const g of review.groups) {
    for (const s of g.senders) {
      // A card saved before the per-conversation list: its ids, with the sample subjects.
      const convs = s.conversations ?? s.conversation_ids.map((id, i) => ({ conversation_id: id, subject: s.subjects[i] ?? '', date: null }))
      for (const c of convs) {
        out.push({ id: c.conversation_id, from: s.from, email: s.email, subject: c.subject, date: c.date, orig: keyOf(g), suggest: !!s.suggest_filter })
      }
    }
  }
  for (const r of review.kept) out.push({ id: r.conversation_id, from: r.from, email: r.email, subject: r.subject, date: r.date, orig: 'keep', reason: r.reason, byYou: !!r.by_you })
  for (const r of review.unsure) out.push({ id: r.conversation_id, from: r.from, email: r.email, subject: r.subject, date: r.date, orig: 'unsure', reason: r.reason })
  return out
}

/** The labels the move menu offers: those the review could add, and any its groups use. */
function labelsOf(review: ReviewPreview): { id: number; name: string }[] {
  const out = new Map<number, string>()
  for (const l of review.labels ?? []) out.set(l.id, l.name)
  for (const g of review.groups) if (g.move === 'label' && g.label_id != null) out.set(g.label_id, g.label ?? '')
  return [...out].map(([id, name]) => ({ id, name }))
}

function moveName(key: MoveKey, labels: { id: number; name: string }[]): string {
  if (key === 'keep') return tr('rail.assistant.review.keep')
  if (key === 'unsure') return tr('rail.assistant.review.notSure')
  if (key.startsWith('label:')) return tr(MOVE_KEY.label, { label: labels.find((l) => `label:${l.id}` === key)?.name ?? '' })
  return tr(MOVE_KEY[key as 'trash' | 'archive' | 'read'])
}

/** The move chip: what happens to a sender's or one conversation's mail, as a compact menu. */
function MoveChip({ value, labels, onChange, disabled, what }: {
  value: MoveKey
  labels: { id: number; name: string }[]
  onChange: (m: MoveKey) => void
  disabled: boolean
  what: string
}) {
  const keys = ['trash', 'archive', 'read', ...labels.map((l) => `label:${l.id}`), 'keep']
  return (
    <select className={`rv-move ${value.split(':')[0]}`} value={value} disabled={disabled}
      aria-label={tr('rail.assistant.review.moveFor', { what })} onChange={(e) => onChange(e.currentTarget.value)}>
      {value === 'unsure' && <option value="unsure" disabled>{moveName('unsure', labels)}</option>}
      {keys.map((k) => <option key={k} value={k}>{moveName(k, labels)}</option>)}
    </select>
  )
}

function day(d?: string | null): string {
  if (!d) return ''
  const t = new Date(`${d}T12:00:00`)
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString(appLocale(), { day: 'numeric', month: 'short' })
}

function Rows({ title, rows, more, current, labels, onMove, editable, startOpen = false }: {
  title: string
  rows: Item[]
  more: number
  current: (i: Item) => MoveKey
  labels: { id: number; name: string }[]
  onMove: (ids: number[], m: MoveKey) => void
  editable: boolean
  startOpen?: boolean
}) {
  const [open, setOpen] = useState(startOpen)
  if (!rows.length && !more) return null
  return (
    <div className="rv-rows">
      <button type="button" className="btn text sm" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />} {title}
      </button>
      {open && (
        <ul>
          {rows.map((r) => (
            <li key={r.id} className="rv-row">
              <span className="rv-what"><b>{r.from}</b> <span>{r.subject}</span>
                {r.reason && <span className="muted"> · {r.reason}</span>}</span>
              <MoveChip value={current(r)} labels={labels} disabled={!editable} what={`${r.from}: ${r.subject}`}
                onChange={(m) => onMove([r.id], m)} />
            </li>
          ))}
          {more > 0 && <li className="muted">{tr('rail.assistant.review.andMore', { count: more })}</li>}
        </ul>
      )}
    </div>
  )
}

function SenderRow({ k, move, from, items, all, labels, onMove, editable }: {
  k: MoveKey
  /** The review's own group for this move (none for a label only the user picked). */
  move: Move | null
  from: string
  items: Item[]
  /** This sender's conversations the review proposed for this move (for "Always …"). */
  all: Item[]
  labels: { id: number; name: string }[]
  onMove: (ids: number[], m: MoveKey) => void
  editable: boolean
}) {
  const [open, setOpen] = useState(false)
  const n = (x: number) => x.toLocaleString(appLocale())
  const ids = items.map((i) => i.id)
  // "Always …" while the sender's mail is still all in the move the review proposed for it.
  const whole = all.length === items.length && items.every((i) => i.orig === k)
  const sender: ReviewSender | null = move && whole
    ? { from, count: items.length, conversation_ids: ids, subjects: [], email: items[0]?.email, suggest_filter: !!items[0]?.suggest }
    : null
  return (
    <li>
      <div className="rv-sender">
        {items.length > 1 ? (
          <button type="button" className="btn icon xs rv-expand" aria-expanded={open}
            aria-label={tr('rail.assistant.review.conversationsFrom', { from })} onClick={() => setOpen(!open)}>
            {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
          </button>
        ) : <span className="rv-expand" aria-hidden />}
        <span className="rv-what"><b>{from}</b> <span className="muted">{n(items.length)}</span></span>
        <MoveChip value={k} labels={labels} disabled={!editable} what={from} onChange={(m) => onMove(ids, m)} />
      </div>
      {open ? (
        <ul className="rv-convs">
          {items.map((c) => (
            <li key={c.id} className="rv-row">
              <span className="rv-what">{c.subject}{c.date && <span className="muted"> · {day(c.date)}</span>}</span>
              <MoveChip value={k} labels={labels} disabled={!editable} what={c.subject || from}
                onChange={(m) => onMove([c.id], m)} />
            </li>
          ))}
        </ul>
      ) : (
        <div className="rv-samples muted">{items.slice(0, 3).map((c) => c.subject).filter(Boolean).join(' · ')}</div>
      )}
      {sender && move && <AlwaysButton g={move} s={sender} />}
    </li>
  )
}

export function ReviewPlan({ review, changes, onChanges, editable }: {
  review: ReviewPreview
  /** The user's moves so far, by conversation id: only those that differ from the review's. */
  changes: Record<number, MoveKey>
  onChanges: (next: Record<number, MoveKey>) => void
  editable: boolean
}) {
  const n = (x: number) => x.toLocaleString(appLocale())
  const items = useMemo(() => itemsOf(review), [review])
  const labels = useMemo(() => labelsOf(review), [review])
  const current = (i: Item): MoveKey => changes[i.id] ?? i.orig
  const onMove = (ids: number[], m: MoveKey) => {
    const next = { ...changes }
    for (const id of ids) {
      const it = items.find((x) => x.id === id)
      if (!it || it.orig === m) delete next[id]
      else next[id] = m
    }
    onChanges(next)
  }
  // The groups as they stand now: the review's moves, then any label the user picked, in menu order.
  const order = ['trash', 'archive', 'read', ...labels.map((l) => `label:${l.id}`)]
  const byMove = new Map<MoveKey, Item[]>()
  for (const it of items) {
    const k = current(it)
    if (k === 'keep' || k === 'unsure') continue
    byMove.set(k, [...(byMove.get(k) ?? []), it])
  }
  const keptModel = items.filter((i) => i.orig === 'keep' && !i.byYou && current(i) === 'keep')
  const keptByYou = items.filter((i) => (i.orig !== 'keep' || i.byYou) && current(i) === 'keep')
  const unsure = items.filter((i) => current(i) === 'unsure')
  const keptMore = Math.max(0, review.kept_count - review.kept.length)
  const unsureMore = Math.max(0, review.unsure_count - review.unsure.length)
  const keptModelCount = keptModel.length + keptMore
  const aside = Object.entries(review.set_aside).filter(([, c]) => c > 0)
  return (
    <div className="review-plan">
      {order.filter((k) => byMove.has(k)).map((k) => {
        const its = byMove.get(k)!
        const move = review.groups.find((g) => keyOf(g) === k) ?? null
        const senders = new Map<string, Item[]>()
        for (const it of its) senders.set(it.from, [...(senders.get(it.from) ?? []), it])
        const rows = [...senders].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
        return (
          <section key={k} className={`rv-group ${k.split(':')[0]}`}>
            <h4>{moveName(k, labels)} <span className="muted">{n(its.length)}</span></h4>
            <ul>
              {rows.map(([from, mine]) => (
                <SenderRow key={from} k={k} move={move} from={from} items={mine} labels={labels} onMove={onMove}
                  editable={editable} all={items.filter((i) => i.from === from && i.orig === k)} />
              ))}
            </ul>
          </section>
        )
      })}
      {/* The mail set aside by a rule; what the model or you kept has its own rows below, with the
          count in their heading, so it is not counted twice here. */}
      {aside.length > 0 && (
        <p className="rv-kept muted">
          {tr('rail.assistant.review.keptLead')}{' '}
          {aside.map(([why, c]) => tr(ASIDE_KEY[why] ?? 'rail.assistant.review.asideOther', { count: c })).join(', ')}
        </p>
      )}
      <Rows title={tr('rail.assistant.review.keptByYouRows', { count: keptByYou.length })} rows={keptByYou} more={0}
        current={current} labels={labels} onMove={onMove} editable={editable} startOpen />
      <Rows title={tr('rail.assistant.review.keptRows', { count: keptModelCount })} rows={keptModel} more={keptMore}
        current={current} labels={labels} onMove={onMove} editable={editable} />
      <Rows title={tr('rail.assistant.review.unsureRows', { count: unsure.length + unsureMore })} rows={unsure} more={unsureMore}
        current={current} labels={labels} onMove={onMove} editable={editable} />
    </div>
  )
}
