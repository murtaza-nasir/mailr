// Opening an email at a cited passage: the server returns the sanitised body with the quote
// wrapped in <mark class="mailr-hl" id="mailr-hl-1"> (GET /messages/{id}/body-highlight). The
// body renders in a same-origin, script-less iframe, so the page can find the mark, scroll the
// conversation to it, and fade it after a few seconds.

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MessageBody } from '../../api/types'
import { scrollBehavior } from '../../lib/motion'

export interface HighlightedBody extends MessageBody {
  highlighted?: boolean
  highlight_id?: string | null
}

export function useHighlightedBody(id: number, quote: string | null, images: boolean) {
  return useQuery<HighlightedBody>({
    queryKey: ['body-highlight', id, quote, images],
    queryFn: ({ signal }) =>
      api.get<HighlightedBody>(`/messages/${id}/body-highlight`, { q: quote ?? '', images: images ? 'allowed' : 'blocked' }, signal),
    enabled: !!quote,
    staleTime: Infinity,
    retry: false,
  })
}

/** Styles injected into the message iframe for the highlight (works in light and dark bodies). */
export const MARK_CSS = `
mark.mailr-hl{--hl:light-dark(#fde68a,#6b5a1e);--hl-fade:light-dark(rgba(253,230,138,.45),rgba(250,204,21,.18));background:var(--hl);color:light-dark(#1f1f1f,#fff7d6);border-radius:2px;box-shadow:0 0 0 2px var(--hl);transition:background-color 1.2s ease,box-shadow 1.2s ease,color 1.2s ease}
mark.mailr-hl.faded{background:var(--hl-fade);color:inherit;box-shadow:none}
`

function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let n = el?.parentElement ?? null
  while (n) {
    const s = getComputedStyle(n)
    if (/(auto|scroll)/.test(s.overflowY) && n.scrollHeight > n.clientHeight) return n
    n = n.parentElement
  }
  return null
}

/** Scroll the page so the mark inside `frame` sits in the upper third of the view, then fade
 *  it after `fadeMs`. Returns false when the iframe has no mark. */
export function revealMark(frame: HTMLIFrameElement, fadeMs = 4000, scroll = true): boolean {
  const doc = frame.contentDocument
  const mark = doc?.getElementById('mailr-hl-1') ?? doc?.querySelector('mark.mailr-hl')
  if (!doc || !mark) return false
  if (!doc.getElementById('mailr-hl-style')) {
    const st = doc.createElement('style')
    st.id = 'mailr-hl-style'
    st.textContent = MARK_CSS
    doc.head.appendChild(st)
  }
  if (!scroll) {
    doc.querySelectorAll('mark.mailr-hl').forEach((m) => m.classList.add('faded'))
    return true
  }
  const container = scrollParent(frame)
  const frameTop = frame.getBoundingClientRect().top
  const markTop = (mark as HTMLElement).getBoundingClientRect().top
  if (container) {
    const cTop = container.getBoundingClientRect().top
    const target = container.scrollTop + frameTop - cTop + markTop - container.clientHeight / 3
    container.scrollTo({ top: Math.max(0, target), behavior: scrollBehavior() })
  } else {
    window.scrollTo({ top: window.scrollY + frameTop + markTop - window.innerHeight / 3, behavior: scrollBehavior() })
  }
  window.setTimeout(() => doc.querySelectorAll('mark.mailr-hl').forEach((m) => m.classList.add('faded')), fadeMs)
  return true
}

/** Mark `quote` inside an already loaded body without reloading it: the next citation into the
 *  same email moves the mark and scrolls there, instead of re-fetching a highlighted copy (which
 *  reloads the frame and jumps to the top first). Matching ignores case and runs of whitespace,
 *  and a block boundary (paragraph, cell, line break) counts as a space, as on the server; when
 *  the whole quote is not found, its first eight words are tried. False: not found. */
export function markInFrame(frame: HTMLIFrameElement, quote: string): boolean {
  const doc = frame.contentDocument
  if (!doc?.body || !quote.trim()) return false
  const clean = (q: string) => q.replace(/\s+/g, ' ').trim().toLowerCase()
  let idx = indexText(doc)
  let q = clean(quote)
  let at = idx.norm.indexOf(q)
  if (at < 0) {
    q = clean(quote.split(/\s+/).slice(0, 8).join(' '))
    at = q.length >= 12 ? idx.norm.indexOf(q) : -1
  }
  if (at < 0) return false
  // Remove the previous mark(s); the text nodes change, so index again before wrapping.
  doc.querySelectorAll('mark.mailr-hl').forEach((m) => m.replaceWith(...Array.from(m.childNodes)))
  doc.body.normalize()
  idx = indexText(doc)
  const a = idx.pos[at]
  const b = idx.pos[at + q.length - 1]
  if (!a || !b) return false
  let first = true
  for (let ni = a.node; ni <= b.node; ni++) {
    const t = idx.nodes[ni]
    const from = ni === a.node ? a.off : 0
    const to = ni === b.node ? b.off + 1 : t.data.length
    if (to <= from || !t.data.slice(from, to).trim()) continue
    const range = doc.createRange()
    range.setStart(t, from)
    range.setEnd(t, to)
    const mark = doc.createElement('mark')
    mark.className = 'mailr-hl'
    if (first) {
      mark.id = 'mailr-hl-1'
      first = false
    }
    range.surroundContents(mark)
  }
  // Trimmed history that is not shown sits in an inert <template> (lib/html quotedHtml), which
  // this search does not enter: a quote there is marked by the server copy instead.
  return !first
}

const BLOCK = 'p,div,li,td,th,tr,table,h1,h2,h3,h4,h5,h6,blockquote,pre,section,article,header,footer,ul,ol,dd,dt'

/** The body's text normalised for matching, with each normalised character's source position.
 *  A virtual position (off = -1) stands for a space implied by a block boundary. */
function indexText(doc: Document) {
  const nodes: Text[] = []
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement?.closest('style, script, title') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  })
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text)
  let norm = ''
  const pos: { node: number; off: number }[] = []
  let space = true
  let prevBlock: Element | null = null
  nodes.forEach((t, ni) => {
    const block = t.parentElement?.closest(BLOCK) ?? null
    if (ni > 0 && block !== prevBlock && !space) {
      norm += ' '
      pos.push({ node: ni, off: 0 })
      space = true
    }
    prevBlock = block
    for (let i = 0; i < t.data.length; i++) {
      if (/\s/.test(t.data[i])) {
        if (!space) {
          norm += ' '
          pos.push({ node: ni, off: i })
          space = true
        }
      } else {
        norm += t.data[i].toLowerCase()
        pos.push({ node: ni, off: i })
        space = false
      }
    }
  })
  return { nodes, norm, pos }
}
