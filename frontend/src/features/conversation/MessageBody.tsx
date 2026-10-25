import { ImageOff, ShieldCheck } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useAttachedEmail, useMessageBody, useSettings } from '../../api/hooks'
import type { MessageBody as Body } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { Popover } from '../../components/Popover'
import { bodyColorScheme, buildBodyDoc, fitBodyZoom, isTextAsHtml, looksStyled, quotedHtml, reflowPlainText, setQuotedShown, splitMarkedQuote, textToHtml, trackersLabel } from '../../lib/html'
import { useTheme } from '../../hooks/useTheme'
import { usePhoneChrome } from '../pwa/PhoneChrome'
import { markInFrame, revealMark, useHighlightedBody } from './highlight'
import { captureMailtoClicks } from '../compose/mailtoLinks'
import { captureMaskedLinks } from './maskedLinks'
import { ConfirmDialog } from '../../components/Dialog'
import { imagesShownByDefault, trustSender } from './imageTrust'
import { t } from '../../i18n'

// The body (srcDoc) each frame document was made from: tells a frame's new document from the
// previous body's, which stays in place until the new one has loaded.
const loadedDoc = new WeakMap<Document, string>()
// Messages whose images the reader shows right now: a reply or forward started from one shows
// them in its quote too (through the image proxy); any other quote keeps them as placeholders.
const imagesShown = new Set<number>()
export function readerShowsImages(messageId: number): boolean {
  return imagesShown.has(messageId)
}


// The height each body had the last time it was shown, by message, image mode and width (in 16 px
// steps): a body opened again takes its place at once, so nothing below it moves when it appears.
const heights = new Map<string, number>()
const heightKey = (id: number, images: boolean, width: number) => `${id}:${images ? 1 : 0}:${Math.round(width / 16)}`
function rememberHeight(id: number, images: boolean, width: number, h: number) {
  const k = heightKey(id, images, width)
  heights.delete(k)
  heights.set(k, h)
  if (heights.size > 500) heights.delete(heights.keys().next().value as string)
}
export function rememberedHeight(id: number, images: boolean, width: number): number | undefined {
  return heights.get(heightKey(id, images, width))
}

/** A first guess at a body's height from its text (or its HTML's), for the space it takes until
 *  it is measured. */
export function estimateBodyHeight(b: Pick<Body, 'text' | 'html'>, width: number): number {
  const perLine = Math.max(24, Math.floor(Math.max(200, width || 600) / 7.5))
  const text = b.text?.trim() ? b.text : (b.html ?? '').replace(/<(br|\/p|\/div|\/tr|\/li|\/h\d)\b[^>]*>/gi, '\n').replace(/<style[\s\S]*?<\/style>|<[^>]+>/gi, '').replace(/[ \t]+/g, ' ')
  let lines = 0
  for (const para of text.replace(/\n\s*\n(\s*\n)+/g, '\n\n').trim().split('\n')) lines += Math.max(1, Math.ceil(para.trim().length / perLine))
  return Math.min(1600, Math.max(60, Math.round(lines * 20.3) + 8))
}

// The width of the last reader box measured: a body's first render looks its height up by it, so
// a remembered height applies before the box has been measured.
let lastBoxWidth = 0

/** The sanitised body of a message, in a sandboxed frame. With `attachedPart` it shows the email
 *  attached as that part instead (mail.open_attached_email): same sanitising, same image rules,
 *  and `sender` is then the OUTER message's sender (the attached email's own From is unverified). */
export function MessageBody({ messageId, sender, spam, highlight = null, attachedPart }: { messageId: number; sender: string; spam: boolean; highlight?: string | null; attachedPart?: number }) {
  const attachedView = attachedPart !== undefined
  const settings = useSettings()
  const dark = useTheme() === 'dark'
  // Images come through mailr's image proxy (backend core/imageproxy.py). ACT mail.show_images @web.reader
  const [byDefault] = useState(() => imagesShownByDefault(settings.images, spam, sender))
  const [clicked, setClicked] = useState(false)
  // The server's word on Spam wins over the list's (a message moved there by the spam check).
  const [serverJunk, setServerJunk] = useState(false)
  const allow = clicked || (byDefault && !serverJunk)
  const setAllow = setClicked
  useEffect(() => {
    if (attachedView) return
    if (allow) imagesShown.add(messageId)
    else imagesShown.delete(messageId)
  }, [allow, messageId, attachedView])
  const [confirming, setConfirming] = useState(false)
  // A link whose text names another host than the one it opens (maskedLinks.ts): asked first.
  const [masked, setMasked] = useState<{ href: string; shown: string; real: string } | null>(null)
  const revealed = useRef<string | null>(null)
  // The quote the server highlighted the loaded body for. A later citation into the same body
  // is marked in place (markInFrame) and keeps this, so the frame does not reload; only a quote
  // that cannot be found locally asks the server again.
  const [serverHl, setServerHl] = useState(attachedView ? null : highlight)
  const frameRef = useRef<HTMLIFrameElement>(null)
  useEffect(() => {
    if (attachedView || highlight === serverHl) return
    const frame = frameRef.current
    if (frame?.contentDocument?.body && highlight && markInFrame(frame, highlight)) {
      revealed.current = highlight
      revealMark(frame)
      return
    }
    if (highlight) setServerHl(highlight)
  }, [highlight, serverHl, attachedView])
  const attached = useAttachedEmail(messageId, attachedPart, allow, attachedView)
  const plain = useMessageBody(messageId, allow, !serverHl && !attachedView)
  const marked = useHighlightedBody(messageId, serverHl, allow)
  // A cited quote: use the server-highlighted body; fall back to the plain body if that fails.
  const useMarked = !!serverHl && !marked.isError
  const body = attachedView ? attached : useMarked ? marked : plain
  const fallback = useMessageBody(messageId, allow, !!serverHl && marked.isError)
  const bodyData = attachedView ? attached.data : useMarked ? marked.data : serverHl ? fallback.data : plain.data
  useEffect(() => {
    if (bodyData?.junk) setServerJunk(true)
  }, [bodyData?.junk])
  // A highlight inside trimmed history must be visible.
  const [showQuoted, setShowQuoted] = useState(false)
  const markInQuote = !!serverHl && !!bodyData?.html && /mailr-hl/.test(bodyData.html.split(/data-mailr-quote|mailr_quote/)[1] ?? '')
  // The frame's height: its measured content, or until the first measure a placeholder (the
  // height remembered for this body, else a guess from its text). Until that first measure the
  // frame is laid out but hidden, then it appears at its real height in one step.
  const [height, setHeight] = useState<number | null>(null)
  const [shown, setShown] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const [boxWidth, setBoxWidth] = useState(() => lastBoxWidth)
  // On a phone a body wider than the screen is zoomed to fit (fitBodyZoom), as Gmail shows it.
  const phone = usePhoneChrome()

  const { main, quoted, paper } = useMemo(() => {
    const b = bodyData
    if (!b) return { main: '', quoted: '', paper: false }
    // A plain-text message the mail store dressed as HTML (a <div> of lines and <br>s) shows as the
    // plain text it is, so it reflows like one (lib/html.ts reflowPlainText).
    if (b.html && !(b.text && isTextAsHtml(b.html))) {
      const marked = splitMarkedQuote(b.html)
      if (marked) return { main: marked.main, quoted: marked.quoted, paper: dark && looksStyled(b.html) }
      const cut = b.quoted_html_start ?? -1
      const main = cut > 0 ? b.html.slice(0, cut) : b.html
      const quoted = cut > 0 ? b.html.slice(cut) : ''
      return { main, quoted, paper: dark && looksStyled(b.html) }
    }
    const text = b.text ?? ''
    // Plain text: collapse the trailing quoted block ("On … wrote:" + "> " lines).
    const m = /\n(On .{5,200}wrote:\s*\n(>.*\n?)+|(>.*\n?){3,})\s*$/s.exec(text)
    const mainText = m ? text.slice(0, m.index) : text
    const quotedText = m ? text.slice(m.index) : ''
    return {
      main: `<div style="white-space:pre-wrap">${textToHtml(reflowPlainText(mainText))}</div>`,
      quoted: quotedText ? `<div style="white-space:pre-wrap">${textToHtml(quotedText)}</div>` : '',
      paper: false,
    }
  }, [bodyData, dark])

  const doc = useMemo(
    () => buildBodyDoc({ html: `${main}${quotedHtml(quoted)}`, dark, paper, allowImages: allow, csp: bodyData?.csp, proxied: bodyData?.proxied }),
    [main, quoted, dark, paper, allow, bodyData?.csp, bodyData?.proxied],
  )

  // Auto-size the iframe to its content. The body is measured as soon as its document is parsed
  // (a frame's load event waits for every image), then again as it grows (images, fonts).
  const remember = useRef({ messageId, allow, attachedView, showQuoted })
  remember.current = { messageId, allow, attachedView, showQuoted }
  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    let ro: ResizeObserver | null = null
    let unMailto: (() => void) | null = null
    let unMasked: (() => void) | null = null
    let fitted = -1
    let raf = 0
    let sized = false
    let ready: Document | null = null
    const measure = () => {
      const d = frame.contentDocument
      if (!d?.body) return
      // Fit again only when the frame's width changes (a rotation), not on every growth.
      const w = frame.clientWidth
      if (phone && w !== fitted) {
        fitted = w
        fitBodyZoom(d)
      }
      // The first measure starts from a 1 px frame: a placeholder taller than the body (or a body
      // styled height:100%) would otherwise keep its own height as the scroll height. The frame
      // is hidden or unchanged meanwhile, and nothing paints between the two writes.
      let h: number
      if (!sized) {
        sized = true
        frame.style.height = '1px'
        h = Math.max(40, d.documentElement.scrollHeight)
        frame.style.height = `${h}px`
      } else {
        h = Math.max(40, d.documentElement.scrollHeight)
      }
      setHeight(h)
      setShown(true)
      const r = remember.current
      // By the width of the box around the frame, the width the next opening looks it up by.
      if (!r.attachedView && !r.showQuoted) rememberHeight(r.messageId, r.allow, boxRef.current?.clientWidth ?? 0, h)
    }
    const prepare = (d: Document) => {
      if (ready === d) return
      ready = d
      loadedDoc.set(d, doc)
      if (showQuoted || markInQuote) setQuotedShown(d, true)
      // A mailto: link in the body opens mailr's composer, not the computer's email app.
      // ACT compose.deeplink @web.reader
      unMailto?.()
      unMasked?.()
      unMailto = captureMailtoClicks(d)
      unMasked = captureMaskedLinks(d, setMasked)
      measure()
      ro?.disconnect()
      ro = new ResizeObserver(measure)
      ro.observe(d.body)
      d.querySelectorAll('img').forEach((img) => img.addEventListener('load', measure))
    }
    // This body's document, once parsed: not the frame's first about:blank, and not the previous
    // body's document, which stays until the new one replaces it.
    const current = () => {
      const d = frame.contentDocument
      if (!d?.body || d.readyState === 'loading' || d.location.href !== 'about:srcdoc') return null
      const was = loadedDoc.get(d)
      return was === undefined || was === doc ? d : null
    }
    // Looked for every frame for about two seconds; after that the load event takes over.
    let polls = 120
    const poll = () => {
      const d = current()
      if (d) prepare(d)
      else if (polls-- > 0) raf = requestAnimationFrame(poll)
    }
    const onLoad = () => {
      const d = frame.contentDocument
      if (!d) return
      cancelAnimationFrame(raf)
      prepare(d)
      measure()
      // Scroll to the cited passage once; a reload of the same body (refetch) keeps the mark
      // but leaves the reader where they are.
      if (serverHl && revealed.current !== serverHl) {
        revealed.current = serverHl
        setTimeout(() => revealMark(frame), 120)
      } else if (serverHl) {
        revealMark(frame, 0, false)
      }
    }
    frame.addEventListener('load', onLoad)
    poll()
    // Never leave a body hidden, whatever happens to its document.
    const fallback = setTimeout(() => setShown(true), 1500)
    return () => {
      frame.removeEventListener('load', onLoad)
      cancelAnimationFrame(raf)
      clearTimeout(fallback)
      ro?.disconnect()
      unMailto?.()
      unMasked?.()
    }
  }, [doc, showQuoted, serverHl, markInQuote, phone])

  useEffect(() => {
    const d = frameRef.current?.contentDocument
    if (!d?.body) return
    setQuotedShown(d, showQuoted)
    setHeight(Math.max(40, d.documentElement.scrollHeight))
  }, [showQuoted])

  // A body fetched with images allowed that turns out to be in Spam is never shown: the blocked
  // one replaces it first (the effect above), so the proxy fetches nothing for it.
  const spamPending = !!bodyData?.junk && allow && !clicked
  const waiting = body.isLoading || spamPending || (!!serverHl && marked.isError && fallback.isLoading)
  // Each time the frame is about to appear (first, or after a refetch with images), it starts
  // hidden at its placeholder height, before the browser paints.
  const hasBody = !!bodyData
  useLayoutEffect(() => {
    const w = boxRef.current?.clientWidth ?? 0
    if (w) lastBoxWidth = w
    setBoxWidth(w)
    if (waiting) {
      setShown(false)
      setHeight(null)
    }
  }, [waiting, hasBody])
  const known = rememberedHeight(messageId, allow, boxWidth)
  const guess = useMemo(() => (bodyData ? estimateBodyHeight(bodyData, boxWidth) : 60), [bodyData, boxWidth])
  const placeholder = known ?? guess
  if (waiting)
    return (
      <div className="msg-body" ref={boxRef} style={known ? { minHeight: known } : undefined}>
        <Skeleton width="80%" />
        <Skeleton width="65%" style={{ marginTop: 8 }} />
        <Skeleton width="72%" style={{ marginTop: 8 }} />
      </div>
    )
  if (body.isError) return <div className="msg-body banner error">{t('conversation.messageBody.couldntLoadThisMessage', { message: (body.error as Error).message })}</div>

  const junk = spam || !!body.data?.junk
  return (
    <div className="msg-body" ref={boxRef}>
      {body.data?.images_paused && (
        <div className="images-banner" data-testid="images-banner">
          <ImageOff size={18} />
          <span>{t('conversation.messageBody.imagesAreNotDisplayed2', { imagesPaused: body.data.images_paused })}</span>
        </div>
      )}
      {body.data?.remote_images && !allow && !body.data.images_paused && (
        <div className="images-banner" data-testid="images-banner">
          <ImageOff size={18} />
          {junk && confirming ? (
            <>
              <span>{t('conversation.messageBody.loadingImagesCanTellThe')}</span>
              <button type="button" onClick={() => setAllow(true)}>
                {t('conversation.messageBody.loadImages')}
              </button>
              <span className="muted">-</span>
              <button type="button" onClick={() => setConfirming(false)}>
                {t('common.cancel')}
              </button>
            </>
          ) : (
            <>
              <span>{junk ? t('conversation.messageBody.imagesAreNotDisplayedIn') : t('conversation.messageBody.imagesAreNotDisplayed')}</span>
              <button type="button" onClick={() => (junk ? setConfirming(true) : setAllow(true))}>
                {t('conversation.messageBody.displayImagesBelow')}
              </button>
              {/* Not for an attached email: its From line is unverified, and `sender` is the outer
                  message's. */}
              {!junk && settings.images === 'ask' && !attachedView && (
                <>
                  <span className="muted">-</span>
                  <button
                    type="button"
                    onClick={() => {
                      trustSender(sender)
                      setAllow(true)
                    }}
                  >
                    {t('conversation.messageBody.alwaysDisplayImagesFrom', { sender })}
                  </button>
                </>
              )}
            </>
          )}
        </div>
      )}
      {body.data && <TrackersBlocked body={body.data} />}
      <iframe
        ref={frameRef}
        title={t('conversation.messageBody.messageBody')}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        srcDoc={doc}
        style={{ height: height ?? placeholder, visibility: shown ? undefined : 'hidden', borderRadius: paper ? 8 : 0, colorScheme: bodyColorScheme(dark, paper) }}
        data-shown={shown || undefined}
        referrerPolicy="no-referrer"
      />
      <ConfirmDialog
        open={!!masked}
        title={t('conversation.messageBody.maskedLinkTitle')}
        body={masked ? t('conversation.messageBody.maskedLinkBody', { shown: masked.shown, real: masked.real }) : ''}
        confirmLabel={masked ? t('conversation.messageBody.maskedLinkOpen', { real: masked.real }) : ''}
        onConfirm={() => {
          if (masked) window.open(masked.href, '_blank', 'noopener,noreferrer')
          setMasked(null)
        }}
        onClose={() => setMasked(null)}
      />
      {quoted && (
        <button type="button" className="quote-toggle" aria-expanded={showQuoted} aria-label={showQuoted ? t('conversation.messageBody.hideTrimmedContent') : t('conversation.messageBody.showTrimmedContent')} data-tip={showQuoted ? t('conversation.messageBody.hideExpandedContent') : t('conversation.messageBody.showTrimmedContent')} onClick={() => setShowQuoted(!showQuoted)}>
          •••
        </button>
      )}
    </div>
  )
}

/** "N trackers blocked": open-tracking images the server removed, and wrapped links it
 *  rewrote, with the hosts in a popover. */
function TrackersBlocked({ body }: { body: Body }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const count = body.trackers?.count ?? 0
  const links = body.links_unwrapped ?? 0
  if (!count && !links) return null
  const label = count ? trackersLabel(count) : t('conversation.messageBody.trackingLinksRemoved', { count: links })
  return (
    <div className="trackers-row">
      <button type="button" className="trackers-chip" aria-haspopup="dialog" aria-expanded={!!anchor} onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}>
        <ShieldCheck size={14} />
        {label}
      </button>
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} role="dialog" label={t('conversation.messageBody.trackersBlocked')} placement="bottom-end" minWidth={240}>
        <div className="trackers-pop">
          {count > 0 && (
            <>
              <p>{t('conversation.messageBody.mailrRemovedThatWouldTell', { value: t('conversation.messageBody.images', { count }) })}</p>
              <ul>
                {(body.trackers?.domains ?? []).map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </>
          )}
          {links > 0 && (
            <p>
              {t('conversation.messageBody.throughATrackingRedirectThey', { value: t('conversation.messageBody.linksWent', { count: links }) })}
            </p>
          )}
          {body.proxied && <p className="muted">{t('conversation.messageBody.imagesInThisMessageLoad')}</p>}
        </div>
      </Popover>
    </div>
  )
}
