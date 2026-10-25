import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useLabelList } from '../../api/hooks'
import type { InboxLayout, LayoutFilter, LayoutSection, LayoutTab } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { LAYOUT_ICONS, layoutIcon } from '../../lib/layoutIcons'
import { indicatorsOf, type TabIndicatorKind } from '../../lib/tabIndicators'
import { CATEGORIES } from '../../lib/views'
import { t as tr, tx, appLocale } from '../../i18n'

const BASES: [string, string][] = [
  ['inbox', tr('common.inbox')],
  ['all', tr('common.allMail')],
  ['starred', tr('common.starred')],
  ['important', tr('common.important')],
  ['snoozed', tr('common.snoozed')],
  ['sent', tr('common.sent')],
  ['drafts', tr('common.drafts')],
  ['archive', tr('common.archive')],
]
const FILTERS: [LayoutFilter | '', string][] = [
  ['', tr('settings.layoutEditor.allConversations')],
  ['unread', tr('common.unread')],
  ['important', tr('common.important')],
  ['important_unread', tr('settings.layoutEditor.importantAndUnread')],
  ['starred', tr('common.starred')],
]
const SIZES: [number | null, string][] = [
  [5, tr('settings.layoutEditor.top5')],
  [10, tr('settings.layoutEditor.top10')],
  [20, tr('settings.layoutEditor.top20')],
  [null, tr('settings.layoutEditor.everythingPaged')],
]
const MAX_TABS = 8
const MAX_SECTIONS = 8

let seq = 0
const tmpId = (p: string) => `${p}-${Date.now().toString(36)}${(seq++).toString(36)}`

export function newSection(title = tr('common.inbox'), over: Partial<LayoutSection> = {}): LayoutSection {
  return { id: tmpId('s'), title, base: 'inbox', category: null, filter: null, q: '', limit: null, exclude_above: true, inbox_only: false, ...over }
}

/** The change a new "Conversations from" makes. A section that becomes a label section starts
 *  with "Only mail in the Inbox" on; an inbox section
 *  has no use for it. A category only applies to the Inbox. */
export function baseChange(sec: LayoutSection, base: string): Partial<LayoutSection> {
  const out: Partial<LayoutSection> = { base, category: base === 'inbox' ? sec.category : null }
  if (base === 'inbox') out.inbox_only = false
  else if (base.startsWith('label:') && !sec.base.startsWith('label:')) out.inbox_only = true
  return out
}

export function newTab(name = tr('common.inbox'), over: Partial<LayoutTab> = {}): LayoutTab {
  return { id: tmpId('t'), name, icon: 'inbox', badge: 'unread', indicators: ['unread'], sections: [newSection()], ...over }
}

/** A deep copy that can be edited and saved as a custom layout. */
export function copyLayout(l: InboxLayout, name: string): InboxLayout {
  return {
    id: '',
    name,
    tab_detail: l.tab_detail,
    hide_empty: l.hide_empty,
    tabs: l.tabs.map((t) => ({ ...t, sections: t.sections.map((s) => ({ ...s, view_all_q: undefined })) })),
  }
}

interface Props {
  open: boolean
  initial: InboxLayout
  isNew: boolean
  active: boolean
  busy: boolean
  error: string | null
  onSave: (layout: InboxLayout, makeActive: boolean) => void
  onClose: () => void
}

/** What a tab can show next to its name (LayoutTab.indicators, lib/tabIndicators.ts). */
const TAB_INDICATORS: [TabIndicatorKind, string, string][] = [
  ['unread', tr('common.unread'), tr('settings.layoutEditor.unreadConversations3New')],
  ['all', tr('common.all'), tr('settings.layoutEditor.everyConversationInTheTab')],
  ['due', tr('common.due'), tr('settings.layoutEditor.conversationsWithADeadlineAnd')],
]

/** Edit one custom layout: its tabs (name, icon, count) and each tab's sections. */
export function LayoutEditor({ open, initial, isNew, active, busy, error, onSave, onClose }: Props) {
  const [lay, setLay] = useState<InboxLayout>(initial)
  const [tabIdx, setTabIdx] = useState(0)
  const [makeActive, setMakeActive] = useState(active || isNew)
  useEffect(() => {
    if (open) {
      setLay(initial)
      setTabIdx(0)
      setMakeActive(active || isNew)
    }
  }, [open, initial, active, isNew])

  const tab = lay.tabs[Math.min(tabIdx, lay.tabs.length - 1)]
  const setTabs = (tabs: LayoutTab[]) => setLay((l) => ({ ...l, tabs }))
  const updTab = (i: number, patch: Partial<LayoutTab>) => setTabs(lay.tabs.map((t, j) => (j === i ? { ...t, ...patch } : t)))
  const moveTab = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= lay.tabs.length) return
    const next = lay.tabs.slice()
    ;[next[i], next[j]] = [next[j], next[i]]
    setTabs(next)
    setTabIdx(j)
  }
  const secs = tab.sections
  const setSecs = (sections: LayoutSection[]) => updTab(tabIdx, { sections })
  const updSec = (i: number, patch: Partial<LayoutSection>) => setSecs(secs.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const moveSec = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= secs.length) return
    const next = secs.slice()
    ;[next[i], next[j]] = [next[j], next[i]]
    setSecs(next)
  }

  const problems = useMemo(() => {
    const out: string[] = []
    if (!lay.name.trim()) out.push(tr('settings.layoutEditor.giveTheLayoutAName'))
    lay.tabs.forEach((t) => {
      if (!t.name.trim()) out.push(tr('settings.layoutEditor.everyTabNeedsAName'))
    })
    return [...new Set(out)]
  }, [lay])

  return (
    <Dialog
      open={open}
      onClose={onClose}
      modal
      wide
      title={isNew ? tr('settings.layoutEditor.newInboxLayout') : tr('settings.layoutEditor.edit', { initialName: initial.name })}
      footer={
        <>
          <label className="choice" style={{ marginInlineEnd: 'auto' }}>
            <input type="checkbox" className="checkbox" checked={makeActive} onChange={(e) => setMakeActive(e.target.checked)} />
            <span>{tr('settings.layoutEditor.makeThisLayoutMyInbox')}</span>
          </label>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={busy || problems.length > 0} onClick={() => onSave(lay, makeActive)}>
            {busy ? tr('common.saving') : tr('settings.layoutEditor.saveLayout')}
          </button>
        </>
      }
    >
      <div className="lay-ed">
        <label className="field">
          <span className="field-label">{tr('settings.layoutEditor.layoutName')}</span>
          <input className="input" value={lay.name} maxLength={60} onChange={(e) => setLay({ ...lay, name: e.target.value })} placeholder={tr('settings.layoutEditor.eGWorkAndLater')} autoFocus />
        </label>
        {/* ACT layout.tab_detail @web.settings: every indicator on a tab while the strip has room (InboxTabs). */}
        <label className="choice" data-tip={tr('settings.layoutEditor.allOfATabsIndicators')}>
          <input type="checkbox" className="checkbox" checked={!!lay.tab_detail} onChange={(e) => setLay({ ...lay, tab_detail: e.target.checked })} />
          <span>{tr('settings.layoutEditor.showMoreOnTabsWhen')}</span>
        </label>
        {/* ACT layout.hide_empty @web.settings: sections with no conversations are left out (MailList Section). */}
        <label className="choice" data-tip={tr('settings.layoutEditor.hideEmptySectionsTip')}>
          <input type="checkbox" className="checkbox" checked={!!lay.hide_empty} onChange={(e) => setLay({ ...lay, hide_empty: e.target.checked })} />
          <span>{tr('settings.layoutEditor.hideEmptySections')}</span>
        </label>

        <div className="lay-ed-tabs" role="tablist" aria-label={tr('settings.layoutEditor.tabs')}>
          {lay.tabs.map((t, i) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={i === tabIdx}
              className={`lay-ed-tab${i === tabIdx ? ' on' : ''}`}
              data-tip={i === 0 ? tr('settings.layoutEditor.countedInTheSidebar') : undefined}
              onClick={() => setTabIdx(i)}
            >
              {layoutIcon(t.icon)}
              <span>{t.name || tr('common.untitled')}</span>
            </button>
          ))}
          <button
            type="button"
            className="btn text sm"
            disabled={lay.tabs.length >= MAX_TABS}
            onClick={() => {
              setTabs([...lay.tabs, newTab(tr('settings.layoutEditor.tab', { value: lay.tabs.length + 1 }), { icon: 'bookmark' })])
              setTabIdx(lay.tabs.length)
            }}
          >
            <Plus />{' '}{tr('settings.layoutEditor.addTab')}
          </button>
        </div>
        {lay.tabs.length === 1 && <p className="lay-ed-hint">{tr('settings.layoutEditor.withOneTabTheInbox')}</p>}

        <div className="lay-ed-tabcard">
          {/* ACT settings.inbox_count @web.settings: the first tab is the counted one (Settings → Mail → Inbox → Inbox count). */}
          {tabIdx === 0 && (
            <p className="lay-ed-hint lay-ed-counted">
              {tr('settings.layoutEditor.countedInTheSidebarThe')}
            </p>
          )}
          <div className="lay-ed-row">
            <label className="field grow">
              <span className="field-label">{tr('settings.layoutEditor.tabName')}</span>
              <input className="input" value={tab.name} maxLength={40} onChange={(e) => updTab(tabIdx, { name: e.target.value })} />
            </label>
            <label className="field">
              <span className="field-label">{tr('settings.layoutEditor.icon')}</span>
              <select className="input" value={tab.icon ?? 'inbox'} onChange={(e) => updTab(tabIdx, { icon: e.target.value })}>
                {Object.entries(LAYOUT_ICONS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="lay-ed-tools">
              <button
                type="button"
                className="icon-btn sm"
                aria-label={tr('settings.layoutEditor.moveTabLeft')}
                data-tip={tr('settings.layoutEditor.moveLeft', { count: tabIdx })}
                disabled={tabIdx === 0}
                onClick={() => moveTab(tabIdx, -1)}
              >
                <ArrowLeft />
              </button>
              <button
                type="button"
                className="icon-btn sm"
                aria-label={tr('settings.layoutEditor.moveTabRight')}
                data-tip={tabIdx === 0 ? tr('settings.layoutEditor.moveRightTheNextTab') : tr('settings.layoutEditor.moveRight')}
                disabled={tabIdx === lay.tabs.length - 1}
                onClick={() => moveTab(tabIdx, 1)}
              >
                <ArrowRight />
              </button>
              <button
                type="button"
                className="icon-btn sm"
                aria-label={tr('settings.layoutEditor.deleteTab')}
                data-tip={tabIdx === 0 ? tr('settings.layoutEditor.theFirstTabIsCounted') : tr('settings.layoutEditor.deleteTab')}
                disabled={tabIdx === 0 || lay.tabs.length <= 1}
                onClick={() => {
                  setTabs(lay.tabs.filter((_, j) => j !== tabIdx))
                  setTabIdx(Math.max(0, tabIdx - 1))
                }}
              >
                <Trash2 />
              </button>
            </div>
          </div>
          <div className="lay-ed-inds" role="group" aria-labelledby="lay-ed-inds-label">
            <span className="field-label" id="lay-ed-inds-label">{tr('settings.layoutEditor.showOnTheTab')}</span>
            {TAB_INDICATORS.map(([v, label, hint]) => (
              <label key={v} className="choice" data-tip={hint}>
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={indicatorsOf(tab).includes(v)}
                  onChange={(e) => {
                    const on = new Set(indicatorsOf(tab))
                    if (e.target.checked) on.add(v)
                    else on.delete(v)
                    const indicators = TAB_INDICATORS.map(([k]) => k).filter((k) => on.has(k))
                    updTab(tabIdx, { indicators, badge: indicators.includes('unread') ? 'unread' : indicators.includes('all') ? 'all' : 'none' })
                  }}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
          <label className="choice">
            <input type="checkbox" className="checkbox" checked={!!tab.claims} onChange={(e) => updTab(tabIdx, { claims: e.target.checked })} />
            <span>{tx('settings.layoutEditor.keepThisTabsMailOut', undefined, { span: (c) => <span className="muted">{c}</span> })}</span>
          </label>

          <div className="lay-ed-sections">
            {secs.map((s, i) => (
              <SectionCard
                key={s.id}
                sec={s}
                index={i}
                count={secs.length}
                above={secs.slice(0, i)}
                onChange={(p) => updSec(i, p)}
                onMove={(d) => moveSec(i, d)}
                onDelete={() => setSecs(secs.filter((_, j) => j !== i))}
              />
            ))}
            <button
              type="button"
              className="btn tonal sm"
              disabled={secs.length >= MAX_SECTIONS}
              onClick={() => {
                // A new section goes above the last one, which stays the paged main list.
                const next = secs.map((x) => x)
                next.splice(Math.max(0, secs.length - 1), 0, newSection(tr('common.unread'), { filter: 'unread', limit: 10 }))
                setSecs(next)
              }}
            >
              <Plus />{' '}{tr('common.addSection')}
            </button>
          </div>
        </div>
        {(error || problems.length > 0) && (
          <div className="lay-ed-error" role="alert">
            {error ?? problems[0]}
          </div>
        )}
      </div>
    </Dialog>
  )
}

function SectionCard({ sec, index, count, above, onChange, onMove, onDelete }: {
  sec: LayoutSection
  index: number
  count: number
  above: LayoutSection[]
  onChange: (p: Partial<LayoutSection>) => void
  onMove: (d: number) => void
  onDelete: () => void
}) {
  const labels = useLabelList()
  const userLabels = labels.filter((l) => !l.role).sort((a, b) => a.name.localeCompare(b.name))
  const preview = useSectionPreview(sec, above)
  return (
    <div className="lay-ed-sec">
      <div className="lay-ed-row">
        <span className="lay-ed-num" aria-hidden>
          {index + 1}
        </span>
        <label className="field grow">
          <span className="field-label">{tr('settings.layoutEditor.sectionTitle')}</span>
          <input className="input" value={sec.title} maxLength={60} onChange={(e) => onChange({ title: e.target.value })} />
        </label>
        <div className="lay-ed-tools">
          <button type="button" className="icon-btn sm" aria-label={tr('settings.layoutEditor.moveSectionUp')} data-tip={tr('common.moveUp')} disabled={index === 0} onClick={() => onMove(-1)}>
            <ArrowUp />
          </button>
          <button type="button" className="icon-btn sm" aria-label={tr('settings.layoutEditor.moveSectionDown')} data-tip={tr('common.moveDown')} disabled={index === count - 1} onClick={() => onMove(1)}>
            <ArrowDown />
          </button>
          <button type="button" className="icon-btn sm" aria-label={tr('settings.layoutEditor.deleteSection')} data-tip={tr('settings.layoutEditor.deleteSection')} disabled={count <= 1} onClick={onDelete}>
            <Trash2 />
          </button>
        </div>
      </div>
      <div className="lay-ed-grid">
        <label className="field">
          <span className="field-label">{tr('settings.layoutEditor.conversationsFrom')}</span>
          <select className="input" value={sec.base} onChange={(e) => onChange(baseChange(sec, e.target.value))}>
            {BASES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
            {userLabels.length > 0 && (
              <optgroup label={tr('common.labels')}>
                {userLabels.map((l) => (
                  <option key={l.id} value={`label:${l.id}`}>
                    {l.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {sec.base === 'inbox' && (
          <label className="field">
            <span className="field-label">{tr('common.category')}</span>
            <select className="input" value={sec.category ?? ''} onChange={(e) => onChange({ category: e.target.value || null })}>
              <option value="">{tr('settings.layoutEditor.anyCategory')}</option>
              {CATEGORIES.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span className="field-label">{tr('common.show')}</span>
          <select className="input" value={sec.filter ?? ''} onChange={(e) => onChange({ filter: (e.target.value || null) as LayoutFilter | null })}>
            {FILTERS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">{tr('settings.layoutEditor.howMany')}</span>
          <select className="input" value={sec.limit == null ? '' : String(sec.limit)} onChange={(e) => onChange({ limit: e.target.value ? Number(e.target.value) : null })}>
            {SIZES.map(([v, l]) => (
              <option key={l} value={v == null ? '' : String(v)}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field span2">
          <span className="field-label">{tr('settings.layoutEditor.matchingSearchOptional')}</span>
          <input className="input mono" value={sec.q ?? ''} maxLength={500} placeholder="e.g. from:me to:me   or   {from:boss@x.example from:team@x.example}" onChange={(e) => onChange({ q: e.target.value })} spellCheck={false} />
        </label>
      </div>
      <div className="lay-ed-foot">
        <div className="lay-ed-checks">
          {/* ACT layout.inbox_only @web.settings */}
          {sec.base !== 'inbox' && (
            <label className="choice">
              <input type="checkbox" className="checkbox" checked={!!sec.inbox_only} onChange={(e) => onChange({ inbox_only: e.target.checked })} />
              <span>{tr('settings.layoutEditor.onlyMailInTheInbox')}</span>
            </label>
          )}
          {index > 0 && (
            <label className="choice">
              <input type="checkbox" className="checkbox" checked={sec.exclude_above} onChange={(e) => onChange({ exclude_above: e.target.checked })} />
              <span>{tr('settings.layoutEditor.leaveOutConversationsAlreadyShown')}</span>
            </label>
          )}
        </div>
        <span className={`lay-ed-count${preview.error ? ' err' : ''}`} aria-live="polite">
          {preview.error
            ? preview.error
            : preview.data
              ? tr('settings.layoutEditor.conversationsUnread', { count: preview.data.total, value: preview.data.capped ? '+' : '', unread: preview.data.unread.toLocaleString(appLocale()) })
              : tr('settings.layoutEditor.counting')}
        </span>
      </div>
    </div>
  )
}

/** Live count for a section being edited (debounced). */
function useSectionPreview(sec: LayoutSection, above: LayoutSection[]) {
  const body = useMemo(() => JSON.stringify({ section: sec, above }), [sec, above])
  const [state, setState] = useState<{ data: { total: number; unread: number; capped?: boolean } | null; error: string | null }>({ data: null, error: null })
  useEffect(() => {
    const ctl = new AbortController()
    const t = setTimeout(() => {
      api
        .post<{ total: number; unread: number; capped?: boolean }>('/layouts/preview', JSON.parse(body), { signal: ctl.signal })
        .then((data) => setState({ data, error: null }))
        .catch((e: unknown) => {
          if (ctl.signal.aborted) return
          setState({ data: null, error: e instanceof Error ? e.message : tr('settings.layoutEditor.couldNotCount') })
        })
    }, 350)
    return () => {
      clearTimeout(t)
      ctl.abort()
    }
  }, [body])
  return state
}
