// Finds the user-facing strings in a TypeScript/TSX file.
// Used by check-strings.mjs (the lint that fails on a new hard-coded string) and by the one-off
// extraction codemod. A heuristic, not a parser of meaning: text between JSX tags, the text
// attributes (aria-label, title, placeholder, label…), and string literals in code that read
// like prose. Code-looking strings (paths, identifiers, CSS, key names in comparisons, API
// routes) are left alone.

import ts from 'typescript'

/** JSX attributes that always hold text a person reads (or hears). */
export const TEXT_ATTRS = new Set([
  'aria-label', 'aria-description', 'aria-roledescription', 'aria-valuetext', 'aria-placeholder',
  'title', 'placeholder', 'alt', 'label', 'desc', 'sub', 'k', 'hint', 'message', 'text', 'tooltip',
  'heading', 'caption', 'description', 'emptyText', 'empty', 'confirmLabel', 'actionLabel', 'cancelLabel',
  'okLabel', 'subtitle', 'note', 'help', 'body', 'question', 'prompt', 'summary', 'lead', 'intro', 'detail',
  'ariaLabel', 'tip', 'name', 'data-tip', 'data-label', 'confirm', 'what', 'empty',
])

/** A JSX attribute that holds text: the list above, or a name such as `skipLabel` or `emptyText`. */
export function isTextAttr(n) {
  return TEXT_ATTRS.has(n) || /^[a-z]+(Label|Text|Title|Hint|Placeholder|Message|Desc|Tip)$/.test(n)
}

/** A JSX attribute that never holds text. */
export function isCodeAttr(n) {
  if (isTextAttr(n)) return false
  return CODE_ATTRS.has(n) || n.startsWith('data-') || /^on[A-Z]/.test(n)
}

/** JSX attributes that never hold text a person reads. */
const CODE_ATTRS = new Set([
  'className', 'class', 'id', 'key', 'href', 'src', 'type', 'role', 'value', 'defaultValue', 'to', 'rel',
  'target', 'htmlFor', 'autoComplete', 'inputMode', 'pattern', 'accept', 'method', 'action', 'download',
  'lang', 'dir', 'form', 'style', 'variant', 'size', 'tone', 'icon', 'kind', 'mode', 'layout', 'align',
  'side', 'placement', 'testId', 'as', 'step', 'min', 'max', 'enterKeyHint', 'autoCapitalize', 'spellCheck',
  'wrap', 'tabIndex', 'sizes', 'srcSet', 'loading', 'decoding', 'crossOrigin', 'referrerPolicy', 'sandbox',
  'allow', 'viewBox', 'd', 'fill', 'stroke', 'xmlns', 'points', 'transform', 'path', 'route', 'section',
  'group', 'shortcut', 'keys', 'hotkey', 'zone', 'color', 'width', 'height', 'storageKey', 'scope', 'surface',
  'area', 'field', 'tab', 'view', 'state', 'status', 'format', 'accessKey', 'list', 'slot', 'part', 'cols',
  'rows', 'strokeWidth', 'strokeLinecap', 'strokeLinejoin', 'fillRule', 'clipRule', 'cx', 'cy', 'r', 'x',
  'y', 'x1', 'x2', 'y1', 'y2', 'rx', 'ry', 'textAnchor', 'dominantBaseline', 'preserveAspectRatio',
  'gradientUnits', 'offset', 'stopColor', 'clipPath', 'mask', 'filter', 'mime', 'contentType', 'kbd',
  'which', 'axis', 'orientation', 'capture', 'autoFocus', 'inert', 'hidden', 'draggable', 'mod',
  'aria-hidden', 'aria-live', 'aria-controls', 'aria-expanded', 'aria-selected', 'aria-checked',
  'aria-pressed', 'aria-current', 'aria-haspopup', 'aria-labelledby', 'aria-describedby', 'aria-busy',
  'aria-disabled', 'aria-modal', 'aria-orientation', 'aria-sort', 'aria-autocomplete', 'aria-activedescendant',
  'aria-owns', 'aria-atomic', 'aria-relevant', 'aria-invalid', 'aria-required', 'aria-multiselectable',
  'aria-readonly', 'aria-level', 'aria-posinset', 'aria-setsize', 'aria-rowindex', 'aria-colindex',
  'aria-rowcount', 'aria-colcount', 'aria-valuenow', 'aria-valuemin', 'aria-valuemax', 'aria-keyshortcuts',
  'aria-errormessage', 'aria-details', 'aria-flowto',
])

/** Properties in object literals whose string values are text a person reads. */
export const TEXT_PROPS = new Set([
  'label', 'title', 'message', 'text', 'desc', 'description', 'hint', 'placeholder', 'sub', 'subtitle',
  'help', 'tooltip', 'caption', 'heading', 'body', 'empty', 'emptyText', 'actionLabel', 'confirmLabel',
  'cancelLabel', 'okLabel', 'summary', 'question', 'note', 'detail', 'ariaLabel', 'aria-label', 'tip',
  'short', 'long', 'lead', 'intro', 'blurb', 'what', 'why', 'how', 'explain', 'plain', 'say', 'verb',
  'noun', 'one', 'many', 'other', 'past', 'done', 'doing', 'undo', 'busy', 'error', 'success', 'confirm',
  'button', 'cta', 'action', 'name', 'group', 'section', 'k', 'v', 'hintText', 'none', 'detailText',
])

/** Calls whose string arguments are code, not text. */
const CODE_CALLS = new Set([
  'log', 'warn', 'error', 'info', 'debug', 'trace', 'get', 'post', 'patch', 'put', 'delete', 'del', 'request',
  'fetch', 'querySelector', 'querySelectorAll', 'closest', 'matches', 'getElementById', 'addEventListener',
  'removeEventListener', 'dispatchEvent', 'setAttribute', 'getAttribute', 'removeAttribute', 'hasAttribute',
  'add', 'remove', 'toggle', 'contains', 'getItem', 'setItem', 'removeItem', 'RegExp', 'createElement',
  'startsWith', 'endsWith', 'includes', 'indexOf', 'lastIndexOf', 'split', 'replace', 'replaceAll', 'match',
  'matchAll', 'test', 'search', 'navigate', 'nav', 'URL', 'URLSearchParams', 'set', 'has', 'append',
  'Symbol', 'require', 'postMessage', 'matchMedia', 'DateTimeFormat', 'NumberFormat', 'PluralRules',
  'toLocaleString', 'toLocaleDateString', 'toLocaleTimeString', 'useQuery', 'invalidateQueries',
  'setQueryData', 'getQueryData', 'join', 'padStart', 'padEnd', 'Event', 'CustomEvent', 'KeyboardEvent',
  'MouseEvent', 'BroadcastChannel', 'Blob', 'File', 'open', 'execCommand', 'setData', 'getData',
  'json', 'stringify', 'parse', 'encodeURIComponent', 'decodeURIComponent', 'useParams', 'useSearchParams',
  'sendBeacon', 'registerShortcut', 'localeCompare', 'isNode', 'getElementsByTagName', 'getPropertyValue',
  'setProperty', 'mark', 'measure', 'Worker', 'ResizeObserver', 'importScripts', 'insertAdjacentHTML',
  'attr', 'scrollIntoView', 'focus', 'useMatch', 'matchPath', 'generatePath', 'qs', 'qsa', 'hasClass',
  'ok', 'is', 'isActive', 'toHaveText', 'emit', 'on', 'off', 'once', 'subscribe', 'record', 'track',
  'setCommand', 'run', 'chain', 'insertContent', 'command', 'extend', 'configure', 'storageGet', 'storageSet',
  'readLocal', 'writeLocal', 'clearLocal', 'Intl', 'Locale', 'supportedValuesOf', 'pubid', 'qk', 'key',
])

/** A word that reads as a person's language: letters, then a lowercase letter. */
const WORDY = /[A-Za-z][a-z]/

/** Strings that look like code even with a space inside. */
function looksLikeCode(s, lenient = false) {
  const t = s.trim()
  if (!t) return true
  if (!/[A-Za-z]/.test(t)) return true
  if (/^(https?:|mailto:|tel:|data:|blob:|\/|\.\/|\.\.\/|#[\w-]|\?|&)/.test(t)) return true
  if (/^[@.:#[]?[\w-]+(\s*[>+~,]\s*[.#:[]?[\w-]+)*$/.test(t) && !/\s/.test(t) && !/^[A-Z][a-z]/.test(t)) return true
  if (/^(var|calc|rgba?|hsla?|url|translate|scale|rotate|cubic-bezier|linear-gradient|color-mix|env|min|max|clamp)\(/.test(t)) return true
  if (/^[\d.]+(px|em|rem|%|vh|vw|ms|s|fr|ch|deg)\b/.test(t)) return true
  if (/\d+px\b|\bminmax\(/.test(t)) return true // CSS sizes, grid templates
  if (/^\(\s*(max|min)-(width|height)|\(orientation:|\(pointer:|\(prefers-/.test(t)) return true // media queries
  if (/\[(aria-|class|href|data-|tabindex|contenteditable|role)|:not\(|^(a|button|input|label|select|textarea|div|span|li|img)\s*,\s*\w/.test(t)) return true // selectors
  if (/^\s*<[a-z][a-z0-9]*[\s>/]/i.test(t) && /<\/?[a-z]+[\s>]/i.test(t) && /["=]|<\/[a-z]+>$|^<(div|p|span|a|br|img)\b/i.test(t)) return true // HTML
  if (/\b(default|font|img|style|script|connect|media|frame)-src\b/.test(t)) return true // CSP
  if (/^(mail|mailr|Mailr)$/.test(t)) return true // the product's name
  if (/^<!doctype/i.test(t)) return true
  if (/[{}]|=>|&&|\|\||\$\{/.test(t) && !/[.!?]$/.test(t)) return true
  if (/;/.test(t) && !/[A-Za-z]{2,} [A-Za-z]{2,}/.test(t)) return true
  if (/^[a-z-]+ [\d.]+m?s\b/.test(t)) return true // CSS animation / transition
  if (/^[\w.-]+=[\w"'-]/.test(t)) return true
  // class lists and space-separated identifiers: "btn tonal sm", "row gap-8"
  if (/^[a-z0-9_-]+( [a-z0-9_-]+)*$/.test(t) && !/^[a-z]+ [a-z]+ [a-z]+ [a-z]+/.test(t) && /[-_]|^(btn|row|col|chip|icon|input|select|muted|stack|card|tonal|outlined|text|filled|sm|lg|xs|grid|list|item|selected|active|open|on|off|is|has)( |$)/.test(t)) return true
  if (/^[a-z]+[A-Z]\w*$/.test(t)) return true // camelCase
  if (/^[A-Z][A-Z0-9_]+$/.test(t)) return true // CONSTANT, also acronyms such as PDF
  if (/^[a-z0-9]+([_.:-][a-z0-9]+)+$/i.test(t)) return true // snake_case, kebab-case, dotted.keys, a:b
  if (/^\w+\/\w+/.test(t) && !/\s/.test(t)) return true // mime types, paths
  if (/^(Mod|Ctrl|Shift|Alt|Meta|Cmd)[-+]/.test(t)) return true // key chords
  if (lenient) return /^\W*$/.test(t)
  if (/^(Enter|Escape|Backspace|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|PageUp|PageDown|Spacebar|F\d+)$/.test(t)) return true
  if (/^(GET|POST|PUT|PATCH|DELETE|Bearer|Content-Type|Accept|Authorization|UTC|GMT)$/.test(t)) return true
  if (/^[A-Z][a-z]+\/[A-Z][a-z_]+/.test(t)) return true // time zones Europe/London
  if (/^\W*$/.test(t)) return true
  return false
}

/** Text a person reads, by its shape: two words, or one capitalised word. */
export function isProse(s, { singleWord = false, lenient = false } = {}) {
  const t = s.replace(/\{\{\w+\}\}/g, 'x').trim()
  if (looksLikeCode(t, lenient)) return false
  if (!WORDY.test(t)) return false
  const words = t.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w))
  if (words.length >= 2) return true
  if (singleWord) return /^[A-Z][A-Za-z’'-]*[a-z][A-Za-z’'-]*[.!?…:]?$/.test(t)
  // A single word in code counts only when capitalised and not an identifier (Archive, Today…)
  return /^[A-Z][a-z]+[.!?…]?$/.test(t) || /^[A-Z][a-z]+[’'][a-z]+$/.test(t) || /^[A-Z][a-z]+…$/.test(t)
}

function attrName(node) {
  const n = node.name
  return ts.isIdentifier(n) ? n.text : n.getText()
}

function propName(node) {
  if (!node) return null
  const n = node.name
  if (!n) return null
  if (ts.isIdentifier(n) || ts.isStringLiteral(n) || ts.isPrivateIdentifier?.(n)) return n.text
  return null
}

function calleeName(call) {
  const e = call.expression
  if (ts.isIdentifier(e)) return e.text
  if (ts.isPropertyAccessExpression(e)) return e.name.text
  return null
}

function calleeObject(call) {
  const e = call.expression
  if (ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.expression)) return e.expression.text
  return null
}

/** Whether the node is inside a style object (style={{ … }}, style: { … }). */
function inStyle(node) {
  for (let p = node.parent, d = 0; p && d < 6; p = p.parent, d++) {
    if (ts.isJsxAttribute(p)) return /^(style|sx|css)$/.test(p.name.getText())
    if (ts.isPropertyAssignment(p) && /^(style|sx|css)$/.test(p.name.getText()) && p.initializer !== node) return true
  }
  return false
}

/** Whether `node` (a string-ish expression) sits where text is never shown. */
function codeContext(node) {
  if (inStyle(node)) return 'code'
  let child = node
  let p = node.parent
  // Climb through wrappers that keep the string as the value.
  while (p && (ts.isParenthesizedExpression(p) || ts.isConditionalExpression(p) && p.condition !== child ||
    ts.isBinaryExpression(p) && (p.operatorToken.kind === ts.SyntaxKind.BarBarToken || p.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken || p.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) ||
    ts.isAsExpression(p) || ts.isSatisfiesExpression?.(p) || ts.isNonNullExpression(p))) {
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && p.left === child) return 'cond'
    child = p
    p = p.parent
  }
  if (!p) return null
  if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isImportTypeNode?.(p) || ts.isExternalModuleReference(p)) return 'import'
  if (ts.isLiteralTypeNode(p) || ts.isTypeNode(p)) return 'type'
  if (ts.isPropertyAssignment(p) && p.name === child) return 'key'
  if (ts.isPropertyAssignment(p) || ts.isPropertyDeclaration(p)) {
    const k = propName(p)
    if (k === 'className' || k === 'id' || k === 'key' || k === 'href' || k === 'to' || k === 'path' || k === 'type' || k === 'kind' ||
      k === 'icon' || k === 'mode' || k === 'role' || k === 'value' || k === 'route' || k === 'testid' || k === 'testId' ||
      k === 'shortcut' || k === 'keys' || k === 'code' || k === 'color' || k === 'zone' || k === 'method' || k === 'url' ||
      k === 'mime' || k === 'tz' || k === 'slug' || k === 'event' || k === 'field' || k === 'filename' || k === 'query' || k === 'q' ||
      k === 'search' || k === 'pattern' || k === 'rule' || k === 'css' || k === 'style' || k === 'cmd' || k === 'action_id' || k === 'act') return 'code-prop'
    if (k && TEXT_PROPS.has(k)) return 'text-prop'
    return 'prop'
  }
  if (ts.isElementAccessExpression(p) && p.argumentExpression === child) return 'key'
  if (ts.isBinaryExpression(p)) {
    const op = p.operatorToken.kind
    if ([ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken, ts.SyntaxKind.EqualsEqualsToken,
      ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.InKeyword, ts.SyntaxKind.InstanceOfKeyword].includes(op)) return 'compare'
    if (op === ts.SyntaxKind.PlusToken) return 'concat'
  }
  if (ts.isCaseClause(p)) return 'compare'
  if (ts.isCallExpression(p) || ts.isNewExpression(p)) {
    const name = calleeName(p)
    const obj = calleeObject(p)
    if (obj === 'console' || obj === 'api' || obj === 'localStorage' || obj === 'sessionStorage' || obj === 'classList' || obj === 'JSON' || obj === 'Object' || obj === 'Reflect' || obj === 'document' || obj === 'window' || obj === 'navigator' || obj === 'history' || obj === 'location' || obj === 'qc' || obj === 'queryClient' || obj === 'editor' || obj === 'page' || obj === 'test' || obj === 'expect' || obj === 'describe' || obj === 'it') return 'code-call'
    if (name && CODE_CALLS.has(name)) return 'code-call'
    if (name && /^(it|describe|test|expect|vi|beforeEach|afterEach|track|logEvent|perf|mark|setTheme|useTheme|cls|cx|clsx|classNames|cn)$/.test(name)) return 'code-call'
    if (name && /^(Error|TypeError|RangeError|ApiError|AbortError)$/.test(name)) return 'error'
    return 'call'
  }
  if (ts.isThrowStatement(p)) return 'error'
  if (ts.isJsxExpression(p)) {
    const gp = p.parent
    if (gp && ts.isJsxAttribute(gp)) {
      const n = attrName(gp)
      if (isCodeAttr(n)) return 'code-attr'
      if (isTextAttr(n)) return 'text-attr'
      return 'attr'
    }
    return 'jsx-child'
  }
  if (ts.isJsxAttribute(p)) return 'attr'
  if (ts.isReturnStatement(p) || ts.isArrowFunction(p)) return 'return'
  if (ts.isVariableDeclaration(p)) return 'var'
  if (ts.isArrayLiteralExpression(p)) return 'array'
  if (ts.isTemplateSpan(p)) return 'template'
  if (ts.isEnumMember(p)) return 'code'
  if (ts.isBindingElement(p) || ts.isParameter(p)) return 'default'
  return 'other'
}

/** Whether the string is inside a function body (false: evaluated once at import). */
function inFunction(node) {
  let p = node.parent
  while (p) {
    if (ts.isFunctionLike(p) && !ts.isConstructorTypeNode?.(p)) return true
    p = p.parent
  }
  return false
}

function templateText(node) {
  if (ts.isNoSubstitutionTemplateLiteral(node)) return { text: node.text, exprs: [] }
  let text = node.head.text
  const exprs = []
  node.templateSpans.forEach((span, i) => {
    exprs.push(span.expression)
    text += `{{${i}}}` + span.literal.text
  })
  return { text, exprs }
}

/** Whether the file's line holds an `i18n-ignore` comment (this line or the line before). */
function ignored(sf, node, lines) {
  const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
  return /i18n-ignore/.test(lines[line] ?? '') || /i18n-ignore-next-line/.test(lines[line - 1] ?? '')
}

/**
 * Every user-facing string in a source file.
 * @returns {{kind: string, ctx: string, text: string, node: ts.Node, line: number, module: boolean}[]}
 */
export function scanSource(fileName, source, given) {
  const sf = given ?? ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, fileName.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const lines = source.split('\n')
  if (/i18n-ignore-file/.test(source.slice(0, 2000))) return []
  const out = []
  const push = (kind, ctx, text, node) => {
    if (ignored(sf, node, lines)) return
    out.push({ kind, ctx, text, node, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, module: !inFunction(node) })
  }
  const visit = (node) => {
    if (ts.isJsxText(node)) {
      const raw = node.getText(sf)
      const text = raw.replace(/&(nbsp|amp|lt|gt|quot|apos|[a-z]?dquo|[a-z]?squo|mdash|ndash|hellip|middot|times|bull|#\d+|#x[0-9a-f]+);/gi, ' ').replace(/\s+/g, ' ').trim()
      if (text && /[A-Za-z]/.test(text) && !looksLikeJsxSymbol(text) && !/^(mail|mailr|Mailr)$/.test(text)) push('jsx-text', 'jsx-child', text, node)
      return
    }
    if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)) {
      const n = attrName(node)
      const v = node.initializer.text
      if (isCodeAttr(n)) return
      const text = isTextAttr(n)
      if (text ? isProse(v, { singleWord: true, lenient: true }) || /[a-z]{2}/.test(v) && !looksLikeCode(v) : isProse(v)) push('attr', text ? 'text-attr' : 'attr', v, node.initializer)
      return
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)) {
      if (ts.isJsxAttribute(node.parent)) return
      const ctx = codeContext(node)
      if (['import', 'type', 'key', 'compare', 'code-call', 'code-attr', 'code-prop', 'code', 'cond'].includes(ctx)) return
      const { text } = ts.isStringLiteral(node) ? { text: node.text } : templateText(node)
      const single = ctx === 'text-attr' || ctx === 'text-prop'
      if (isProse(text, { singleWord: single, lenient: single })) push(ctx === 'error' ? 'error' : 'code', ctx, text, node)
      // Template expressions: their own spans are visited by the substitutions below.
      if (ts.isTemplateExpression(node)) node.templateSpans.forEach((s) => ts.forEachChild(s, visit))
      return
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

/** JSX text that is a symbol or a lone code word: "·", "—", "×", "→", "A–Z", "⌘K". */
function looksLikeJsxSymbol(t) {
  if (!/[A-Za-z]{2}/.test(t) && t.length <= 3) return true
  return false
}

export { ts, looksLikeCode, codeContext, templateText, inFunction, attrName }
