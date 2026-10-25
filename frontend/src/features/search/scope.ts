// The search box's scope chip: All · Mail · Calendar · Tasks · Notes. It starts on
// the app you are in; Esc in the box returns it to All. All shows the mail results with the Events,
// Tasks and Notes groups, in the order of the app you searched from; Mail shows mail only; the other
// scopes search in their own app.

import { create } from 'zustand'
import { t } from '../../i18n'

export type SearchScope = 'all' | 'mail' | 'calendar' | 'tasks' | 'notes'
export type GlanceGroup = 'notes' | 'events' | 'tasks'

export const SCOPES: { id: SearchScope; label: string }[] = [
  { id: 'all', label: t('common.all') },
  { id: 'mail', label: t('common.mail2') },
  { id: 'calendar', label: t('common.calendar') },
  { id: 'tasks', label: t('common.tasks') },
  { id: 'notes', label: t('common.notes') },
]

export const scopeLabel = (s: SearchScope) => SCOPES.find((x) => x.id === s)?.label ?? t('common.all')

/** The app a path belongs to, as a scope; All where no app has search of its own (Settings, People…). */
export function appScopeOf(pathname: string): SearchScope {
  const first = pathname.split('/').filter(Boolean)[0] ?? ''
  if (first === 'mail' || first === '') return 'mail'
  if (first === 'calendar') return 'calendar'
  if (first === 'tasks') return 'tasks'
  if (first === 'notes' || first === 'tables') return 'notes'
  return 'all'
}

/** The groups above the mail results in the All scope, in the order of the app searched from:
 * that app's group first, the others in their usual order. Mail's own results follow them. */
export function glanceOrder(from: SearchScope): GlanceGroup[] {
  const usual: GlanceGroup[] = ['notes', 'events', 'tasks']
  const first: GlanceGroup | null = from === 'calendar' ? 'events' : from === 'tasks' ? 'tasks' : from === 'notes' ? 'notes' : null
  return first ? [first, ...usual.filter((g) => g !== first)] : usual
}

interface Results {
  /** What the mail search page shows: everything (All) or mail only. */
  scope: 'all' | 'mail'
  /** The app the search started in (the order of the groups). */
  from: SearchScope
  set: (p: Partial<Omit<Results, 'set'>>) => void
}

/** The last search's scope, for the mail search page. A link to /mail/search?q= with no search
 * before it shows everything, as it always did. */
export const useSearchResults = create<Results>((set) => ({
  scope: 'all',
  from: 'mail',
  set: (p) => set(p),
}))
