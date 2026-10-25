// Every "email this" in mailr opens mailr's own composer, never the computer's
// email app: mailto: links anywhere on the page (Ambrose's answers, contact cards), in a message body
// (its sandboxed frame), the invite dialog's "Email it", and the /compose link that the browser's
// email-link handler and the desktop's mailto handler open (lib/mailto.ts parses them all).
// A link only fills a draft. Nothing is sent until the user presses Send.
// ACT compose.deeplink @web.panel: links in Ambrose's answers (the page-wide catch below)

import { useEffect } from 'react'
import type { ComposePayload } from '../../api/types'
import { isMailto, parseMailto, type MailtoFields } from '../../lib/mailto'
import { emptyPayload, useCompose } from '../../stores/compose'
import { useUI } from '../../stores/ui'
import { isPhoneLayout } from '../rail/assistant/ambroseNav'
import { textToParagraphs } from './composeText'

/** The draft a mailto: link or a /compose link starts. */
export function payloadFromFields(f: MailtoFields): ComposePayload {
  return { ...emptyPayload(), to: f.to, cc: f.cc, bcc: f.bcc, subject: f.subject, html: f.body ? textToParagraphs(f.body) : '' }
}

/** Open the composer with these fields. On the phone layout Ambrose steps aside first, as for any
 *  item opened from the chat (ambroseNav.ts), so the draft is not hidden under the panel. */
export function openComposeWith(f: MailtoFields): string {
  if (isPhoneLayout() && useUI.getState().panel === 'agent') useUI.getState().leaveAmbrose()
  return useCompose.getState().open({ payload: payloadFromFields(f) })
}

/** Open the composer for a mailto: href; false when the href is not one. */
export function openMailtoHref(href: string | null | undefined): boolean {
  if (!isMailto(href)) return false
  const f = parseMailto(href)
  if (!f) return false
  openComposeWith(f)
  return true
}

/** The mailto: link a click landed on, if the click is a plain one (no modifier, main button). */
export function mailtoOfClick(e: MouseEvent): string | null {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return null
  // A message body's frame is another window, whose nodes are not this window's Element: test the node type.
  const node = e.target as Node | null
  const el = node?.nodeType === 1 ? (node as Element) : node?.parentElement ?? null
  const a = el?.closest('a[href]')
  // data-mailto: the link handles its own click ("self", e.g. a card that closes first) or is left
  // to the browser ("native": the test link in Settings → Mail → Writing → Email links).
  if (!a || a.hasAttribute('data-mailto')) return null
  // A link inside text being edited (a draft, a note) is text, not a way out.
  if ((a as HTMLElement).isContentEditable) return null
  const href = a.getAttribute('href')
  return isMailto(href) ? href : null
}

/** Catch clicks on mailto: links in a document (the page, or a message body's frame). */
export function captureMailtoClicks(doc: Document, after?: () => void): () => void {
  const onClick = (e: MouseEvent) => {
    const href = mailtoOfClick(e)
    if (!href) return
    e.preventDefault()
    e.stopPropagation()
    if (openMailtoHref(href)) after?.()
  }
  doc.addEventListener('click', onClick, true)
  return () => doc.removeEventListener('click', onClick, true)
}

/** The signed-in app: every mailto: link on the page opens the composer (except links marked
 *  `data-mailto`, see mailtoOfClick). */
export function useMailtoLinks(): void {
  useEffect(() => captureMailtoClicks(document), [])
}
