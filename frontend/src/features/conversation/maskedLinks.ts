// Masked links in a message body: a link whose text reads as a web address or a domain
// ("www.yourbank.example", "https://yourbank.example/login") while it opens another host. A click
// on one in the reader's frame asks first and names the host it really opens, so a phishing mail
// cannot pass off one address as another (the status bar shows the target on hover, never on
// touch). Ambrose's drafts show such links as "text (address)" (backend agentwrite.reveal_links_html).

/** The host a link's text names, when the whole text reads as a web address or a domain. */
export function shownHost(text: string): string | null {
  const s = text.replace(/[\s\u200b-\u200f\u2060\ufeff]+/g, '').replace(/^[<([{"'\u201c\u2018]+|[>)\]}"'\u201d\u2019.,;:!]+$/g, '')
  if (!s || s.length > 300) return null
  const m = /^(?:https?:\/\/)?((?:[\p{L}\p{N}-]+\.)+[\p{L}]{2,63})(?::\d{1,5})?(?:[/?#].*)?$/iu.exec(s)
  if (!m) return null
  return hostOf(`https://${m[1]}`)
}

/** A URL's host, lower-case and in its ASCII form, without "www.". */
export function hostOf(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    return u.hostname.toLowerCase().replace(/^www\./, '').replace(/\.$/, '') || null
  } catch {
    return null
  }
}

/** {shown, real} when the link's text names one host and the link opens another; else null.
 *  A subdomain of the shown host, or a shown subdomain of the real one, is the same site. */
export function maskedLink(text: string, href: string): { shown: string; real: string } | null {
  const shown = shownHost(text)
  const real = hostOf(href)
  if (!shown || !real || shown === real || real.endsWith(`.${shown}`) || shown.endsWith(`.${real}`)) return null
  return { shown, real }
}

/** The masked link a plain click landed on, if any. */
export function maskedOfClick(e: MouseEvent): { href: string; shown: string; real: string } | null {
  if (e.defaultPrevented || e.button !== 0) return null
  // The frame is another window: test the node type, not instanceof.
  const node = e.target as Node | null
  const el = node?.nodeType === 1 ? (node as Element) : node?.parentElement ?? null
  const a = el?.closest('a[href]')
  if (!a) return null
  const href = a.getAttribute('href') ?? ''
  const m = maskedLink(a.textContent ?? '', href)
  return m ? { href, ...m } : null
}

/** Catch clicks on masked links in a message body's document; `ask` decides what happens. */
export function captureMaskedLinks(doc: Document, ask: (link: { href: string; shown: string; real: string }) => void): () => void {
  const onClick = (e: MouseEvent) => {
    const m = maskedOfClick(e)
    if (!m) return
    e.preventDefault()
    e.stopPropagation()
    ask(m)
  }
  doc.addEventListener('click', onClick, true)
  return () => doc.removeEventListener('click', onClick, true)
}
