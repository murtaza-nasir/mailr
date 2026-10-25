// An assistant turn as a timeline, like Claude Code: text → tool steps → text → … → answer.
//
// Every server event carries `segment`, its position in the turn. Text of a segment streams as
// `token` events and is finalised by `segment` (text the model wrote before calling tools) or
// `final` (the answer); the tool steps of a segment follow its text, and an approval card follows
// the steps that asked for it. Nothing is replaced: after a decision the turn continues with a
// `resume` item and later segments. `applyEvent` folds one event into a turn; `fromReplay`
// rebuilds a turn from GET /agent/sessions/{id}.

import { msgText } from './present/serverText'
import { cleanMarkers } from './citeGrammar'
import { sourceRows, type Citation } from './citations'
import { markLegacyRepeats, type Step } from './steps'
import { t as tr } from '../../../i18n'

export interface TextItem {
  kind: 'text'
  segment: number
  raw: string
  final: string | null
}

export interface StepsItem {
  kind: 'steps'
  segment: number
  steps: Step[]
}

/** An approval card, placed after the steps that asked for it; the card's data is on the turn. */
export interface ApprovalItem {
  kind: 'approval'
  segment: number
  id: number
}

/** Where the turn continues after the user decided an approval. */
export interface ResumeItem {
  kind: 'resume'
  segment: number
  text: string
}

export type TimelineItem = TextItem | StepsItem | ApprovalItem | ResumeItem

export interface ConfigChange {
  title: string
  undo?: { method: string; path: string; body?: unknown } | null
  undone?: boolean
}

export interface DraftEditNote {
  window: string
  draftId: number | null
  applied: boolean
  summary: string
}

export type TurnStatus = 'running' | 'done' | 'stopped' | 'error' | 'interrupted'

/** The model that wrote an answer and whether its endpoint is local (the server's `model`). */
export interface AnswerModel {
  name: string | null
  endpoint: string
  local: boolean
}

export interface TimelineTurn {
  items: TimelineItem[]
  citations: Citation[]
  stopReason?: string | null
  stopNote?: string
  model?: AnswerModel | null
  /** The reply claims a change that no tool confirmed in this turn (the server's claim check,
   *  agent/claims.py): the panel says nothing was saved. */
  unconfirmed?: boolean
}

function answerModel(v: unknown): AnswerModel | null {
  if (!v || typeof v !== 'object') return null
  const m = v as Record<string, unknown>
  return typeof m.endpoint === 'string' ? { name: typeof m.name === 'string' ? m.name : null, endpoint: m.endpoint, local: m.local === true } : null
}

function textIndex(items: TimelineItem[], segment: number): number {
  return items.findIndex((i) => i.kind === 'text' && i.segment === segment)
}

function stepsIndex(items: TimelineItem[], segment: number): number {
  return items.findIndex((i) => i.kind === 'steps' && i.segment === segment)
}

/** Text of `segment`, created in order (text comes before that segment's steps). */
function withText(items: TimelineItem[], segment: number, fn: (t: TextItem) => TextItem): TimelineItem[] {
  const i = textIndex(items, segment)
  if (i >= 0) {
    const copy = items.slice()
    copy[i] = fn(items[i] as TextItem)
    return copy
  }
  const item = fn({ kind: 'text', segment, raw: '', final: null })
  const s = stepsIndex(items, segment)
  if (s >= 0) return [...items.slice(0, s), item, ...items.slice(s)]
  return [...items, item]
}

function withSteps(items: TimelineItem[], segment: number, fn: (s: StepsItem) => StepsItem): TimelineItem[] {
  const i = stepsIndex(items, segment)
  if (i >= 0) {
    const copy = items.slice()
    copy[i] = fn(items[i] as StepsItem)
    return copy
  }
  return [...items, fn({ kind: 'steps', segment, steps: [] })]
}

function seg(d: Record<string, unknown>): number {
  return typeof d.segment === 'number' ? d.segment : 0
}

function mergeCitations(a: Citation[], b: Citation[]): Citation[] {
  const seen = new Set(a.map((c) => c.n))
  return [...a, ...b.filter((c) => !seen.has(c.n))]
}

/** Fold one server event into the turn's timeline. Unknown events leave it unchanged. */
export function applyEvent<T extends TimelineTurn>(turn: T, type: string, d: Record<string, unknown>, label?: string): T {
  switch (type) {
    case 'token':
    case 'delta':
      return { ...turn, items: withText(turn.items, seg(d), (t) => ({ ...t, raw: t.raw + String(d.text ?? '') })) }
    case 'cite': {
      // A citation the streamed text is about to link to (the server sends it before the text).
      const c = d.citation as Citation | undefined
      return c && typeof c.n === 'number' ? { ...turn, citations: mergeCitations(turn.citations, [c]) } : turn
    }
    case 'segment':
    case 'final': {
      const cites = (d.citations as Citation[]) ?? []
      const content = String(d.content ?? '')
      const items = content || textIndex(turn.items, seg(d)) >= 0
        ? withText(turn.items, seg(d), (t) => ({ ...t, final: content, raw: t.raw || content }))
        : turn.items
      return {
        ...turn, items, citations: mergeCitations(turn.citations, cites),
        ...(type === 'final' && d.stop_reason ? { stopReason: String(d.stop_reason) } : {}),
        ...(type === 'final' && answerModel(d.model) ? { model: answerModel(d.model) } : {}),
        ...(type === 'final' && d.unconfirmed === true ? { unconfirmed: true } : {}),
      }
    }
    case 'tool_start':
      return {
        ...turn,
        items: withSteps(turn.items, seg(d), (s) => ({
          ...s,
          steps: [...s.steps, { id: String(d.id ?? s.steps.length), name: String(d.name ?? ''), label: label ?? msgText(d.label_msg, String(d.label ?? d.name ?? '')), icon: typeof d.icon === 'string' ? d.icon : undefined, args: d.args, status: 'running' }],
        })),
      }
    case 'tool': {
      const id = String(d.id ?? '')
      const done: Partial<Step> = {
        status: d.ok === false ? 'failed' : 'done',
        summary: d.ok === false ? undefined : d.summary ? msgText(d.summary_msg, String(d.summary)) : undefined,
        // A failed step's error (the server's `error`; an older server put it in `summary`).
        ...(d.ok === false ? { error: String(d.error ?? d.summary ?? '') || undefined } : {}),
        count: typeof d.count === 'number' ? d.count : null,
        ms: typeof d.ms === 'number' ? d.ms : undefined,
        ...(typeof d.repeat_of === 'string' && d.repeat_of ? { repeatOf: d.repeat_of } : {}),
      }
      let found = false
      const items = turn.items.map((it) => {
        if (it.kind !== 'steps') return it
        const i = it.steps.findIndex((s) => s.id === id)
        if (i < 0) return it
        found = true
        const steps = it.steps.slice()
        steps[i] = { ...steps[i], ...done }
        return { ...it, steps }
      })
      if (found) return { ...turn, items }
      return {
        ...turn,
        items: withSteps(turn.items, seg(d), (s) => ({ ...s, steps: [...s.steps, { id: id || String(s.steps.length), name: String(d.name ?? ''), label: msgText(d.label_msg, String(d.label ?? d.name ?? '')), status: 'done', ...done } as Step] })),
      }
    }
    case 'approval_required': {
      const id = Number(d.approval_id)
      if (!Number.isFinite(id) || turn.items.some((i) => i.kind === 'approval' && i.id === id)) return turn
      return { ...turn, items: [...turn.items, { kind: 'approval', segment: seg(d), id }] }
    }
    case 'resume':
      return { ...turn, items: [...turn.items, { kind: 'resume', segment: seg(d), text: String(d.text ?? tr('rail.assistant.timeline.afterYourApproval')) }] }
    case 'done':
      return { ...turn, stopReason: d.stop_reason ? String(d.stop_reason) : turn.stopReason, stopNote: d.note ? String(d.note) : turn.stopNote }
    default:
      return turn
  }
}

/** Mark the last running step as waiting for an approval. */
export function markWaiting<T extends TimelineTurn>(turn: T, approvalId: number): T {
  const idx = [...turn.items].reverse().findIndex((i) => i.kind === 'steps')
  if (idx < 0) return turn
  const at = turn.items.length - 1 - idx
  const it = turn.items[at] as StepsItem
  const steps = it.steps.map((s, i) => (i === it.steps.length - 1 ? { ...s, status: 'waiting' as const, approvalId } : s))
  const items = turn.items.slice()
  items[at] = { ...it, steps }
  return { ...turn, items }
}

/** The running turn is waiting on the model: not streaming text, no step running, no card
 *  waiting. The "Thinking…" row then sits at the end of the timeline, where the turn is. */
export function thinkingNow(turn: TimelineTurn & { running: boolean; approvals?: { id: number; status: string }[] }): boolean {
  if (!turn.running) return false
  const last: TimelineItem | undefined = turn.items[turn.items.length - 1]
  if (!last) return true
  if (last.kind === 'text') return last.final !== null || !last.raw.trim()
  if (last.kind === 'steps') return !last.steps.some((s) => s.status === 'running' || s.status === 'waiting')
  if (last.kind === 'approval') return (turn.approvals ?? []).find((a) => a.id === last.id)?.status !== 'pending'
  return true
}

/** The timeline as shown: a group of steps that only repeats earlier calls joins the group that
 *  holds those calls, when nothing visible lies between them. One question that asked for the same
 *  search sixteen times reads as one line, "Searching your tasks ×16". */
export function foldRepeatGroups(items: TimelineItem[]): TimelineItem[] {
  const out: TimelineItem[] = []
  for (const it of items) {
    if (it.kind === 'steps' && it.steps.length && it.steps.every((s) => s.repeatOf != null)) {
      let j = out.length - 1
      while (j >= 0 && out[j].kind === 'text' && !((out[j] as TextItem).final ?? (out[j] as TextItem).raw).trim()) j--
      const prev = j >= 0 ? out[j] : undefined
      if (prev?.kind === 'steps') {
        const ids = new Set(prev.steps.map((s) => s.id))
        if (it.steps.every((s) => ids.has(s.repeatOf!))) {
          out[j] = { ...prev, steps: [...prev.steps, ...it.steps] }
          continue
        }
      }
    }
    out.push(it)
  }
  return out
}

export function allSteps(turn: TimelineTurn): Step[] {
  return turn.items.flatMap((i) => (i.kind === 'steps' ? i.steps : []))
}

/** The answer text of a turn (every finalised segment, in order). */
export function turnText(turn: TimelineTurn): string {
  return turn.items
    .filter((i): i is TextItem => i.kind === 'text')
    .map((i) => cleanMarkers(i.final ?? i.raw, i.final == null))
    .filter((t) => t.trim())
    .join('\n\n')
}

export interface ReplayTimelineItem {
  type: 'text' | 'steps' | 'approval' | 'resume'
  content?: string
  citations?: Citation[]
  steps?: (Step & { ok?: boolean; approval_id?: number | null })[]
  /** `approval`: the card (GET /approvals shape with display and outcome). */
  approval?: { id: number }
  /** `resume`: the line shown where the turn continues. */
  text?: string
}

/** The stored turn's model (GET /agent/sessions/{id}), or null for an older answer. */
export const replayModel = answerModel

/** Timeline items from a stored turn (GET /agent/sessions/{id}). */
export function fromReplay(timeline: ReplayTimelineItem[] | undefined, content: string, steps: Step[]): TimelineItem[] {
  const norm = (list: (Step & { ok?: boolean; approval_id?: number | null })[]) =>
    list.map((s) => {
      const failed = (s.status ?? (s.ok === false ? 'failed' : 'done')) === 'failed'
      const m = s as Step & { label_msg?: unknown; summary_msg?: unknown; repeat_of?: string | null }
      return { ...s, label: msgText(m.label_msg, s.label), ...(s.summary ? { summary: msgText(m.summary_msg, s.summary) } : {}),
        ...(m.repeat_of ? { repeatOf: m.repeat_of } : {}),
        approvalId: s.approvalId ?? s.approval_id ?? undefined, status: s.status ?? (s.ok === false ? 'failed' : 'done'),
        // A stored failed step's text is its error; it moves under Technical details.
        ...(failed ? { summary: undefined, error: s.error ?? s.summary } : {}) } as Step
    })
  if (!timeline) {
    const out: TimelineItem[] = []
    if (steps.length) out.push({ kind: 'steps', segment: 0, steps: markLegacyRepeats(norm(steps)) })
    if (content) out.push({ kind: 'text', segment: 1, raw: content, final: content })
    return out
  }
  let n = 0
  // Repeats of an older server are found across the whole turn (they came one round each).
  const all = markLegacyRepeats(timeline.flatMap((t) => (t.type === 'steps' ? norm(t.steps ?? []) : [])))
  let k = 0
  return timeline.map((t): TimelineItem => {
    if (t.type === 'steps') {
      const steps = all.slice(k, k + (t.steps ?? []).length)
      k += steps.length
      return { kind: 'steps', segment: n++, steps }
    }
    if (t.type === 'approval') return { kind: 'approval', segment: n, id: Number(t.approval?.id) }
    if (t.type === 'resume') return { kind: 'resume', segment: n, text: t.text ?? tr('rail.assistant.timeline.afterYourApproval') }
    return { kind: 'text', segment: n, raw: t.content ?? '', final: t.content ?? '' }
  })
}

/** A reply as Markdown with citation links turned into plain "[n]" and a source list. */
export function copyText(md: string, citations: Citation[]): string {
  const used = new Set<number>()
  const body = md.replace(/\[(\d+)\]\(mailr-cite:\d+\)/g, (_m, n: string) => {
    used.add(Number(n))
    return `[${n}]`
  })
  // One line per source (sourceRows): "[1][2] Subject — From (date)".
  const src = sourceRows(citations.filter((c) => used.has(c.n)))
    .map(({ first: c, all }) => tr('rail.assistant.timeline.text', { n: all.map((x) => x.n).join(']['), subject: c.subject || c.title || tr('common.noSubject'), value: c.from ? ` — ${c.from}` : '', value2: c.date ? ` (${String(c.date).slice(0, 10)})` : '' }))
  return src.length ? tr('rail.assistant.timeline.sources', { body: body.trim(), src: src.join('\n') }) : body.trim()
}
