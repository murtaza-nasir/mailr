// Pure helpers of the Notes app (unit-tested in model.test.ts): the display order of a checklist's
// unchecked items, the note colours, the grid's sort, Trash days, the
// Markdown copy and the light Markdown of text notes.

import type { Item, Note, NoteLabel, OrderMode, Section } from './types'
import { t as tr } from '../../i18n'

// ---------------------------------------------------------------- colours

/** The 12 swatches of the colour palette, stored by name in lower case. Their card tints are theme tokens
 * (notes.css: --note-<name>), with light and dark variants. */
export const NOTE_COLORS: { id: string; name: string; hex: string }[] = [
  { id: 'tomato', name: tr('common.tomato'), hex: '#d50000' },
  { id: 'flamingo', name: tr('common.flamingo'), hex: '#e67c73' },
  { id: 'tangerine', name: tr('common.tangerine'), hex: '#f4511e' },
  { id: 'banana', name: tr('common.banana'), hex: '#f6bf26' },
  { id: 'sage', name: tr('common.sage'), hex: '#33b679' },
  { id: 'basil', name: tr('common.basil'), hex: '#0b8043' },
  { id: 'peacock', name: tr('common.peacock'), hex: '#039be5' },
  { id: 'blueberry', name: tr('common.blueberry'), hex: '#3f51b5' },
  { id: 'lavender', name: tr('common.lavender'), hex: '#7986cb' },
  { id: 'grape', name: tr('common.grape'), hex: '#8e24aa' },
  { id: 'graphite', name: tr('common.graphite'), hex: '#616161' },
  { id: 'cocoa', name: tr('common.cocoa'), hex: '#795548' },
]

export function colorName(id: string | null | undefined): string {
  if (!id) return tr('common.default')
  return NOTE_COLORS.find((c) => c.id === id)?.name ?? id
}

export function colorHex(id: string | null | undefined): string | null {
  return NOTE_COLORS.find((c) => c.id === id)?.hex ?? null
}

// ---------------------------------------------------------------- checklist order

const byKey = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** Unchecked items of one group, in display order: manual by order key; learned by rank (ties by
 * order key), then the items with no rank in the order they were added. */
export function sortGroup(items: Item[], mode: OrderMode): Item[] {
  if (mode === 'manual') return [...items].sort((a, b) => byKey(a.order, b.order))
  const ranked = items.filter((i) => i.rank != null).sort((a, b) => (a.rank! - b.rank!) || byKey(a.order, b.order))
  const rest = items.filter((i) => i.rank == null).sort((a, b) => byKey(a.added_at ?? '', b.added_at ?? '') || byKey(a.order, b.order))
  return [...ranked, ...rest]
}

export interface Group {
  /** null: the top group. */
  section: Section | null
  mode: OrderMode
  /** The items shown in the group: unchecked ones, plus checked ones when they stay in place. */
  items: Item[]
}

export interface ChecklistView {
  groups: Group[]
  /** The "n checked" fold (checked_at_bottom): newest check first. */
  checked: Item[]
}

/** The whole checklist in display order. */
export function checklistView(note: Pick<Note, 'checked_at_bottom' | 'top_order_mode' | 'top'>, sections: Section[], items: Item[], stay: ReadonlySet<string> = new Set()): ChecklistView {
  // `stay`: items just checked, struck through in place for a moment before they move to the fold.
  const fold = note.checked_at_bottom
  const known = new Set(sections.map((s) => s.id))
  const inGroup = (sid: string | null) => items.filter((i) => (sid === null ? !i.section_id || !known.has(i.section_id) : i.section_id === sid) && (!fold || !i.checked || stay.has(i.id)))
  const ordered = [...sections].sort((a, b) => byKey(a.order, b.order))
  const top: Group = { section: null, mode: note.top?.order_mode ?? note.top_order_mode ?? 'manual', items: [] }
  top.items = arrange(inGroup(null), top.mode)
  const groups: Group[] = [top, ...ordered.map((s) => ({ section: s, mode: s.order_mode, items: arrange(inGroup(s.id), s.order_mode) }))]
  const checked = fold ? items.filter((i) => i.checked && !stay.has(i.id)).sort((a, b) => byKey(b.checked_at ?? '', a.checked_at ?? '')) : []
  return { groups, checked }
}

// Without the fold, checked items keep their place: they are sorted with the rest.
function arrange(items: Item[], mode: OrderMode): Item[] {
  return sortGroup(items, mode)
}

/** The item ids of a checklist in the order the editor shows them (for ↑/↓ focus moves). */
export function flatOrder(view: ChecklistView): string[] {
  return view.groups.flatMap((g) => g.items.map((i) => i.id))
}

// ---------------------------------------------------------------- the grid

export type NoteSort = 'manual' | 'edited' | 'created' | 'title'

type Sortable = Pick<Note, 'title' | 'state' | 'updated_at' | 'created_at'>

export function sortNotes<T extends Sortable>(notes: T[], sort: NoteSort): T[] {
  const out = [...notes]
  if (sort === 'edited') out.sort((a, b) => byKey(b.updated_at, a.updated_at))
  else if (sort === 'created') out.sort((a, b) => byKey(b.created_at, a.created_at))
  else if (sort === 'title') out.sort((a, b) => (a.title || '￿').localeCompare(b.title || '￿') || byKey(b.updated_at, a.updated_at))
  else out.sort((a, b) => byKey(a.state.order ?? '￿', b.state.order ?? '￿') || byKey(b.updated_at, a.updated_at))
  return out
}

/** Pinned first, then the rest (Archive and Trash have no Pinned group). */
export function splitPinned<T extends Pick<Note, 'state'>>(notes: T[], withPinned: boolean): { pinned: T[]; others: T[] } {
  if (!withPinned) return { pinned: [], others: notes }
  return { pinned: notes.filter((n) => n.state.pinned), others: notes.filter((n) => !n.state.pinned) }
}

// ---------------------------------------------------------------- Trash

const DAY = 86_400_000

/** "Deleted 3 days ago · 27 days left". */
export function trashLine(n: Pick<Note, 'deleted_at' | 'purge_at'>, now = Date.now()): string {
  if (!n.deleted_at) return ''
  const deleted = new Date(n.deleted_at).getTime()
  const ago = Math.max(0, Math.floor((now - deleted) / DAY))
  const purge = n.purge_at ? new Date(n.purge_at).getTime() : deleted + 30 * DAY
  const left = Math.max(0, Math.ceil((purge - now) / DAY))
  const when = ago === 0 ? tr('notes.model.deletedToday') : tr('notes.model.deletedDaysAgo', { count: ago })
  return `${when} · ${tr('notes.model.daysLeft', { count: left })}`
}

// ---------------------------------------------------------------- quantities

/** A quantity as its chip shows it: "2", "500 g", "1.5 kg". A count of 1 shows no chip. */
export function qtyLabel(q: Item['qty']): string {
  if (!q) return ''
  if (!q.unit && q.amount === '1') return ''
  return q.unit ? `${q.amount} ${q.unit}` : q.amount
}

/** − and + on a count. Returns null when the count would fall below 1 (the chip goes). */
export function stepQty(q: Item['qty'], d: 1 | -1): Item['qty'] {
  const amount = q ? Number(q.amount) : 1
  const next = Math.round((amount + d) * 1000) / 1000
  if (next < 1 && !q?.unit) return null
  if (next <= 0) return null
  return { amount: String(next), unit: q?.unit ?? null }
}

// ---------------------------------------------------------------- Markdown copy

/** The Markdown copy of a note (the export's form: `- [ ] milk ×2`, sections as `Name:` lines). */
export function noteMarkdown(note: Note): string {
  const lines: string[] = []
  if (note.title) lines.push(`# ${note.title}`, '')
  if (note.kind === 'text') {
    lines.push(note.body)
    return lines.join('\n').trimEnd() + '\n'
  }
  const sections = [...(note.sections ?? [])].sort((a, b) => byKey(a.order, b.order))
  const items = note.items ?? []
  const line = (i: Item) => {
    const q = i.qty ? (i.qty.unit ? ` ${i.qty.amount} ${i.qty.unit}` : ` ×${i.qty.amount}`) : ''
    return tr('notes.model.text', { value: i.checked ? 'x' : ' ', text: i.text, q })
  }
  const known = new Set(sections.map((s) => s.id))
  for (const i of sortGroup(items.filter((i) => !i.section_id || !known.has(i.section_id)), 'manual')) lines.push(line(i))
  for (const s of sections) {
    lines.push('', `${s.name}:`)
    for (const i of sortGroup(items.filter((i) => i.section_id === s.id), 'manual')) lines.push(line(i))
  }
  return lines.join('\n').trimEnd() + '\n'
}

/** A note as plain text for a task, an event or an email made from it (Create from note): the body,
 * or a checklist's open items as "- milk" lines under their sections. No title: that becomes the
 * new thing's own. */
export function noteText(note: Note): string {
  if (note.kind === 'text') return note.body.trim()
  const sections = [...(note.sections ?? [])].sort((a, b) => byKey(a.order, b.order))
  const items = (note.items ?? []).filter((i) => !i.checked)
  const line = (i: Item) => `- ${i.text}${i.qty ? ` (${qtyLabel(i.qty)})` : ''}`
  const known = new Set(sections.map((s) => s.id))
  const lines = sortGroup(items.filter((i) => !i.section_id || !known.has(i.section_id)), 'manual').map(line)
  for (const s of sections) {
    const mine = sortGroup(items.filter((i) => i.section_id === s.id), 'manual')
    if (mine.length) lines.push(...(lines.length ? [''] : []), `${s.name}:`, ...mine.map(line))
  }
  return lines.join('\n')
}

/** The Markdown of many notes (Empty Trash offers it first). */
export function notesMarkdown(notes: Note[]): string {
  return notes.map(noteMarkdown).join('\n---\n\n')
}

export function downloadText(name: string, text: string, type = 'text/markdown'): void {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ---------------------------------------------------------------- light Markdown (text notes)

export type MdPart = { kind: 'text' | 'bold' | 'italic' | 'link'; text: string; href?: string }

/** One line of light Markdown: **bold**, *italics* or _italics_, links ([a](url) and bare URLs). */
export function mdInline(line: string): MdPart[] {
  const out: MdPart[] = []
  const rx = /\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g
  let at = 0
  let m: RegExpExecArray | null
  while ((m = rx.exec(line))) {
    if (m.index > at) out.push({ kind: 'text', text: line.slice(at, m.index) })
    if (m[1] !== undefined) out.push({ kind: 'bold', text: m[1] })
    else if (m[2] !== undefined) out.push({ kind: 'italic', text: m[2] })
    else if (m[3] !== undefined) out.push({ kind: 'italic', text: m[3] })
    else if (m[4] !== undefined) out.push({ kind: 'link', text: m[4], href: m[5] })
    else out.push({ kind: 'link', text: m[6], href: m[6] })
    at = m.index + m[0].length
  }
  if (at < line.length) out.push({ kind: 'text', text: line.slice(at) })
  return out
}

/** A body as blocks: paragraphs (lines) and `-` bullets. */
export function mdBlocks(body: string): { bullet: boolean; parts: MdPart[] }[] {
  return body.split('\n').map((l) => {
    const b = /^\s*[-*•]\s+(.*)$/.exec(l)
    return { bullet: !!b, parts: mdInline(b ? b[1] : l) }
  })
}

// ---------------------------------------------------------------- labels

export function sortLabels(labels: NoteLabel[]): NoteLabel[] {
  return [...labels].sort((a, b) => byKey(a.order, b.order) || a.name.localeCompare(b.name))
}

/** A UUID v7 for a new item (the client makes item ids, so an add sent twice adds once). */
export function uuid7(now = Date.now()): string {
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  let t = now
  for (let i = 5; i >= 0; i--) {
    b[i] = t % 256
    t = Math.floor(t / 256)
  }
  b[6] = (b[6] & 0x0f) | 0x70
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** "Added by Dana", "Checked by Dana": the other person who wrote or checked an item, or ''. */
export function authorLine(item: Pick<Item, 'added_by' | 'checked' | 'checked_by'>, me: number | undefined): string {
  if (me == null) return ''
  if (item.checked && item.checked_by?.id != null && item.checked_by.id !== me && item.checked_by.name) return tr('notes.model.checkedBy', { checkedByName: item.checked_by.name })
  if (item.added_by?.id != null && item.added_by.id !== me && item.added_by.name) return tr('notes.model.addedBy', { addedByName: item.added_by.name })
  return ''
}
