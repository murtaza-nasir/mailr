// One-off codemod (Oct 2026): moves every user-facing string in src/ into src/locales/en.json and
// replaces it with t('key') / tx('key', …). Kept for a later sweep of a branch that predates the
// extraction: `node scripts/i18n/extract.mjs [files…]` (no files: all of src). Run tsc and the
// tests afterwards; check-strings.mjs lists what is left.

import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ts, isProse, looksLikeCode, scanSource, templateText } from './scan.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const EN = path.join(ROOT, 'src/locales/en.json')
const SKIP = [/^src\/i18n\//, /^src\/locales\//, /\.test\.tsx?$/, /\.d\.ts$/]

const argFiles = process.argv.slice(2).filter((a) => !a.startsWith('--'))
const DRY = process.argv.includes('--dry')
const files = (argFiles.length ? argFiles : execSync(`git ls-files 'src/*.ts' 'src/*.tsx' 'src/**/*.ts' 'src/**/*.tsx'`, { cwd: ROOT }).toString().split('\n'))
  .map((f) => f.trim()).filter((f) => f && !SKIP.some((r) => r.test(f)))

// ---------- helpers ----------

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', mdash: '—', ndash: '–', hellip: '…', middot: '·', times: '×', rarr: '→', larr: '←', bull: '•', copy: '©' }
function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') return String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10))
    return ENTITIES[e] ?? m
  })
}

/** React's JSX text rule: lines trimmed, blank lines dropped, joined by one space. */
function cleanJsxText(raw) {
  const lines = raw.split(/\r\n|\n|\r/)
  let lastNonEmpty = 0
  lines.forEach((l, i) => { if (/[^ \t]/.test(l)) lastNonEmpty = i })
  let str = ''
  lines.forEach((line, i) => {
    const isFirst = i === 0
    const isLast = i === lines.length - 1
    let tr = line.replace(/\t/g, ' ')
    if (!isFirst) tr = tr.replace(/^[ ]+/, '')
    if (!isLast) tr = tr.replace(/[ ]+$/, '')
    if (tr) {
      if (i !== lastNonEmpty) tr += ' '
      str += tr
    }
  })
  return decodeEntities(str)
}

const unwrap = (e) => {
  while (e && (ts.isParenthesizedExpression(e) || ts.isNonNullExpression(e) || ts.isAsExpression(e) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(e)))) e = e.expression
  return e
}
const stringy = (e) => { e = unwrap(e); return e && (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e) || ts.isTemplateExpression(e)) }
const hasJsx = (n) => {
  let found = false
  const v = (x) => { if (found) return; if (ts.isJsxElement(x) || ts.isJsxSelfClosingElement(x) || ts.isJsxFragment(x)) { found = true; return } ts.forEachChild(x, v) }
  v(n)
  return found
}

const camel = (s) => (/^[A-Z0-9_]+$/.test(s) ? s.toLowerCase() : s).replace(/[_-]+(\w)/g, (_, c) => c.toUpperCase())
const lowerFirst = (s) => s ? s[0].toLowerCase() + s.slice(1) : s

/** `n === 1` / `n !== 1` → { count: n, eq: true|false } */
function pluralTest(cond) {
  cond = unwrap(cond)
  if (!cond || !ts.isBinaryExpression(cond)) return null
  const op = cond.operatorToken.kind
  const eq = op === ts.SyntaxKind.EqualsEqualsEqualsToken || op === ts.SyntaxKind.EqualsEqualsToken
  const ne = op === ts.SyntaxKind.ExclamationEqualsEqualsToken || op === ts.SyntaxKind.ExclamationEqualsToken
  if (!eq && !ne) return null
  const isOne = (x) => { x = unwrap(x); return x && ts.isNumericLiteral(x) && x.text === '1' }
  if (isOne(cond.right)) return { count: cond.left, eq }
  if (isOne(cond.left)) return { count: cond.right, eq }
  return null
}

const NORM = (sf, e) => unwrap(e).getText(sf).replace(/\.toLocaleString\([^)]*\)$/, '').replace(/\s+/g, '').replace(/^\((.*)\)$/, '$1')

// ---------- key namespaces ----------

function namespaceOf(file) {
  const rel = file.replace(/^src\//, '').replace(/\.(tsx?|mjs)$/, '')
  const parts = rel.split('/')
  let ns
  if (parts[0] === 'features') ns = parts.slice(1)
  else if (parts.length === 1) ns = ['app', parts[0]]
  else ns = parts
  return ns.map((p) => lowerFirst(camel(p.replace(/[^A-Za-z0-9_-]/g, '')))).join('.')
}

function slugOf(text) {
  const words = text
    .replace(/\{\{\s*\w+\s*\}\}/g, ' ')
    .replace(/<\/?\w+\s*\/?>/g, ' ')
    .replace(/[’']/g, '')
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .slice(0, 5)
  let s = words.map((w, i) => (i === 0 ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase())).join('')
  if (!s) s = 'text'
  if (/^\d/.test(s)) s = 'n' + s
  return s.slice(0, 40)
}

// ---------- analysis ----------

const INLINE = new Set(['Ext', 'b', 'strong', 'em', 'i', 'u', 'kbd', 'code', 'a', 'Link', 'NavLink', 'span', 'abbr', 'mark', 'small', 'sub', 'sup', 's', 'q', 'cite', 'time', 'bdi', 'samp', 'var', 'ins', 'del'])
const SELF = new Set(['br', 'wbr'])

function tagNameOf(sf, el) {
  const tn = ts.isJsxElement(el) ? el.openingElement.tagName : el.tagName
  return tn.getText(sf)
}

/** Binding names in the file (to avoid shadowing t / tx). */
function bindings(sf) {
  const names = new Set()
  const v = (n) => {
    if ((ts.isVariableDeclaration(n) || ts.isParameter(n) || ts.isBindingElement(n) || ts.isFunctionDeclaration(n) || ts.isImportSpecifier(n) || ts.isImportClause(n) || ts.isNamespaceImport(n)) && n.name && ts.isIdentifier(n.name)) names.add(n.name.text)
    ts.forEachChild(n, v)
  }
  v(sf)
  return names
}

/** Names of variables holding the assistant's name. */
function assistantVars(sf) {
  const out = new Set()
  const v = (n) => {
    if (ts.isVariableDeclaration(n) && n.initializer && ts.isIdentifier(n.name)) {
      const i = unwrap(n.initializer)
      if (ts.isCallExpression(i) && /^(useAssistantName|assistantNameNow)$/.test(i.expression.getText(sf))) out.add(n.name.text)
    }
    ts.forEachChild(n, v)
  }
  v(sf)
  return out
}

/**
 * A message: segments of text, params and tags, plus (for a plural) its count expression.
 * seg: {k:'text', v} | {k:'param', e} | {k:'tag', el, segs, self}
 */
function analyzeFile(file, src) {
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const cands = scanSource(file, src, sf)
  const nodeCands = new Map() // node -> cand
  const jsxTexts = new Set()
  for (const c of cands) {
    if (c.kind === 'jsx-text') jsxTexts.add(c.node)
    else nodeCands.set(c.node, c)
  }
  const msgs = [] // {node (or run), segs | forms, ...}
  const replace = new Map() // node -> msg
  const runsOf = new Map() // jsx parent -> [{start, end, msg}]

  // --- string literal / template candidates
  const segsOfStringy = (e) => {
    e = unwrap(e)
    if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) {
      const parentAttr = e.parent && ts.isJsxAttribute(e.parent)
      return [{ k: 'text', v: parentAttr ? decodeEntities(e.text) : e.text }]
    }
    const segs = [{ k: 'text', v: e.head.text }]
    for (const span of e.templateSpans) {
      const x = unwrap(span.expression)
      if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) segs.push({ k: 'text', v: x.text })
      else segs.push({ k: 'param', e: span.expression })
      segs.push({ k: 'text', v: span.literal.text })
    }
    return segs
  }

  const handled = new Set()
  for (const [node, c] of nodeCands) {
    if (handled.has(node)) continue
    // Whole-ternary plural: n === 1 ? 'One reply' : `${n} replies`
    let p = node.parent
    while (p && ts.isParenthesizedExpression(p)) p = p.parent
    if (p && ts.isConditionalExpression(p) && p.condition !== node) {
      const pt = pluralTest(p.condition)
      // Only when every value in both texts is the count itself: the other branch's values may
      // not be valid then (`items[0].text` when there are none).
      const onlyCount = (e) => {
        e = unwrap(e)
        if (!ts.isTemplateExpression(e)) return true
        return e.templateSpans.every((sp) => NORM(sf, sp.expression) === NORM(sf, pt.count) || ts.isStringLiteral(unwrap(sp.expression)))
      }
      if (pt && stringy(p.whenTrue) && stringy(p.whenFalse) && onlyCount(p.whenTrue) && onlyCount(p.whenFalse)) {
        const one = segsOfStringy(pt.eq ? p.whenTrue : p.whenFalse)
        const other = segsOfStringy(pt.eq ? p.whenFalse : p.whenTrue)
        const msg = { file, sf, plural: { count: pt.count }, forms: { one, other }, attr: false, anchor: p }
        replace.set(p, msg)
        msgs.push(msg)
        handled.add(unwrap(p.whenTrue)); handled.add(unwrap(p.whenFalse))
        continue
      }
    }
    const segs = segsOfStringy(node)
    const msg = { file, sf, segs, attr: node.parent && ts.isJsxAttribute(node.parent), anchor: node }
    replace.set(node, msg)
    msgs.push(msg)
    handled.add(node)
  }

  // --- JSX runs
  const classify = (ch) => {
    if (ts.isJsxText(ch)) return { k: 'text', v: cleanJsxText(src.slice(ch.pos, ch.end)), node: ch }
    if (ts.isJsxExpression(ch)) {
      if (!ch.expression) return { k: 'break' }
      const x = unwrap(ch.expression)
      if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) return { k: 'str', v: x.text, node: ch }
      if (hasJsx(ch.expression)) return { k: 'break' }
      if (ch.dotDotDotToken) return { k: 'break' }
      return { k: 'expr', e: ch.expression, node: ch }
    }
    if (ts.isJsxSelfClosingElement(ch)) {
      return SELF.has(tagNameOf(sf, ch)) ? { k: 'br', el: ch, node: ch } : { k: 'break' }
    }
    if (ts.isJsxElement(ch)) {
      const name = tagNameOf(sf, ch)
      if (!INLINE.has(name)) return { k: 'break' }
      const inner = []
      for (const c2 of ch.children) {
        const x = classify(c2)
        if (x.k === 'text' || x.k === 'str' || x.k === 'expr') inner.push(x)
        else return { k: 'break' }
      }
      return { k: 'inline', el: ch, inner, node: ch }
    }
    return { k: 'break' }
  }
  const segOf = (x) => {
    if (x.k === 'text' || x.k === 'str') return { k: 'text', v: x.v }
    if (x.k === 'expr') return { k: 'param', e: x.e }
    if (x.k === 'br') return { k: 'tag', el: x.el, self: true, segs: [] }
    if (x.k === 'inline') return { k: 'tag', el: x.el, self: false, segs: x.inner.map(segOf) }
  }
  const inRun = new Set()
  const visitJsx = (node) => {
    if (inRun.has(node)) return
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
      const kids = node.children.map(classify)
      if (kids.some((k) => k.k === 'text' && jsxTexts.has(k.node))) {
        let i = 0
        while (i < kids.length) {
          if (kids[i].k === 'break') { i++; continue }
          let j = i
          while (j < kids.length && kids[j].k !== 'break') j++
          // trim edges: whitespace-only text and <br/>
          let a = i, b = j - 1
          const blank = (k) => (k.k === 'text' || k.k === 'str') && !k.v.trim() || k.k === 'br'
          while (a <= b && blank(kids[a])) a++
          while (b >= a && blank(kids[b])) b--
          const direct = kids.slice(a, b + 1)
          const hasWords = direct.some((k) => (k.k === 'text' && jsxTexts.has(k.node)) || (k.k === 'str' && /[A-Za-z]{2}/.test(k.v) && !looksLikeCode(k.v)))
          if (a <= b && hasWords) {
            const fold = (k) => {
              if (k.k === 'str') {
                const x = unwrap(k.node.expression)
                if (replace.has(x)) { const old = replace.get(x); replace.delete(x); msgs.splice(msgs.indexOf(old), 1) }
              }
              if (k.k === 'inline') { inRun.add(k.node); k.inner.forEach(fold) }
            }
            direct.forEach(fold)
            const segs = direct.map(segOf)
            const msg = { file, sf, segs, jsx: true, kids: direct, anchor: node }
            if (!runsOf.has(node)) runsOf.set(node, [])
            runsOf.get(node).push({ start: a, end: b, msg })
            msgs.push(msg)
          }
          i = j
        }
      }
    }
    ts.forEachChild(node, visitJsx)
  }
  visitJsx(sf)
  return { sf, src, msgs, replace, runsOf, binds: bindings(sf), aVars: assistantVars(sf) }
}

// ---------- message → text, params ----------

/** Conditionals a plural message took in (their own messages are dropped). */
const consumed = new Set()

function paramNameOf(sf, e, aVars) {
  e = unwrap(e)
  if (ts.isCallExpression(e) && /^(assistantNameNow|useAssistantName)$/.test(e.expression.getText(sf))) return 'name'
  if (ts.isIdentifier(e)) {
    if (aVars.has(e.text)) return 'name'
    return e.text === 'name' ? 'itemName' : camel(e.text)
  }
  if (ts.isPropertyAccessExpression(e)) {
    const n = camel(e.name.text)
    const generic = /^(name|length|total|value|title|label|id|text|size|data|current|email|count)$/.test(n)
    if (generic) {
      const obj = unwrap(e.expression)
      const on = ts.isIdentifier(obj) ? obj.text : ts.isPropertyAccessExpression(obj) ? obj.name.text : ''
      if (n === 'count' || n === 'email' || n === 'total' || n === 'length' && !on) return n === 'length' ? 'n' : n
      if (on && on.length > 1 && !/^(data|props|p|e|r|s|x|it|item|q)$/.test(on)) return lowerFirst(camel(on)) + n[0].toUpperCase() + n.slice(1)
      return n === 'name' ? 'itemName' : n === 'length' ? 'n' : n
    }
    return n
  }
  if (ts.isCallExpression(e)) {
    const callee = e.expression
    if (ts.isPropertyAccessExpression(callee) && /^(toLocaleString|toString|toFixed|trim|toLowerCase|toUpperCase|join|slice)$/.test(callee.name.text)) return paramNameOf(sf, callee.expression, aVars)
    const cn = ts.isIdentifier(callee) ? callee.text : ts.isPropertyAccessExpression(callee) ? callee.name.text : ''
    const stripped = cn.replace(/^(format|fmt|show|get|to|render|describe)(?=[A-Z])/, '')
    if (stripped) return lowerFirst(stripped)
  }
  if (ts.isElementAccessExpression(e)) return paramNameOf(sf, e.expression, aVars)
  if (ts.isBinaryExpression(e) && (e.operatorToken.kind === ts.SyntaxKind.BarBarToken || e.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) return paramNameOf(sf, e.left, aVars)
  return 'value'
}

/** Build {text|forms, params:[{name, e|raw}], tags:[{name, el, self}]} */
function render(msg, aVars) {
  const sf = msg.sf
  const params = [] // {name, key(normalized expr), e}
  const tags = []
  let pluralCount = msg.plural ? msg.plural.count : null
  // find a plural conditional among params, if no plural yet
  const allSegs = (segs) => segs.flatMap((s) => (s.k === 'tag' ? [s, ...allSegs(s.segs)] : [s]))
  if (!pluralCount && msg.segs) {
    for (const s of allSegs(msg.segs)) {
      if (s.k !== 'param') continue
      const x = unwrap(s.e)
      if (ts.isConditionalExpression(x)) {
        const pt = pluralTest(x.condition)
        const lit = (y) => { y = unwrap(y); return ts.isStringLiteral(y) || ts.isNoSubstitutionTemplateLiteral(y) }
        if (pt && lit(x.whenTrue) && lit(x.whenFalse)) { pluralCount = pt.count; break }
      }
    }
  }
  const countKey = pluralCount ? NORM(sf, pluralCount) : null
  const nameFor = (e) => {
    const key = NORM(sf, e)
    if (countKey && key === countKey) return 'count'
    const found = params.find((p) => p.key === key)
    if (found) return found.name
    let base = paramNameOf(sf, e, aVars)
    if (base === 'count' && countKey) base = 'n'
    if (!/^[A-Za-z_]\w*$/.test(base)) base = 'value'
    let name = base
    let i = 2
    while (params.some((p) => p.name === name) || (name === 'count' && countKey)) name = base + i++
    params.push({ name, key, e })
    return name
  }
  const tagFor = (s) => {
    const elText = s.el.getText(sf)
    const found = tags.find((t) => t.elKey === (s.self ? elText : s.el.openingElement.getText(sf) + '|' + s.el.closingElement.getText(sf)))
    if (found) return found.name
    let base = lowerFirst(tagNameOf(sf, s.el).replace(/[^A-Za-z0-9]/g, ''))
    let name = base
    let i = 2
    while (tags.some((t) => t.name === name)) name = base + i++
    tags.push({ name, el: s.el, self: s.self, elKey: s.self ? elText : s.el.openingElement.getText(sf) + '|' + s.el.closingElement.getText(sf) })
    return name
  }
  const textOf = (segs, form) => segs.map((s) => {
    if (s.k === 'text') return s.v
    if (s.k === 'tag') {
      const n = tagFor(s)
      return s.self ? `<${n}/>` : `<${n}>${textOf(s.segs, form)}</${n}>`
    }
    const x = unwrap(s.e)
    if (form && ts.isConditionalExpression(x)) {
      const pt = pluralTest(x.condition)
      if (pt && countKey && NORM(sf, pt.count) === countKey) {
        const lit = (z) => { z = unwrap(z); return ts.isStringLiteral(z) || ts.isNoSubstitutionTemplateLiteral(z) }
        if (lit(x.whenTrue) && lit(x.whenFalse)) {
          consumed.add(x)
          return unwrap((form === 'one') === pt.eq ? x.whenTrue : x.whenFalse).text
        }
      }
    }
    return `{{${nameFor(s.e)}}}`
  }).join('')
  let out
  if (msg.forms) out = { forms: { one: textOf(msg.forms.one, 'one'), other: textOf(msg.forms.other, 'other') } }
  else if (countKey) out = { forms: { one: textOf(msg.segs, 'one'), other: textOf(msg.segs, 'other') } }
  else out = { text: textOf(msg.segs, null) }
  // Ambrose → {{name}}
  const fix = (s) => s.replace(/\bAmbrose\b/g, '{{name}}')
  const usesAmbrose = (out.text ?? out.forms.one + out.forms.other).match(/\bAmbrose\b/) && !/[aA]ssistantName\.ts$/.test(msg.file)
  if (usesAmbrose) {
    if (out.text) out.text = fix(out.text)
    else out.forms = { one: fix(out.forms.one), other: fix(out.forms.other) }
    if (!params.some((p) => p.name === 'name')) params.push({ name: 'name', key: '__assistant', raw: 'assistantNameNow()' })
    else {
      const p = params.find((q) => q.name === 'name')
      if (p.key !== '__assistant' && !aVars.has(p.key)) { /* a different name: rename it */ }
    }
    msg.needsAssistant = !params.find((p) => p.name === 'name')?.e
  }
  if (countKey) params.unshift({ name: 'count', key: countKey, e: pluralCount })
  // the trimmed text; leading/trailing spaces go outside (JSX) or stay (code)
  return { ...out, params, tags }
}

// ---------- pass 1: analyze all ----------

const analyses = []
for (const f of files) {
  const src = readFileSync(path.join(ROOT, f), 'utf8')
  const a = analyzeFile(f, src)
  a.file = f
  for (const m of a.msgs) m.r = render(m, a.aVars)
  analyses.push(a)
}

for (const a of analyses) {
  a.msgs = a.msgs.filter((m) => !(m.anchor && consumed.has(m.anchor)))
  for (const [node, m] of [...a.replace]) if (consumed.has(node) && m.anchor === node) a.replace.delete(node)
}

// common.* for short param-free texts used in 3+ files
const filesByText = new Map()
for (const a of analyses) for (const m of a.msgs) {
  if (!m.r.text || m.r.params.length || m.r.tags.length) continue
  const t = m.r.text.trim()
  if (t.split(/\s+/).length > 3 || t.length > 30) continue
  if (!filesByText.has(t)) filesByText.set(t, new Set())
  filesByText.get(t).add(a.file)
}
const commonKey = new Map()
const usedCommon = new Set()
for (const [t, fs] of [...filesByText].sort((x, y) => x[0].localeCompare(y[0]))) {
  if (fs.size < 3) continue
  let k = slugOf(t)
  let i = 2
  while (usedCommon.has(k)) k = slugOf(t) + i++
  usedCommon.add(k)
  commonKey.set(t, `common.${k}`)
}

// ---------- en.json ----------

const en = existsSync(EN) ? JSON.parse(readFileSync(EN, 'utf8')) : {}
function setKey(key, value) {
  const parts = key.split('.')
  let o = en
  for (const p of parts.slice(0, -1)) {
    if (typeof o[p] === 'string') throw new Error(`key clash at ${key}`)
    o = o[p] ??= {}
  }
  const last = parts[parts.length - 1]
  if (typeof o[last] === 'object') throw new Error(`key clash at ${key} (namespace)`)
  if (o[last] !== undefined && o[last] !== value) throw new Error(`key ${key} already holds a different text`)
  o[last] = value
}
function getKey(key) {
  let o = en
  for (const p of key.split('.')) { if (!o || typeof o !== 'object') return undefined; o = o[p] }
  return o
}

let extracted = 0
const touched = []
// Every namespace and its prefixes: a slug may not take one of these names.
const nsPrefixes = new Set()
for (const a of analyses) {
  const parts = namespaceOf(a.file).split('.')
  for (let i = 1; i <= parts.length; i++) nsPrefixes.add(parts.slice(0, i).join('.'))
}

for (const a of analyses) {
  if (!a.msgs.length) continue
  const ns = namespaceOf(a.file)
  const keyOfText = new Map()
  const usedSlugs = new Set()
  // existing slugs under this namespace
  const existing = getKey(ns)
  if (existing && typeof existing === 'object') for (const k of Object.keys(existing)) usedSlugs.add(k.replace(/_(one|other|zero|two|few|many)$/, ''))
  for (const m of a.msgs) {
    const r = m.r
    if (m.jsx && r.forms) {
      m.lead = /^\s/.test(r.forms.other)
      m.trail = /\s$/.test(r.forms.other)
      r.forms = { one: r.forms.one.trim(), other: r.forms.other.trim() }
    }
    const sig = r.text !== undefined ? r.text.trim() : `${r.forms.one}\u0000${r.forms.other}`
    const lead = r.text !== undefined ? /^\s/.test(r.text) : m.lead
    const trail = r.text !== undefined ? /\s$/.test(r.text) : m.trail
    m.lead = m.jsx && lead
    m.trail = m.jsx && trail
    const value = r.text !== undefined ? (m.jsx ? r.text.trim() : r.text) : null
    if (r.text !== undefined && !r.params.length && !r.tags.length && commonKey.has(r.text.trim()) && (m.jsx || r.text === r.text.trim())) {
      m.key = commonKey.get(r.text.trim())
      setKey(m.key, r.text.trim())
      extracted++
      continue
    }
    const dedupe = m.jsx ? sig : r.text !== undefined ? r.text : sig
    if (keyOfText.has(dedupe)) { m.key = keyOfText.get(dedupe); continue }
    const base = slugOf(r.text ?? r.forms.other)
    let slug = base
    let i = 2
    while (usedSlugs.has(slug) || nsPrefixes.has(`${ns}.${slug}`)) slug = base + i++
    usedSlugs.add(slug)
    m.key = `${ns}.${slug}`
    keyOfText.set(dedupe, m.key)
    if (value !== null) setKey(m.key, value)
    else { setKey(`${m.key}_one`, r.forms.one); setKey(`${m.key}_other`, r.forms.other) }
    extracted++
  }
}

// ---------- pass 2: emit ----------

function relImport(file) {
  let rel = path.relative(path.dirname(path.join(ROOT, file)), path.join(ROOT, 'src/i18n'))
  if (!rel.startsWith('.')) rel = './' + rel
  return rel
}
function relAssistant(file, mod = 'useAssistantName') {
  let rel = path.relative(path.dirname(path.join(ROOT, file)), path.join(ROOT, 'src/features/rail/assistant', mod))
  if (!rel.startsWith('.')) rel = './' + rel
  return rel
}

for (const a of analyses) {
  if (!a.msgs.length) continue
  const { sf, src, replace, runsOf } = a
  const T = a.binds.has('t') ? (a.binds.has('tr') ? 'i18nT' : 'tr') : 't'
  const TX = a.binds.has('tx') ? 'txr' : 'tx'
  let usedT = false, usedTX = false, usedAssistant = false, usedHook = false
  // The assistant's name: useAssistantName() at the top of the enclosing component or hook,
  // else assistantNameNow().
  const hookFns = new Map() // fn -> var name
  const componentOf = (node) => {
    for (let p = node.parent; p; p = p.parent) {
      if (ts.isFunctionDeclaration(p) && p.name && /^([A-Z]|use[A-Z])/.test(p.name.text)) return p
      if (ts.isArrowFunction(p) || ts.isFunctionExpression(p)) {
        const name = p.name?.text ?? (ts.isVariableDeclaration(p.parent) && ts.isIdentifier(p.parent.name) ? p.parent.name.text : null)
        if (name && /^([A-Z]|use[A-Z])/.test(name)) return p
      }
    }
    return null
  }
  if (!/[aA]ssistantName\.ts$/.test(a.file)) for (const m of a.msgs) {
    const p = m.r.params.find((q) => q.name === 'name' && q.raw)
    if (!p) continue
    const fn = componentOf(m.anchor)
    if (!fn || !fn.body || !ts.isBlock(fn.body)) continue
    if (!hookFns.has(fn)) {
      let v = null
      for (const st of fn.body.statements) {
        if (ts.isVariableStatement(st)) for (const d of st.declarationList.declarations) {
          const i = d.initializer && unwrap(d.initializer)
          if (i && ts.isCallExpression(i) && i.expression.getText(sf) === 'useAssistantName' && ts.isIdentifier(d.name)) v = { name: d.name.text, existing: true }
        }
      }
      hookFns.set(fn, v ?? { name: a.binds.has('assistantName') ? 'assistantName2' : 'assistantName', existing: false })
    }
    p.raw = hookFns.get(fn).name
    p.hook = true
  }

  const call = (m, asJsx) => {
    const r = m.r
    const ps = r.params.map((p) => {
      const val = p.raw ?? emit(p.e).trim()
      if (p.name === 'name' && p.raw && !p.hook) usedAssistant = true
      return /^[A-Za-z_]\w*$/.test(val) && val === p.name ? p.name : `${p.name}: ${val}`
    })
    if (r.tags.length) {
      usedTX = true
      const tg = r.tags.map((tag) => tag.self ? `${tag.name}: () => ${emit(tag.el)}` : `${tag.name}: (c) => ${emit(tag.el.openingElement)}{c}${emit(tag.el.closingElement)}`)
      return `${TX}('${m.key}', ${ps.length ? `{ ${ps.join(', ')} }` : 'undefined'}, { ${tg.join(', ')} })`
    }
    usedT = true
    return ps.length ? `${T}('${m.key}', { ${ps.join(', ')} })` : `${T}('${m.key}')`
  }

  const isDoc = (k) => k.kind >= ts.SyntaxKind.FirstJSDocNode && k.kind <= ts.SyntaxKind.LastJSDocNode
  function emit(node) {
    if (ts.isBlock(node) && node.parent && hookFns.has(node.parent) && !hookFns.get(node.parent).existing) {
      const v = hookFns.get(node.parent)
      v.existing = true
      usedHook = true
      const inner = emit(node)
      const first = node.statements[0]
      const indent = first ? /[ \t]*$/.exec(src.slice(0, first.getStart(sf)))[0] : '  '
      const at = inner.indexOf('{') + 1
      return inner.slice(0, at) + `\n${indent}const ${v.name} = useAssistantName()` + inner.slice(at)
    }
    if (replace.has(node)) {
      const m = replace.get(node)
      const c = call(m)
      return src.slice(node.pos, node.getStart(sf)) + (m.attr ? `{${c}}` : c)
    }
    if (runsOf.has(node)) return emitJsxParent(node)
    const kids = node.getChildren(sf).filter((k) => !isDoc(k))
    if (!kids.length) return src.slice(node.pos, node.end)
    let out = ''
    let pos = node.pos
    for (const k of kids) {
      out += src.slice(pos, k.pos)
      out += emit(k)
      pos = k.end
    }
    return out + src.slice(pos, node.end)
  }

  function emitJsxParent(node) {
    const runs = runsOf.get(node)
    const isEl = ts.isJsxElement(node)
    const open = isEl ? node.openingElement : node.openingFragment
    const close = isEl ? node.closingElement : node.closingFragment
    let out = src.slice(node.pos, open.pos) + emit(open)
    let pos = open.end
    const children = node.children
    let ci = 0
    while (ci < children.length) {
      const run = runs.find((r) => r.start === ci)
      const ch = children[ci]
      if (run) {
        const first = children[run.start]
        const last = children[run.end]
        let lead = ''
        if (ts.isJsxText(first)) {
          const ws = /^\s*/.exec(src.slice(first.pos, first.end))[0]
          if (ws.includes('\n')) lead = ws
        }
        let trailWs = ''
        if (ts.isJsxText(last)) {
          const ws = /\s*$/.exec(src.slice(last.pos, last.end))[0]
          if (ws.includes('\n')) trailWs = ws
        }
        out += src.slice(pos, first.pos)
        const m = run.msg
        out += lead + (m.lead ? "{' '}" : '') + `{${call(m, true)}}` + (m.trail ? "{' '}" : '') + trailWs
        pos = last.end
        ci = run.end + 1
        continue
      }
      out += src.slice(pos, ch.pos) + emit(ch)
      pos = ch.end
      ci++
    }
    out += src.slice(pos, close.pos) + emit(close) + src.slice(close.end, node.end)
    return out
  }

  let out = emit(sf) + src.slice(sf.end)
  // imports
  const names = []
  if (usedT) names.push(T === 't' ? 't' : `t as ${T}`)
  if (usedTX) names.push(TX === 'tx' ? 'tx' : `tx as ${TX}`)
  let imports = names.length ? `import { ${names.join(', ')} } from '${relImport(a.file)}'\n` : ''
  if (!/[aA]ssistantName\.ts$/.test(a.file)) {
    if (usedAssistant && !/import \{[^}]*\bassistantNameNow\b[^}]*\} from/.test(src)) imports += `import { assistantNameNow } from '${relAssistant(a.file, 'assistantName')}'\n`
    if (usedHook && !/import \{[^}]*\buseAssistantName\b[^}]*\} from/.test(src)) imports += `import { useAssistantName } from '${relAssistant(a.file)}'\n`
  }
  if (imports) {
    const sf2 = ts.createSourceFile(a.file, out, ts.ScriptTarget.Latest, true, a.file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    const lastImport = [...sf2.statements].filter((s) => ts.isImportDeclaration(s)).pop()
    if (lastImport) out = out.slice(0, lastImport.end) + '\n' + imports.trimEnd() + out.slice(lastImport.end)
    else {
      const first = sf2.statements[0]
      const at = first ? first.getStart(sf2) : 0
      out = out.slice(0, at) + imports + '\n' + out.slice(at)
    }
  }
  if (!DRY) writeFileSync(path.join(ROOT, a.file), out)
  touched.push(a.file)
}

const sortDeep = (o) => (typeof o === 'object' ? Object.fromEntries(Object.keys(o).sort().map((k) => [k, sortDeep(o[k])])) : o)
if (!DRY) writeFileSync(EN, JSON.stringify(sortDeep(en), null, 2) + '\n')
console.log(`messages: ${extracted}, files: ${touched.length}, common keys: ${commonKey.size}`)
