// The checklist item grammar. The Python twin
// is backend/mailr/notes/itemadd.py (and, later, a Kotlin one in the shared Android module); all of them run
// the vectors in spec/cases/notes-quickadd-cases.yaml.
//
// One input becomes one or more checklist items. No model is involved: a word is taken only when a rule
// matches it exactly, and every taken word comes back as a token with its span (UTF-16 offsets into the
// input), so the add line can highlight it and turn it back into text.
//
// The parser never deletes characters while it works: a span it has used (a list token, a bullet, a
// section prefix) is masked with spaces in a working copy, so every offset stays an offset into the input.

export type ItemMode = 'list' | 'capture' | 'edit'

export type ItemTokenKind = 'list' | 'section' | 'qty' | 'check'

export interface ItemToken {
  kind: ItemTokenKind
  start: number
  end: number
  text: string
}

export interface ItemQty {
  /** A decimal string ("2", "0.5", "0.333"). */
  amount: string
  /** A canonical unit, or null for a count. */
  unit: string | null
  /** The typed form ("2 cans of", "x2"). */
  text: string
}

export interface ParsedItem {
  text: string
  qty: ItemQty | null
  /** A section name as the list spells it, or null for the top group (or none named). */
  section: string | null
  checked: boolean
  /** The span of the item's text in the input. */
  start: number
  end: number
}

export interface ItemList {
  id: string
  name: string
  sections: string[]
}

export interface ItemListRef {
  id: string | null
  name: string
  create: boolean
}

export interface ItemAddOptions {
  mode: ItemMode
  /** list and edit mode: the list the input belongs to (its sections are read). */
  list?: ItemList | null
  /** capture mode: the lists a `#List` token may name (the checklists the user may edit). */
  lists?: ItemList[]
  /** capture mode: the default list when no token names one. */
  defaultList?: ItemList | null
  /** Token texts the user turned back into text (× on a chip). */
  keep?: string[]
  /** list mode: an unknown `Name:` prefix makes that section, as a header line does. The new-list
   * composer sets it ("Costco: 2 milk" in a new list); the add line of an existing list does not, so
   * "Note: buy ripe ones" stays text there. */
  prefixCreates?: boolean
}

export interface ItemAddResult {
  list: ItemListRef | null
  items: ParsedItem[]
  new_sections: string[]
  tokens: ItemToken[]
}

// ---------------------------------------------------------------- units

const UNIT_FORMS: Record<string, string[]> = {
  g: ['g', 'gram', 'grams', 'gr'],
  kg: ['kg', 'kilo', 'kilos', 'kilogram', 'kilograms'],
  mg: ['mg'],
  lb: ['lb', 'lbs', 'pound', 'pounds'],
  oz: ['oz', 'ounce', 'ounces'],
  ml: ['ml', 'millilitre', 'millilitres', 'milliliter', 'milliliters'],
  l: ['l', 'litre', 'litres', 'liter', 'liters'],
  cl: ['cl'],
  dl: ['dl'],
  gal: ['gal', 'gallon', 'gallons'],
  qt: ['qt', 'quart', 'quarts'],
  pt: ['pt', 'pint', 'pints'],
  cup: ['cup', 'cups'],
  tbsp: ['tbsp', 'tablespoon', 'tablespoons'],
  tsp: ['tsp', 'teaspoon', 'teaspoons'],
  dozen: ['dozen', 'doz'],
  pack: ['pack', 'packs', 'pk'],
  can: ['can', 'cans', 'tin', 'tins'],
  bottle: ['bottle', 'bottles'],
  box: ['box', 'boxes'],
  bag: ['bag', 'bags'],
  jar: ['jar', 'jars'],
  bunch: ['bunch', 'bunches'],
  loaf: ['loaf', 'loaves'],
  carton: ['carton', 'cartons'],
  piece: ['piece', 'pieces', 'pc', 'pcs', 'ct'],
}

const UNIT_OF: Record<string, string> = {}
for (const [canon, forms] of Object.entries(UNIT_FORMS)) for (const f of forms) UNIT_OF[f] = canon

const UNIT_RX = Object.keys(UNIT_OF)
  .sort((a, b) => b.length - a.length)
  .join('|')

/** The canonical unit of a typed form, or null. */
export function unitOf(form: string): string | null {
  return UNIT_OF[form.toLowerCase()] ?? null
}

// ---------------------------------------------------------------- amounts

const VULGAR: Record<string, [number, number]> = {
  '½': [1, 2], '⅓': [1, 3], '⅔': [2, 3], '¼': [1, 4], '¾': [3, 4], '⅕': [1, 5], '⅖': [2, 5], '⅗': [3, 5],
  '⅘': [4, 5], '⅙': [1, 6], '⅚': [5, 6], '⅛': [1, 8], '⅜': [3, 8], '⅝': [5, 8], '⅞': [7, 8],
}
const VULGAR_CLASS = Object.keys(VULGAR).join('')
// Mixed number, fraction, integer with a Unicode fraction, decimal or integer. The first alternative that
// matches wins, so "1 1/2" is one amount and "1½" another.
const AMOUNT = `(?:\\d+\\s+\\d+/\\d+|\\d+/\\d+|\\d*[${VULGAR_CLASS}]|\\d+(?:\\.\\d+)?)`

/** An amount as a decimal string: integers as they are, others to 3 decimals without trailing zeros. */
export function amountValue(typed: string): string | null {
  const t = typed.trim()
  let value: number
  let m: RegExpExecArray | null
  if ((m = /^(\d+)\s+(\d+)\/(\d+)$/.exec(t))) {
    if (+m[3] === 0) return null
    value = +m[1] + +m[2] / +m[3]
  } else if ((m = /^(\d+)\/(\d+)$/.exec(t))) {
    if (+m[2] === 0) return null
    value = +m[1] / +m[2]
  } else if ((m = new RegExp(`^(\\d*)([${VULGAR_CLASS}])$`).exec(t))) {
    const [n, d] = VULGAR[m[2]]
    value = (m[1] ? +m[1] : 0) + n / d
  } else if (/^\d+(?:\.\d+)?$/.test(t)) {
    if (/^\d+$/.test(t)) return String(parseInt(t, 10))
    value = parseFloat(t)
  } else return null
  if (!isFinite(value) || value <= 0) return null
  return formatAmount(value)
}

export function formatAmount(value: number): string {
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

const isBareInteger = (typed: string) => /^\d+$/.test(typed.trim())

// ---------------------------------------------------------------- the item key

/** The item key: duplicates, learned order and usual items match on it. */
export function itemKey(text: string): string {
  let s = text.normalize('NFKC').toLowerCase().normalize('NFD').replace(/\p{M}+/gu, '').normalize('NFC')
  s = s.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '').replace(/\s+/g, ' ')
  if (!s) return ''
  const words = s.split(' ')
  words[words.length - 1] = foldPlural(words[words.length - 1])
  return words.join(' ')
}

function foldPlural(w: string): string {
  if (w.length >= 5 && w.endsWith('ies')) return w.slice(0, -3) + 'y'
  if (/(xes|ches|shes|sses)$/.test(w)) return w.slice(0, -2)
  if (w.length >= 7 && w.endsWith('oes')) return w.slice(0, -2)
  if (w.length >= 4 && w.endsWith('s') && !/(ss|us|is)$/.test(w)) return w.slice(0, -1)
  return w
}

// ---------------------------------------------------------------- sections and lists

const squash = (s: string) => s.toLowerCase().replace(/\s+/g, '')

/** A typed section name against the list's sections: exact (case and spaces ignored), else the only one
 * that starts with it when it has at least 3 characters. */
export function matchSection(typed: string, sections: string[]): string | null {
  const t = squash(typed)
  if (!t) return null
  const exact = sections.find((s) => squash(s) === t)
  if (exact) return exact
  if (t.length < 3) return null
  const hits = sections.filter((s) => squash(s).startsWith(t))
  return hits.length === 1 ? hits[0] : null
}

/** A typed `#list` name: exact first, else the lists whose name starts with it, shortest first. */
export function matchList(typed: string, lists: ItemList[]): ItemList | null {
  const t = squash(typed)
  if (!t) return null
  const exact = lists.find((l) => squash(l.name) === t)
  if (exact) return exact
  const hits = lists.filter((l) => squash(l.name).startsWith(t)).sort((a, b) => a.name.length - b.name.length)
  return hits[0] ?? null
}

// ---------------------------------------------------------------- the parser

interface Span {
  start: number
  end: number
}

const mask = (work: string[], start: number, end: number) => {
  for (let i = start; i < end; i++) work[i] = ' '
}

/** A section name: 1 to 4 words, starting with a letter. */
const SECTION_NAME = /^\p{L}[^\s:]*(?: [^\s:]+){0,3}$/u

/** Split a span of the working copy on commas and semicolons outside quotes and parentheses. */
function splitPieces(work: string, from: number, to: number): Span[] {
  const out: Span[] = []
  let depth = 0
  let quote = false
  let start = from
  for (let i = from; i < to; i++) {
    const c = work[i]
    if (c === '"' || c === '“' || c === '”') quote = !quote
    else if (!quote && c === '(') depth++
    else if (!quote && c === ')') depth = Math.max(0, depth - 1)
    else if (!quote && depth === 0 && (c === ',' || c === ';')) {
      out.push({ start, end: i })
      start = i + 1
    }
  }
  out.push({ start, end: to })
  return out
}

/** The quoted parts of a piece, blanked (a quantity is never read inside quotes). */
function blankQuotes(s: string): string {
  return s.replace(/"[^"]*"?|“[^”]*”?/g, (m) => '\u0001'.repeat(m.length))
}

interface QtyHit {
  qty: ItemQty
  token: Span
  text: Span
}

function anchoredRegex(src: string) {
  return new RegExp(src, 'iu')
}

const LEAD_UNIT = anchoredRegex(`^(${AMOUNT})\\s*(${UNIT_RX})(?![\\p{L}\\p{N}])(?:\\s+of(?=\\s))?(?=\\s)`)
const LEAD_TIMES = anchoredRegex(`^(${AMOUNT})\\s*[x×](?=\\s)`)
const LEAD_BARE = anchoredRegex(`^(${AMOUNT})(?=\\s)`)
const TRAIL_TIMES = anchoredRegex(`(?<=\\s)[x×]\\s*(${AMOUNT})$`)
const TRAIL_PAREN_UNIT = anchoredRegex(`(?<=\\s|^)\\(\\s*(${AMOUNT})\\s*(${UNIT_RX})\\s*\\)$`)
const TRAIL_UNIT = anchoredRegex(`(?<=\\s)(${AMOUNT})\\s*(${UNIT_RX})$`)
const TRAIL_PAREN = anchoredRegex(`(?<=\\s|^)\\(\\s*(${AMOUNT})\\s*\\)$`)
const UNIT_ONLY = anchoredRegex(`^(?:${UNIT_RX})$`)

/** The quantity of one piece (already trimmed, `s` is its text, `at` its offset), or null. */
function readQty(s: string, at: number, keep: Set<string>): QtyHit | null {
  const b = blankQuotes(s)
  // "2 x 4 lumber": an x between two numbers takes every quantity off the line.
  if (/\d\s*[x×]\s*\d/i.test(b)) return null
  const hit = (tokStart: number, tokEnd: number, amount: string, unit: string | null, textStart: number, textEnd: number): QtyHit | null => {
    const tokText = s.slice(tokStart, tokEnd)
    if (keep.has(tokText)) return null
    const value = amountValue(amount)
    if (value === null) return null
    const rest = s.slice(textStart, textEnd).trim()
    if (!rest) return null
    const lead = s.slice(textStart, textEnd).length - s.slice(textStart, textEnd).trimStart().length
    const tail = s.slice(textStart, textEnd).length - s.slice(textStart, textEnd).trimEnd().length
    return {
      qty: { amount: value, unit, text: tokText },
      token: { start: at + tokStart, end: at + tokEnd },
      text: { start: at + textStart + lead, end: at + textEnd - tail },
    }
  }
  const tries: Array<() => QtyHit | null> = [
    () => {
      const m = LEAD_UNIT.exec(b)
      return m ? hit(0, m[0].length, m[1], unitOf(m[2]), m[0].length, s.length) : null
    },
    () => {
      const m = LEAD_TIMES.exec(b)
      return m ? hit(0, m[0].length, m[1], null, m[0].length, s.length) : null
    },
    () => {
      const m = LEAD_BARE.exec(b)
      if (!m) return null
      if (isBareInteger(m[1]) && parseInt(m[1], 10) >= 100) return null
      if (UNIT_ONLY.test(b.slice(m[0].length).trim())) return null
      return hit(0, m[0].length, m[1], null, m[0].length, s.length)
    },
    () => {
      const m = TRAIL_TIMES.exec(b)
      return m ? hit(m.index, s.length, m[1], null, 0, m.index) : null
    },
    () => {
      const m = TRAIL_PAREN_UNIT.exec(b)
      return m ? hit(m.index, s.length, m[1], unitOf(m[2]), 0, m.index) : null
    },
    () => {
      const m = TRAIL_UNIT.exec(b)
      return m ? hit(m.index, s.length, m[1], unitOf(m[2]), 0, m.index) : null
    },
    () => {
      const m = TRAIL_PAREN.exec(b)
      return m ? hit(m.index, s.length, m[1], null, 0, m.index) : null
    },
  ]
  for (const t of tries) {
    const h = t()
    if (h) return h
  }
  return null
}

/** Item text: trimmed, single inner spaces, a trailing `, ; .` removed. Case kept. */
export function cleanText(s: string): string {
  return s.replace(/\s+/g, ' ').trim().replace(/[\s,;.]+$/, '').trim()
}

const CHECKBOX = /^(\s*)((?:[-*•–]\s+)?\[([ xX])\]|[✓✔])(\s+|$)/
const BULLET = /^(\s*)([-*•–]|\d+[.)])(\s+)/
const LIST_TOKEN = /(^|\s)(#\p{L}[\p{L}\p{N}_\-/]*)/u
const MD_HEADER = /^(\s*)(#{1,2}\s+(\S.*?))\s*$/u
const PREFIX = /^(\s*)([^:\n]+?):(?=\s|$)/u

/** Parse one input with the item grammar. */
export function parseItems(input: string, opts: ItemAddOptions): ItemAddResult {
  const keep = new Set(opts.keep ?? [])
  const tokens: ItemToken[] = []
  const work = Array.from({ length: input.length }, (_, i) => input[i])
  let list: ItemListRef | null = null
  let sections: string[] = []

  if (opts.mode === 'capture') {
    const m = LIST_TOKEN.exec(input)
    if (m && !keep.has(m[2])) {
      const start = m.index + m[1].length
      const end = start + m[2].length
      const found = matchList(m[2].slice(1), opts.lists ?? [])
      list = found ? { id: found.id, name: found.name, create: false } : { id: null, name: m[2].slice(1), create: true }
      sections = found ? [...found.sections] : []
      tokens.push({ kind: 'list', start, end, text: m[2] })
      mask(work, start, end)
    } else if (opts.defaultList) {
      list = { id: opts.defaultList.id, name: opts.defaultList.name, create: false }
      sections = [...opts.defaultList.sections]
    }
  } else {
    sections = [...(opts.list?.sections ?? [])]
  }

  const items: ParsedItem[] = []
  const newSections: string[] = []

  if (opts.mode === 'edit') {
    const t = work.join('')
    const lead = t.length - t.trimStart().length
    const body = t.trim()
    const piece = trimTrailingPunct(body)
    const q = readQty(input.slice(lead, lead + piece.length), lead, keep)
    if (q) tokens.push({ kind: 'qty', ...q.token, text: q.qty.text })
    const span = q ? q.text : { start: lead, end: lead + piece.length }
    const text = cleanText(input.slice(span.start, span.end))
    if (text) items.push({ text, qty: q?.qty ?? null, section: null, checked: false, ...span })
    return { list: null, items, new_sections: [], tokens: sortTokens(tokens) }
  }

  const known = () => [...sections, ...newSections]
  let header: string | null = null
  let lineStart = 0
  for (const rawLine of input.split('\n')) {
    const from = lineStart
    const to = from + rawLine.length
    lineStart = to + 1
    let line = work.slice(from, to).join('')
    if (!line.trim()) continue

    // A header line: "Name:" alone, or "## Name".
    const md = MD_HEADER.exec(line)
    if (md && !keep.has(md[2])) {
      const name = md[3].trim()
      const start = from + md[1].length
      tokens.push({ kind: 'section', start, end: start + md[2].length, text: input.slice(start, start + md[2].length) })
      header = headerSection(name, known(), newSections)
      continue
    }
    const hp = /^(\s*)([^:]+):\s*$/u.exec(line)
    if (hp && SECTION_NAME.test(hp[2].trim()) && hp[2] === hp[2].trimStart()) {
      const typed = hp[2].trim()
      const start = from + hp[1].length
      const tokText = input.slice(start, start + hp[2].length + 1)
      if (!keep.has(tokText) && tokText === `${typed}:`) {
        tokens.push({ kind: 'section', start, end: start + tokText.length, text: tokText })
        header = headerSection(typed, known(), newSections)
        continue
      }
    }

    // A checkbox or a bullet at the start of the line.
    let checked = false
    const cb = CHECKBOX.exec(line)
    if (cb) {
      const start = from + cb[1].length
      const mark = cb[2]
      const isCheck = mark === '✓' || mark === '✔' || /\[[xX]\]$/.test(mark)
      if (isCheck && !keep.has(mark)) {
        checked = true
        tokens.push({ kind: 'check', start, end: start + mark.length, text: mark })
      }
      if (!keep.has(mark)) mask(work, start, start + mark.length)
    } else {
      const bl = BULLET.exec(line)
      if (bl) mask(work, from + bl[1].length, from + bl[1].length + bl[2].length)
    }
    line = work.slice(from, to).join('')

    // A section prefix: "Costco: milk, eggs".
    let section = header
    const pf = PREFIX.exec(line)
    if (pf && SECTION_NAME.test(pf[2]) && line.slice(pf[0].length).trim()) {
      const hit = matchSection(pf[2], known())
      const start = from + pf[1].length
      const tokText = input.slice(start, start + pf[2].length + 1)
      if ((hit || (opts.prefixCreates && opts.mode === 'list')) && !keep.has(tokText)) {
        section = hit ?? headerSection(pf[2], known(), newSections)
        tokens.push({ kind: 'section', start, end: start + tokText.length, text: tokText })
        mask(work, start, start + tokText.length)
      }
    }

    const w = work.join('')
    for (const p of splitPieces(w, from, to)) {
      const raw = w.slice(p.start, p.end)
      if (!raw.trim()) continue
      const lead = raw.length - raw.trimStart().length
      const body = trimTrailingPunct(raw.trim())
      if (!body) continue
      const at = p.start + lead
      const original = input.slice(at, at + body.length)
      const q = readQty(original, at, keep)
      if (q) tokens.push({ kind: 'qty', ...q.token, text: q.qty.text })
      const span = q ? q.text : { start: at, end: at + body.length }
      const text = cleanText(input.slice(span.start, span.end).replace(/[\s]+/g, ' '))
      // A masked list token inside the piece leaves its text out.
      const shown = cleanText(w.slice(span.start, span.end))
      const finalText = shown || text
      if (!finalText) continue
      items.push({ text: finalText, qty: q?.qty ?? null, section, checked, ...span })
    }
  }
  return { list, items, new_sections: newSections, tokens: sortTokens(tokens) }
}

function headerSection(typed: string, known: string[], created: string[]): string {
  const hit = matchSection(typed, known)
  if (hit) return hit
  created.push(typed)
  return typed
}

function trimTrailingPunct(s: string): string {
  return s.replace(/[\s,;.]+$/, '')
}

function sortTokens(t: ItemToken[]): ItemToken[] {
  return [...t].sort((a, b) => a.start - b.start)
}

// ---------------------------------------------------------------- quick note

export type QuickNote =
  | { kind: 'note'; title: string; body: string }
  | ({ kind: 'items' } & ItemAddResult)

const QUICK_LIST = /^\s*#\p{L}[\p{L}\p{N}_\-/]*/u

/** "New note…" from anywhere: text that starts with a `#List` token adds items to that list (capture
 *  mode with no default list: `#Shopping Costco: milk` puts milk in the Costco section of Shopping; an
 *  unknown name makes the list); anything else is a text note, its first line the title. A `#name`
 *  later in the text stays text. The Python twin is notes/itemadd.py `quick`, the Kotlin twin
 *  ItemGrammar.quick; the vectors are the `quick` cases of notes-quickadd-cases.yaml. */
export function quickNote(input: string, lists: ItemList[]): QuickNote {
  const src = input ?? ''
  if (QUICK_LIST.test(src)) return { kind: 'items', ...parseItems(src, { mode: 'capture', lists, defaultList: null }) }
  const lines = src.trim().split('\n').map((l) => l.trim())
  return { kind: 'note', title: (lines[0] ?? '').slice(0, 500), body: lines.slice(1).join('\n').trim() }
}
