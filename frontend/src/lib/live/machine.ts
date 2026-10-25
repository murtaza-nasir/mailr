// The state of one live editor: the version it started from and that
// version's content (its base), and what to do when the server or another writer moves on.
//
//   remote change, editor clean   -> replace (the editor shows theirs, with Undo)
//   remote change, editor dirty   -> merge three ways; clean merge -> apply, still dirty
//                                    overlap -> conflict bar (Use mine / Use theirs / Compare)
//   own save answered "merged"    -> rebase: what was sent vs. what the editor holds now vs. stored
//   own save answered 409         -> conflict bar
//
// Pure: the editors (ComposeForm, EventEditor, TaskPane, …) apply what it decides.

import { mergeFields, same, type Conflict, type MergeMode } from './merge3'

export type Content = Record<string, unknown>

export interface Versioned<T extends Content> {
  version: number | null
  content: T
  by?: string | null
}

export interface ConflictState<T extends Content> {
  base: T | null
  mine: T
  theirs: Versioned<T>
  fields: string[]
  texts: Record<string, Conflict[]>
}

export type Decision<T extends Content> =
  | { type: 'ignore' }
  | { type: 'replace'; content: T; before: T }
  | { type: 'merge'; content: T; before: T }
  | { type: 'conflict'; conflict: ConflictState<T> }

export class LiveDoc<T extends Content> {
  base: Versioned<T> | null
  conflict: ConflictState<T> | null = null
  readonly textFields: Record<string, MergeMode>

  constructor(textFields: Record<string, MergeMode>, base: Versioned<T> | null = null) {
    this.textFields = textFields
    this.base = base
  }

  get version(): number | null {
    return this.base?.version ?? null
  }

  /** The editor learned its base (opened, or created by its first save). */
  setBase(version: number | null, content: T) {
    this.base = { version, content }
  }

  /** Another writer stored `theirs`. `mine` is what the editor holds; `dirty`: it holds changes
   *  not saved yet. */
  onRemote(theirs: Versioned<T>, mine: T, dirty: boolean): Decision<T> {
    const v = this.base?.version
    if (theirs.version != null && v != null && theirs.version <= v) return { type: 'ignore' }
    if (this.conflict) {
      // Still deciding: the newer copy replaces the old "theirs".
      this.conflict = { ...this.conflict, theirs }
      return { type: 'conflict', conflict: this.conflict }
    }
    if (!dirty || same(mine, theirs.content)) {
      // Clean, or already showing theirs (a list refresh got there first): Undo goes back to the base.
      const before = this.base && same(mine, theirs.content) ? this.base.content : mine
      this.base = theirs
      return { type: 'replace', content: theirs.content, before }
    }
    const base = this.base?.content ?? null
    const r = mergeFields(base, mine, theirs.content, this.textFields)
    if (r.ok) {
      this.base = theirs
      return { type: 'merge', content: r.merged, before: mine }
    }
    this.conflict = { base, mine, theirs, fields: r.conflicts, texts: r.texts }
    return { type: 'conflict', conflict: this.conflict }
  }

  /** The server stored this editor's save of `sent`. When it merged (`stored` differs from what
   *  was sent), the editor's text now (`mine`) is rebased onto it: returns what the editor must
   *  show, or null when it can stay as it is. */
  onSaved(sent: T, version: number | null, stored: T | null, mine: T): { content: T; conflict: boolean } | null {
    const server = stored ?? sent
    this.base = { version, content: server }
    if (!stored || same(stored, sent)) return null
    const r = mergeFields(sent, mine, server, this.textFields)
    if (!r.ok) {
      // The user changed the merged paragraph again while the save was on its way.
      this.conflict = { base: sent, mine, theirs: { version, content: server }, fields: r.conflicts, texts: r.texts }
      return { content: mine, conflict: true }
    }
    return same(r.merged, mine) ? null : { content: r.merged, conflict: false }
  }

  /** The save was refused (HTTP 409 with base, theirs and mine). */
  onRejected(body: { base?: { version?: number | null; content?: T | null }; theirs?: { version?: number | null; content?: T; by?: string | null }; fields?: string[]; texts?: Record<string, Conflict[]> }, mine: T): ConflictState<T> {
    this.conflict = {
      base: body.base?.content ?? this.base?.content ?? null,
      mine,
      theirs: { version: body.theirs?.version ?? null, content: (body.theirs?.content ?? mine) as T, by: body.theirs?.by ?? null },
      fields: body.fields ?? [],
      texts: body.texts ?? {},
    }
    return this.conflict
  }

  /** "Use mine": keep the editor's text, now based on theirs (the next save replaces theirs). */
  useMine(): T | null {
    const c = this.conflict
    if (!c) return null
    this.base = c.theirs
    this.conflict = null
    return c.mine
  }

  /** "Use theirs": the editor shows theirs, which is saved already. */
  useTheirs(): T | null {
    const c = this.conflict
    if (!c) return null
    this.base = c.theirs
    this.conflict = null
    return c.theirs.content
  }
}

/** Indexes of the units of `after` that are not in `before` (to highlight what changed). */
export function changedUnits(before: string[], after: string[]): number[] {
  const seen = new Map<string, number>()
  for (const u of before) seen.set(u, (seen.get(u) ?? 0) + 1)
  const out: number[] = []
  after.forEach((u, i) => {
    const n = seen.get(u) ?? 0
    if (n > 0) seen.set(u, n - 1)
    else out.push(i)
  })
  return out
}
