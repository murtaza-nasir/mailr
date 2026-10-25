// Settings: groups, the pages of a group, and a page's sections, all from the one map in
// groups.ts. Addresses are /settings/<group>/<page>#<section>. Old addresses
// (/settings/general, /settings/assistant?section=models, /settings/agents#approvals …) redirect
// to their new place (groups.ts legacyTarget), so links in docs, notifications and the palette
// keep working. Every page saves at once, with Undo (save.ts).

import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { NavLink, Navigate, useLocation, useParams } from 'react-router'
import { api } from '../../api/client'
import { ErrorBoundary, Switch } from '../../components/bits'
import { ScrollTabs } from '../../components/ScrollTabs'
import { scrollBehavior } from '../../lib/motion'
import { qkA, type MemoryList } from './assistant/types'
import { findSection, groupById, legacyTarget, SETTINGS_GROUPS, type SettingsGroup, type SettingsPageDef } from './groups'
import { useSaveSettings } from './save'
import { SECTION_COMPONENTS } from './sections'
import { useVisibility } from './useVisibility'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { filtersTitle, pageVisible, reachable, type SettingsVisibility } from './visibility'
import '../../styles/assistant-settings.css'
import { t as tr, tx } from '../../i18n'

/** The page title a user sees (the Filters page is "Blocked addresses" in simple mode). */
function pageTitle(v: SettingsVisibility, g: SettingsGroup, p: SettingsPageDef): string {
  return g.id === 'mail' && p.id === 'filters' ? filtersTitle(v) : p.title
}

export default function SettingsPage() {
  const { tab, page: pageParam } = useParams()
  const location = useLocation()
  const v = useVisibility()
  const save = useSaveSettings()
  // The assistant's group carries the name the user gave the assistant (About you): its title and
  // description read the name this call remembers (groups.ts getters), so a rename shows at once.
  useAssistantName()

  useEffect(() => {
    document.title = tr('settings.settingsPage.settingsMailr')
  }, [])

  // A section named in the address: scroll to it once it is drawn.
  const hash = location.hash.replace(/^#/, '')
  useEffect(() => {
    if (!hash) return
    const t = setTimeout(() => document.getElementById(`set-${hash}`)?.scrollIntoView({ behavior: scrollBehavior(), block: 'start' }), 50)
    return () => clearTimeout(t)
  }, [hash, tab, pageParam])

  const old = legacyTarget(tab, pageParam, location.search, location.hash)
  if (old) return <Navigate to={old} replace />
  const groups = SETTINGS_GROUPS.filter((g) => v.tabs[g.id] || g.id === tab)
  const group = groupById(tab ?? '') ?? groups[0]
  if (!reachable(v, group.id)) return <Navigate to="/settings/you" replace />
  // A page the strip leaves out still opens from a link (an approval link opens Agents & API).
  const pages = group.pages.filter((p) => pageVisible(v, group.id, p.id) || p.id === pageParam)
  const page = pages.find((p) => p.id === pageParam) ?? pages[0]
  if (!page) return <Navigate to="/settings/you" replace />
  // A section of this group asked for on the wrong page (/settings/mail/reading#vip): go to its page.
  const asked = hash ? findSection(hash) : null
  if (asked && asked.group.id === group.id && asked.page.id !== page.id) return <Navigate to={`/settings/${group.id}/${asked.page.id}${location.search}#${hash}`} replace />

  return (
    <div className="settings">
      <div className="settings-head">
        <h1>{tr('common.settings')}</h1>
        {/* Simple or advanced (setting advanced_settings). Admins always see everything. */}
        {!v.admin && (
          <label className="settings-mode">
            <Switch checked={v.advanced} onChange={(on) => void save({ advanced_settings: on }, { message: on ? tr('settings.settingsPage.everySettingIsShown') : tr('settings.settingsPage.onlyTheEverydaySettingsAre') })} label={tr('settings.settingsPage.advancedSettings')} />
            <span>
              {tx('settings.settingsPage.advancedSettings2', { value: v.advanced ? tr('settings.settingsPage.everySettingIsShown2') : tr('settings.settingsPage.onlyTheEverydaySettingsAre2') }, { span: (c) => <span className="muted">{c}</span> })}
            </span>
          </label>
        )}
      </div>
      {/* On narrow screens the strip scrolls, and the current group stays in view. */}
      <ScrollTabs className="settings-tabs" kind="nav" label={tr('settings.settingsPage.settingsGroups')} revealKey={group.id}>
        {groups.map((g) => (
          <NavLink key={g.id} to={`/settings/${g.id}`} className={() => `settings-tab${g.id === group.id ? ' active' : ''}`} aria-current={g.id === group.id ? 'page' : undefined}>
            {g.title}
          </NavLink>
        ))}
      </ScrollTabs>
      <div className="settings-body" data-group={group.id} data-page={page.id}>
        {group.desc && <p className="as-intro">{group.desc}</p>}
        {group.id === 'ambrose' && v.message && (
          <div className="banner" role="status" data-testid="no-assistant" style={{ marginBottom: 12 }}>
            {v.message}
          </div>
        )}
        {pages.length > 1 && (
          // Links to pages, so the current one is aria-current="page"; aria-pressed is a toggle
          // button's state and not allowed on a link (axe aria-allowed-attr).
          <ScrollTabs className="as-nav" kind="nav" label={tr('settings.settingsPage.pages', { named: group.title })} revealKey={page.id}>
            {pages.map((p) => (
              <NavLink key={p.id} to={`/settings/${group.id}/${p.id}`} className="chip" aria-current={p.id === page.id ? 'page' : undefined}>
                {pageTitle(v, group, p)}
                {group.id === 'ambrose' && p.id === 'memory' && <SuggestedBadge enabled={v.parts.memory} />}
              </NavLink>
            ))}
          </ScrollTabs>
        )}
        <ErrorBoundary key={`${group.id}/${page.id}`}>
          {page.sections.map((s) => {
            const Comp = SECTION_COMPONENTS[s.id]
            return Comp ? (
              <div key={s.id} id={`set-${s.id}`} className="set-anchor" data-section={s.id}>
                <Comp />
              </div>
            ) : null
          })}
        </ErrorBoundary>
      </div>
    </div>
  )
}

/** Memories Ambrose suggests and you have not decided on yet. */
function SuggestedBadge({ enabled }: { enabled: boolean }) {
  const n = useQuery<MemoryList>({
    queryKey: qkA.memories('suggested'),
    queryFn: () => api.get<MemoryList>('/assistant/memories', { status: 'suggested' }),
    enabled,
  }).data?.counts.suggested
  return n ? <span className="badge" aria-label={tr('settings.settingsPage.suggested', { n })}>{n}</span> : null
}
