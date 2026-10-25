// Which parts of Settings a user sees: simple or advanced mode (setting advanced_settings), and
// what their access policy allows (GET /me "access", backend core/access.py). Admins see everything.

import type { AccessFeature, AccessInfo, Settings, User } from '../../api/types'
import type { GroupId } from './groups'
import { t } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

/** The groups of Settings (groups.ts). */
export type SettingsTabId = GroupId

export type AssistantPart = 'about' | 'orders' | 'skills' | 'memory' | 'people' | 'models' | 'lab' | 'importance'

/** The assistant parts that only make sense while the assistant is on. */
const NEEDS_ASSISTANT: AssistantPart[] = ['about', 'orders', 'skills', 'memory', 'people']

export interface SettingsVisibility {
  admin: boolean
  advanced: boolean
  /** Tabs shown in the strip. A hidden tab still opens from a direct link (see `reachable`). */
  tabs: Record<SettingsTabId, boolean>
  /** Filters (the rules table) on the Filters tab; blocked addresses are always there. */
  filters: boolean
  /** Agents & API: tokens, connected apps and the audit log. Approvals and failed messages always show. */
  agentTools: boolean
  /** The user may mark an agent token local-only (policy switch "Mark agents as local-only"). */
  localOnlyTokens: boolean
  /** Label learning detail ("Favour recent labels" and its half-life). */
  learningDetail: boolean
  assistant: boolean
  /** The no-assistant message, when the assistant is off. */
  message: string | null
  parts: Record<AssistantPart, boolean>
}

const ALL_ON: Record<AccessFeature, boolean> = { assistant: true, choose_models: true, lab: true, api_tokens: true, local_only_tokens: true, hosted_models: true }
export const noAssistantMessage = (): string => t('settings.visibility.yourAdministratorHasntGivenYou', { name: assistantNameNow() })

export function settingsVisibility(me: { user: Pick<User, 'role'>; settings?: Partial<Settings>; access?: AccessInfo } | undefined): SettingsVisibility {
  const admin = me?.user.role === 'admin'
  const advanced = admin || me?.settings?.advanced_settings === true
  // An admin is never restricted. A member's features come from their access policy (the built-in
  // Everyone policy when no group of theirs has one); a server too old to send `access` shows all.
  const f = admin || !me?.access ? ALL_ON : me.access.features
  const assistant = f.assistant
  const parts = {} as Record<AssistantPart, boolean>
  for (const p of ['about', 'orders', 'skills', 'memory', 'people', 'models', 'lab', 'importance'] as AssistantPart[]) {
    parts[p] = NEEDS_ASSISTANT.includes(p) ? assistant : true
  }
  parts.lab = advanced && f.lab
  const tabs: Record<SettingsTabId, boolean> = {
    you: true, look: true, mail: true, calendar: true, notes: true, notifications: true, ambrose: true,
    agents: advanced && f.api_tokens,
    admin,
  }
  return {
    admin, advanced, tabs,
    filters: advanced,
    agentTools: advanced && f.api_tokens,
    localOnlyTokens: !!f.local_only_tokens,
    learningDetail: advanced,
    assistant,
    message: assistant ? null : (me?.access?.message ?? noAssistantMessage()),
    parts,
  }
}

/** A group opens from a link even when the strip leaves it out (an approval link opens Agents &
 *  API with its approvals), except Admin for a member. */
export function reachable(v: SettingsVisibility, group: string): boolean {
  if (group === 'admin') return v.admin
  return group in v.tabs
}

/** Whether a page of a group shows: Ambrose's pages follow the assistant and the access policy;
 *  Agents & API's own page follows the mode and the policy (a link still opens it: SettingsPage);
 *  every other page shows whenever its group does. */
export function pageVisible(v: SettingsVisibility, group: string, page: string): boolean {
  if (group === 'ambrose' && page in v.parts) return v.parts[page as AssistantPart]
  if (group === 'agents' && page === 'agents') return v.agentTools
  return true
}

/** The title of the Filters page: only blocked addresses show in simple mode. */
export function filtersTitle(v: SettingsVisibility): string {
  return v.filters ? t('settings.visibility.filtersAndBlocked') : t('settings.visibility.blockedAddresses')
}

/** The local-only part of the Create token dialog. Hidden without a local-only account (the flag
 *  would do nothing). With one, the checkbox when the policy allows local-only agents, else a short
 *  note that the administrator decides. */
export type LocalOnlyChoice = 'hidden' | 'note' | 'checkbox'

export function localOnlyChoice(localOnlyAccounts: number, allowed: boolean): LocalOnlyChoice {
  if (localOnlyAccounts === 0) return 'hidden'
  return allowed ? 'checkbox' : 'note'
}
