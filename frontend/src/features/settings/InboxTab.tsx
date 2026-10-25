import { ArrowDown, ArrowUp, Copy, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { useLayouts, useSaveLayouts, useUpdateSettings } from '../../api/hooks'
import type { InboxLayout, InboxType, PrioritySection } from '../../api/types'
import { ConfirmDialog } from '../../components/Dialog'
import { layoutIcon } from '../../lib/layoutIcons'
import { toast } from '../../stores/ui'
import { LayoutEditor, copyLayout, newTab } from './LayoutEditor'
import { CATEGORIES } from '../../lib/views'
import { Check, Radio, Section, SetRow } from './common'
import { useInstantSettings, useTextDraft } from './save'
import { inboxCountMode } from '../../lib/inboxCount'
import { useMailAccounts } from '../layout/accounts'
import { t as tr } from '../../i18n'

const SECTION_NAMES: Record<PrioritySection, string> = { important: tr('common.important'), important_unread: tr('settings.inboxTab.importantAndUnread'), unread: tr('common.unread'), starred: tr('common.starred'), everything: tr('settings.inboxTab.everythingElse') }

/** Settings → Mail → Inbox: the inbox type and your layouts, the count, sections, categories and
 *  markers. Every change saves at once with Undo; a section's name and search save when you leave
 *  the field (save.ts). The reading pane moved to Look → This layout. */
export function InboxTab() {
  const { s, set } = useInstantSettings()
  const updSettings = useUpdateSettings()
  const { accounts } = useMailAccounts()
  const sections = s.priority_sections
  const multiSaved = s.multiple_inboxes ?? []
  const multiText = useTextDraft(multiSaved, (v) => set('multiple_inboxes', v))
  const multi = multiText.value
  const types: [InboxType, string, string][] = [
    ['default', tr('common.categories'), tr('settings.inboxTab.newestFirstWithCategoryTabs')],
    ['simple', tr('common.simple'), tr('settings.inboxTab.oneListNewestFirstWith')],
    ['important_first', tr('common.importantFirst'), tr('settings.inboxTab.importantConversationsThenEverythingElse')],
    ['unread_first', tr('settings.inboxTab.unreadFirst'), tr('settings.inboxTab.unreadConversationsThenEverythingElse')],
    ['starred_first', tr('settings.inboxTab.starredFirst'), tr('settings.inboxTab.starredConversationsThenEverythingElse')],
    ['priority', tr('settings.inboxTab.customSections'), tr('settings.inboxTab.sectionsYouChooseSuchAs')],
    ['multiple', tr('settings.inboxTab.multipleInboxes'), tr('settings.inboxTab.extraSectionsDefinedBySearches')],
  ]
  const move = (i: number, d: number) => {
    const next = sections.slice()
    const j = i + d
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    set('priority_sections', next)
  }
  return (
    <Section title={tr('common.inbox')}>
      <SetRow k={tr('settings.inboxTab.inboxType')}>
        {types.map(([v, l, d]) => (
          <Radio key={v} name="it" value={v} current={s.inbox_type} onChange={(x) => set('inbox_type', x)} label={l} desc={d} />
        ))}
      </SetRow>
      <CustomLayouts current={s.inbox_type} onPick={(v) => set('inbox_type', v)} />
      {/* ACT settings.inbox_count @web.settings */}
      <SetRow k={tr('settings.inboxTab.inboxCount')} sub={tr('settings.inboxTab.theNumberByInboxIn')}>
        <Radio name="ic" value="first_tab" current={inboxCountMode(s.inbox_count)} onChange={(x) => set('inbox_count', x)} label={tr('settings.inboxTab.inboxTabOnly')} />
        <Radio name="ic" value="all" current={inboxCountMode(s.inbox_count)} onChange={(x) => set('inbox_count', x)} label={tr('settings.inboxTab.allTabs')} />
      </SetRow>
      {s.inbox_type === 'priority' && (
        <SetRow k={tr('settings.inboxTab.inboxSections')} sub={tr('settings.inboxTab.orderAndChooseTheSections')}>
          {sections.map((sec, i) => (
            <div key={sec} className="row">
              <span style={{ width: 24 }}>{i + 1}.</span>
              <span style={{ width: 140 }}>{SECTION_NAMES[sec]}</span>
              <button type="button" className="icon-btn sm" aria-label={tr('common.moveUp')} disabled={i === 0} onClick={() => move(i, -1)}>
                <ArrowUp size={18} />
              </button>
              <button type="button" className="icon-btn sm" aria-label={tr('common.moveDown')} disabled={i === sections.length - 1} onClick={() => move(i, 1)}>
                <ArrowDown size={18} />
              </button>
              <button type="button" className="icon-btn sm" aria-label={tr('settings.inboxTab.removeSection')} disabled={sections.length <= 1} onClick={() => set('priority_sections', sections.filter((x) => x !== sec))}>
                <Trash2 size={18} />
              </button>
            </div>
          ))}
          <div className="row">
            {(Object.keys(SECTION_NAMES) as PrioritySection[])
              .filter((x) => !sections.includes(x))
              .map((x) => (
                <button key={x} type="button" className="chip" onClick={() => set('priority_sections', [...sections, x])}>
                  <Plus /> {SECTION_NAMES[x]}
                </button>
              ))}
          </div>
        </SetRow>
      )}
      {s.inbox_type === 'multiple' && (
        <SetRow k={tr('settings.inboxTab.multipleInboxes2')} sub={tr('settings.inboxTab.eachSectionShowsConversationsMatching')}>
          {multi.map((m, i) => (
            <div key={i} className="row" style={{ width: '100%', maxWidth: 640 }}>
              <input className="input" style={{ width: 180 }} placeholder={tr('settings.inboxTab.sectionName')} value={m.name} onChange={(e) => multiText.set(multi.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} onBlur={multiText.flush} aria-label={tr('settings.inboxTab.sectionName')} />
              <input className="input" placeholder={tr('settings.inboxTab.searchQueryEGIs')} value={m.q} onChange={(e) => multiText.set(multi.map((x, j) => (j === i ? { ...x, q: e.target.value } : x)))} onBlur={multiText.flush} aria-label={tr('settings.inboxTab.sectionQuery')} />
              <button type="button" className="icon-btn sm" aria-label={tr('settings.inboxTab.removeSection')} onClick={() => { multiText.flush(); set('multiple_inboxes', multi.filter((_, j) => j !== i)) }}>
                <Trash2 size={18} />
              </button>
            </div>
          ))}
          <button type="button" className="btn text" onClick={() => { multiText.flush(); set('multiple_inboxes', [...multi, { name: '', q: '' }]) }} disabled={multi.length >= 5}>
            <Plus />{' '}{tr('common.addSection')}
          </button>
        </SetRow>
      )}
      {/* ACT mail.account_folders @web.settings: one unified mailbox by default; each account's own
          folders in the navigation when this is on (features/layout/accounts.tsx). */}
      <SetRow id="set-account-folders" k={tr('settings.inboxTab.accounts')} sub={tr('settings.inboxTab.accountsSub')}>
        <Check
          checked={!!s.account_folders}
          onChange={(v) => {
            set('account_folders', v)
            // The navigation's hint about this setting has done its job (features/layout/accounts.tsx).
            // Saved quietly, so the toast's Undo stays with the setting itself.
            if (!s.account_folders_hint_dismissed) updSettings.mutate({ account_folders_hint_dismissed: true })
          }}
          label={tr('settings.inboxTab.showEachAccountSeparately')}
          desc={accounts.length > 1 ? tr('settings.inboxTab.eachAccountGetsItsOwn') : tr('settings.inboxTab.takesEffectWithTwoAccounts')}
        />
        {/* ACT mail.account_folders_show_unread @web.settings: an unpinned folder with unread mail
            shows above "More folders" until it is read (features/layout/folderPins.tsx). */}
        <div style={{ paddingInlineStart: 32 }}>
          <Check
            checked={!!s.account_folders_show_unread}
            disabled={!s.account_folders}
            onChange={(v) => set('account_folders_show_unread', v)}
            label={tr('settings.inboxTab.showUnreadFoldersAboveMore')}
            desc={tr('settings.inboxTab.showUnreadFoldersAboveMoreDesc')}
          />
        </div>
      </SetRow>
      <SetRow k={tr('settings.inboxTab.categories')} sub={tr('settings.inboxTab.selectWhichCategoriesToShow')}>
        {CATEGORIES.map((c) => (
          <Check
            key={c.id}
            checked={s.categories.includes(c.id)}
            disabled={c.id === 'primary'}
            onChange={(v) => set('categories', v ? [...s.categories, c.id] : s.categories.filter((x) => x !== c.id))}
            label={c.title}
          />
        ))}
      </SetRow>
      <SetRow k={tr('settings.inboxTab.importanceMarkers')}>
        <Radio name="im" value="on" current={s.importance_markers ? 'on' : 'off'} onChange={() => set('importance_markers', true)} label={tr('settings.inboxTab.showMarkers')} desc={tr('settings.inboxTab.showAMarkerByMessages')} />
        <Radio name="im" value="off" current={s.importance_markers ? 'on' : 'off'} onChange={() => set('importance_markers', false)} label={tr('settings.inboxTab.noMarkers')} />
        <p className="muted" style={{ fontSize: 12.5, margin: 0, maxWidth: 560 }}>
          {tr('settings.inboxTab.whatIsImportantIsLearned')}
        </p>
      </SetRow>
    </Section>
  )
}

function layoutSummary(l: InboxLayout): string {
  const tabs = l.tabs.length
  const secs = l.tabs.reduce((n, t) => n + t.sections.length, 0)
  return tabs > 1 ? tr('settings.inboxTab.tabs', { tabs, map: l.tabs.map((t) => t.name).join(', ') }) : tr('settings.inboxTab.sections', { count: secs, map: l.tabs[0]?.sections.map((x) => x.title).join(', ') })
}

/** Your own layouts: tabs across the top (optional) and sections inside each tab. */
function CustomLayouts({ current, onPick }: { current: InboxType; onPick: (v: InboxType) => void }) {
  const layouts = useLayouts()
  const save = useSaveLayouts()
  const [editing, setEditing] = useState<{ layout: InboxLayout; isNew: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<InboxLayout | null>(null)
  const all = layouts.data?.layouts ?? []
  const custom = all.filter((l) => !l.preset)
  const presets = all.filter((l) => l.preset)
  const activeId = layouts.data?.active

  const persist = (next: InboxLayout[], active: string | undefined, done: () => void) => {
    setError(null)
    save.mutate(
      { layouts: next, active },
      {
        onSuccess: () => done(),
        onError: (e) => setError(e instanceof ApiError ? e.message : tr('settings.inboxTab.couldNotSaveTheLayout')),
      },
    )
  }

  const startNew = (from?: InboxLayout) => {
    setError(null)
    setEditing({
      isNew: true,
      layout: from ? copyLayout(from, tr('settings.inboxTab.myCopy', { fromName: from.name })) : { id: '', name: tr('settings.inboxTab.myInbox'), tabs: [newTab(tr('common.inbox'), { badge: 'none' })] },
    })
  }

  return (
    <SetRow k={tr('settings.inboxTab.yourLayouts')} sub={tr('settings.inboxTab.tabsAcrossTheTopOptional')}>
      {custom.length === 0 && <p className="muted" style={{ margin: 0 }}>{tr('settings.inboxTab.buildYourOwnInboxFor')}</p>}
      {custom.map((l) => (
        <div key={l.id} className="lay-row">
          <input type="radio" className="radio" name="it" aria-label={tr('settings.inboxTab.use', { itemName: l.name })} checked={current === `layout:${l.id}`} onChange={() => onPick(`layout:${l.id}`)} />
          <span className="lay-row-icons" aria-hidden>
            {l.tabs.slice(0, 4).map((t) => (
              <span key={t.id}>{layoutIcon(t.icon)}</span>
            ))}
          </span>
          <span className="lay-row-text">
            <span className="name">
              {l.name}
              {activeId === l.id && <span className="chip-sm">{tr('settings.inboxTab.inUse')}</span>}
            </span>
            <span className="desc">{layoutSummary(l)}</span>
          </span>
          <button type="button" className="icon-btn sm" aria-label={tr('settings.inboxTab.edit', { itemName: l.name })} data-tip={tr('common.edit2')} onClick={() => { setError(null); setEditing({ layout: l, isNew: false }) }}>
            <Pencil />
          </button>
          <button type="button" className="icon-btn sm" aria-label={tr('settings.inboxTab.duplicate', { itemName: l.name })} data-tip={tr('common.duplicate')} onClick={() => startNew(l)}>
            <Copy />
          </button>
          <button type="button" className="icon-btn sm" aria-label={tr('settings.inboxTab.delete', { itemName: l.name })} data-tip={tr('common.delete2')} onClick={() => setDeleting(l)}>
            <Trash2 />
          </button>
        </div>
      ))}
      <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
        <button type="button" className="btn tonal" onClick={() => startNew()} disabled={custom.length >= (layouts.data?.options.limits.layouts ?? 12)}>
          <Plus />{' '}{tr('settings.inboxTab.newLayout')}
        </button>
        {presets.length > 0 && (
          <select
            className="input"
            style={{ width: 'auto' }}
            value=""
            aria-label={tr('settings.inboxTab.startFromABuiltIn')}
            onChange={(e) => {
              const p = presets.find((x) => x.id === e.target.value)
              if (p) startNew(p)
            }}
          >
            <option value="">{tr('settings.inboxTab.startFromABuiltIn2')}</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </div>
      {editing && (
        <LayoutEditor
          open
          initial={editing.layout}
          isNew={editing.isNew}
          active={!editing.isNew && activeId === editing.layout.id}
          busy={save.isPending}
          error={error}
          onClose={() => setEditing(null)}
          onSave={(lay, makeActive) => {
            // New layouts get their id here (from the name), so it can be made active in the
            // same save; the server keeps it unless it clashes.
            const id = editing.isNew
              ? `${lay.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30) || 'layout'}-${Math.random().toString(36).slice(2, 6)}`
              : editing.layout.id
            const saved = { ...lay, id, preset: undefined }
            const next = editing.isNew ? [...custom, saved] : custom.map((x) => (x.id === id ? saved : x))
            persist(next, makeActive ? id : activeId === id ? 'default' : undefined, () => {
              setEditing(null)
              toast({ message: makeActive ? tr('settings.inboxTab.isNowYourInbox', { layName: lay.name }) : tr('settings.inboxTab.layoutSaved') })
            })
          }}
        />
      )}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.inboxTab.deleteLayout')}
        body={deleting ? tr('settings.inboxTab.willBeDeleted', { deletingName: deleting.name, value: activeId === deleting.id ? tr('settings.inboxTab.yourInboxGoesBackTo') : '' }) : ''}
        confirmLabel={tr('common.delete2')}
        danger
        busy={save.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (!deleting) return
          persist(custom.filter((x) => x.id !== deleting.id), undefined, () => {
            setDeleting(null)
            toast({ message: tr('settings.inboxTab.layoutDeleted') })
          })
        }}
      />
    </SetRow>
  )
}
