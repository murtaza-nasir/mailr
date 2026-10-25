// Three-way merge for live editing: the web copy of backend/mailr/core/merge3.py (read its
// docstring). The same cases (backend/tests/fixtures/merge3_cases.json) run against both, and
// against Android's LiveMerge.kt, so a merge gives the same result everywhere.

export type MergeMode = 'lines' | 'html'

/** Gaps larger than this (units times units) without a unique anchor are one change. */
export const DP_LIMIT = 250_000

const WS = '[ \\t\\r\\n\\f]'
const HTML_SPLIT = new RegExp(`</(?:p|div|li|ul|ol|h[1-6]|blockquote|pre|table|tr)>|<br${WS}*/?>|\\n`, 'gi')
const K_NBSP = /&nbsp;/gi
const K_DIV = /<(\/?)div\b/gi
const K_BR = new RegExp(`<br${WS}*/?>`, 'gi')
const K_ATTR = new RegExp(`${WS}(?:class|style|dir)="[^"]*"`, 'gi')
const K_SPACE = new RegExp(`${WS}+`, 'g')

/** The units of `text`; joined, they give `text` back. */
export function tokens(text: string, mode: MergeMode = 'lines'): string[] {
  if (!text) return []
  const out: string[] = []
  let start = 0
  if (mode === 'html') {
    HTML_SPLIT.lastIndex = 0
    for (let m = HTML_SPLIT.exec(text); m; m = HTML_SPLIT.exec(text)) {
      const end = m.index + m[0].length
      out.push(text.slice(start, end))
      start = end
    }
  } else {
    for (;;) {
      const i = text.indexOf('\n', start)
      if (i < 0) break
      out.push(text.slice(start, i + 1))
      start = i + 1
    }
  }
  if (start < text.length) out.push(text.slice(start))
  return out
}

function stripSpaces(s: string): string {
  let a = 0
  let b = s.length
  while (a < b && s[a] === ' ') a++
  while (b > a && s[b - 1] === ' ') b--
  return s.slice(a, b)
}

export function key(unit: string, mode: MergeMode = 'lines'): string {
  if (mode !== 'html') return unit
  let s = unit.replace(K_NBSP, ' ')
  s = s.replace(K_DIV, (_m, slash: string) => `<${slash}p`)
  s = s.replace(K_BR, '<br>')
  s = s.replace(K_ATTR, '')
  return stripSpaces(s.replace(K_SPACE, ' '))
}

// ---------------------------------------------------------------- diff

type Pair = [number, number]
export type Hunk = [number, number, number, number]

function uniqueLcs(a: string[], a0: number, a1: number, b: string[], b0: number, b1: number): Pair[] {
  const ca = new Map<string, number>()
  const cb = new Map<string, number>()
  const pb = new Map<string, number>()
  for (let i = a0; i < a1; i++) ca.set(a[i], (ca.get(a[i]) ?? 0) + 1)
  for (let j = b0; j < b1; j++) {
    cb.set(b[j], (cb.get(b[j]) ?? 0) + 1)
    pb.set(b[j], j)
  }
  const pairs: Pair[] = []
  for (let i = a0; i < a1; i++) if (ca.get(a[i]) === 1 && cb.get(a[i]) === 1) pairs.push([i, pb.get(a[i])!])
  if (!pairs.length) return []
  const tops: number[] = []
  const back: number[] = new Array(pairs.length).fill(-1)
  for (let k = 0; k < pairs.length; k++) {
    const j = pairs[k][1]
    let lo = 0
    let hi = tops.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (pairs[tops[mid]][1] < j) lo = mid + 1
      else hi = mid
    }
    back[k] = lo > 0 ? tops[lo - 1] : -1
    if (lo === tops.length) tops.push(k)
    else tops[lo] = k
  }
  const out: Pair[] = []
  for (let k = tops[tops.length - 1]; k >= 0; k = back[k]) out.push(pairs[k])
  return out.reverse()
}

function dp(a: string[], a0: number, a1: number, b: string[], b0: number, b1: number, out: Pair[]) {
  const n = a1 - a0
  const m = b1 - b0
  const w = m + 1
  const L = new Int32Array((n + 1) * w)
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (a[a0 + i] === b[b0 + j]) L[i * w + j] = L[(i + 1) * w + j + 1] + 1
      else {
        const x = L[(i + 1) * w + j]
        const y = L[i * w + j + 1]
        L[i * w + j] = x >= y ? x : y
      }
    }
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[a0 + i] === b[b0 + j]) {
      out.push([a0 + i, b0 + j])
      i++
      j++
    } else if (L[(i + 1) * w + j] >= L[i * w + j + 1]) i++
    else j++
  }
}

function match(a: string[], a0: number, a1: number, b: string[], b0: number, b1: number, out: Pair[]) {
  while (a0 < a1 && b0 < b1 && a[a0] === b[b0]) {
    out.push([a0, b0])
    a0++
    b0++
  }
  const suffix: Pair[] = []
  while (a1 > a0 && b1 > b0 && a[a1 - 1] === b[b1 - 1]) {
    a1--
    b1--
    suffix.push([a1, b1])
  }
  if (a0 < a1 && b0 < b1) {
    const anchors = uniqueLcs(a, a0, a1, b, b0, b1)
    if (anchors.length) {
      let pa = a0
      let pb = b0
      for (const [i, j] of anchors) {
        match(a, pa, i, b, pb, j, out)
        out.push([i, j])
        pa = i + 1
        pb = j + 1
      }
      match(a, pa, a1, b, pb, b1, out)
    } else if ((a1 - a0) * (b1 - b0) <= DP_LIMIT) {
      dp(a, a0, a1, b, b0, b1, out)
    }
  }
  for (let k = suffix.length - 1; k >= 0; k--) out.push(suffix[k])
}

/** Hunks [a0, a1, b0, b1]: a[a0:a1] became b[b0:b1]. In order, never overlapping. */
export function diff(a: string[], b: string[]): Hunk[] {
  const matches: Pair[] = []
  match(a, 0, a.length, b, 0, b.length, matches)
  matches.push([a.length, b.length])
  const hunks: Hunk[] = []
  let i = 0
  let j = 0
  for (const [mi, mj] of matches) {
    if (mi > i || mj > j) hunks.push([i, mi, j, mj])
    i = mi + 1
    j = mj + 1
  }
  return hunks
}

// ---------------------------------------------------------------- merge

export interface Conflict {
  base: string
  mine: string
  theirs: string
}

export interface TextMerge {
  ok: boolean
  /** The merged text; where a conflict is, the "mine" side. */
  text: string
  conflicts: Conflict[]
}

type SideHunk = [number, number, number, number, 0 | 1]

function overlap(x: SideHunk, y: SideHunk): boolean {
  const [x0, x1] = x
  const [y0, y1] = y
  if (x0 === x1 && y0 === y1) return x0 === y0
  if (x0 === x1) return y0 < x0 && x0 < y1
  if (y0 === y1) return x0 < y0 && y0 < x1
  return Math.max(x0, y0) < Math.min(x1, y1)
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i])
}

export function mergeText(base: string | null | undefined, mine: string | null | undefined, theirs: string | null | undefined, mode: MergeMode = 'lines'): TextMerge {
  const b = base ?? ''
  const m = mine ?? ''
  const t = theirs ?? ''
  if (m === t || t === b) return { ok: true, text: m, conflicts: [] }
  if (m === b) return { ok: true, text: t, conflicts: [] }
  const tb = tokens(b, mode)
  const tm = tokens(m, mode)
  const tt = tokens(t, mode)
  const kb = tb.map((u) => key(u, mode))
  const km = tm.map((u) => key(u, mode))
  const kt = tt.map((u) => key(u, mode))
  const hunks: SideHunk[] = [
    ...diff(kb, km).map((h): SideHunk => [...h, 0]),
    ...diff(kb, kt).map((h): SideHunk => [...h, 1]),
  ]
  hunks.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[4] - y[4])
  const groups: SideHunk[][] = []
  for (const h of hunks) {
    const last = groups[groups.length - 1]
    if (last && last.some((g) => g[4] !== h[4] && overlap(g, h))) last.push(h)
    else groups.push([h])
  }
  const out: string[] = []
  const conflicts: Conflict[] = []
  let pos = 0
  let dm = 0
  let dt = 0
  for (const g of groups) {
    const g0 = Math.min(...g.map((h) => h[0]))
    const g1 = Math.max(...g.map((h) => h[1]))
    for (let k = pos; k < g0; k++) out.push(tm[k + dm])
    let em = dm
    let et = dt
    for (const h of g) {
      const d = h[3] - h[2] - (h[1] - h[0])
      if (h[4] === 0) em += d
      else et += d
    }
    const m0 = g0 + dm
    const m1 = g1 + em
    const t0 = g0 + dt
    const t1 = g1 + et
    const sides = new Set(g.map((h) => h[4]))
    if (sides.size === 1 && sides.has(0)) out.push(...tm.slice(m0, m1))
    else if (sides.size === 1) out.push(...tt.slice(t0, t1))
    else if (sameList(km.slice(m0, m1), kt.slice(t0, t1))) out.push(...tm.slice(m0, m1))
    else {
      conflicts.push({ base: tb.slice(g0, g1).join(''), mine: tm.slice(m0, m1).join(''), theirs: tt.slice(t0, t1).join('') })
      out.push(...tm.slice(m0, m1))
    }
    pos = g1
    dm = em
    dt = et
  }
  for (let k = pos; k < tb.length; k++) out.push(tm[k + dm])
  return { ok: conflicts.length === 0, text: out.join(''), conflicts }
}

/** JSON with sorted keys and Python's separators, for comparing values as merge3.py does. */
export function canon(v: unknown): string {
  if (v === undefined) return 'null'
  if (v === null || typeof v !== 'object') return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(canon).join(',')}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canon(o[k])}`).join(',')}}`
}

/** Equal values; a missing value, null and "" are the same, and so are null and []. */
export function same(a: unknown, b: unknown): boolean {
  const norm = (v: unknown) => (v === '' || v === undefined || (Array.isArray(v) && v.length === 0) ? null : v)
  return canon(norm(a)) === canon(norm(b))
}

export interface FieldMerge<T extends Record<string, unknown> = Record<string, unknown>> {
  ok: boolean
  /** Where a field conflicts, the "mine" value. */
  merged: T
  conflicts: string[]
  texts: Record<string, Conflict[]>
}

/** Per field: one side's change wins over an unchanged field; equal changes agree; text fields
 *  merge by units; any other field both sides changed differently is a conflict. */
export function mergeFields<T extends Record<string, unknown>>(base: Record<string, unknown> | null | undefined, mine: T, theirs: Record<string, unknown>, textFields: Record<string, MergeMode> = {}): FieldMerge<T> {
  const b0 = base ?? {}
  const merged: Record<string, unknown> = {}
  const conflicts: string[] = []
  const texts: Record<string, Conflict[]> = {}
  const keys = [...new Set([...Object.keys(b0), ...Object.keys(mine), ...Object.keys(theirs)])]
  for (const k of keys) {
    const b = b0[k]
    const m = mine[k]
    const t = theirs[k]
    let v: unknown
    if (same(m, b)) v = k in theirs || !same(t, m) ? t : m
    else if (same(t, b) || same(m, t)) v = m
    else if (k in textFields && [b, m, t].every((x) => x == null || typeof x === 'string')) {
      const r = mergeText(b as string | null, m as string | null, t as string | null, textFields[k])
      v = r.text
      if (!r.ok) {
        conflicts.push(k)
        texts[k] = r.conflicts
      }
    } else {
      conflicts.push(k)
      v = m
    }
    if (k in mine || k in theirs) merged[k] = v === undefined ? null : v
  }
  return { ok: conflicts.length === 0, merged: merged as T, conflicts, texts }
}
