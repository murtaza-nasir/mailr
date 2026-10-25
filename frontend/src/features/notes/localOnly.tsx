// "Local models only" on one note, list or table. The menus of the note editor, a card and a table show it as a switch.
// Anyone who can edit may turn it on; only the owner may turn it off (notes/localonly.py). An editor
// who cannot turn it off sees it on and locked, with the reason under it.

import { Lock } from 'lucide-react'
import { MenuItem } from '../../components/Popover'
import type { AccessLevel } from './types'
import { t } from '../../i18n'

export interface LocalOnlyFields {
  local_only: boolean
  can_lower_local_only?: boolean
  level: AccessLevel
}

/** Why the switch cannot be used now, or null when it can. */
export function localOnlyLock(x: LocalOnlyFields, online = true): string | null {
  if (x.level === 'view') return t('notes.localOnly.youCanViewThisSo')
  if (!online) return t('notes.localOnly.youAreOfflineChangeIt')
  if (x.local_only && x.can_lower_local_only === false) return t('notes.localOnly.onlyTheOwnerCanTurn')
  return null
}

/** The menu switch: on, off, or on and locked with its reason. */
// ACT note.local_only @web.note @web.notes, ACT table.local_only @web.table @web.notes
export function LocalOnlyItem({ item, noun, online = true, onToggle }: { item: LocalOnlyFields; noun: 'note' | 'list' | 'table'; online?: boolean; onToggle: (on: boolean) => void }) {
  const lock = localOnlyLock(item, online)
  return (
    <>
      <MenuItem checked={item.local_only} disabled={!!lock} icon={lock && item.local_only ? <Lock size={16} aria-hidden /> : undefined}
        tip={lock ?? (item.local_only ? t('notes.localOnly.hostedModelsCannotReadThis', { noun }) : t('notes.localOnly.keepThisAwayFromHosted', { noun }))}
        onSelect={() => onToggle(!item.local_only)}>
        {t('common.localModelsOnly')}
      </MenuItem>
      {lock && item.local_only && <div className="menu-hint" data-testid="local-only-lock">{lock}</div>}
    </>
  )
}

/** The toast after a change. */
export function localOnlyLabel(on: boolean): string {
  return on ? t('notes.localOnly.localModelsOnlyOn') : t('notes.localOnly.localModelsOnlyOff')
}
