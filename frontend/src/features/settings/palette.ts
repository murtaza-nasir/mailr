// The palette's "Settings: …" entries (themes/shared/CommandPalette.tsx): one per section of
// groups.ts that the user can see, found by its title, its page and group, and the settings it
// holds. They show only for a search.

import { allSections, sectionSearchText } from './groups'
import { pageVisible, reachable, type SettingsVisibility } from './visibility'
import { t as tr } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

export interface SettingsEntry {
  id: string
  label: string
  hint: string
  words: string
  path: string
}

export function settingsEntries(v: SettingsVisibility, assistantName = tr('settings.palette.text', { name: assistantNameNow() })): SettingsEntry[] {
  const n = (t: string) => t.replace(/\bAmbrose\b/g, assistantName)
  return allSections()
    .filter(({ group, page }) => reachable(v, group.id) && pageVisible(v, group.id, page.id))
    .map((s) => ({
      id: `settings:${s.section.id}`,
      label: tr('settings.palette.settings', { n: n(s.section.title) }),
      hint: n(s.group.pages.length > 1 && s.page.title !== s.section.title ? `${s.group.title} › ${s.page.title}` : s.group.title),
      words: `${sectionSearchText(s)} ${assistantName.toLowerCase()}`,
      path: `/settings/${s.group.id}/${s.page.id}#${s.section.id}`,
    }))
}
