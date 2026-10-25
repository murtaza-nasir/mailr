import { Fragment, useLayoutEffect, useRef, useState } from 'react'
import type { InboxLayout, LayoutTab } from '../../api/types'
import { ScrollTabs } from '../../components/ScrollTabs'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { layoutIcon } from '../../lib/layoutIcons'
import {
  detailRoom,
  fitDetail,
  pickIndicators,
  stripRoom,
  tabIndicators,
  type IndicatorCapacity,
  type StripRoom,
  type TabCounts,
  type TabIndicator,
} from '../../lib/tabIndicators'
import { toast } from '../../stores/ui'
import { t as tr } from '../../i18n'

const LONG_PRESS_MS = 500

interface Props {
  layout: InboxLayout
  activeId: string | undefined
  counts: Record<string, TabCounts> | undefined
  /** The theme's room for indicators (ListPresentation.tabIndicators). */
  capacity: IndicatorCapacity
  /** The list pane is narrow (a reading pane beside it, or a small window). */
  narrow: boolean
  onSelect: (tab: LayoutTab) => void
}

/**
 * The inbox layout's tab strip: icon, name and the tab's indicators, as many
 * as the theme and the space allow (lib/tabIndicators.ts). Strips wider than the pane scroll
 * sideways (ScrollTabs).
 *
 * With the layout's `tab_detail`, a tab shows all its indicators while the strip has room. A
 * hidden copy of the tabs, in both forms, gives each tab's width; fitDetail picks the tabs that
 * take the fuller form, and a ResizeObserver picks again when the strip or a copy changes size.
 */
export function InboxTabs({ layout, activeId, counts, capacity, narrow, onSelect }: Props) {
  const phone = useMediaQuery('(max-width: 600px)')
  const room = stripRoom(capacity, narrow, phone)
  const more = layout.tab_detail ? detailRoom(capacity, phone) : null
  const lists = layout.tabs.map((t) => tabIndicators(t, counts?.[t.id]))
  // A tab whose fuller form shows more indicators than its usual one.
  const richer = lists.map((l) => !!more && pickIndicators(l, more).length > pickIndicators(l, room).length)
  const measuring = !!more && richer.some(Boolean)
  const active = layout.tabs.findIndex((t) => t.id === activeId)
  // What the hidden copies hold; a change measures again. The selected tab is not part of it:
  // selecting a tab never changes which tabs take their fuller form.
  const sig = JSON.stringify([room, more, layout.tabs.map((t) => [t.name, t.icon]), lists.map((l) => l.map((i) => i.text))])
  const { ghost, on } = useDetailFit(measuring, sig)
  // The selected tab is brought into view again once the counts arrive and widen the tabs.
  return (
    <ScrollTabs
      className="cat-tabs"
      kind="tablist"
      label={tr('mail.inboxTabs.tabs', { layoutName: layout.name })}
      revealKey={`${layout.id}:${activeId}:${!!counts}`}
      aside={
        measuring && more ? (
          <div className="cat-tabs-measure" aria-hidden inert ref={ghost}>
            {layout.tabs.map((t, i) => (
              <div key={t.id} data-measure-tab>
                <TabFace tab={t} active={i === active} shown={pickIndicators(lists[i], room)} room={room} detail={false} />
                {richer[i] && <TabFace tab={t} active={i === active} shown={pickIndicators(lists[i], more)} room={more} detail />}
              </div>
            ))}
          </div>
        ) : null
      }
    >
      {layout.tabs.map((t, i) => {
        const detail = !!more && measuring && richer[i] && !!on[i]
        const r = detail && more ? more : room
        return <TabButton key={t.id} tab={t} active={i === active} shown={pickIndicators(lists[i], r)} room={r} detail={detail} onSelect={() => onSelect(t)} />
      })}
    </ScrollTabs>
  )
}

/**
 * Which tabs take their fuller form. Under `ghost`, each tab has a hidden copy in its usual form
 * and, when its fuller form shows more, a second copy in that form; the strip is the ScrollTabs
 * strip beside it. `sig` changes with what the copies hold.
 */
function useDetailFit(measuring: boolean, sig: string) {
  const ghost = useRef<HTMLDivElement>(null)
  const [on, setOn] = useState<boolean[]>([])
  useLayoutEffect(() => {
    const g = ghost.current
    const strip = g?.parentElement?.querySelector<HTMLElement>(':scope > .scroll-tabs-strip')
    if (!measuring || !g || !strip) {
      setOn((prev) => (prev.length ? [] : prev))
      return
    }
    const run = () => {
      const cs = getComputedStyle(strip)
      const inner = strip.clientWidth - (parseFloat(cs.paddingInlineStart) || 0) - (parseFloat(cs.paddingInlineEnd) || 0)
      const gap = parseFloat(cs.columnGap) || 0
      const widths = Array.from(g.children).map((pair) => {
        const [usual, fuller] = Array.from(pair.children).map((c) => c.getBoundingClientRect().width)
        return { compact: usual, detail: fuller ?? usual }
      })
      const next = fitDetail(inner, widths, gap)
      setOn((prev) => (prev.length === next.length && prev.every((v, i) => v === next[i]) ? prev : next))
    }
    run()
    const ro = new ResizeObserver(run)
    ro.observe(strip)
    for (const el of Array.from(g.querySelectorAll<HTMLElement>('.cat-tab'))) ro.observe(el)
    return () => ro.disconnect()
  }, [measuring, sig])
  return { ghost, on }
}

interface FaceProps {
  tab: LayoutTab
  active: boolean
  shown: TabIndicator[]
  room: StripRoom
  /** The fuller form: every indicator the tab shows (layout.tab_detail). */
  detail: boolean
}

/** A hidden copy of a tab for measuring: the same element, classes and content. */
function TabFace({ tab, active, shown, room, detail }: FaceProps) {
  return (
    <button type="button" tabIndex={-1} className={`cat-tab${detail ? ' ti-more' : ''}`} aria-selected={active}>
      <TabContent tab={tab} shown={shown} room={room} />
    </button>
  )
}

/** Icon, name and indicators. Between pills sits a dot, shown only in themes whose pills have
 *  no fill (Office, Focus): "22 new · 23". */
function TabContent({ tab, shown, room }: Omit<FaceProps, 'active' | 'detail'>) {
  return (
    <>
      {layoutIcon(tab.icon)}
      <span className="cat-tab-name" data-text={tab.name}>
        {tab.name}
      </span>
      {shown.map((i, n) => (
        <Fragment key={i.kind}>
          {n > 0 && room.form === 'pill' && (
            <span className="ti-sep" aria-hidden>
              ·
            </span>
          )}
          <Indicator ind={i} form={room.form} />
        </Fragment>
      ))}
    </>
  )
}

function TabButton({ tab, active, shown, room, detail, onSelect }: FaceProps & { onSelect: () => void }) {
  const full = shown.map((i) => i.title).join('; ')
  // A long press on a touch screen shows the full text (a tooltip needs a mouse).
  const press = useRef<{ timer: ReturnType<typeof setTimeout> | null; fired: boolean }>({ timer: null, fired: false })
  const cancel = () => {
    if (press.current.timer) clearTimeout(press.current.timer)
    press.current.timer = null
  }
  return (
    <button
      type="button"
      role="tab"
      className={`cat-tab${detail ? ' ti-more' : ''}`}
      aria-selected={active}
      aria-label={full ? `${tab.name}, ${shown.map((i) => i.title).join(', ')}` : undefined}
      data-detail={detail || undefined}
      onClick={() => {
        if (press.current.fired) {
          press.current.fired = false
          return
        }
        onSelect()
      }}
      onPointerDown={(e) => {
        press.current.fired = false
        if (e.pointerType !== 'touch' || !full) return
        cancel()
        press.current.timer = setTimeout(() => {
          press.current.fired = true
          toast({ message: `${tab.name}: ${full}` })
        }, LONG_PRESS_MS)
      }}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onContextMenu={(e) => {
        if (press.current.fired) e.preventDefault()
      }}
    >
      <TabContent tab={tab} shown={shown} room={room} />
    </button>
  )
}

const PILL_CLASS: Record<TabIndicator['kind'], string> = { unread: 'new', all: 'count', due: 'due' }

function Indicator({ ind, form }: { ind: TabIndicator; form: StripRoom['form'] }) {
  const cls = `${PILL_CLASS[ind.kind]}${ind.tone === 'urgent' ? ' urgent' : ''}`
  if (form === 'pill') {
    return (
      <span className={cls} data-tip={ind.title} data-indicator={ind.kind}>
        {ind.text}
      </span>
    )
  }
  // Compact: a small number, or a coloured dot for a deadline (the tooltip has the words).
  const urgent = ind.tone === 'urgent' ? ' urgent' : ''
  if (ind.kind === 'due') return <span className={`ti-dot${urgent}`} data-tip={ind.title} data-indicator={ind.kind} />
  return (
    <span className={`ti-num ti-${ind.kind}`} data-tip={ind.title} data-indicator={ind.kind}>
      {ind.short}
    </span>
  )
}
