// The editor's undo and redo history. One model for
// the web note editor and the Android one (EditHistory.kt in android/notes-core is the twin); both
// run the vectors in spec/cases/edit-history-cases.yaml.
//
// A step is a text edit of one field (the title, the body) or an operation (a tick, a deleted item, a
// colour). Typing joins the step before it while the same kind of typing goes on in the same place
// within IDLE_MS and no word ends; a paste, a cut, a replacement (autocorrect, replace-all, an
// Ambrose rewrite, dictation) is one step of its own. A new step after an undo drops the redo tail.
// The history keeps CAP steps; the oldest go first. Undo and redo move a step between the two stacks;
// the editor applies it (a text step sets the field and autosaves as any edit does; an operation runs
// its payload).

export const IDLE_MS = 1000
export const CAP = 200

/** Input types that are always one step of their own (the browser's InputEvent.inputType). */
const ONE_STEP = new Set([
  'insertFromPaste', 'insertFromPasteAsQuotation', 'insertFromDrop', 'insertFromYank', 'insertReplacementText',
  'insertTranspose', 'deleteByCut', 'deleteByDrag', 'historyUndo', 'historyRedo',
])

export type TypingKind = 'insert' | 'delete' | 'one'

export interface TextStep {
  kind: 'text'
  field: string
  before: string
  after: string
  /** When the step began and when its last keystroke landed (ms). */
  at: number
  last: number
  group: TypingKind
  /** Typing may still join it. */
  open: boolean
}

export interface OpStep<P> {
  kind: 'op'
  label: string
  op: P
}

export type Step<P> = TextStep | OpStep<P>

/** What changed between two values: the common head and tail, and what sits between them. */
export interface Change { at: number; removed: string; inserted: string }

export function diff(before: string, after: string): Change {
  let p = 0
  const max = Math.min(before.length, after.length)
  while (p < max && before.charCodeAt(p) === after.charCodeAt(p)) p++
  let s = 0
  while (s < max - p && before.charCodeAt(before.length - 1 - s) === after.charCodeAt(after.length - 1 - s)) s++
  // Never split a surrogate pair.
  if (p > 0 && isHigh(before.charCodeAt(p - 1))) p--
  return { at: p, removed: before.slice(p, before.length - s), inserted: after.slice(p, after.length - s) }
}

const isHigh = (c: number) => c >= 0xd800 && c <= 0xdbff
const chars = (s: string) => Array.from(s).length
const isSpace = (s: string) => /^\s$/u.test(s)

/** How a change is grouped: one typed character, one deleted character, or a step of its own. */
export function classify(c: Change, inputType?: string | null): TypingKind {
  if (inputType && ONE_STEP.has(inputType)) return 'one'
  if (!c.removed && chars(c.inserted) === 1 && c.inserted !== '\n') return 'insert'
  if (!c.inserted && chars(c.removed) === 1) return 'delete'
  return 'one'
}

/** Lines a removal takes away (a line break at either end of it does not count as a line). */
export function linesRemoved(c: Change): number {
  return c.removed ? c.removed.replace(/^\n|\n$/g, '').split('\n').length : 0
}

/** A removal big enough to say so ("Text removed · Undo"): more than three lines, or a field cleared
 *  of 20 characters or more, in one step. */
export function isBigRemoval(before: string, after: string): boolean {
  const c = diff(before, after)
  if (!c.removed) return false
  if (linesRemoved(c) > 3) return true
  return after.trim() === '' && c.removed.trim().length >= 20
}

/**
 * Undo a text step on the field's current value: `before` when nothing else changed it; otherwise the
 * step's change is found by its context (up to 20 characters each side) and reversed there, when that
 * context occurs once. null: the text changed too much to undo the step.
 */
export function revertText(current: string, step: { before: string; after: string }): string | null {
  return rebase(current, step.after, step.before)
}

/** Redo a text step on the current value (the mirror of revertText). */
export function reapplyText(current: string, step: { before: string; after: string }): string | null {
  return rebase(current, step.before, step.after)
}

function rebase(current: string, from: string, to: string): string | null {
  if (current === from) return to
  const c = diff(from, to)
  const head = from.slice(Math.max(0, c.at - 20), c.at)
  const tail = from.slice(c.at + c.removed.length, c.at + c.removed.length + 20)
  const needle = head + c.removed + tail
  if (!needle) return null
  const i = current.indexOf(needle)
  if (i < 0 || current.indexOf(needle, i + 1) >= 0) return null
  const at = i + head.length
  return current.slice(0, at) + c.inserted + current.slice(at + c.removed.length)
}

/** Where the caret goes after a text step is applied: the end of what came back. */
export function caretAfter(from: string, to: string): number {
  const c = diff(from, to)
  return c.at + c.inserted.length
}

export interface Recorded<P> {
  step: Step<P>
  /** The edit joined the step before it. */
  merged: boolean
  /** A removal to say out loud (isBigRemoval). */
  big: boolean
}

export class EditHistory<P> {
  private done: Step<P>[] = []
  private undone: Step<P>[] = []
  readonly cap: number
  readonly idleMs: number

  constructor(opts: { cap?: number; idleMs?: number } = {}) {
    this.cap = opts.cap ?? CAP
    this.idleMs = opts.idleMs ?? IDLE_MS
  }

  get canUndo(): boolean { return this.done.length > 0 }
  get canRedo(): boolean { return this.undone.length > 0 }
  get undoSize(): number { return this.done.length }
  get redoSize(): number { return this.undone.length }
  /** The step an undo would take back. */
  peekUndo(): Step<P> | null { return this.done[this.done.length - 1] ?? null }
  peekRedo(): Step<P> | null { return this.undone[this.undone.length - 1] ?? null }
  steps(): readonly Step<P>[] { return this.done }

  /** A field changed from `before` to `after` at `at` ms. */
  recordText(field: string, before: string, after: string, at: number, inputType?: string | null): Recorded<P> | null {
    if (before === after) return null
    const c = diff(before, after)
    const group = classify(c, inputType)
    const big = group === 'one' && isBigRemoval(before, after)
    const top = this.peekUndo()
    if (group !== 'one' && top?.kind === 'text' && top.open && top.field === field && top.group === group &&
      top.after === before && at - top.last <= this.idleMs && this.continues(top, c, group)) {
      top.after = after
      top.last = at
      this.undone = []
      return { step: top, merged: true, big: false }
    }
    this.seal()
    const step: TextStep = { kind: 'text', field, before, after, at, last: at, group, open: group !== 'one' }
    this.push(step)
    return { step, merged: false, big }
  }

  /** An operation (a tick, a deleted item, a colour): always a step of its own. */
  recordOp(label: string, op: P): OpStep<P> {
    this.seal()
    const step: OpStep<P> = { kind: 'op', label, op }
    this.push(step)
    return step
  }

  /** End the typing group (the field lost focus, an undo ran). */
  seal(): void {
    const top = this.peekUndo()
    if (top?.kind === 'text') top.open = false
  }

  /** Take the newest step back: it moves to the redo stack. The caller applies it. */
  undo(): Step<P> | null {
    this.seal()
    const s = this.done.pop()
    if (!s) return null
    this.undone.push(s)
    return s
  }

  redo(): Step<P> | null {
    const s = this.undone.pop()
    if (!s) return null
    if (s.kind === 'text') s.open = false
    this.done.push(s)
    return s
  }

  /** A step that could not be applied leaves both stacks. */
  drop(step: Step<P>): void {
    this.done = this.done.filter((s) => s !== step)
    this.undone = this.undone.filter((s) => s !== step)
  }

  clear(): void {
    this.done = []
    this.undone = []
  }

  /** The text steps, for a restore (an operation's payload stays with the process). */
  toJSON(): { done: TextStep[]; undone: TextStep[] } {
    const text = (xs: Step<P>[]) => xs.filter((s): s is TextStep => s.kind === 'text').map((s) => ({ ...s, open: false }))
    return { done: text(this.done), undone: text(this.undone) }
  }

  static fromJSON<P>(j: { done?: TextStep[]; undone?: TextStep[] } | null | undefined, opts: { cap?: number; idleMs?: number } = {}): EditHistory<P> {
    const h = new EditHistory<P>(opts)
    h.done = [...(j?.done ?? [])]
    h.undone = [...(j?.undone ?? [])]
    return h
  }

  private continues(top: TextStep, c: Change, group: TypingKind): boolean {
    const typed = diff(top.before, top.after)
    if (group === 'insert') {
      // The caret moved on from where the last character went in, and a word did not just end.
      if (c.at !== typed.at + typed.inserted.length) return false
      const prev = Array.from(typed.inserted).pop() ?? ''
      return !(isSpace(prev) && !isSpace(c.inserted))
    }
    // Backspace runs leftwards, Delete stays put.
    const end = typed.at
    if (c.at !== end - c.removed.length && c.at !== end) return false
    if (c.at === end - c.removed.length) {
      // Backspacing: stop at the start of a word (a space deleted after letters).
      const prev = typed.removed.charAt(0)
      return !(isSpace(c.removed) && prev !== '' && !isSpace(prev))
    }
    return true
  }

  private push(step: Step<P>): void {
    this.undone = []
    this.done.push(step)
    if (this.done.length > this.cap) this.done.splice(0, this.done.length - this.cap)
  }
}
